@AGENTS.md

# Claude Code notes

- Use plan mode before changes that cross module boundaries or alter shared contracts.
- Show the intended files and verification commands before a multi-file change.
- After editing, run the narrowest relevant check first, then `make check`.
- Do not rewrite a teammate's work merely to match a preferred style; the formatter owns style.
- Use `/context` to confirm this file loaded when repository instructions appear missing.

