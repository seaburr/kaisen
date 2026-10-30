# Tasks: {{feature or wave name}}

Spec -> tasks. The manager writes this (from BRIEF.md / the feedback doc) BEFORE dispatching workers and ticks rows
as DONE lines land. One row per task; a task fits one chunk (one invocation). Workers in the same batch never share files.

**Spec (2-5 lines):** what must be true when done, and what is out of scope.

| # | Task | Acceptance check (command or observable) | Owner role | Files | Size | Status |
|---|---|---|---|---|---|---|
| 1 | {{task}} | {{e.g. `pytest tests/test_x.py` green; GET /x returns 200}} | backend-dev | app/x.py | S | todo |
| 2 | {{task}} | {{e.g. `npm run typecheck && npm run build`; checked at 390px}} | frontend-dev | frontend/src/X.vue | S | todo |

Gate order per task: implementer DONE -> qa-tester spec check (fresh context) -> security-reviewer if risky -> scm-security.
Status values: todo, doing, done, blocked (reason in the ledger).
