#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROCESS_TITLE_PREFIX = "sglang::scheduler_TP"
DEFAULT_STATE_DIR = Path.home() / ".cache" / "sglang_scheduler_tp2"

WORKER_SOURCE = r'''
import ctypes
import sys

PROCESS_TITLE = sys.argv[3] if len(sys.argv) > 3 else "sglang::scheduler_TP0"


def set_process_title():
    try:
        import setproctitle
        setproctitle.setproctitle(PROCESS_TITLE)
        return
    except Exception:
        pass

    try:
        libc = ctypes.CDLL(None)
        libc.prctl(15, PROCESS_TITLE.encode("utf-8")[:15], 0, 0, 0)
    except Exception:
        pass


set_process_title()

import torch

gpu = int(sys.argv[1])
fraction = float(sys.argv[2])

torch.cuda.set_device(gpu)
free, total = torch.cuda.mem_get_info(gpu)
dev = f"cuda:{gpu}"

# Mirrors gpu_stress.py: use three matrices for continuous matmul, then pad memory.
target = int(total * fraction)
mat_bytes_budget = min(target // 3, free // 3)
dim = int((mat_bytes_budget / 4) ** 0.5)
dim = max(1024, (dim // 256) * 256)

a = torch.randn(dim, dim, device=dev)
b = torch.randn(dim, dim, device=dev)
c = torch.empty(dim, dim, device=dev)

pad = []
while True:
    alloc = torch.cuda.memory_reserved(gpu)
    remaining = target - alloc
    if remaining <= (512 << 20):
        break
    free_now, _ = torch.cuda.mem_get_info(gpu)
    chunk = min(remaining, free_now - (1 << 30))
    if chunk <= (256 << 20):
        break
    pad.append(torch.zeros(int(chunk // 4), dtype=torch.float32, device=dev))

alloc = torch.cuda.memory_reserved(gpu)
print(
    f"{PROCESS_TITLE} GPU{gpu}: reserved {alloc/1024**3:.1f}GB / "
    f"total {total/1024**3:.1f}GB ({alloc/total*100:.0f}%), matmul dim={dim}",
    flush=True,
)

while True:
    torch.matmul(a, b, out=c)
    torch.cuda.synchronize(gpu)
'''

TORCH_CHECK_CODE = '''
import sys
import torch
if not torch.cuda.is_available():
    print("torch imported but CUDA is not available", file=sys.stderr)
    raise SystemExit(3)
print(torch.__version__)
'''

TORCH_QUERY_CODE = '''
import json
import torch
rows = []
if not torch.cuda.is_available():
    raise SystemExit("torch.cuda is not available")
for index in range(torch.cuda.device_count()):
    free_bytes, total_bytes = torch.cuda.mem_get_info(index)
    total_mib = int(total_bytes // (1 << 20))
    free_mib = int(free_bytes // (1 << 20))
    rows.append({
        "index": index,
        "total_mib": total_mib,
        "used_mib": max(0, total_mib - free_mib),
        "util_pct": 0,
    })
print(json.dumps(rows))
'''


@dataclass(frozen=True)
class GpuInfo:
    index: int
    total_mib: int
    used_mib: int
    util_pct: int

    @property
    def used_fraction(self) -> float:
        return self.used_mib / self.total_mib if self.total_mib else 1.0


