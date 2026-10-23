"""Claude Code integration: the settings and MCP config to print, and the PreToolUse hook.

Imports nothing from session, vault, sanitize, ingest or egress. claude builds the JSON text
blocks; hook is the command Claude Code runs before each tool call. Both are best effort: the
strong boundary is running the agent as another OS user.
"""
