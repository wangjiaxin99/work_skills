#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


SERVER_PATTERNS = [
    ("vllm", re.compile(r"\bvllm(?:\s+bench)?\s+serve\b", re.IGNORECASE)),
    ("uvicorn", re.compile(r"\buvicorn\b", re.IGNORECASE)),
    ("http-server", re.compile(r"\bpython(?:3)?\s+-m\s+http\.server\b", re.IGNORECASE)),
    ("django", re.compile(r"\bmanage\.py\s+runserver\b", re.IGNORECASE)),
    ("streamlit", re.compile(r"\bstreamlit\s+run\b", re.IGNORECASE)),
    ("next-dev", re.compile(r"\bnext\s+dev\b|\b(?:npm|pnpm|yarn)\s+run\s+dev\b", re.IGNORECASE)),
    ("vite", re.compile(r"\bvite(?:\s+dev)?\b", re.IGNORECASE)),
]

DEFAULT_PORTS = {
    "vllm": 8000,
    "http-server": 8000,
    "django": 8000,
    "streamlit": 8501,
    "next-dev": 3000,
    "vite": 5173,
}

QUICK_GATE_TIMEOUTS = {
    "vllm": 25,
    "uvicorn": 10,
    "http-server": 5,
    "django": 8,
    "streamlit": 12,
    "next-dev": 15,
    "vite": 10,
}

TOTAL_MONITOR_TIMEOUTS = {
    "vllm": 90,
    "uvicorn": 45,
    "http-server": 20,
    "django": 30,
    "streamlit": 45,
    "next-dev": 60,
    "vite": 45,
}

QUICK_GATE_POLL_INTERVAL_SEC = 1
BACKGROUND_POLL_INTERVAL_SEC = 5
LOG_LINES = 20
LOG_CHARS = 1600
TAIL_BYTES = 16 * 1024

PROGRESS_MONITOR_TIMEOUTS = {
    "vllm": 360,
}

VLLM_PROGRESS_PATTERNS = [
    re.compile(r"vLLM is using nccl==", re.IGNORECASE),
    re.compile(r"DP group leader:", re.IGNORECASE),
    re.compile(r"Starting to load model", re.IGNORECASE),
    re.compile(r"Loading safetensors checkpoint shards", re.IGNORECASE),
    re.compile(r"Loading weights took", re.IGNORECASE),
    re.compile(r"Model loading took", re.IGNORECASE),
    re.compile(r"Using cache directory: .*torch_compile_cache", re.IGNORECASE),
    re.compile(r"torch\.compile took", re.IGNORECASE),
    re.compile(r"Initial profiling/warmup run took", re.IGNORECASE),
    re.compile(r"Profiling CUDA graph memory", re.IGNORECASE),
    re.compile(r"Estimated CUDA graph memory", re.IGNORECASE),
    re.compile(r"Available KV cache memory", re.IGNORECASE),
    re.compile(r"GPU KV cache size", re.IGNORECASE),
    re.compile(r"Capturing CUDA graphs", re.IGNORECASE),
    re.compile(r"Graph capturing finished", re.IGNORECASE),
]

VLLM_ERROR_PATTERNS = [
    re.compile(r"CUDA out of memory", re.IGNORECASE),
    re.compile(r"torch\.OutOfMemoryError", re.IGNORECASE),
    re.compile(r"RuntimeError: .*", re.IGNORECASE),
    re.compile(r"Engine core initialization failed", re.IGNORECASE),
    re.compile(r"Worker failed with error", re.IGNORECASE),
    re.compile(r"WorkerProc hit an exception", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"address already in use", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"no such file or directory", re.IGNORECASE),
]


@dataclass
class LaunchInfo:
    kind: str
    command: str
    cwd: Path
    host: str
    port: int | None
    health_urls: list[str]
    log_path: Path | None
    quick_gate_timeout_sec: int
    total_monitor_timeout_sec: int


@dataclass
class MonitorResult:
    ready: bool
    elapsed_sec: int
    check: str
    detail: str
    pid: str | None
    log_excerpt: str
    progressing: bool = False
    explicit_error: bool = False


