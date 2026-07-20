---
name: nvitop-rocm-setup
description: Install, repair, and verify the ROCm fork of nvitop for AMD GPUs. Use when the user asks to install nvitop, run nvitop on ROCm, monitor AMD GPUs, or fix nvitop errors on MI-series GPUs.
---

# nvitop ROCm Setup

## Purpose

Install `nvitop` from BowenBao's ROCm branch so typing `nvitop` in a terminal launches the AMD GPU monitor.

Expected source:

```bash
git clone https://github.com/BowenBao/nvitop.git
cd nvitop
git checkout rocm
pip install .
```

## Workflow

1. Check whether `/home/jiaxwang/workspace/nvitop` already exists.
   - If it does not exist, clone `https://github.com/BowenBao/nvitop.git`.
   - If it exists, inspect it before reusing it; do not delete user changes.
2. Ensure the repo is on the `rocm` branch:

```bash
git checkout rocm
```

3. Install from the repo root:

```bash
pip install .
```

4. Verify command discovery:

```bash
command -v nvitop
python3 -m pip show nvitop
nvitop --version
nvitop --help
```

If `pip install .` places the executable in `~/.local/bin/nvitop` but an already-open terminal still says `nvitop: command not found`, refresh the shell or expose a stable wrapper:

```bash
source ~/.bashrc
```

If sudo is available and `/usr/local/bin` is on `PATH`, make a system PATH symlink:

```bash
sudo ln -sf "$HOME/.local/bin/nvitop" /usr/local/bin/nvitop
```

5. Verify ROCm output:

```bash
nvitop --once
```

Success means the output shows `ROCm Driver Version` and AMD GPUs such as Instinct MI devices. In an interactive terminal, typing `nvitop` should launch the live monitor UI.

## Known ROCm Fix

On ROCm machines, the fork may work but still print a misleading NVML error before falling back to ROCm:

```text
FATAL ERROR: NVIDIA Management Library (NVML) not found.
```

If `nvitop --once` still shows AMD GPUs after that message, the ROCm path works and the NVML message is only probe noise.

Patch `nvitop/api/device.py` in `Device.is_rocm()` so the NVML probe is silent while checking for ROCm:

```python
logger_disabled = libnvml.LOGGER.disabled
try:
    libnvml.LOGGER.disabled = True
    libnvml.nvmlQuery('nvmlDeviceGetCount', default=0)
    cls.__is_rocm__ = False
except (libnvml.NVMLError_LibraryNotFound, libnvml.NVMLError):
    librocm.initializeRsmi()
    _ = librocm.listDevices()
    cls.__is_rocm__ = True
finally:
    libnvml.LOGGER.disabled = logger_disabled
```

Then reinstall:

```bash
pip install .
nvitop --once
```

## Troubleshooting

- `nvitop: command not found`
  - `pip install .` may have installed into `~/.local/bin`; ensure `~/.local/bin` is on `PATH`.
  - Old terminals may need `source ~/.bashrc`.
  - If needed, symlink `~/.local/bin/nvitop` into `/usr/local/bin/nvitop`.
- `ROCm SMI library` load errors
  - Check that ROCm is installed and `librocm_smi64.so` exists, usually under `/opt/rocm/lib`.
  - If needed, set `ROCM_PATH=/opt/rocm` or `ROCM_SMI_LIB_PATH=/opt/rocm/lib/librocm_smi64.so`.
- `NVML not found` appears but ROCm GPUs render
  - Apply the silent NVML probe patch above and reinstall.
- `pip install .` succeeds with warnings about unrelated system packages
  - Warnings such as invalid Ubuntu package versions from `distro-info` or `python-debian` can be ignored if `nvitop --once` works.

## Reporting Back

After installation or repair, tell the user:

- where the repo was installed
- which branch is active
- where the `nvitop` command resolves
- whether `nvitop --once` displayed ROCm GPUs
- any errors fixed, especially the NVML probe-noise patch
