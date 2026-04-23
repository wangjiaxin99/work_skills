#!/usr/bin/env python3

import json
import re
import shlex
import sys

WRAPPER_COMMANDS = {"sudo", "env", "timeout", "nice", "nohup", "stdbuf"}
SHELL_COMMANDS = {"bash", "sh", "zsh"}
DESTRUCTIVE_COMMANDS = {"rm", "unlink", "shred", "rmdir"}
SEPARATORS = {"&&", "||", ";", "|", "|&", "&"}
FIND_EXEC_FLAGS = {"-exec", "-execdir"}
INLINE_INTERPRETERS = {
    "python": {"-c"},
    "python3": {"-c"},
    "node": {"-e", "--eval"},
    "nodejs": {"-e", "--eval"},
    "ruby": {"-e"},
    "perl": {"-e"},
}

SUSPICIOUS_FALLBACK_PATTERNS = (
    "git reset --hard",
    "git push --force",
    "git checkout --",
    "git restore --worktree",
    "git clean -f",
    "rm -rf",
    "find -delete",
)

PYTHON_DELETE_PATTERNS = (
    r"\bshutil\.rmtree\s*\(",
    r"\bos\.(?:remove|unlink|rmdir)\s*\(",
    r"\b(?:pathlib\.)?Path\s*\([^)]*\)\.(?:unlink|rmdir)\s*\(",
    r"\bos\.system\s*\([^)]*rm\b",
    r"\bsubprocess\.(?:run|call|Popen|check_call|check_output)\s*\([^)]*rm\b",
    r"\bsubprocess\.(?:run|call|Popen|check_call|check_output)\s*\([^)]*git\s+reset\s+--hard",
)

NODE_DELETE_PATTERNS = (
    r"\bfs\.(?:rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync)\s*\(",
    r"\bchild_process\.(?:exec|execSync|spawn|spawnSync)\s*\([^)]*rm\b",
    r"\bchild_process\.(?:exec|execSync|spawn|spawnSync)\s*\([^)]*git\s+reset\s+--hard",
)

RUBY_DELETE_PATTERNS = (
    r"\bFileUtils\.rm_rf\b",
    r"\bFile\.delete\b",
    r"\bDir\.rmdir\b",
    r"\bsystem\s*\([^)]*rm\b",
    r"\bsystem\s*\([^)]*git\s+reset\s+--hard",
)

PERL_DELETE_PATTERNS = (
    r"\bunlink\b",
    r"\brmdir\b",
    r"\bsystem\s*\([^)]*rm\b",
    r"\bsystem\s*\([^)]*git\s+reset\s+--hard",
)


def strip_wrappers(tokens):
    tokens = list(tokens)
    while tokens:
        head = tokens[0]
        if head in WRAPPER_COMMANDS:
            tokens = tokens[1:]
            continue
        if "=" in head and not head.startswith("-") and head.split("=", 1)[0]:
            tokens = tokens[1:]
            continue
        break
    return tokens


def split_segments(tokens):
    segments = []
    current = []
    for token in tokens:
        if token in SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def first_non_option(tokens, start_idx=1):
    for idx in range(start_idx, len(tokens)):
        token = tokens[idx]
        if token == "--":
            if idx + 1 < len(tokens):
                return idx + 1
            return None
        if not token.startswith("-"):
            return idx
    return None


def shell_command_index(tokens):
    for idx, token in enumerate(tokens[1:], start=1):
        if token == "-c":
            return idx + 1 if idx + 1 < len(tokens) else None
        if token.startswith("-") and "c" in token[1:]:
            return idx + 1 if idx + 1 < len(tokens) else None
        if not token.startswith("-"):
            break
    return None


def inline_code_index(cmd, tokens):
    flags = INLINE_INTERPRETERS.get(cmd)
    if not flags:
        return None
    for idx, token in enumerate(tokens[1:], start=1):
        if token in flags:
            return idx + 1 if idx + 1 < len(tokens) else None
        if token.startswith("-") and token not in flags:
            continue
        if not token.startswith("-"):
            break
    return None


