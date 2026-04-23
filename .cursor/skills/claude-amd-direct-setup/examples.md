## Example 1: User provides the key and wants full automatic setup

User request:

```text
Set up Claude Code on this machine for AMD LLM Gateway, and do not put my key into git.
```

Expected behavior:

1. Check current state first:
   - `claude --version`
   - `which claude`
   - `claude-route`
   - `echo "${AMD_LLM_GATEWAY_KEY:+set}"`
2. If the key is missing, ask the user for `AMD_LLM_GATEWAY_KEY`.
3. Install Claude Code first:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

4. Then write local config:
   - `~/.bashrc`
   - `~/.claude/settings.json`
   - the wrapper
   - `claude-route`
5. Confirm that `which claude` resolves to the wrapper.
6. Verify in order:

```bash
claude-route
claude -p --output-format json 'Reply with exactly OK'
claude -p --output-format json --allowedTools Bash -- \
  'Use the Bash tool to run pwd, then answer with only the absolute path.'
```

7. Confirm that no real key was written into repository files.

## Example 2: User does not want to share the key

User request:

```text
I want this skill, but I do not want to paste my gateway key into chat.
```

Expected behavior:

1. Do not invent a fake key and do not write secret-bearing config automatically.
2. Provide only a local placeholder:

```bash
export AMD_LLM_GATEWAY_KEY="PASTE_YOUR_KEY_HERE"
```

3. Explain which local files must be updated:
   - `~/.bashrc`
   - `~/.claude/settings.json`
   - the wrapper
4. Explain the final verification commands:

```bash
claude-route
claude -p --output-format json 'Reply with exactly OK'
```

## Example 3: Route passes but `401 missing subscription key`

User request:

```text
`claude-route` says direct mode, but `claude -p` fails with 401 missing subscription key. Fix it.
```

Expected behavior:

1. Do not assume the key itself is wrong.
2. First check that the wrapper contains:

```bash
export ANTHROPIC_CUSTOM_HEADERS="Ocp-Apim-Subscription-Key: ${AMD_LLM_GATEWAY_KEY}"
```

3. Confirm that it is a string, not JSON.
4. Confirm that the wrapper is the actual entrypoint:

```bash
which claude
```

5. If `~/.local/bin` is earlier in `PATH`, fix the `claude` entry there too.
6. After the repair, rerun route validation, the text request, and the Bash tool call in order.

## Example 4: Official installer downloads the binary but hangs at the end

User request:

```text
The install command ran for a long time and did not exit, but I can already see a Claude binary under ~/.claude/downloads. What should I do next?
```

Expected behavior:

1. Do not blindly rerun the installer.
2. First confirm that the downloaded binary works:

```bash
~/.claude/downloads/claude-*-linux-x64 --version
```

3. If the version command works, wire that binary into `claude.real` or use it as the wrapper target.
4. Continue with wrapper setup, local settings, and full verification.

## Example 5: `/model` selection breaks direct mode

User request:

```text
I changed models inside Claude earlier, and now AMD direct mode fails with 400. I think `/model` broke it.
```

Expected behavior:

1. Inspect the `model` field in `~/.claude/settings.json`.
2. If it contains aliases such as `opus[1m]` or `claude-opus-4.5[1m]`:
   - change it back to `claude-sonnet-4.6` or `claude-opus-4.6`
   - or let the wrapper normalize it automatically at startup
3. Re-run a real `claude -p` verification request.
