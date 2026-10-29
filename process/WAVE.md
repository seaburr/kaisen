# Process playbook (wave workflow)

> KIT NOTE: copied from the PicoPaaS project and de-branded. The role table's owned paths, the "Retired ad-hoc roles"
> paragraph and the Ghost/recipes references are examples; edit them for your project. The single-committer
> scm-security loop is kept as-is. The /wave skill, issues + events.jsonl state, PR-per-issue, staging and a headless
> orchestrator are PLANNED (see ../README.md), not present: today the manager (main Claude session) drives the wave by hand.

How the agent team works. `BRIEF.md` is the contract; this is the playbook. Agents read this after `CLAUDE.md`.

## Roles (the only ledger role names)

Use exactly these in `[role]`. No ad-hoc names (`backend`, `frontend`, `main`, `claude`, `qa-engineer`, `marketing`, ...).

| Role | Is | Owns |
|---|---|---|
| manager | the main Claude session (no agent file) | intake, grouping, dispatch (max 3 parallel workers), comms with {{HUMAN}} via `docs/QUESTIONS.md`, wave exit gates |
| backend-dev | agent | `app/`, `alembic/`, `tests/`, `cli/` |
| frontend-dev | agent | `frontend/src` |
| ui-ux-designer | agent | mockups, browser checks, visual diffs, small UI fixes |
| infra-ops | agent | `infra/`, `deploy/`, `.github/workflows`, images, approved applies, Ghost/app recipes' infra side |
| qa-tester | agent (ledger name `qa-tester`; old `qa`, `qa-engineer` are retired) | coverage passes, live E2E on throwaway apps |
| security-reviewer | agent | review/pentest of risky diffs, `docs/PENTEST.md` |
| scm-security | agent | the ONLY committer/pusher; secret gate; CI watch |
| dependabot-steward | agent | Dependabot PR queue (merge via `gh pr merge` or hand to scm-security) |
| tech-writer | agent | `docs/*` audits and guides, recipes docs |
| marketing-writer | agent (old `marketing`) | landing page, README tagline, meta copy |
| release-blogger | agent | devlog posts on the Ghost blog |
| sre | agent | read-only performance/capacity diagnosis |

Retired ad-hoc roles seen in old ledger lines: `ghost-expert`, `wp-expert`, `ghost-poc`. **Per-app experts are not
agents.** What they learned (image quirks, env vars, migration steps, perf tuning) is written as a recipe in
`docs/RECIPES.md` plus `examples/<app>/`, owned by tech-writer and infra-ops. Anyone running an app recipe logs under
their real role (infra-ops, qa-tester).

## Ledger line format

`- YYYY-MM-DD [role] STATUS: what (files / SHAs / counts)` under "Now / Next", newest first. STATUS is START, DONE,
BLOCKED or HANDOFF. DONE lines state verification honestly: test counts, `NOT browser-checked`, `NOT applied`,
`NEEDS-SECURITY-REVIEW`, `uncommitted` until scm-security pushes (it clears the marker and records SHAs + CI run ids).
One START and one DONE per chunk. Keep each line to what the next reader needs; detail goes in the PR/commit/docs.

## Rules for everyone

- Only scm-security commits/pushes. Dependabot-steward may merge PRs and push to PR branches only.
- Reviewer independence: the committer is not the security reviewer of its own push. Risky diffs (authz, RBAC,
  NetworkPolicy, SQL/YAML generation, secrets, quotas, egress/ingress, provider calls) get `security-reviewer` before
  or right after push; every wave gets one pass regardless.
- Tests: SQLite in-memory, real register/login flow, infra faked behind `app/providers` interfaces, fixtures that do
  not look like secrets. Repeatable checks belong in CI; agents explore.
- Frontend: typecheck + build, and a browser check or an explicit `NOT browser-checked` handoff to ui-ux-designer.
- Caps and defaults come from `app/config.py`; docs quote them, never redefine them.
- Questions for {{HUMAN}} go in `docs/QUESTIONS.md`; paid resources need `docs/requests/` first.
- Token thrift: one chunk per invocation; read ranges not whole files; focused tests while iterating, full suite once;
  sonnet for workers; no re-reading what the ledger already says; no new .md files unless asked.

## Feedback wave (the /wave workflow)

Run when {{HUMAN}} hands over a review/feedback doc. The manager drives it; each phase has an exit gate.

1. **Intake.** Read the doc; number every finding; put unclear items in `docs/QUESTIONS.md` and proceed on the rest.
   *Gate:* every finding has an id, an owner role and a size (S/M/L). Ledger `[manager] START`.
2. **Grouping.** Cluster findings by area (backend, frontend, infra, docs, copy) and by file overlap; items touching the
   same files go to the same worker or run serially. Mark risky ones `NEEDS-SECURITY-REVIEW`; queue Dependabot work for
   dependabot-steward. *Gate:* no two parallel workers share a file; max 3 workers at once.
3. **Parallel dev.** Dispatch workers (backend-dev, frontend-dev, infra-ops, marketing-writer, dependabot-steward);
   backend contract first when the frontend depends on it. Each worker: START line, tests, DONE line.
   *Gate:* pytest full suite green; `npm run typecheck && npm run build` green; DONE lines present.
4. **Coverage.** qa-tester adds tests for the changed code and reports before/after %. *Gate:* coverage not below the
   previous total (CI floor once set); real defects fixed or strict-xfail'd.
5. **UX.** ui-ux-designer checks every UI change in a browser (desktop + 390px), including all `NOT browser-checked`
   items, and fixes small issues. *Gate:* no P1/P2 open; P3s listed in the ledger.
6. **Pentest.** security-reviewer reviews the wave's diff and probes on throwaway apps. *Gate:* no open HIGH/CRIT;
   MED+ either fixed or in `docs/PENTEST.md` + `docs/QUESTIONS.md`.
7. **Docs and devlog.** tech-writer audits docs against the code (API, guides, recipes, config tables);
   marketing-writer touches public copy only if limits/claims changed; release-blogger publishes the roundup.
   *Gate:* docs match shipped behavior.
8. **Ship.** scm-security commits in logical chunks, gitleaks clean, pushes, watches CI and the deploy. *Gate:* CI,
   image and deploy runs green, SHAs and run ids in the ledger. Manager writes `[manager] DONE` with what remains for
   next time (blocked PRs, tabled items) and updates the agenda memory/"Now / Next".

Metrics to glance at after each wave: fix-after-fix commits (<15%), ledger overhead (<10% of tokens), frontend checked
before merge, spend within cap or an approved request.
