---
name: gpu-occupy
description: Occupy requested idle GPUs with scheduler-like worker processes. Use when the user says "帮我占用x张gpu", "占用前x张卡", "占用 GPU", "占用几张卡", "reserve GPUs", "hold GPUs", or asks to scan idle GPUs and start GPU load without exposing gpu_stress.py in the process command line.
---

# GPU Occupy

## Purpose

Occupy requested GPUs with long-running worker processes that reserve about 80% VRAM and keep the device busy. On this ROCm machine, prefer the container launcher so the workers run inside `sglang-qwen35-397-jiaxwang0722-2` and process listings show only scheduler-like names.

## When to Use

Use this skill when the user asks in Chinese or English to occupy, reserve, hold, or stress one or more GPUs, especially requests like:

- "帮我占用2张gpu"
- "占用前4张卡"
- "scan idle GPUs and hold 1 GPU"

## Preferred ROCm Container Workflow

Default container:

```bash
sglang-qwen35-397-jiaxwang0722-2
```

From the `work_skills` repository root, occupy the first N physical GPUs with:

```bash
.cursor/skills/gpu-occupy/scripts/occupy_rocm_container.sh 4 --first
```

Equivalent explicit form:

```bash
.cursor/skills/gpu-occupy/scripts/occupy_rocm_container.sh --gpus 0,1,2,3
```

Useful options:

```bash
.cursor/skills/gpu-occupy/scripts/occupy_rocm_container.sh --container sglang-qwen35-397-jiaxwang0722-2 --gpus 0,1,2,3
.cursor/skills/gpu-occupy/scripts/occupy_rocm_container.sh --status
.cursor/skills/gpu-occupy/scripts/occupy_rocm_container.sh --stop
```

## Process Naming

Workers must show only the command-line title, without GPU id, fraction, script names, or source file names. Required display:

```text
sglang::scheduler_TP0
sglang::scheduler_TP1
sglang::scheduler_TP2
sglang::scheduler_TP3
```

For `TPn`, `n` is the selected-card order for this launch:

- first selected GPU -> `TP0`
- second selected GPU -> `TP1`
- third selected GPU -> `TP2`

The ROCm launcher passes GPU id, fraction, and title through environment variables, then starts the compiled HIP worker with `exec -a`, so tools such as `nvitop` do not display extra arguments like `1 0.80 sglang::scheduler_TP1`.

## Operational Rules

1. If the user says "前4张卡", use physical GPUs `0,1,2,3`; do not reorder by free-memory ranking.
2. Before starting, stop existing workers if the user asks to replace them.
3. If the target container is stopped, start it with `docker start`.
4. Compile `scripts/rocm_hip_worker.cpp` inside the target container with `/opt/rocm/bin/hipcc`.
5. Write container worker records under `/tmp/sglang_scheduler_tp2/pids.jsonl` and logs under `/tmp/sglang_scheduler_tp2/logs/`.
6. Do not run `/home/jiaxwang/workspace/gpu_stress.py` directly for the ROCm/container workflow.

## Validation

After starting workers, validate both process names and GPU occupancy:

```bash
docker exec sglang-qwen35-397-jiaxwang0722-2 bash -lc 'ps -eo pid,stat,cmd | grep "sglang::scheduler_TP" | grep -v grep'
rocm-smi --showuse --showmemuse
```

Expected result for occupying the first 4 cards:

- GPU `0-3` show high GPU utilization and about 80% VRAM use
- GPU `4-7` are unchanged
- process command lines show only `sglang::scheduler_TP0` through `sglang::scheduler_TP3`

## PyTorch Fallback

`scripts/sglang_scheduler_tp2.py` remains available for CUDA/PyTorch environments:

```bash
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py 2 --fraction 0.80
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py --status
python3 .cursor/skills/gpu-occupy/scripts/sglang_scheduler_tp2.py --stop
```

Use the ROCm container workflow first on AMD/ROCm hosts.
