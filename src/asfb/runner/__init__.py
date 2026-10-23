"""Run one allowed test tool and hand its junit report to the parser. Imports no vault or sanitizer.

Modules: tools (the allowed tools and their argv), netns (the network prefix on Linux), and execute
(the child process with a wall-clock limit). Session.run wires them together.
"""
