---
name: claude-amd-direct-setup
description: Install, configure, repair, and verify Claude Code against AMD LLM Gateway in direct mode. Use when the user mentions Claude Code, AMD LLM Gateway, ANTHROPIC_BASE_URL, ANTHROPIC_CUSTOM_HEADERS, 401 missing subscription key, which claude, claude-route, or claude -p, or when Claude must be forced through a wrapper entrypoint.
---

# Claude AMD Direct Setup

## Purpose

Provide a reusable, local-first workflow for connecting Claude Code directly to AMD LLM Gateway. The intended end state is:

- `claude` always goes through a wrapper before invoking the real Claude Code binary
- `~/.claude/settings.json` keeps Claude Code-managed fields while the wrapper enforces `apiKeyHelper` and `model`
- only the direct AMD Anthropic endpoint is used, with no local proxy fallback
- only `sonnet` and `opus` are treated as supported settings model aliases
- validation always includes a real `claude -p` request, not just route inspection or a healthcheck
- if the official installer downloads the binary but hangs during the final install step, setup can still be completed by wiring the downloaded binary into the wrapper flow

## When to Use

- first-time Claude Code installation or reinstallation is needed
- Claude must be locked to AMD Gateway direct mode
- `401 missing subscription key` appears
- `which claude` resolves to the wrong entrypoint and bypasses the wrapper
- `claude-route` looks correct but real `claude -p` requests still fail
- `/model` persisted an AMD-incompatible alias such as `opus[1m]`

## Prerequisites

The wrapper does not need to be prepared in advance. This skill should create or repair the wrapper after Claude Code itself is installed.

Required conditions:

- `bash`, `curl`, and `python3` are available
- the machine can reach the Claude installer and AMD Gateway endpoints
- the user provides `AMD_LLM_GATEWAY_KEY` if automatic local setup is expected
- there is a writable location for the wrapper, usually `/usr/local/bin`
- if `/usr/local/bin` is not usable, pick another stable directory already on `PATH` and ensure `which claude` resolves there

## Non-Negotiable Rules

1. Never write a real gateway key into repository files or print it in output.
2. Always ask for `AMD_LLM_GATEWAY_KEY` before editing any local secret-bearing config.
3. If the user does not provide the key, stop automatic setup and switch to placeholder-only manual guidance.
4. Keep secrets in user-local files such as `~/.bashrc`, not in the repository.
5. Install Claude Code first, then create or repair the wrapper:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

6. The wrapper must export these variables:

```bash
export ANTHROPIC_BASE_URL="https://llm-api.amd.com/Anthropic"
export ANTHROPIC_CUSTOM_HEADERS="Ocp-Apim-Subscription-Key: ${AMD_LLM_GATEWAY_KEY}"
export CLAUDE_CODE_DISABLE_ADVISOR_TOOL="1"
```

7. The second line is critical. `ANTHROPIC_CUSTOM_HEADERS` must be a single string, not JSON.
8. The advisor env var is required for AMD Gateway direct mode because some Claude Code versions add `advisor-tool-2026-03-01` to the `anthropic-beta` header, which AMD Gateway rejects.
9. Do not rely only on the default `Authorization` or `X-Api-Key` behavior from `apiKeyHelper`.
10. Preserve unrelated `~/.claude/settings.json` fields. The wrapper may enforce `apiKeyHelper` and `model`, but it must not rewrite the file to only those fields because Claude Code stores other state there, including plugin enablement and permission prompts.
11. Default to `sonnet`. Use `opus` as the high-tier direct model.
12. If `~/.local/bin` appears earlier in `PATH`, make the `claude` binary there point to the wrapper as well.
13. Never claim success from `claude-route` or healthcheck alone. Run real `claude -p` validation.

## Preferred Local Layout

- `~/.bashrc`
  - stores only `export AMD_LLM_GATEWAY_KEY="PASTE_YOUR_KEY_HERE"`
- `~/.claude/settings.json`
  - stores `apiKeyHelper`, the selected direct model, and other Claude Code-managed fields
- `/usr/local/bin/claude`
  - wrapper that loads environment, normalizes bad aliases, and forwards to the real Claude binary
- `/usr/local/bin/claude-route`
  - route inspector that reports direct mode, normalized model, and entrypoint information
- `/usr/local/bin/claude.real`
  - the real Claude Code binary, or a symlink to the official downloaded binary
- `~/.local/bin/claude`
  - if this location can win in `PATH`, link it to the wrapper too

## Setup And Repair Workflow

### Step 1: Inspect current state