def normalize_host(host: str | None) -> str:
    host = (host or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::", "[::]", "localhost"}:
        return "127.0.0.1"
    return host


def detect_kind(command: str) -> str | None:
    for kind, pattern in SERVER_PATTERNS:
        if pattern.search(command):
            return kind
    return None


def extract_host(command: str, kind: str) -> str:
    patterns = [
        r"--host(?:=|\s+)([^\s]+)",
        r"--bind(?:=|\s+)([^\s]+)",
    ]
    if kind == "django":
        patterns.insert(0, r"runserver(?:\s+([0-9a-zA-Z\.\-]+):\d+)")

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return normalize_host(match.group(1))
    return "127.0.0.1"


def extract_port(command: str, kind: str) -> int | None:
    kind_specific = {
        "http-server": [
            r"http\.server(?:\s+(\d+))",
        ],
        "django": [
            r"runserver(?:\s+[0-9a-zA-Z\.\-]+:)?(\d+)",
        ],
        "streamlit": [
            r"--server\.port(?:=|\s+)(\d+)",
        ],
    }
    generic = [
        r"--port(?:=|\s+)(\d+)",
        r"\s-p\s+(\d+)",
    ]

    for pattern in kind_specific.get(kind, []):
        match = re.search(pattern, command)
        if match:
            return int(match.group(1))

    for pattern in generic:
        match = re.search(pattern, command)
        if match:
            return int(match.group(1))

    return DEFAULT_PORTS.get(kind)


def detect_log_path(command: str, cwd: Path) -> Path | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    for idx, token in enumerate(tokens):
        if token == "tee" and idx + 1 < len(tokens):
            return resolve_path(tokens[idx + 1], cwd)
        if token in {">", "1>", ">>", "1>>"} and idx + 1 < len(tokens):
            return resolve_path(tokens[idx + 1], cwd)

    return None


def resolve_path(raw: str, cwd: Path) -> Path:
    raw = raw.strip().strip("'").strip('"')
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (cwd / path).resolve()


def build_health_urls(kind: str, host: str, port: int | None) -> list[str]:
    if port is None:
        return []

    base = f"http://{host}:{port}"
    if kind == "vllm":
        return [f"{base}/health", f"{base}/v1/models"]
    if kind == "streamlit":
        return [f"{base}/_stcore/health", f"{base}/"]
    if kind == "http-server":
        return [f"{base}/"]
    return [f"{base}/health", f"{base}/"]


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def http_probe(url: str) -> tuple[bool, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            status = response.getcode()
            return True, f"{url} -> HTTP {status}"
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return True, f"{url} -> HTTP {exc.code}"
        return False, f"{url} -> HTTP {exc.code}"
    except Exception as exc:
        return False, f"{url} -> {type(exc).__name__}"


def best_effort_pid(port: int) -> str | None:
    commands = [
        ["bash", "-lc", f"lsof -t -i:{port} 2>/dev/null | tr '\\n' ' '"],
        ["bash", "-lc", f"ss -ltnp '( sport = :{port} )' 2>/dev/null"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
        except Exception:
            continue
        output = (result.stdout or "").strip()
        if not output:
            continue
        if "LISTEN" in output:
            pid_match = re.search(r"pid=(\d+)", output)
            if pid_match:
                return pid_match.group(1)
        else:
            digits = " ".join(token for token in output.split() if token.isdigit())
            if digits:
                return digits
    return None


def read_recent_log(log_path: Path | None) -> str:
    if log_path is None or not log_path.exists():
        return ""

    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read()
    except OSError:
        return ""

    text = data.decode("utf-8", errors="replace")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) > LOG_LINES:
        lines = lines[-LOG_LINES:]
    text = "\n".join(line for line in lines if line.strip())
    if len(text) > LOG_CHARS:
        return text[-LOG_CHARS:]
    return text


def last_matching_line(log_excerpt: str, patterns: list[re.Pattern[str]]) -> str | None:
    if not log_excerpt:
        return None

    for line in reversed(log_excerpt.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in patterns:
            if pattern.search(stripped):
                return stripped
    return None


def detect_explicit_startup_failure(launch: LaunchInfo, log_excerpt: str) -> str | None:
    if launch.kind == "vllm":
        return last_matching_line(log_excerpt, VLLM_ERROR_PATTERNS)
    return None


def detect_startup_progress(launch: LaunchInfo, log_excerpt: str) -> str | None:
    if launch.kind == "vllm":
        return last_matching_line(log_excerpt, VLLM_PROGRESS_PATTERNS)
    return None


def find_project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / ".claude").exists():
            return candidate
    return start


def write_runtime_state(launch: LaunchInfo, status: str, summary: str, pid: str | None, log_excerpt: str) -> None:
    root = find_project_root(launch.cwd)
    state_path = root / ".claude" / "state" / "server-runtime.md"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    health_line = ", ".join(launch.health_urls) if launch.health_urls else "none inferred"
    log_path = str(launch.log_path) if launch.log_path else "none"
    pid_value = pid or "unknown"

    body = [
        "# Server Runtime State",
        "",
        f"Status: {status}",
        f"Last verified: {timestamp}",
        "",
        "## Runtime",
        f"- Command: `{launch.command}`",
        f"- CWD: `{launch.cwd}`",
        f"- PID: `{pid_value}`",
        f"- Port: `{launch.port if launch.port is not None else 'unknown'}`",
        f"- Health URL(s): {health_line}",
        f"- Log path: `{log_path}`",
        "",
        "## Summary",
        summary,
    ]

    if log_excerpt:
        body.extend(["", "## Recent Log Excerpt", "```text", log_excerpt, "```"])

    state_path.write_text("\n".join(body) + "\n", encoding="utf-8")


def monitor_launch(launch: LaunchInfo, timeout_sec: int, poll_interval_sec: int) -> MonitorResult:
    start = time.time()
    deadline = start + timeout_sec
    last_detail = "readiness check not started"
    last_log_excerpt = ""
    port_seen = False

    while time.time() < deadline:
        last_log_excerpt = read_recent_log(launch.log_path)
        explicit_failure = detect_explicit_startup_failure(launch, last_log_excerpt)
        if explicit_failure:
            pid = best_effort_pid(launch.port) if launch.port is not None else None
            return MonitorResult(
                ready=False,
                elapsed_sec=int(time.time() - start),
                check="log",
                detail=f"Explicit startup failure detected in logs: {explicit_failure}",
                pid=pid,
                log_excerpt=last_log_excerpt,
                explicit_error=True,
            )

        if launch.port is not None and port_open(launch.host, launch.port):
            port_seen = True
            if launch.health_urls:
                for url in launch.health_urls:
                    ok, detail = http_probe(url)
                    last_detail = detail
                    if ok:
                        pid = best_effort_pid(launch.port)
                        last_log_excerpt = read_recent_log(launch.log_path)
                        return MonitorResult(
                            ready=True,
                            elapsed_sec=int(time.time() - start),
                            check="http",
                            detail=detail,
                            pid=pid,
                            log_excerpt=last_log_excerpt,
                        )
            else:
                pid = best_effort_pid(launch.port)
                last_log_excerpt = read_recent_log(launch.log_path)
                return MonitorResult(
                    ready=True,
                    elapsed_sec=int(time.time() - start),
                    check="tcp",
                    detail=f"{launch.host}:{launch.port} is accepting TCP connections",
                    pid=pid,
                    log_excerpt=last_log_excerpt,
                )
        else:
            if launch.port is not None:
                last_detail = f"{launch.host}:{launch.port} is not listening yet"

        time.sleep(poll_interval_sec)

    failure_detail = last_detail
    if port_seen and launch.health_urls:
        failure_detail = f"Port opened but health checks never passed. Last probe: {last_detail}"

    progress_detail = detect_startup_progress(launch, last_log_excerpt)
    pid = best_effort_pid(launch.port) if launch.port is not None else None
    if progress_detail:
        return MonitorResult(
            ready=False,
            elapsed_sec=int(time.time() - start),
            check="log",
            detail=f"Startup is still making progress: {progress_detail}",
            pid=pid,
            log_excerpt=last_log_excerpt,
            progressing=True,
        )

    return MonitorResult(
        ready=False,
        elapsed_sec=int(time.time() - start),
        check="http" if launch.health_urls else "tcp",
        detail=failure_detail,
        pid=pid,
        log_excerpt=last_log_excerpt,
    )


def monitor_stability(launch: LaunchInfo, duration_sec: int, poll_interval_sec: int) -> MonitorResult:
    start = time.time()
    deadline = start + duration_sec
    last_detail = "stability monitoring has not started"
    last_log_excerpt = ""

    while time.time() < deadline:
        if launch.port is None or not port_open(launch.host, launch.port):
            last_log_excerpt = read_recent_log(launch.log_path)
            return MonitorResult(
                ready=False,
                elapsed_sec=int(time.time() - start),
                check="tcp",
                detail=f"{launch.host}:{launch.port} stopped listening during background monitoring",
                pid=best_effort_pid(launch.port) if launch.port is not None else None,
                log_excerpt=last_log_excerpt,
            )

        if launch.health_urls:
            healthy = False
            for url in launch.health_urls:
                ok, detail = http_probe(url)
                last_detail = detail
                if ok:
                    healthy = True
                    break
            if not healthy:
                last_log_excerpt = read_recent_log(launch.log_path)
                return MonitorResult(
                    ready=False,
                    elapsed_sec=int(time.time() - start),
                    check="http",
                    detail=f"Health check failed during background monitoring. Last probe: {last_detail}",
                    pid=best_effort_pid(launch.port),
                    log_excerpt=last_log_excerpt,
                )
        else:
            last_detail = f"{launch.host}:{launch.port} is still accepting TCP connections"

        last_log_excerpt = read_recent_log(launch.log_path)
        time.sleep(poll_interval_sec)

    return MonitorResult(
        ready=True,
        elapsed_sec=int(time.time() - start),
        check="http" if launch.health_urls else "tcp",
        detail=last_detail,
        pid=best_effort_pid(launch.port) if launch.port is not None else None,
        log_excerpt=last_log_excerpt,
    )


def detect_launch(command: str, cwd: Path) -> LaunchInfo | None:
    kind = detect_kind(command)
    if not kind:
        return None

    host = extract_host(command, kind)
    port = extract_port(command, kind)
    health_urls = build_health_urls(kind, host, port)
    log_path = detect_log_path(command, cwd)
    quick_gate_timeout_sec = QUICK_GATE_TIMEOUTS.get(kind, 12)
    total_monitor_timeout_sec = TOTAL_MONITOR_TIMEOUTS.get(kind, 45)

    return LaunchInfo(
        kind=kind,
        command=command,
        cwd=cwd,
        host=host,
        port=port,
        health_urls=health_urls,
        log_path=log_path,
        quick_gate_timeout_sec=quick_gate_timeout_sec,
        total_monitor_timeout_sec=total_monitor_timeout_sec,
    )


def payload_dir_for(launch: LaunchInfo) -> Path:
    return find_project_root(launch.cwd) / ".claude" / "state" / "monitor-payloads"


def payload_path_for(launch: LaunchInfo) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    slug = launch.kind.replace("/", "-")
    return payload_dir_for(launch) / f"{timestamp}-{slug}.json"


def spawn_background_monitor(
    launch: LaunchInfo,
    quick_elapsed_sec: int,
    mode: str = "stability",
    timeout_override_sec: int | None = None,
) -> None:
    total_timeout_sec = timeout_override_sec or launch.total_monitor_timeout_sec
    remaining_sec = max(0, total_timeout_sec - quick_elapsed_sec)
    if remaining_sec <= 0:
        return

    payload_path = payload_path_for(launch)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "launch": {
            "kind": launch.kind,
            "command": launch.command,
            "cwd": str(launch.cwd),
            "host": launch.host,
            "port": launch.port,
            "health_urls": launch.health_urls,
            "log_path": str(launch.log_path) if launch.log_path else None,
            "quick_gate_timeout_sec": launch.quick_gate_timeout_sec,
            "total_monitor_timeout_sec": launch.total_monitor_timeout_sec,
        },
        "remaining_sec": remaining_sec,
        "mode": mode,
    }
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--background-monitor", str(payload_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(launch.cwd),
    )


def run_background_monitor(payload_path: Path) -> int:
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    launch_data = payload.get("launch", {})
    launch = LaunchInfo(
        kind=launch_data["kind"],
        command=launch_data["command"],
        cwd=Path(launch_data["cwd"]),
        host=launch_data["host"],
        port=launch_data["port"],
        health_urls=launch_data["health_urls"],
        log_path=Path(launch_data["log_path"]) if launch_data.get("log_path") else None,
        quick_gate_timeout_sec=int(launch_data["quick_gate_timeout_sec"]),
        total_monitor_timeout_sec=int(launch_data["total_monitor_timeout_sec"]),
    )
    remaining_sec = int(payload.get("remaining_sec", 0))
    mode = str(payload.get("mode", "stability"))
    try:
        payload_path.unlink(missing_ok=True)
    except OSError:
        pass

    if remaining_sec <= 0 or launch.port is None:
        return 0

    if mode == "startup":
        result = monitor_launch(
            launch,
            timeout_sec=remaining_sec,
            poll_interval_sec=BACKGROUND_POLL_INTERVAL_SEC,
        )
        if result.ready:
            summary = (
                "Quick gate timed out, but background startup monitoring later observed a healthy service. "
                f"{result.detail}"
            )
            write_runtime_state(launch, "ready-confirmed", summary, result.pid, result.log_excerpt)
        elif result.progressing:
            summary = (
                "Quick gate timed out, and background startup monitoring still saw healthy startup progress, "
                "but readiness was not reached before the extended timeout. "
                f"Last progress: {result.detail}"
            )
            write_runtime_state(launch, "starting-timeout", summary, result.pid, result.log_excerpt)
        else:
            summary = (
                "Quick gate timed out, and background startup monitoring later observed an explicit failure or "
                f"never reached readiness. {result.detail}"
            )
            write_runtime_state(launch, "failed-startup", summary, result.pid, result.log_excerpt)
        return 0

    result = monitor_stability(launch, duration_sec=remaining_sec, poll_interval_sec=BACKGROUND_POLL_INTERVAL_SEC)
    if result.ready:
        summary = f"Startup quick gate passed, and background monitoring still saw healthy service via {result.detail}."
        write_runtime_state(launch, "ready-confirmed", summary, result.pid, result.log_excerpt)
    else:
        summary = (
            "Startup quick gate passed, but background monitoring later observed an unhealthy or disappeared service. "
            f"{result.detail}"
        )
        write_runtime_state(launch, "degraded", summary, result.pid, result.log_excerpt)
    return 0


def make_success_response(launch: LaunchInfo, result: MonitorResult) -> dict:
    context_lines = [
        f"Automatic startup monitor: `{launch.kind}` quick gate passed.",
        f"- Port: {launch.port}",
        f"- Check: {result.detail}",
        f"- Elapsed: {result.elapsed_sec}s",
        f"- Background monitoring: active (up to {max(0, launch.total_monitor_timeout_sec - result.elapsed_sec)}s more)",
    ]
    if launch.log_path:
        context_lines.append(f"- Log: {launch.log_path}")
    if result.pid:
        context_lines.append(f"- PID: {result.pid}")
    if result.log_excerpt:
        context_lines.append("Recent log excerpt:\n```text\n" + result.log_excerpt + "\n```")

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(context_lines),
        }
    }


