#!/usr/bin/env bash
# PostToolUse hook: auto-format Python files after Claude edits them.
#
# Reads the Edit/Write event JSON from stdin, extracts the file path, and (if
# it's a .py file inside a project with pyproject.toml) runs:
#   1. `uv run ruff format`     — apply the project's ruff format
#   2. `uv run ruff check --fix` — apply auto-fixable lint rules
#
# Always exits 0 so a missing tool, non-Python file, or formatter failure
# never blocks the edit. Diagnostics go to /dev/null on purpose: the hook is
# "best effort, fast, silent on success or known no-op."
set -u

file=$(jq -r '.tool_input.file_path // empty')
case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -z "$file" ] && exit 0

# Walk up from the file to find the closest pyproject.toml.
dir=$(dirname "$file")
while [ "$dir" != / ] && [ ! -f "$dir/pyproject.toml" ]; do
  dir=$(dirname "$dir")
done
[ -f "$dir/pyproject.toml" ] || exit 0

cd "$dir" || exit 0
uv run ruff format "$file"     >/dev/null 2>&1 || true
uv run ruff check --fix "$file" >/dev/null 2>&1 || true
exit 0
