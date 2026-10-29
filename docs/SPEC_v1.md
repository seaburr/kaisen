# Kaisen v0 build spec

Status: draft for Chas, 2026-10-29. Kaisen runs a governed agent team that turns a brief into running apps on PicoPaaS. v0 is single-tenant (Chas) and must build two apps: #1 a checklist app, #2 a space-invaders-style browser game. A third goal arrives later, so nothing here is specific to those two.

## 1. Goals and non-goals

Goals
- The orchestrator itself runs as one PicoPaaS app, inside the platform caps.
- From a brief, it plans, builds, verifies, security-reviews, documents and deploys an app, with no human dispatching.
- Humans only approve spend/risk and answer questions, through one inbox.
- It improves its own kit (prompts, checklists) through human-approved PRs only.
- Every run is metered, with hard dollar/token caps and a kill switch.

Non-goals (v0)
- Multi-tenancy, billing, signup, vault per customer.
- A runner tier or pods (not available under PicoPaaS caps; see 3).
- Cloud-account credentials of any kind. Never supported.
- Changing the orchestrator's own Python code by agent (Chas does that by hand).
- Apps needing more than one container, SMTP, or private images.

## 2. Architecture

```
 Chas -- browser --> [Kaisen orchestrator on PicoPaaS]  <-- DATABASE_URL (pg, 50MB)
                         |  scheduler + agent loop + inbox UI + /metrics
          +--------------+------------------+
          v              v                   v
   api.anthropic.com   GitHub API/git     platform.picopaas.xyz API
   (tool-use loop)     (one repo)         (deploy, status, logs)
                         |
                   GitHub Actions in the same repo: tests, coverage, gitleaks,
                   Playwright smoke, image build -> ghcr.io
```

Decision: one workspace repo (`KAISEN_BRIEF_REPO`, e.g. `owner/kaisen-work`) holds everything Chas supplies:
- `brief.yaml` - the contract (mission, per-project goals, caps, budget, stack, gates).
- `kit/` - prompt/checklist overlay on the baked-in kit (self-enhancement edits only this).
- `projects/<slug>/` - one directory per generated app (own Dockerfile, tests, `picopaas.yaml`).
- `.github/workflows/` - generic CI, path-filtered per project.
- Issues, PRs and labels are the work queue.

One repo means one fine-grained token and nothing else for Chas to create.

Orchestrator process (Python 3.12, FastAPI, `anthropic` SDK, APScheduler, SQLAlchemy; same house style as PicoPaaS):
- Scheduler tick every 60 s: pick the highest-priority `state:ready` issue, check budget, run one role, log events. One run at a time (single replica, single worker).
- Agent runner: the Anthropic Messages API tool-use loop (section 3).
- Inbox UI: server-rendered pages, protected by `KAISEN_ADMIN_PASSWORD` plus a session cookie.
- `/healthz`, `/metrics` (Prometheus text, same style as PicoPaaS: runs, tokens, dollars, gate failures, queue depth).

Deployment spec (app `kaisen`):
```yaml
apiVersion: picopaas/v1
name: kaisen
image: ghcr.io/<owner>/kaisen:<tag>   # public, non-root USER 10001, <512MB
port: 8080
healthCheck: { enabled: true, path: /healthz }
resources: { db: true, db_engine: pg }
env:
  ENVIRONMENT: prod
  KAISEN_BRIEF_REPO: owner/kaisen-work
  KAISEN_DAILY_USD_CAP: "10"
  KAISEN_RUN_USD_CAP: "3"
secrets: [ANTHROPIC_API_KEY, GITHUB_TOKEN, PICOPAAS_API_KEY, KAISEN_ADMIN_PASSWORD, SECRET_KEY]
```
Caps check: 384MB RAM, 200m CPU (`DEFAULT_CPU_LIMIT_M`), 250MB scratch, image under 512MB. Python slim + git + gitleaks is about 150MB. The process is I/O bound (waiting on model and GitHub), so 200m CPU is enough. Memory budget: app about 120MB, one repo checkout in scratch (shallow clone, `/tmp`, re-cloned per run, so no volume needed), one run at a time.

