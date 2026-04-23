# Non-root Claude setup

## What was configured
- Created a non-root user: `claudeuser`
- Changed `/app` ownership to `claudeuser:claudeuser` so Claude can write in the workspace
- Added a wrapper script: `.claude/bin/claude-as-user.sh`

## What the wrapper does
When started as root, the wrapper:
1. Creates a private mount namespace
2. Remounts these paths read-only inside that namespace:
   - `/group`
   - `/mnt`
   - `/scratch`
3. Creates a minimal private `/root` view and bind-mounts root's existing `.local` tree there as readonly
4. Reuses the existing root-installed `/opt/venv` environment so commands like `python3` and `vllm` still resolve
5. Creates writable user-owned cache/config directories for Hugging Face, Transformers, Triton, Torch Inductor, and vLLM under `claudeuser`'s home
6. Bind-mounts the existing root-installed Claude native binaries into `claudeuser`'s home
7. Drops privileges to `claudeuser`
8. Starts `claude` in `/app`

This means the Claude process sees those three mounts as read-only, while `/app` stays writable.

## Usage
Start Claude as the non-root user with readonly mounts:

```bash
/app/.claude/bin/claude-as-user.sh --resume
```

Start Claude normally without resume:

```bash
/app/.claude/bin/claude-as-user.sh
```

Run any other command as `claudeuser` with the same readonly mount view:

```bash
/app/.claude/bin/claude-as-user.sh -- bash -lc 'whoami && touch /app/test && touch /scratch/test'
```

The `/scratch/test` write should fail because `/scratch` is remounted readonly in the wrapper namespace.

## Stronger, container-level enforcement
The wrapper protects the launched Claude session, but the strongest setup is still to mount these paths readonly when the container starts:

```bash
-v /group:/group:ro \
-v /mnt:/mnt:ro \
-v /scratch:/scratch:ro
```

If you recreate the container, prefer those `:ro` flags as well.