Check the machine without leaking the key:

- `claude --version`
- `which claude`
- `which claude-route`
- `claude-route`
- `echo "${AMD_LLM_GATEWAY_KEY:+set}"`
- `bash ".cursor/skills/claude-amd-direct-setup/scripts/healthcheck.sh"`

Never print the real key. Only confirm whether it is set.

### Step 2: Ask for the key if needed

If `AMD_LLM_GATEWAY_KEY` is missing:

- say that the key is still needed
- explain that the real key will be written only to local user files
- if the user does not want to share it, switch to `PASTE_YOUR_KEY_HERE` manual steps

### Step 3: Install Claude Code first

Run the official installer first:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Operational notes based on real setup experience:

- the installer may download the binary into `~/.claude/downloads/` and then hang during the final `install` subprocess
- if that happens, do not blindly rerun the installer over and over
- first check whether `~/.claude/downloads/claude-*-linux-x64` exists and is executable
- run `~/.claude/downloads/claude-*-linux-x64 --version`
- if the binary works, wire it into `claude.real` and continue with wrapper setup

### Step 4: Write local config

`~/.bashrc` should keep only the key:

```bash
export AMD_LLM_GATEWAY_KEY="PASTE_YOUR_KEY_HERE"
```

`~/.claude/settings.json` should keep only:

```json
{
  "apiKeyHelper": "bash -lc 'printf %s \"$AMD_LLM_GATEWAY_KEY\"'",
  "model": "sonnet"
}
```

Do not add extra auth fields. Model switching should happen here and nowhere else.

If a wrapper repairs or normalizes this file, it must load the existing JSON object, update only `apiKeyHelper` and `model`, and then write the merged object back. Do not replace the whole file with only those two fields.

### Step 5: Wrapper requirements

The wrapper should do all of the following:

- if the current shell does not already have `AMD_LLM_GATEWAY_KEY`, try loading `~/.bashrc`
- export `ANTHROPIC_BASE_URL`
- export `ANTHROPIC_CUSTOM_HEADERS`
- export `CLAUDE_CODE_DISABLE_ADVISOR_TOOL="1"`
- normalize bad persisted model aliases into supported direct models
- `exec` into the real Claude Code binary

Minimum alias normalization rules:

- `opus[1m]` -> `opus`
- `claude-opus-4.5[1m]` -> `opus`
- any other unknown value -> `sonnet`

### Step 6: Ensure the real entrypoint is the wrapper

Always check:

```bash
which claude
```

If the result is not the expected wrapper:

- fix `PATH` ordering, or
- point `~/.local/bin/claude` to the wrapper, or
- replace whichever `claude` binary wins first on `PATH` with the wrapper

Do not assume `/usr/local/bin/claude` will always be chosen.

### Step 7: Verify in the correct order

Use this validation order exactly:

1. Route inspection first:

```bash
claude-route
```

Expected direct-mode indicators:

- `"mode": "direct"`
- `"backend": "claude-amd-anthropic"`
- `"normalized_model": "sonnet"` or `"opus"`

2. Then run a real text request:

```bash
claude -p --output-format json 'Reply with exactly OK'
```

3. Then verify the reported model:

```bash
claude -p --output-format json 'Reply with exactly OK' | \
  python3 ".cursor/skills/claude-amd-direct-setup/scripts/verify_output_model.py"
```

4. Finally verify tool use:

```bash
claude -p --output-format json --allowedTools Bash -- \
  'Use the Bash tool to run pwd, then answer with only the absolute path.'
```

Do not stop at healthcheck. The setup is only truly complete when:

- the text request succeeds
- `modelUsage` reports a Claude model for the selected alias, such as `claude-sonnet-5`
- the Bash tool call succeeds

### Step 8: Fallback when the user does not provide the key

Do not invent a fake key. Provide placeholder-only guidance and explicitly mention the local files that must be updated:

- `~/.bashrc`
- `~/.claude/settings.json`
- the wrapper file

## Supported Direct Models

Use only these exact model names:

- `sonnet`
- `opus`

A successful `claude -p --output-format json ...` request may report the resolved backend model in `modelUsage`, for example `claude-sonnet-5`.

Do not treat interactive `/model` as the final source of truth. For this direct setup, `~/.claude/settings.json` is the only reliable model switch location.

## Common Failure Modes

1. `401 missing subscription key`
   - the first check is not whether the key itself is correct
   - first confirm that `ANTHROPIC_CUSTOM_HEADERS` is set
   - then confirm that it is the single string `Ocp-Apim-Subscription-Key: ...`
   - do not store it as JSON

