"""MCP server over stdio, JSON-RPC 2.0 written here, with no SDK dependency.

server holds the protocol: one message per line, strict parsing, the methods and their error
codes. tools holds the four tools, their argument checks and the text each one returns. The
package imports nothing from vault, sanitize, ingest, normalize or types.untrusted.
"""
