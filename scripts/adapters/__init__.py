"""Adapters: one module per background agent CLI.

Each module defines `Adapter(cfg)` with
    run(text, session_id, workdir, auto, prompt_file, timeout) -> dict
returning: ok, reply, error, session_id, usage, seconds, edits (paths),
commands (shell commands run), model, and optionally note.
"""