Persistent state decision: PicoPaaS Postgres (`db: true, db_engine: pg`, 50MB, backed up when backups are on) holds the `events`, `runs`, `inbox`, `budget` tables. No volume in v0: volumes force single-replica and stop-then-start deploys, and everything on one is reconstructible from GitHub plus the DB. Truth hierarchy: GitHub (issues, PRs, code) is the work state; the DB is the audit/metering state; losing the DB loses history, not work. `events.jsonl` is an export (`GET /events.jsonl`, written into `docs/events/YYYY-MM-DD.jsonl` in the repo by the daily digest PR) so history also lives in git.

Admin overrides: none required for the orchestrator. One is needed for the account: `DEFAULT_APP_LIMIT` is 1 and `ADMIN_DEFAULT_APP_LIMIT` is 5. Kaisen needs orchestrator + 2 prod apps + up to 2 concurrent previews = 5. If the deploy account is not an admin, file a docs/requests-style note asking for a limit of 5 (no cost, shares existing node capacity; each app takes up to 200m/384MB, which counts against `NODE_CPU_CAP_M` 1500, so check headroom). Previews are deleted when done to stay under the cap.

## 3. How agents run: decision

Recommendation: a direct Anthropic Messages API tool-use loop (the `anthropic` Python SDK), with a small set of Kaisen-defined tools. Not the Claude Code CLI and not the Agent SDK (which wraps the CLI).

| Factor | API loop | Claude Code CLI / Agent SDK |
|---|---|---|
| Image | +~40MB (SDK) | +~250MB (Node, CLI), near the 512MB cap |
| RAM | ~30MB | 300-500MB node process, exceeds 384MB with the app |
| CPU at 200m | fine (I/O bound) | slow startup, throttled |
| Sandbox | tools I write, so I control them | built-in Bash/Edit run arbitrary commands in the same container as every secret |
| Metering | exact `usage` per call, hard cap per loop | cost visible only after the fact |
| Prompt caching | explicit, easy | opaque |

The key consequence: the container cannot run `npm install` or `pytest` for big projects. So execution happens in GitHub Actions, which is free for public repos and already the right place for gates. Builder loop:
1. Tools: `list_files`, `read_file`, `grep` (on the shallow checkout), `write_file`, `delete_file`, `commit_and_push` (orchestrator runs gitleaks on the staged diff, then commits to the branch `kaisen/<issue>-<slug>`; the model never gets a shell or the token), `get_ci_result` (blocks up to N minutes, returns failing job log tails), `ask_human` (creates an inbox item and ends the run), `finish`.
2. Loop: edit, push, wait for CI, read logs, fix. Cap 12 CI rounds per issue.
3. Each model call is limited by `max_tokens`, a per-run dollar cap and a turn cap (40 turns). Older tool results are truncated to keep context small; the system prompt and kit use prompt caching.
4. Model is set by `KAISEN_MODEL` (default Sonnet-class; planner and security-reviewer may use a stronger model via `KAISEN_MODEL_REVIEW`). Prices live in one table in `config.py`; unknown model means refuse to run.

If later a task truly needs a shell, add a Phase-2 runner tier (separate request); the tool interface stays the same.

## 4. Work state and roles

Issues in the workspace repo are the queue. Labels: `project:<slug>`, `role:<name>`, `state:ready|building|verifying|review|blocked|done`, `needs-approval`, `human` (needs an answer), `kaisen-friction`, `kaisen-self`.

Events (DB, mirrored daily to git): `{ts, run_id, role, issue, kind, tokens_in, tokens_out, usd, detail}` with kinds `run_start|run_end|tool_error|ci_fail|gate_fail|approval_asked|approval_given|budget_stop|friction`.

