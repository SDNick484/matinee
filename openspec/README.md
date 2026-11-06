# OpenSpec (retired)

OpenSpec is retired for Matinee as of 2026-11-06. This folder is **historical reference only**. Do not propose new OpenSpec changes or run the `/opsx` commands.

## Where things live now

| What | Where |
|---|---|
| Design specs | `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md` |
| Implementation plans | `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` |
| Subagent-driven development ledger | `.superpowers/sdd/` |
| Product vision | `docs/vision.md` |

## What is in this folder

- `archive/2026-10-31-add-catalog-core/`: the phase-1 change (Title/Copy catalog, TMDB metadata, collection browse, editor access). It shipped in the initial release, commit `bcac5d9` on 2026-10-31. Its deploy and smoke-test tasks (11.1 to 11.4) were never ticked off here, and are left as written.
- `config.yaml`: the old project context and artifact rules, kept for reference. Its Title / Package / Copy model is superseded: Packages were considered and dropped (see `docs/vision.md`).
- `specs/`: empty. The four capability specs from `add-catalog-core` were never synced up, so they exist only inside the archived change.
