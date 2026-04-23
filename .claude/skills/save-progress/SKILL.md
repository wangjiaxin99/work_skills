---
name: save-progress
description: Saves the current task state into .claude/state/current-session.md and creates a timestamped checkpoint in .claude/state/checkpoints/. Use when stopping mid-task, after a meaningful milestone, or before context gets compacted.
argument-hint: "[short stop note]"
disable-model-invocation: true
---

# Save Progress

Save a resumable task handoff for the current work.

## Inputs
- Optional stop note: `$ARGUMENTS`

## Required outputs
1. Update `.claude/state/current-session.md`
2. Create a timestamped checkpoint in `.claude/state/checkpoints/`
3. Tell the user where the checkpoint was saved and how to resume from it

## What to capture
- Task summary in 2-4 sentences
- Completed work
- Verification already performed
- Current blockers or open questions
- The exact next step
- Files or directories that matter for resuming
- If a long-running process matters, reference `.claude/state/server-runtime.md`

## File format
Use this structure for both the current-session file and the checkpoint file:

```markdown
# Session: <short task name>

Saved: <timestamp>
Stop note: <short note or "none">

## Goal
<what this task is trying to achieve>

## Completed
- [x] <completed item>

## Verified
- <command or check>: <result>

## In Progress
- [ ] <current step>

## Blockers
- None

## Important Files
- `<path>`

## Next Step
<one exact next action>
```

## Checkpoint naming
- Save checkpoints under `.claude/state/checkpoints/`
- Use a timestamp plus a short slug, for example:
  - `.claude/state/checkpoints/2026-04-17-1530-benchmark-resume.md`

## Quality bar
- Be concrete, not generic
- Prefer exact commands, file paths, and verification results
- Do not just summarize the conversation; write a handoff another session can execute immediately

## Final response
Return:
- the checkpoint path
- a 1-2 sentence progress summary
- the exact `/resume ...` command to run next time