def make_progress_response(launch: LaunchInfo, result: MonitorResult, remaining_sec: int) -> dict:
    context_lines = [
        f"Automatic startup monitor: `{launch.kind}` quick gate timed out, but logs show healthy startup progress.",
        f"- Port: {launch.port}",
        f"- Progress: {result.detail}",
        f"- Elapsed: {result.elapsed_sec}s",
        f"- Background startup monitoring: active (up to {remaining_sec}s more)",
        "- Do not assume readiness yet; wait for a health check or later startup confirmation before dependent tasks.",
    ]
    if launch.log_path:
        context_lines.append(f"- Log: {launch.log_path}")
    if result.log_excerpt:
        context_lines.append("Recent log excerpt:\n```text\n" + result.log_excerpt + "\n```")

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(context_lines),
        }
    }


def make_failure_response(launch: LaunchInfo, result: MonitorResult) -> dict:
    reason = (
        f"Detected a `{launch.kind}` server-start command, but the quick startup gate did not pass "
        f"within {result.elapsed_sec}s. Do not continue with tasks that depend on this server "
        f"until startup is diagnosed."
    )
    context_lines = [
        "Automatic startup monitor quick gate failed.",
        f"- Kind: {launch.kind}",
        f"- Port: {launch.port if launch.port is not None else 'unknown'}",
        f"- Last check: {result.detail}",
        f"- Elapsed: {result.elapsed_sec}s",
        "- Next action: immediately use `server-observer` to diagnose startup failure before retrying.",
    ]
    if launch.log_path:
        context_lines.append(f"- Log: {launch.log_path}")
    if result.pid:
        context_lines.append(f"- PID still present: {result.pid}")
    if result.log_excerpt:
        context_lines.append("Recent log excerpt:\n```text\n" + result.log_excerpt + "\n```")

    return {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(context_lines),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background-monitor")
    args, _ = parser.parse_known_args()

    if args.background_monitor:
        return run_background_monitor(Path(args.background_monitor))

    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    cwd = Path(payload.get("cwd", ".")).resolve()
    launch = detect_launch(command, cwd)
    if launch is None:
        return 0

    if launch.port is None:
        summary = "Detected a likely server-start command, but no port could be inferred automatically."
        write_runtime_state(launch, "unknown-port", summary, None, "")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            "Detected a likely server-start command, but no port could be inferred. "
                            "Do not assume readiness; run an explicit health or port check before dependent tasks."
                        ),
                    }
                }
            )
        )
        return 0

    result = monitor_launch(
        launch,
        timeout_sec=launch.quick_gate_timeout_sec,
        poll_interval_sec=QUICK_GATE_POLL_INTERVAL_SEC,
    )

    if result.ready:
        summary = (
            f"Quick gate passed via {result.detail} after {result.elapsed_sec}s. "
            "Background monitoring started."
        )
        write_runtime_state(launch, "ready", summary, result.pid, result.log_excerpt)
        spawn_background_monitor(launch, result.elapsed_sec)
        print(json.dumps(make_success_response(launch, result)))
        return 0

    if result.progressing:
        extended_timeout_sec = max(
            launch.total_monitor_timeout_sec,
            PROGRESS_MONITOR_TIMEOUTS.get(launch.kind, launch.total_monitor_timeout_sec),
        )
        summary = (
            f"Quick gate timed out after {result.elapsed_sec}s, but logs show healthy startup progress. "
            f"{result.detail} Background startup monitoring started."
        )
        write_runtime_state(launch, "starting", summary, result.pid, result.log_excerpt)
        spawn_background_monitor(
            launch,
            result.elapsed_sec,
            mode="startup",
            timeout_override_sec=extended_timeout_sec,
        )
        print(
            json.dumps(
                make_progress_response(
                    launch,
                    result,
                    remaining_sec=max(0, extended_timeout_sec - result.elapsed_sec),
                )
            )
        )
        return 0

    summary = f"Quick gate failed after {result.elapsed_sec}s. {result.detail}"
    write_runtime_state(launch, "failed-quick-gate", summary, result.pid, result.log_excerpt)
    print(json.dumps(make_failure_response(launch, result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
