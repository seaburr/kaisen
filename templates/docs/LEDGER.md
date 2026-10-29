# Work Ledger

Append-only. Every agent adds a line when it starts and when it finishes a chunk, so work survives
token exhaustion. Format: `- YYYY-MM-DD [role] STATUS: what (files / SHAs / counts)`.
STATUS is one of START, DONE, BLOCKED, HANDOFF. Roles: only those in `process/WAVE.md`.
DONE lines state verification honestly: test counts, `NOT browser-checked`, `NOT applied`, `NEEDS-SECURITY-REVIEW`,
`uncommitted` until scm-security pushes (it clears the marker and records SHAs + CI run ids).
**Next session: read "Now / Next" first.**

## Now / Next
- {{YYYY-MM-DD}} [manager] START: project kickoff from BRIEF.md.