2. `claude-route` looks healthy but the real request fails
   - continue with `claude -p --output-format json ...`
   - inspect the real API error instead of stopping at route output

3. `which claude` points to the wrong binary
   - inspect `PATH`
   - inspect `~/.local/bin/claude`
   - make sure the wrapper is the actual runtime entrypoint

4. the official installer downloads the binary but hangs at the end
   - inspect `~/.claude/downloads/claude-*-linux-x64`
   - run `--version`
   - if the binary works, connect it to `claude.real` and continue

5. `~/.claude/settings.json` was polluted by an old `/model` selection
   - change it back to `sonnet` or `opus`
   - or let the wrapper normalize it automatically at startup

6. `claude-route` shows `settings_parse_error`
   - launch `claude` once
   - let the wrapper back up the invalid file and rewrite a minimal config

7. the current shell still has stale environment
   - reload `~/.bashrc`
   - or open a new terminal before retesting

8. healthcheck passes but Claude tool use still fails
   - keep going with real `claude -p` and Bash tool validation
   - without that, setup is not complete

9. `Unable to connect to API (ConnectionRefused)`
   - first check DNS and local host overrides:
     - `getent ahosts llm-api.amd.com`
     - `rg 'llm-api\.amd\.com' /etc/hosts /etc/cloud/templates/hosts.debian.tmpl 2>/dev/null || true`
   - if `llm-api.amd.com` resolves to `127.0.0.1`, remove the local override; Claude is trying to connect to localhost port 443 instead of AMD Gateway
   - then verify network reachability with `curl -I --connect-timeout 10 --max-time 20 https://llm-api.amd.com/Anthropic`
   - if `/etc/hosts` is managed by cloud-init, make the fix in the cloud-init hosts template as well so it survives reboot

10. Claude auto-upgraded but the wrapper still launches an old binary
   - compare `claude --version` through the wrapper with the newest version under `~/.local/share/claude/versions/`
   - inspect where the wrapper forwards execution, usually `~/.local/bin/claude.real`
   - if `claude.real` points at an old version, update the symlink to the newest installed Claude binary, or rerun the official installer and then recreate the wrapper
   - after rewiring, rerun `claude-route` and a real `claude -p --output-format json 'Reply with exactly OK'`

11. `claude plugin enable ...` says success but `claude plugin list` still shows `enabled: false`
   - inspect the wrapper first; it may be rewriting `~/.claude/settings.json` and deleting Claude Code-managed fields
   - fix the wrapper so it preserves existing settings keys and only updates `apiKeyHelper` and `model`
   - rerun `claude plugin enable <plugin> --scope user`
   - start a new Claude Code session; enabled plugin skills are loaded at session startup

12. `Unexpected value(s) advisor-tool-2026-03-01 for the anthropic-beta header`
   - this comes from Claude Code's experimental advisor tool, not from `ANTHROPIC_CUSTOM_HEADERS`
   - AMD Gateway direct mode may reject that beta header before the request reaches the model
   - verify with `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 claude -p --output-format json 'Reply with exactly OK'`
   - fix the wrapper by exporting `CLAUDE_CODE_DISABLE_ADVISOR_TOOL="1"` before `exec`ing the real Claude binary

## Manual Fallback Snippet

If the user does not want to share the real key, use only a placeholder:

```bash
# ~/.bashrc
export AMD_LLM_GATEWAY_KEY="PASTE_YOUR_KEY_HERE"
```

Never write the real key into project files.

## Additional Resources

- Usage examples: [examples.md](examples.md)
- Environment and route validation: `scripts/healthcheck.sh`
- Model output validation: `scripts/verify_output_model.py`

## Validation Checklist

- [ ] no real secret was added to repository files
- [ ] the user was asked for the key before secret-bearing local edits
- [ ] Claude Code was installed before wrapper configuration
- [ ] `~/.claude/settings.json` preserves unrelated Claude Code-managed fields while enforcing `apiKeyHelper` and `model`
- [ ] the wrapper exports `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, and `CLAUDE_CODE_DISABLE_ADVISOR_TOOL`
- [ ] `ANTHROPIC_CUSTOM_HEADERS` is a string, not JSON
- [ ] `which claude` resolves to the wrapper
- [ ] if `~/.local/bin` is earlier in `PATH`, it also points to the wrapper
- [ ] `claude-route` reports direct mode and the `sonnet` or `opus` settings alias
- [ ] the real `claude -p` text request succeeds
- [ ] the Bash tool call succeeds, or any remaining limitation is stated clearly
