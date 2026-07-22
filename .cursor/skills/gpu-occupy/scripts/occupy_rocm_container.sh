#!/usr/bin/env bash
set -euo pipefail

CONTAINER="sglang-qwen35-397-jiaxwang0722-2"
FRACTION="0.80"
GPUS=""
COUNT=""
STATE_DIR="/tmp/sglang_scheduler_tp2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_SRC="$SCRIPT_DIR/rocm_hip_worker.cpp"
WORKER_BIN="/tmp/sglang_scheduler_tp_worker"
MODE="start"

usage() {
  cat <<USAGE
Usage:
  $0 4 --first
  $0 --gpus 0,1,2,3
  $0 --container sglang-qwen35-397-jiaxwang0722-2 --gpus 0,1,2,3
  $0 --status
  $0 --stop

Options:
  --container NAME   Docker container to use. Default: $CONTAINER
  --fraction VALUE   Target VRAM fraction. Default: $FRACTION
  --first            Use GPUs 0..COUNT-1 from the positional count.
  --gpus LIST        Comma-separated physical GPU IDs, e.g. 0,1,2,3.
  --status           Show recorded workers and matching processes.
  --stop             Stop recorded workers in the container.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container)
      CONTAINER="$2"
      shift 2
      ;;
    --fraction)
      FRACTION="$2"
      shift 2
      ;;
    --first)
      if [[ -z "$COUNT" ]]; then
        echo "--first requires a positional count" >&2
        exit 2
      fi
      GPUS="$(seq -s, 0 $((COUNT - 1)))"
      shift
      ;;
    --gpus)
      GPUS="$2"
      shift 2
      ;;
    --status)
      MODE="status"
      shift
      ;;
    --stop)
      MODE="stop"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    [0-9]*)
      COUNT="$1"
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == "true" ]]
}

ensure_container() {
  if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "container not found: $CONTAINER" >&2
    exit 1
  fi
  if ! container_running; then
    docker start "$CONTAINER" >/dev/null
  fi
}

status_workers() {
  ensure_container
  docker exec "$CONTAINER" bash -lc '
    set -e
    pid_file="/tmp/sglang_scheduler_tp2/pids.jsonl"
    if [[ -f "$pid_file" ]]; then
      cat "$pid_file"
    else
      echo "no pid file"
    fi
    echo
    ps -eo pid,stat,cmd | grep "sglang::scheduler_TP" | grep -v grep || true
  '
}

stop_workers() {
  ensure_container
  docker exec "$CONTAINER" bash -lc '
    set -e
    pid_file="/tmp/sglang_scheduler_tp2/pids.jsonl"
    if [[ ! -f "$pid_file" ]]; then
      echo "no recorded workers"
      exit 0
    fi
    python3 - <<PY
import json, os, signal
from pathlib import Path
pid_file = Path("/tmp/sglang_scheduler_tp2/pids.jsonl")
stopped = 0
for line in pid_file.read_text().splitlines():
    if not line.strip():
        continue
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue
    pid = int(record.get("pid", 0))
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmdline = handle.read().replace(b"\\0", b" ").decode("utf-8", "replace")
    except OSError:
        continue
    if "sglang::scheduler_TP" not in cmdline:
        continue
    try:
        os.kill(pid, signal.SIGTERM)
        stopped += 1
        print(f"sent SIGTERM to pid={pid} gpu={record.get('gpu')} title={record.get('title')}")
    except ProcessLookupError:
        pass
print(f"stopped {stopped} worker(s)")
pid_file.write_text("")
PY
  '
}

start_workers() {
  ensure_container
  if [[ -z "$GPUS" ]]; then
    if [[ -z "$COUNT" ]]; then
      echo "provide a count with --first or explicit --gpus" >&2
      usage >&2
      exit 2
    fi
    GPUS="$(seq -s, 0 $((COUNT - 1)))"
  fi

  if [[ ! -f "$WORKER_SRC" ]]; then
    echo "worker source missing: $WORKER_SRC" >&2
    exit 1
  fi

  docker cp "$WORKER_SRC" "$CONTAINER:/tmp/sglang_scheduler_tp_worker.cpp"
  docker exec "$CONTAINER" bash -lc '/opt/rocm/bin/hipcc /tmp/sglang_scheduler_tp_worker.cpp -O2 -o /tmp/sglang_scheduler_tp_worker'

  docker exec -d \
    -e SG_GPUS="$GPUS" \
    -e SG_FRACTION="$FRACTION" \
    -e SG_STATE_DIR="$STATE_DIR" \
    "$CONTAINER" bash -lc '
      set -euo pipefail
      IFS="," read -ra gpu_list <<< "$SG_GPUS"
      log_dir="$SG_STATE_DIR/logs"
      mkdir -p "$log_dir"
      pid_file="$SG_STATE_DIR/pids.jsonl"
      : > "$pid_file"
      tp_rank=0
      for gpu in "${gpu_list[@]}"; do
        title="sglang::scheduler_TP${tp_rank}"
        log="$log_dir/$(date +%s)_tp${tp_rank}_gpu${gpu}.log"
        SG_GPU="$gpu" SG_FRACTION="$SG_FRACTION" SG_TITLE="$title" \
          nohup bash -c "exec -a \"$title\" /tmp/sglang_scheduler_tp_worker" >"$log" 2>&1 &
        pid=$!
        printf "{\"pid\":%s,\"gpu\":%s,\"tp_rank\":%s,\"title\":\"%s\",\"log\":\"%s\",\"started_at\":%s}\n" \
          "$pid" "$gpu" "$tp_rank" "$title" "$log" "$(date +%s)" >> "$pid_file"
        tp_rank=$((tp_rank + 1))
      done
    '

  sleep 1
  status_workers
}

case "$MODE" in
  start) start_workers ;;
  status) status_workers ;;
  stop) stop_workers ;;
esac
