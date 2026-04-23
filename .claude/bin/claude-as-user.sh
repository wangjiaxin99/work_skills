#!/usr/bin/env bash
set -euo pipefail

CLAUDE_USER_NAME="${CLAUDE_USER_NAME:-claudeuser}"
CLAUDE_WORKDIR="${CLAUDE_WORKDIR:-/app}"
READONLY_MOUNTS=("/group" "/mnt" "/scratch")
CLAUDE_USER_HOME="$(getent passwd "${CLAUDE_USER_NAME}" | cut -d: -f6)"
CLAUDE_VENV_ROOT="${CLAUDE_VENV_ROOT:-/opt/venv}"
ROOT_LOCAL_SOURCE="${ROOT_LOCAL_SOURCE:-/root/.local}"
ROOT_LOCAL_STAGE="${ROOT_LOCAL_STAGE:-/opt/claude-root-local}"
CLAUDE_NATIVE_SOURCE="${CLAUDE_NATIVE_SOURCE:-${ROOT_LOCAL_STAGE}/share/claude}"
CLAUDE_NATIVE_USER_PATH="${CLAUDE_NATIVE_USER_PATH:-${CLAUDE_USER_HOME}/.local/share/claude}"
TARGET_PATH="${TARGET_PATH:-${CLAUDE_VENV_ROOT}/bin:${CLAUDE_USER_HOME}/.local/bin:/root/.local/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
CLAUDE_XDG_CACHE_HOME="${CLAUDE_XDG_CACHE_HOME:-${CLAUDE_USER_HOME}/.cache}"
CLAUDE_XDG_CONFIG_HOME="${CLAUDE_XDG_CONFIG_HOME:-${CLAUDE_USER_HOME}/.config}"
CLAUDE_HF_HOME="${CLAUDE_HF_HOME:-${CLAUDE_XDG_CACHE_HOME}/huggingface}"
CLAUDE_HF_HUB_CACHE="${CLAUDE_HF_HUB_CACHE:-${CLAUDE_HF_HOME}/hub}"
CLAUDE_VLLM_CACHE_ROOT="${CLAUDE_VLLM_CACHE_ROOT:-${CLAUDE_XDG_CACHE_HOME}/vllm}"
CLAUDE_VLLM_CONFIG_ROOT="${CLAUDE_VLLM_CONFIG_ROOT:-${CLAUDE_XDG_CONFIG_HOME}/vllm}"
CLAUDE_TORCHINDUCTOR_CACHE_DIR="${CLAUDE_TORCHINDUCTOR_CACHE_DIR:-${CLAUDE_VLLM_CACHE_ROOT}/torchinductor}"
CLAUDE_TRITON_CACHE_DIR="${CLAUDE_TRITON_CACHE_DIR:-${CLAUDE_VLLM_CACHE_ROOT}/triton}"

bind_readonly() {
  local source_path="$1"
  local target_path="$2"
  mount --bind "${source_path}" "${target_path}"
  mount -o remount,bind,ro "${target_path}"
}

setup_root_compat_view() {
  if [[ ! -d "${ROOT_LOCAL_SOURCE}" ]]; then
    return
  fi

  mkdir -p "${ROOT_LOCAL_STAGE}"
  bind_readonly "${ROOT_LOCAL_SOURCE}" "${ROOT_LOCAL_STAGE}"

  # The old /opt/venv interpreter chain resolves through /root/.local.
  # Recreate only that subtree in a private namespace so claudeuser can
  # reuse the existing root-installed virtualenv without changing the host.
  mount -t tmpfs -o mode=755,nodev,nosuid tmpfs /root
  mkdir -p /root/.local
  bind_readonly "${ROOT_LOCAL_STAGE}" /root/.local
}

bind_claude_native() {
  if [[ ! -d "${CLAUDE_NATIVE_SOURCE}" ]]; then
    return
  fi

  mkdir -p "$(dirname "${CLAUDE_NATIVE_USER_PATH}")"
  mkdir -p "${CLAUDE_NATIVE_USER_PATH}"
  bind_readonly "${CLAUDE_NATIVE_SOURCE}" "${CLAUDE_NATIVE_USER_PATH}"
}