def matches_any_pattern(code, patterns):
    return any(re.search(pattern, code, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def analyze_inline_interpreter(cmd, code):
    if cmd in {"python", "python3"} and matches_any_pattern(code, PYTHON_DELETE_PATTERNS):
        return "Blocked inline Python that appears to delete files or launch destructive shell commands."
    if cmd in {"node", "nodejs"} and matches_any_pattern(code, NODE_DELETE_PATTERNS):
        return "Blocked inline Node.js that appears to delete files or launch destructive shell commands."
    if cmd == "ruby" and matches_any_pattern(code, RUBY_DELETE_PATTERNS):
        return "Blocked inline Ruby that appears to delete files or launch destructive shell commands."
    if cmd == "perl" and matches_any_pattern(code, PERL_DELETE_PATTERNS):
        return "Blocked inline Perl that appears to delete files or launch destructive shell commands."
    return None


def analyze_find_exec(tokens, depth):
    for idx, token in enumerate(tokens):
        if token not in FIND_EXEC_FLAGS:
            continue
        segment = []
        for part in tokens[idx + 1 :]:
            if part in {";", "+"}:
                break
            segment.append(part)
        if not segment:
            continue
        if "{}" in segment:
            segment = [part for part in segment if part != "{}"]
        reason = analyze_tokens(segment, depth + 1)
        if reason:
            return f"Blocked `find {token}` because it would execute a destructive command."
    return None


def analyze_xargs_parallel(tokens, depth):
    cmd = tokens[0]
    idx = first_non_option(tokens, start_idx=1)
    if idx is None:
        return None

    nested = tokens[idx:]
    reason = analyze_tokens(nested, depth + 1)
    if reason:
        return f"Blocked `{cmd}` because it would dispatch a destructive command."
    return None


def analyze_git(tokens):
    if len(tokens) < 2:
        return None

    subcommand = tokens[1]
    tail = tokens[2:]

    if subcommand == "reset" and "--hard" in tail:
        return "Blocked `git reset --hard` because it discards local changes."

    if subcommand == "checkout" and "--" in tail:
        return "Blocked `git checkout -- ...` because it discards local file changes."

    if subcommand == "restore" and any(flag in tail for flag in ("--worktree", "-W")):
        return "Blocked `git restore --worktree` because it discards local file changes."

    if subcommand == "clean" and any(token.startswith("-") and "f" in token for token in tail):
        return "Blocked `git clean` with force flags because it deletes untracked files."

    if subcommand == "stash" and any(action in tail for action in ("drop", "clear")):
        return "Blocked destructive `git stash` cleanup."

    if subcommand == "branch" and "-D" in tail:
        return "Blocked `git branch -D` because it force-deletes a branch."

    if subcommand == "worktree" and "remove" in tail and "--force" in tail:
        return "Blocked forced `git worktree remove`."

    if subcommand == "push" and any(flag in tail for flag in ("-f", "--force")):
        return "Blocked force push. Use a reviewed workflow before rewriting remote history."

    return None


def analyze_tokens(tokens, depth):
    if depth > 5:
        return None

    tokens = strip_wrappers(tokens)
    if not tokens:
        return None

    segments = split_segments(tokens)
    if len(segments) > 1:
        for segment in segments:
            reason = analyze_tokens(segment, depth + 1)
            if reason:
                return reason
        return None

    cmd = tokens[0]

    if cmd in SHELL_COMMANDS:
        idx = shell_command_index(tokens)
        if idx is not None:
            return analyze_command_string(tokens[idx], depth + 1)

    if cmd == "eval" and len(tokens) > 1:
        return analyze_command_string(" ".join(tokens[1:]), depth + 1)

    if cmd in {"xargs", "parallel"}:
        return analyze_xargs_parallel(tokens, depth)

    if cmd == "find":
        if "-delete" in tokens:
            return "Blocked `find -delete` because it removes files without an explicit review step."
        reason = analyze_find_exec(tokens, depth)
        if reason:
            return reason

    code_idx = inline_code_index(cmd, tokens)
    if code_idx is not None:
        reason = analyze_inline_interpreter(cmd, tokens[code_idx])
        if reason:
            return reason

    if cmd in DESTRUCTIVE_COMMANDS:
        return (
            "Blocked direct filesystem deletion. "
            "Use a safer non-destructive workflow or ask the user before deleting files."
        )

    if cmd == "git":
        return analyze_git(tokens)

    return None


def analyze_command_string(command, depth=0):
    if depth > 5:
        return None

    try:
        tokens = shlex.split(command)
    except ValueError:
        lowered = command.lower()
        if any(pattern in lowered for pattern in SUSPICIOUS_FALLBACK_PATTERNS):
            return "Blocked an unsafe command pattern from an unparseable shell command."
        return None

    return analyze_tokens(tokens, depth)


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    reason = analyze_command_string(command)
    if not reason:
        return 0

    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
