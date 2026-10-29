# {{PROJECT}}: project brief

Non-negotiable contract. Every agent reads it before doing anything. A run that breaks it gets thrown away.
Keep it current; record why it changed in the Amendments log (do not let it drift from reality).

## Mission
{{One paragraph: what the team builds, for whom, and what "done" looks like.}}

## Offering
{{What the product does for a user, in plain bullets. Out of scope: ...}}

## Caps and limits
Single source of truth: `{{config_file}}`. Docs, UI and tests quote those values and never redefine them.
{{List only the names of the caps (e.g. memory, storage, image size, per-user limits), not duplicate values.}}

## Hosting / stack
{{Provider, regions, languages, frameworks, reference repo if any.}}

## Budget
{{Target per month, current baseline, rule: any new paid resource needs a request in `docs/requests/` the human approves.}}

## Tracking
All work lives in {{repo_path}} ({{repo_url}}). One agent (`scm-security`) commits and pushes and watches CI; it is also
responsible for keeping secrets out of SCM. Secrets live in repo secrets or the platform's secret store, never in git.

## Agents
Roster and ledger role names: `process/WAVE.md` (the only list of valid roles). Agent files: `.claude/agents/`.
Invariants: only `scm-security` commits and pushes (a dependency steward may merge PRs and push to PR branches only);
the committer is never the security reviewer of its own push.

## Human gates
{{HUMAN}} approves, in writing in `docs/requests/` or `docs/QUESTIONS.md`: any new paid resource, any apply against real
infra, destructive actions on live data, layout changes (mockup first), secrets and repo settings. Open questions for
{{HUMAN}} live only in `docs/QUESTIONS.md`. Agents proceed on unblocked work and let requests pile up rather than stop.

## Amendments log
Dated, append-only.

- {{YYYY-MM-DD}} Initial brief.
