---
name: resume
description: Restores work from .claude/state/current-session.md or a provided checkpoint file, verifies that the current workspace still matches it, and identifies the exact next step. Use when resuming paused work in a fresh Claude Code session.
argument-hint: "[optional checkpoint path]"
disable-model-invocation: true
---

# Resume

Resume a paused task safely instead of relying on chat history alone.

## Inputs
- Optional checkpoint path: `$ARGUMENTS`

## Source selection
- If `$ARGUMENTS` is provided, read that checkpoint file first
- Otherwise read `.claude/state/current-session.md`
- Also read `.claude/state/server-runtime.md` if it exists and the task involves a long-running process

## Required steps
1. Read the chosen checkpoint file completely
2. Summarize:
   - task goal
   - completed work
   - verification already done
   - blockers
   - exact next step
3. Verify the workspace still matches the checkpoint:
   - important files still exist
   - relevant files were not unexpectedly replaced
   - if server state matters, verify the saved runtime state is still plausible
4. If something drifted, explain the mismatch before continuing
5. If the state is still usable, propose the next concrete action

## Output format
Use a short resume brief:

```text
Resuming: <task name>
- Goal: <goal>
- Completed: <most important completed items>
- Verified: <most important checks>
- Blockers: <none or blockers>
- Next step: <exact next action>
```

## Important rule
- Do not blindly continue from saved state
- Always check whether the current workspace still matches the checkpoint closely enough to proceed safely
