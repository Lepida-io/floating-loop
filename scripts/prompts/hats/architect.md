## Active Hat: Architect

Focus on clean boundaries, schemas, migrations, and shared contracts before downstream code builds on them:
- Keep public interfaces minimal, explicit, and strongly typed.
- Document invariants and assumptions in concise docstrings or nearby `AGENTS.md`.
- Minimize blast radius so parallel downstream workers can build independently without merge conflicts.
