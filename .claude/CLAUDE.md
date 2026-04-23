# Workspace Operating Notes

## Scope
- This file contains cross-workspace rules only.
- Subdirectory `CLAUDE.md` files add more specific guidance for their own areas.

## Persistent Task State
- Treat `.claude/state/current-session.md` as the canonical handoff file for the active task.
- Before ending a non-trivial task, run `/save-progress "<short note>"`.
- When starting or resuming, read `.claude/state/current-session.md` first. If the user gives a checkpoint path, use `/resume <path>`.
- Every meaningful stop point should capture:
  - what was completed
  - what verification already ran
  - any blockers
  - the exact next step

## Server And Process Awareness
- Do not assume a server is stopped or missing based only on chat history.
- First read `.claude/state/server-runtime.md`, then verify live state with process, port, health, and log checks.
- A `PostToolUse` startup monitor hook automatically watches common server-start commands after Bash succeeds.
- The startup monitor now uses a fast quick gate first, then starts background monitoring after success.
- If the quick gate fails, immediately use the `server-observer` subagent before retrying startup.
- When possible, start servers with an explicit port and a log file redirection so the automatic monitor can verify readiness faster and record useful state.
- Whenever you start, restart, or stop a long-running server or benchmark, update `.claude/state/server-runtime.md` with:
  - command
  - cwd
  - pid or pids
  - port or ports
  - health URL
  - log path
  - current status
  - last verified time
- If runtime state is unclear or logs are noisy, use the `server-observer` subagent.

## Correct vLLM Stop Procedure
- Do not kill a vLLM process based only on a PID shown by `nvidia-smi --query-compute-apps`; first verify that the PID is still live with `kill -0 <pid>` or `/proc/<pid>`.
- For a specific vLLM instance, identify it by **port first**. Use `.claude/state/server-runtime.md`, the listening port, the health endpoint, and `lsof -t -i:<port>` or equivalent to resolve the top-level `vllm serve` / `APIServer` PID. Prefer this port-first method over matching `VLLM::EngineCore` by name.
- Identify the live server from exact evidence: `.claude/state/server-runtime.md`, the listening port, the health endpoint, and `ps -fp <pid>`. Prefer exact PID-based inspection over broad `pkill -f`.
- Preferred shutdown order for vLLM:
  1. Send `SIGTERM` with `kill <pid>` to the top-level `vllm serve` or `APIServer` PID.
  2. Wait a few seconds, then re-check the port, health endpoint, child PIDs, and GPU memory.
  3. If exact live child worker PIDs remain, terminate only those remaining live PIDs.
- If the user explicitly says to stop **all** vLLM processes, use the user's preferred method exactly: enumerate only `VLLM::EngineCore` PIDs from `ps aux` or `ps -eo pid,cmd`, send `SIGTERM` to those PIDs first, re-check which ones are still alive, and use `kill -9` only for exact remaining live PIDs that did not exit.
- For the "stop all vLLM processes" case, do **not** use broad command-line matching such as `ps aux | grep [v]llm | awk '{print $2}' | xargs ...` or `pkill -f vllm`; those patterns are too broad and can hit unrelated wrapper or helper processes.
- Use `kill -9` only as a last resort for a still-live stuck process after `SIGTERM` fails. `kill -9` can skip CUDA cleanup and leave driver-side zombie GPU contexts.
- If `nvidia-smi` still reports GPU memory but the PID is already gone (`kill -0` fails and `/proc/<pid>` is missing), treat it as a driver-side zombie context. Do not keep retrying `kill`; escalate to `sudo nvidia-smi --gpu-reset -i <gpu>` or a host-level restart if reset is unavailable.
- After stopping a vLLM server, always verify that the port is down, health checks fail, GPU memory is released as expected, and `.claude/state/server-runtime.md` is updated.

## Safe Autonomy
- In this workspace, `kill` and `pkill` are acceptable for stopping project-local servers or benchmarks after verifying the target process.
- Prefer `kill -0`, process inspection, or port checks before sending a terminating signal.
- Avoid broad `pkill -f vllm` and avoid `grep [v]llm | awk '{print $2}' | xargs kill ...` for vLLM cleanup. For targeted shutdown, use the port-first vLLM procedure. For "stop all vLLM" cleanup, enumerate exact `VLLM::EngineCore` PIDs and act only on those PIDs.
- Avoid destructive filesystem or git-history operations unless the user explicitly asks.
- After a major code change produces a verified improvement or restores a previously broken behavior, stop and create a checkpoint before starting the next risky change. If commit authority is available from the user, make a git commit immediately; otherwise explicitly ask for a checkpoint commit and do not proceed into the next major edit batch without that checkpoint.

## Suggested Workflow
1. Read current task state.
2. Verify runtime state if a server or benchmark process is involved.
3. Do the work.
4. Record verification results as they happen.
5. Run `/save-progress` before stopping.
