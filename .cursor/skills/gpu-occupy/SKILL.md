---
name: gpu-occupy
description: Occupy a requested number of idle GPUs with scheduler-like worker processes. Use when the user says "帮我占用x张gpu", "占用 GPU", "占用几张卡", "reserve GPUs", "hold GPUs", or asks to scan idle GPUs and start GPU load without exposing gpu_stress.py in the process command line.
---

# GPU Occupy

## Purpose

Scan for idle GPUs, pick the requested number of cards, and start long-running CUDA workers that reserve GPU memory and keep compute busy. The worker behavior follows the local `gpu_stress.py` pattern: reserve about 80% of each selected GPU's memory, allocate large matrices, and continuously run `matmul`.

## When to Use

Use this skill when the user asks in Chinese or English to occupy, reserve, hold, or stress one or more GPUs, especially requests like:

- "帮我占用2张gpu"
- "占用4张卡"
- "scan idle GPUs and hold 1 GPU"

## Commands

From the `work_skills` repository root, start workers with:

```bash
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py 2
```

Useful options:

```bash
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py 2 --fraction 0.80
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py 2 --python /path/to/python-with-torch
GPU_OCCUPY_PYTHON=/path/to/python-with-torch python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py 2
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py --status
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py --stop
```

## Workflow

1. Parse the requested GPU count from the user request.
2. Run the launcher from `/home/jiaxwang/workspace/work_skills`.
3. Let the launcher find a Python with `torch.cuda`, scan GPUs with `nvidia-smi` or PyTorch fallback, choose idle cards, and start detached workers.
4. Report selected GPU IDs, PIDs, and the log directory to the user.
5. Do not run `/home/jiaxwang/workspace/gpu_stress.py` directly for this workflow.

## Process Naming

Workers are launched with command-line titles in the form `sglang::scheduler_TPn` so process listings do not expose the original `gpu_stress.py` filename. The `n` value is the selected-card order for this launch: first selected GPU -> `TP0`, second -> `TP1`, third -> `TP2`, and so on. The process is intentionally not hidden: PIDs are recorded under `~/.cache/sglang_scheduler_tp2/pids.jsonl`, and logs are written under `~/.cache/sglang_scheduler_tp2/logs/`.

## Idle GPU Selection

Default selection treats a GPU as idle when `nvidia-smi` is available:

- GPU utilization is at or below 5%
- used memory is at or below 15% of total memory

If `nvidia-smi` is unavailable, the launcher falls back to PyTorch free-memory checks and treats utilization as unknown/0. If not enough idle GPUs are available, report the current GPU usage and do not start a partial set unless the user asks for a smaller number.

## Validation

After starting workers, check:

```bash
nvidia-smi
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py --status
```

Use `--stop` to clean up workers started by this launcher. If no default Python has `torch.cuda`, pass `--python /path/to/python-with-torch` or set `GPU_OCCUPY_PYTHON`.