Roles (prompts in `kit/roles/*.md`, derived from `~/source/kaisen/agents`, de-personalised with `{{owner}}`, `{{stack}}`):
- planner: reads `brief.yaml`, writes the issue breakdown (each issue: acceptance test, size S/M, files in scope), orders it, files `human` questions. No code.
- builder: implements one issue on a branch, with tests first.
- verifier: independent of builder; writes extra tests, drives the Playwright smoke, checks the deployed preview against the acceptance criteria. Owns the coverage floor.
- security-reviewer: reads every PR diff (never the author of it); checks authz, injection, secrets, dependencies, Dockerfile user. Blocks with a comment, never edits.
- docs: README, usage notes, per-project `docs/`.
- scm: not an agent. Code in the orchestrator: gitleaks, push, open PR, merge when gates are green. This keeps the "only one committer" rule and spends no tokens.

Pipeline per issue: `ready` -> builder -> CI green -> verifier -> security-reviewer -> (if label `needs-approval`, wait for human) -> scm merges -> deploy.

## 5. Gates

All are enforced by code or CI, not by prompts.
1. Tests pass in CI (pytest or vitest as per stack).
2. Coverage floor from `brief.yaml` (default 80% lines, never lowered by an agent).
3. gitleaks, no allowlist: CI fails if `.gitleaksignore` exists or any `gitleaks:allow` appears; also run locally by scm before each push.
4. Playwright smoke against the preview URL (page loads, key interaction works, no console errors).
5. Security-reviewer verdict `pass` recorded as a PR review.
6. Human `needs-approval` for: new paid resource, DB or volume addition, a new third-party dependency over a size threshold, any `brief.yaml` change, any change under `kit/protected/`, and the first production deploy of each project.
7. Branch protection on `main`: PRs only, required checks above, no direct push, no force push. The token has no admin scope so it cannot change this.

## 6. Budget and kill switch

- Meter every API response's `usage` (input, output, cache read/write) into `events` with computed USD.
- Caps: per run (`KAISEN_RUN_USD_CAP`, default 3), per day (`KAISEN_DAILY_USD_CAP`, default 10), per project total (`brief.yaml: budget_usd`). A cap hit stops the run at the next call boundary, labels the issue `blocked`, and posts an inbox item. Raising a cap is a human action in the UI only.
- Runaway guards: turn cap, CI-round cap, same-tool-call repeat detector (3 identical calls in a row ends the run), 30-minute wall clock per run.
- Kill switch: a `Pause all` button in the UI and `KAISEN_PAUSED=1` env var. Paused means the scheduler claims nothing; running loops stop at the next call. The Anthropic console spend limit on Chas's key is the outer cap; Chas should set it to the same daily figure times 2.
- The digest reports spend by day, project and role.

## 7. Secrets

| Name | Where | Required from Chas | Minimum scope |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | PicoPaaS secret on `kaisen` only | yes | a dedicated key with a console spend limit set |
| `GITHUB_TOKEN` | PicoPaaS secret on `kaisen` only | yes | fine-grained PAT, one repo only (`KAISEN_BRIEF_REPO`): Contents RW, Pull requests RW, Issues RW, Workflows RW, Actions Read, Metadata Read. No admin, no org, no other repos |
| `KAISEN_BRIEF_REPO` | plain env var | yes (`owner/repo`) | not secret |
| `PICOPAAS_API_KEY` | PicoPaaS secret on `kaisen` only | yes, or Kaisen creates nothing itself | a `full`-scope key (keys are not per-app) from a dedicated PicoPaaS user whose only apps are Kaisen's; never Chas's main account |
| `KAISEN_ADMIN_PASSWORD` | PicoPaaS secret | Chas picks | n/a |
| `SECRET_KEY` | PicoPaaS secret | generated by us | session signing |

Rules: secrets are env vars read once at startup, never put in prompts, tool results, logs or events (a redaction filter replaces any value matching a held secret). The model has no tool that reveals or sends them. The `deploy` capability is orchestrator code only; agents cannot call it directly. Generated apps get their own secrets only through the `secrets` list in `picopaas.yaml`, supplied by Chas in the inbox UI (v0 apps need none).

