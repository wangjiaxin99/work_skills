#!/usr/bin/env bash
set -euo pipefail

if [ -z "${AMD_LLM_GATEWAY_KEY:-}" ] && [ -f "${HOME}/.bashrc" ]; then
    export PS1="${PS1:-claude-healthcheck$ }"
    set +u
    # shellcheck disable=SC1090
    . "${HOME}/.bashrc"
    set -u
fi

status=0

claude_path="$(command -v claude 2>/dev/null || true)"
route_path="$(command -v claude-route 2>/dev/null || true)"

if [ -n "${claude_path}" ]; then
    echo "claude: found (${claude_path})"
else
    echo "claude: missing"
    status=1
fi

if [ -n "${route_path}" ]; then
    echo "claude-route: found (${route_path})"
else
    echo "claude-route: missing"
    status=1
fi

if [ -n "${AMD_LLM_GATEWAY_KEY:-}" ]; then
    echo "AMD_LLM_GATEWAY_KEY: set"
else
    echo "AMD_LLM_GATEWAY_KEY: missing"
    status=1
fi

if [ -f "${HOME}/.claude/settings.json" ]; then
    echo "settings.json: found"
else
    echo "settings.json: missing"
    status=1
fi

if [ -n "${route_path}" ]; then
    route_json="$(claude-route)"
    printf '%s\n' "${route_json}"

    if ! ROUTE_JSON="${route_json}" python3 - <<'PY'
import json
import os
import sys

supported_models = {"claude-sonnet-4.6", "claude-opus-4.6"}

try:
    route = json.loads(os.environ["ROUTE_JSON"])
except Exception as exc:
    print(f"claude-route JSON parse failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

if route.get("mode") != "direct":
    print("route status: not direct", file=sys.stderr)
    raise SystemExit(1)

if route.get("backend") != "claude-amd-anthropic":
    print("route backend: unexpected", file=sys.stderr)
    raise SystemExit(1)

if route.get("settings_parse_error"):
    print("settings parse error: " + str(route["settings_parse_error"]), file=sys.stderr)
    raise SystemExit(1)

normalized_model = route.get("normalized_model")
if normalized_model not in supported_models:
    print(
        "normalized model: unsupported -> " + repr(normalized_model),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
    then
        status=1
    fi
fi

exit "${status}"
