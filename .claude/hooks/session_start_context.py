#!/usr/bin/env python3

import json
import sys
from pathlib import Path

PLACEHOLDER_MARKER = "claude-state:placeholder"
MAX_CHARS_PER_FILE = 2400


def find_project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / ".claude").exists():
            return candidate
    return start


def read_state_file(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text or PLACEHOLDER_MARKER in text:
        return ""
    if len(text) > MAX_CHARS_PER_FILE:
        return text[:MAX_CHARS_PER_FILE].rstrip() + "\n... [truncated]"
    return text


def main() -> int:
    payload = json.load(sys.stdin)
    cwd = Path(payload.get("cwd", ".")).resolve()
    root = find_project_root(cwd)
    state_dir = root / ".claude" / "state"

    current_session = read_state_file(state_dir / "current-session.md")
    server_runtime = read_state_file(state_dir / "server-runtime.md")

    sections = []
    if current_session:
        sections.append("## Saved Task State\n" + current_session)
    if server_runtime:
        sections.append("## Saved Server Runtime\n" + server_runtime)

    if not sections:
        return 0

    message = [
        "Reminder: saved workspace state exists. Use it before relying on chat history alone.",
        *sections,
    ]
    print("\n\n".join(message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