Open prerequisite: GitHub Packages are private by default on first push. PicoPaaS needs public images, so Chas must flip each new package (`kaisen`, `kaisen-work-checklist`, `kaisen-work-invaders`) to public once, and Kaisen asks via an inbox item at first deploy.

## 8. Deploying generated apps

- Each project directory has a `picopaas.yaml` (`apiVersion: picopaas/v1`, name, image, port, healthCheck, resources). CI builds `ghcr.io/<owner>/<repo>-<slug>:<sha>` (non-root `USER 10001`, under 512MB) on every green PR/merge.
- Orchestrator deploys via `POST /apps/spec` with `PICOPAAS_API_KEY`, using an image tag with the commit SHA (never `latest`).
- Names: `kaisen-<slug>-preview` (created per PR, one slot per project, deleted after merge or after 24 h) and `kaisen-<slug>` (prod). The smoke test runs against the preview URL; merging deploys prod; failed prod health check triggers rollback via the versions API and an inbox item.
- Default app shape: static or single-process Node/Python, no DB. A DB needs `needs-approval`.
- Egress for generated apps follows the platform (all TCP except SMTP, private ranges blocked), so we do not rely on it.

## 9. Self-enhancement loop

Goal: the factory notices the same friction repeatedly and proposes a kit change, safely.
1. Detect: each run end writes `friction` events with a normalised signature (e.g. `ci_fail:missing-dockerfile-user`, `gate_fail:coverage`, `turns>30`, `human-question:repeat`). A nightly job groups signatures. Trigger: same signature in >= 3 runs across >= 2 issues, or any one role's median cost 2x the project baseline.
2. Propose: a `kit-improver` role (planner-class model) reads the evidence events, writes a minimal patch to `kit/` only (a role prompt, checklist or skill file) and opens a PR labelled `kaisen-self` with: signature, evidence links, expected effect, how to measure.
3. Test on a fixture: the repo holds `fixtures/mini-todo/` (a tiny known project with a seeded issue set). The orchestrator runs the same fixture issue twice with a hard fixture budget ($1.50): baseline kit vs patched kit. Pass if gates are green in both and the patched kit uses no more tokens/turns, or fixes the signature. Result is attached to the PR as a comment.
4. Approve: a human merges. Nothing self-merges, ever. The orchestrator reads `kit/` from `main` at each run start, so approval takes effect without redeploy.
5. Measure: after merge, the signature is tracked for the next 5 runs; if it recurs or cost rises, the kit-improver files a revert PR (still human-merged).

