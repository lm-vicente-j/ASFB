"""Isolated worker: one short-lived process per job, with limits and an audit hook.

The package init imports nothing, so that `python -m asfb.sandbox.worker` runs the worker module
without a runpy warning. Import the submodules directly.
"""