ensure_user_runtime_dirs() {
  local dir
  for dir in \
    "${CLAUDE_USER_HOME}/.local" \
    "$(dirname "${CLAUDE_NATIVE_USER_PATH}")" \
    "${CLAUDE_XDG_CACHE_HOME}" \
    "${CLAUDE_XDG_CONFIG_HOME}" \
    "${CLAUDE_HF_HOME}" \
    "${CLAUDE_HF_HUB_CACHE}" \
    "${CLAUDE_VLLM_CACHE_ROOT}" \
    "${CLAUDE_VLLM_CONFIG_ROOT}" \
    "${CLAUDE_TORCHINDUCTOR_CACHE_DIR}" \
    "${CLAUDE_TRITON_CACHE_DIR}"; do
    mkdir -p "${dir}"
    chown "${CLAUDE_USER_NAME}:${CLAUDE_USER_NAME}" "${dir}"
  done
}

run_as_target_user() {
  local quoted_cmd
  local quoted_path
  quoted_cmd="$(printf '%q ' "$@")"
  quoted_path="$(printf '%q' "${TARGET_PATH}")"
  ensure_user_runtime_dirs
  exec env \
    AMD_LLM_GATEWAY_KEY="${AMD_LLM_GATEWAY_KEY:-}" \
    HOME="${CLAUDE_USER_HOME}" \
    USER="${CLAUDE_USER_NAME}" \
    LOGNAME="${CLAUDE_USER_NAME}" \
    VIRTUAL_ENV="${CLAUDE_VENV_ROOT}" \
    su -m -s /bin/bash "${CLAUDE_USER_NAME}" -c "export HOME=\"${CLAUDE_USER_HOME}\" USER=\"${CLAUDE_USER_NAME}\" LOGNAME=\"${CLAUDE_USER_NAME}\" PATH=${quoted_path} VIRTUAL_ENV=\"${CLAUDE_VENV_ROOT}\" XDG_CACHE_HOME=\"${CLAUDE_XDG_CACHE_HOME}\" XDG_CONFIG_HOME=\"${CLAUDE_XDG_CONFIG_HOME}\" HF_HOME=\"${CLAUDE_HF_HOME}\" HUGGINGFACE_HUB_CACHE=\"${CLAUDE_HF_HUB_CACHE}\" VLLM_CACHE_ROOT=\"${CLAUDE_VLLM_CACHE_ROOT}\" VLLM_CONFIG_ROOT=\"${CLAUDE_VLLM_CONFIG_ROOT}\" TORCHINDUCTOR_CACHE_DIR=\"${CLAUDE_TORCHINDUCTOR_CACHE_DIR}\" TRITON_CACHE_DIR=\"${CLAUDE_TRITON_CACHE_DIR}\"; cd \"${CLAUDE_WORKDIR}\" && exec ${quoted_cmd}"
}

if [[ "${1:-}" == "--inside-namespace" ]]; then
  shift
  mount --make-rprivate /

  for path in "${READONLY_MOUNTS[@]}"; do
    if [[ -e "${path}" ]]; then
      bind_readonly "${path}" "${path}"
    fi
  done

  setup_root_compat_view
  bind_claude_native

  if [[ "${1:-}" == "--" ]]; then
    shift
    if [[ "$#" -eq 0 ]]; then
      echo "No command provided after --" >&2
      exit 1
    fi
    run_as_target_user "$@"
  else
    run_as_target_user claude "$@"
  fi
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this wrapper as root so it can create a private mount namespace and remount readonly paths." >&2
  exit 1
fi

if ! id "${CLAUDE_USER_NAME}" >/dev/null 2>&1; then
  echo "User ${CLAUDE_USER_NAME} does not exist." >&2
  exit 1
fi

exec unshare --mount --fork "$0" --inside-namespace "$@"