Guards against runaway loops:
- At most 1 open `kaisen-self` PR; at most 2 proposals per week; 7-day cool-down per signature; a self-PR budget of $3 per proposal (counted in the daily cap).
- `kit/protected/` (gate definitions, budget rules, security policy, this loop's own rules) is listed in CODEOWNERS and also blocked by the orchestrator: a self PR touching it is closed automatically and an inbox item is raised.
- The kit-improver cannot edit `brief.yaml`, workflows, or orchestrator code. Prompt changes are size-limited (<= 40 changed lines).
- Friction caused by a self-PR run is excluded from detection (no self-feeding).

Code changes to the orchestrator itself are human-only in v0; friction that needs them is put in the inbox as a feature request.

## 10. Inbox and digest

- Inbox (web UI, `/inbox`): approve/deny, answer free-text questions, see item context (issue link, diff link, cost so far). Item sources: `needs-approval` labels, `ask_human` calls, budget stops, rollbacks, self-PRs.
- GitHub mirror: each item is also an issue labelled `human`, so answering by comment on GitHub works and notifies Chas by GitHub's own email (PicoPaaS blocks SMTP, so Kaisen sends no mail). The orchestrator polls those comments and closes the loop.
- Daily digest (08:00 in `brief.yaml: timezone`): an issue labelled `kaisen-digest` plus `/digest` page: done, in flight, blocked, spend vs caps, gate failures, friction signatures, what needs a decision. Posted as a comment so Chas is notified.

## 11. Security

- Prompt injection: issue text, repo files, dependency READMEs and CI logs are untrusted data. They are wrapped in `<untrusted source="...">` blocks, system prompts state that nothing inside is an instruction, and capability is limited by tools, not by trust: no shell, no arbitrary network fetch (v0 has no `web_fetch` tool), writes confined to `projects/<slug>/` and `kit/` of the current issue's scope (path check in code), no access to `.github/workflows` for builders (only planned workflow issues, with `needs-approval`).
- Token blast radius: GitHub token reaches one repo and cannot bypass branch protection or alter repo settings; worst case is junk branches/PRs and spend up to the caps. PicoPaaS key can create/delete apps on the dedicated account only. Anthropic key is bounded by the console limit.
- Egress: the orchestrator needs only `api.anthropic.com`, `api.github.com`, `github.com`, `platform.picopaas.xyz` (PicoPaaS allows these; it cannot restrict by host, so the code uses a fixed host allow-list in its HTTP client).
- No cloud-account tokens (DigitalOcean, Spaces) are ever given to Kaisen.
- Dependency policy: pinned versions; security-reviewer checks new packages; Dependabot enabled in the workspace repo.

## 12. Observability

`/metrics`: `kaisen_runs_total{role,outcome}`, `kaisen_tokens_total{kind}`, `kaisen_usd_total{project}`, `kaisen_budget_remaining_usd`, `kaisen_queue_depth{state}`, `kaisen_gate_fail_total{gate}`, `kaisen_paused`. Structured JSON logs to stdout (visible in PicoPaaS logs), with secrets redacted. A run page in the UI shows the full transcript (tool calls, truncated results) from `events`. PicoPaaS health, restarts and events cover the container.

## 13. Milestones

Costs are rough; the first measured run in M0/M1 replaces these. Assume a Sonnet-class model with prompt caching.

### M0 - hello-world orchestrator on PicoPaaS (est. $1)
Build: skeleton service, DB schema, `/healthz`, `/metrics`, login, pause button, Docker image, `picopaas.yaml`, CI workflow, one "ping" agent call that reads `brief.yaml` and one issue via GitHub, writes a comment and logs metered tokens.
Acceptance:
- Deployed at `kaisen.picopaas.xyz` (or generated host), healthz 200, image under 512MB, RSS under 250MB at idle.
- Ping run creates an event row, a GitHub issue comment, and nonzero USD that matches the Anthropic console within 5%.
- Hitting `KAISEN_RUN_USD_CAP=0.01` stops the run; pause switch stops the scheduler.
- No secret value appears in logs (test greps).

### M1 - checklist app end to end (est. $15-30)
Build: planner, builder, verifier, security-reviewer, scm, gates, preview and prod deploy, inbox, digest.
Acceptance:
- From a brief ("a checklist web app: add, tick, delete, persists in browser storage; no backend DB"), with no human input beyond approvals, the repo has issues, merged PRs, tests, and the app is live at a PicoPaaS URL.
- All gates ran and were green on each merged PR; coverage >= floor; Playwright smoke passed on the preview.
- At most 5 inbox items and 1 taste review; total spend under project budget ($25 default); zero secrets leaked (gitleaks over history).
- Digest delivered next morning.

### M2 - space invaders end to end plus one self-enhancement cycle (est. $30-70)
Build: canvas game brief; the kit-improver, fixture runner, `kaisen-self` PR flow.
Acceptance:
- Game loads, ship moves/shoots, invaders advance, score and game over work; Playwright smoke plays a scripted round; live on PicoPaaS.
- At least one friction signature from M1/M2 triggers a `kaisen-self` PR with fixture result attached; Chas merges it; the signature measurably drops (or the revert path is demonstrated).
- Loop guards tested: a second proposal inside the cool-down is refused; a self-PR touching `kit/protected/` is auto-closed.

### M3 - generalization hooks for the third goal (est. $10-20 setup)
- `brief.yaml` schema supports `stack` presets (static, node, python-fastapi) and project-level `recipes` (kit skill files) so a new app type is a kit addition, not code.
- Optional per-project secret slots entered in the inbox and passed to PicoPaaS `secrets`.
- A `project import` flow: point the brief at an existing `projects/<slug>` directory.
- Role plug-ins: extra roles declared in `kit/roles/` with tool permission lists, validated against a schema.
- Decide with Chas then whether the third goal needs a DB, volume or runner tier; each becomes a request.

## 14. Test plan

- Orchestrator: pytest, SQLite in-memory, real login flow, no auth mocks (house style).
- Anthropic, GitHub, PicoPaaS clients behind interfaces with fakes; the fake model scripts tool-use sequences (success, tool error, loop, budget exhaustion).
- Unit: budget math, path-confinement, redaction, label state machine, signature normaliser, loop guards.
- Contract: recorded GitHub/PicoPaaS response fixtures. Fixtures use values that do not look like secrets (e.g. `not-the-password`).
- Live: M0 ping, M1 and M2 runs are the end-to-end tests; plus a nightly fixture run (`fixtures/mini-todo`, $1.50 cap) as regression of the kit.
- Coverage floor 85% on the orchestrator in CI; gitleaks in pre-commit and CI.

## 15. Risks and mitigations

1. Remote-only execution is slow and costly in tokens (each fix round trips CI). Mitigation: CI-round cap, small issues, builder checklist to run lint locally via tools, measure at M1, then consider a runner tier.
2. Model cost unknown (no metered baseline). Mitigation: hard caps, dedicated key limit, M0 measures a real number before M1.
3. GHCR visibility and PicoPaaS public-image rule need manual flips and may block deploys. Mitigation: first-deploy inbox item, documented; fall back to Docker Hub if it is a recurring pain.
4. Prompt injection via issues/deps leading to junk or malicious code. Mitigation: tool confinement, branch protection, mandatory independent security review, human approval for risk items.
5. Self-enhancement degrades the kit or loops. Mitigation: protected files, fixture gate, rate limits, human merge only.
6. Orchestrator restarts or deploys mid-run. Mitigation: runs are idempotent by issue state; on boot, orphaned `building` issues are reset to `ready` with a note.
7. PicoPaaS app limit and node CPU headroom (5 apps x 200m against `NODE_CPU_CAP_M` 1500). Mitigation: previews deleted promptly; admin-limit note.
8. 50MB DB too small for transcripts. Mitigation: store truncated transcripts, prune after 30 days, digest to git.

## 16. What Chas must provide

1. A GitHub repo (empty is fine; name goes in `KAISEN_BRIEF_REPO`, as `owner/repo`) with: branch protection on `main` (PR required, required status checks, no force push) and Actions enabled.
2. A fine-grained GitHub PAT limited to that one repo with Contents RW, Pull requests RW, Issues RW, Workflows RW, Actions Read, Metadata Read - secret name `GITHUB_TOKEN`.
3. `ANTHROPIC_API_KEY`, a dedicated key with a console spend limit (suggest $10/day).
4. `PICOPAAS_API_KEY`: a `full` key from a dedicated PicoPaaS user for Kaisen's apps (app limit raised to 5; otherwise Kaisen cannot run previews).
5. `KAISEN_ADMIN_PASSWORD` (any strong value) for the inbox UI.
6. Once per image: set the GHCR packages to public when the inbox asks.
7. Two short briefs (checklist, space invaders) and, later, the third goal.
8. Approvals: first prod deploy per project, `brief.yaml` edits, and every `kaisen-self` PR.

Only the secret names above and `KAISEN_BRIEF_REPO` need to be set on the `kaisen` app; everything else in the spec is non-secret env with defaults.