def candidate_pythons() -> list[str]:
    candidates: list[str] = []
    env_python = os.environ.get("GPU_OCCUPY_PYTHON")
    if env_python:
        candidates.append(env_python)
    candidates.append(sys.executable)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    patterns = [
        Path.home() / "miniconda3/envs/*/bin/python",
        Path.home() / "anaconda3/envs/*/bin/python",
        Path.home() / "mambaforge/envs/*/bin/python",
        Path.home() / "micromamba/envs/*/bin/python",
        Path("/opt/conda/bin/python"),
        Path("/opt/conda/envs/*/bin/python"),
    ]
    for pattern in patterns:
        if "*" in str(pattern):
            candidates.extend(glob.glob(str(pattern)))
        elif pattern.exists():
            candidates.append(str(pattern))

    seen = set()
    unique = []
    for candidate in candidates:
        resolved = shutil.which(candidate) or str(Path(candidate).expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def resolve_torch_python(requested_python: str | None) -> str:
    candidates = [requested_python] if requested_python else candidate_pythons()
    failures = []
    for candidate in candidates:
        if not candidate:
            continue
        python_path = shutil.which(candidate) or str(Path(candidate).expanduser())
        try:
            result = subprocess.run(
                [python_path, "-c", TORCH_CHECK_CODE],
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{python_path}: {exc}")
            continue
        if result.returncode == 0:
            return python_path
        reason = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        failures.append(f"{python_path}: {reason}")

    hint = "set --python /path/to/python or GPU_OCCUPY_PYTHON to an environment with torch.cuda"
    detail = "\n".join(failures[-8:])
    raise SystemExit(f"could not find a Python with torch.cuda; {hint}\n{detail}")


def query_gpus(torch_python: str) -> list[GpuInfo]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,memory.total,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return query_gpus_with_torch(torch_python, exc)

    gpus: list[GpuInfo] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            raise SystemExit(f"unexpected nvidia-smi output line: {line!r}")
        gpus.append(GpuInfo(*(int(part) for part in parts)))
    return sorted(gpus, key=lambda gpu: (gpu.used_fraction, gpu.util_pct, gpu.index))


def query_gpus_with_torch(torch_python: str, nvidia_smi_error: Exception) -> list[GpuInfo]:
    result = subprocess.run(
        [torch_python, "-c", TORCH_QUERY_CODE],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"failed to query GPUs with nvidia-smi ({nvidia_smi_error}) "
            f"and torch fallback failed: {(result.stderr or result.stdout).strip()}"
        )
    rows = json.loads(result.stdout)
    gpus = [GpuInfo(**row) for row in rows]
    return sorted(gpus, key=lambda gpu: (gpu.used_fraction, gpu.util_pct, gpu.index))


def idle_gpus(gpus: list[GpuInfo], max_util: int, max_used_fraction: float) -> list[GpuInfo]:
    return [
        gpu
        for gpu in gpus
        if gpu.util_pct <= max_util and gpu.used_fraction <= max_used_fraction
    ]


def state_paths(state_dir: Path) -> tuple[Path, Path, Path]:
    log_dir = state_dir / "logs"
    pid_file = state_dir / "pids.jsonl"
    return state_dir, log_dir, pid_file


def load_records(pid_file: Path) -> list[dict]:
    if not pid_file.exists():
        return []
    records = []
    for line in pid_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def append_record(pid_file: Path, record: dict) -> None:
    with pid_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def write_records(pid_file: Path, records: list[dict]) -> None:
    if not records:
        pid_file.write_text("")
        return
    with pid_file.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cmdline(pid: int) -> str:
    try:
        data = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def process_title(tp_rank: int) -> str:
    return f"{PROCESS_TITLE_PREFIX}{tp_rank}"


def start_worker(
    gpu: GpuInfo,
    fraction: float,
    log_dir: Path,
    pid_file: Path,
    torch_python: str,
    tp_rank: int,
) -> dict:
    title = process_title(tp_rank)
    log_path = log_dir / f"{int(time.time())}_tp{tp_rank}_gpu{gpu.index}.log"
    worker_b64 = base64.b64encode(WORKER_SOURCE.encode("utf-8")).decode("ascii")
    loader = 'import base64, os; exec(base64.b64decode(os.environ["SG_SCHED_WORKER_B64"]))'
    env = os.environ.copy()
    env["SG_SCHED_WORKER_B64"] = worker_b64

    with log_path.open("ab") as log_handle:
        proc = subprocess.Popen(
            [title, "-c", loader, str(gpu.index), str(fraction), title],
            executable=torch_python,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd="/",
            env=env,
            start_new_session=True,
            close_fds=True,
        )

    record = {
        "pid": proc.pid,
        "gpu": gpu.index,
        "fraction": fraction,
        "log": str(log_path),
        "python": torch_python,
        "started_at": int(time.time()),
        "tp_rank": tp_rank,
        "title": title,
    }
    append_record(pid_file, record)
    return record


def print_gpu_table(gpus: list[GpuInfo]) -> None:
    print("GPU  used/total MiB  used%  util%")
    for gpu in sorted(gpus, key=lambda item: item.index):
        print(
            f"{gpu.index:<4} {gpu.used_mib:>6}/{gpu.total_mib:<6} "
            f"{gpu.used_fraction * 100:>5.1f}  {gpu.util_pct:>5}"
        )


def show_status(pid_file: Path) -> int:
    records = load_records(pid_file)
    if not records:
        print("no recorded workers")
        return 0

    print("PID       GPU  STATE    COMMAND / LOG")
    for record in records:
        pid = int(record.get("pid", 0))
        running = is_running(pid)
        state = "running" if running else "stale"
        command = cmdline(pid) if running else ""
        print(f"{pid:<9} {record.get('gpu', '?'):<4} {state:<8} {command or record.get('log', '')}")
    return 0


def stop_workers(pid_file: Path) -> int:
    records = load_records(pid_file)
    if not records:
        print("no recorded workers")
        return 0

    survivors = []
    stopped = 0
    for record in records:
        pid = int(record.get("pid", 0))
        if not is_running(pid):
            continue
        command = cmdline(pid)
        title = str(record.get("title") or PROCESS_TITLE_PREFIX)
        if title not in command and PROCESS_TITLE_PREFIX not in command:
            survivors.append(record)
            print(f"skip pid {pid}: command line no longer matches {title!r}")
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
            stopped += 1
            print(f"sent SIGTERM to pid {pid} on GPU {record.get('gpu', '?')}")
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            survivors.append(record)
            print(f"failed to stop pid {pid}: {exc}")

    time.sleep(1.0)
    remaining = []
    for record in survivors:
        pid = int(record.get("pid", 0))
        if is_running(pid):
            remaining.append(record)
    write_records(pid_file, remaining)
    print(f"stopped {stopped} worker(s)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan idle GPUs and start scheduler-like GPU occupancy workers."
    )
    parser.add_argument("count", nargs="?", type=int, help="number of idle GPUs to occupy")
    parser.add_argument("--fraction", type=float, default=0.80, help="target memory fraction per GPU")
    parser.add_argument("--max-util", type=int, default=5, help="maximum GPU utilization for idle selection")
    parser.add_argument(
        "--max-used-fraction",
        type=float,
        default=0.15,
        help="maximum used memory fraction for idle selection",
    )
    parser.add_argument("--python", help="Python executable with torch.cuda; also supports GPU_OCCUPY_PYTHON")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--dry-run", action="store_true", help="show selected GPUs without starting workers")
    parser.add_argument("--status", action="store_true", help="show recorded worker status")
    parser.add_argument("--stop", action="store_true", help="stop workers started by this launcher")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_dir, log_dir, pid_file = state_paths(args.state_dir.expanduser())
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        return show_status(pid_file)
    if args.stop:
        return stop_workers(pid_file)

    if args.count is None:
        raise SystemExit("count is required unless --status or --stop is used")
    if args.count <= 0:
        raise SystemExit("count must be positive")
    if not 0.05 <= args.fraction <= 0.95:
        raise SystemExit("--fraction must be between 0.05 and 0.95")

    torch_python = resolve_torch_python(args.python)
    gpus = query_gpus(torch_python)
    candidates = idle_gpus(gpus, args.max_util, args.max_used_fraction)
    if len(candidates) < args.count:
        print(f"requested {args.count} idle GPU(s), but only found {len(candidates)}")
        print_gpu_table(gpus)
        return 2

    selected = candidates[: args.count]
    print("worker python:", torch_python)
    print("selected GPUs:", ", ".join(str(gpu.index) for gpu in selected))
    if args.dry_run:
        return 0

    records = [
        start_worker(gpu, args.fraction, log_dir, pid_file, torch_python, tp_rank)
        for tp_rank, gpu in enumerate(selected)
    ]
    time.sleep(1.0)
    for record in records:
        pid = int(record["pid"])
        state = "running" if is_running(pid) else "exited"
        print(
            f"pid={pid} gpu={record['gpu']} state={state} "
            f"title={record['title']} log={record['log']}"
        )
    print(f"pid file: {pid_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
