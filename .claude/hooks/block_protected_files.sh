#!/usr/bin/env bash
# PreToolUse hook: block direct Edit/Write to files that should only be
# updated through tooling (uv.lock) or that contain secrets (.env*).
#
# Speaks the modern Claude Code hook protocol: emits a JSON object on stdout
# with hookSpecificOutput.permissionDecision = "deny" and a reason. Always
# exits 0; the deny is signalled via the JSON, not the exit code.
#
# Allowlist: .env.example is *not* blocked — that's the template the team
# edits and copies to .env locally.
set -u

file=$(jq -r '.tool_input.file_path // empty')
base=${file##*/}

deny() {
  # Args: $1 = reason text. Emit a single-line JSON deny response.
  local reason=$1
  # JSON-encode the reason via jq to handle quotes/backslashes cleanly.
  jq -cn --arg r "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
}

case "$base" in
  uv.lock)
    deny "Direct edits to uv.lock are blocked. Edit pyproject.toml and then run \`uv lock\` (or \`uv add <package>\`) to refresh the lockfile."
    ;;
  .env|.env.local|.env.dev|.env.development|.env.staging|.env.prod|.env.production|.env.test)
    deny "Direct edits to $base are blocked. Edit .env.example (the team-shared template) and copy it to .env, or set values via your shell environment."
    ;;
esac
exit 0
