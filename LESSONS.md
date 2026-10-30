# Lessons from PicoPaaS (general)

Distilled from a large agent-built project. Apply them as defaults.

## Process
- Contract drift is the top failure: the brief said one domain, budget and roster while reality moved on. Update the
  brief in the same chunk as the change and log it in the Amendments log.
- Undefined ad-hoc ledger roles (backend, main, claude, per-app experts) appear fast. Keep one role registry and use only it.
- Per-app "expert" agents rot. Write what they learned as a recipe doc plus an example directory instead.
- A prose ledger grows without bound (1000+ lines). Keep START/DONE pairs short, newest first, and a small "Now / Next".
- The human as dispatcher is a leak. Give the manager a written wave workflow with exit gates; keep one inbox
  (QUESTIONS.md) for the human and let requests pile up instead of stopping.
- Be honest in DONE lines: "NOT browser-checked", "NOT applied", "uncommitted", "NEEDS-SECURITY-REVIEW".
- Reviewer independence: the agent that commits is not the security reviewer of its own push. Review risky diffs
  (authz, RBAC, network policy, SQL/YAML generation, secrets, quotas, provider calls) before or right after push.
- One writer for git. Concurrent agents committing caused rebases and lost work; workers leave changes in the tree.
- Never run parallel workers on overlapping files (max 3 workers, disjoint files).
- Budget tokens: one chunk per invocation, read ranges not whole files, focused tests while iterating and the full
  suite once, cheaper model for workers.

- Rules agents must follow every time belong in a check script run by CI, not in memory or prose. A page-width rule
  recurred three times until `npm run check:layout` failed the build on a stray `max-w-*`. (2026-10-30)
- Derive test expectations and UI limits from the config source (settings endpoint, constants), never literals. A UI
  hard-coded 4000 while the backend allowed 8000. (2026-10-30)
- Keep instruction files lean (about 60 lines each); shared rules live once in the process doc. Evidence goes in the
  entry's date; prune lessons quarterly.

## Frontend
- `vue-tsc` mis-scopes template variables (v-for / slot vars) in some versions; a clean typecheck is not proof the UI works.
  Also run the build and look at it in a browser. Record `NOT browser-checked` if you cannot.
- A TypeScript major bump can break `vue-tsc` (ERR_PACKAGE_PATH_NOT_EXPORTED); ignore TS majors in Dependabot until vue-tsc supports them.
- Tailwind 3 to 4 upgrade pitfalls: buttons lose the pointer cursor (add a base rule), overridden palette tokens change
  contrast (check active/hover states), config moves to CSS. Always do a visual pass after.
- Layout and navigation changes need a mockup approved by the human first.
- Local-first conveniences in `localStorage` need try/catch.

## Database and data
- Managed MySQL with `sql_require_primary_key` breaks apps whose schema has tables without a primary key (Ghost). Decide
  the policy once (turn the setting off cluster-wide, or conform), and record it.
- One DB server hosting many isolated databases and roles saves cost but widens the blast radius; document the tradeoff.
- Migrations that backfill (e.g. a personal project per user) need a tested upgrade path on SQLite and MySQL.

## Testing house style
- pytest with SQLite in-memory; use the real register/login flow, no auth mocks.
- Infra clients (Kubernetes, DB admin, object storage) are faked behind small interfaces in a providers package.
- Fixtures must not look like secrets (gitleaks).
- Repeatable checks belong in CI (coverage floor, smoke); agents explore.
- Real defects found by coverage passes are fixed or marked strict-xfail.

## Security and ops
- Scope credentials: account-wide cloud tokens in the app pod are a production risk; scope them before real use.
- Live changes to infra need a request, a plan first, and the human's go-ahead. Diagnosis agents are read-only.
- Never print tokens; load them from a file outside the repo.
- Never compare the product to other platforms in app, docs or blog copy.
