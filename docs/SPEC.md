# Kaisen v0 build spec (v2)

Status: v2, 2026-10-29, revised after `docs/SPEC_REVIEW.md`. v1 is preserved as `docs/SPEC_v1.md`. Kaisen runs a governed agent team that turns a brief into running apps on PicoPaaS. v0 is single-tenant (Chas) and must build two apps: #1 a checklist app, #2 a space-invaders-style browser game. A third goal arrives later, so nothing here is specific to those two.

PicoPaaS facts used below were checked against the PicoPaaS repo (`app/config.py`, `app/k8s_manifests.py`, `docs/USER_GUIDE.md`, `docs/API.md`, `docs/ADMIN_GUIDE.md`). Items marked **External** are GitHub or Anthropic behaviour that could not be checked from the repo; M0 contains a rehearsal for each.

## 0. Review resolutions

Correction of a false premise first: **PicoPaaS does not enforce an egress allowlist.** App pods may open all TCP ports except SMTP (25, 465, 587), plus UDP; private ranges are blocked (USER_GUIDE "Internet", lines 527-536). An allowlist to Anthropic/GitHub/registries exists only in the concept doc's future runner tier. Kaisen's in-code host allowlist is a convention against model mistakes, not a security boundary. v2 does not rely on it for containment (section 11).

| Review item | Resolution in v2 |
|---|---|
| Change 1 (review/merge gate bypassable) | Resolved. Second GitHub identity (machine user `kaisen-bot` in a free org) so Chas is a different human; ruleset with no bypass actors; required checks bound to the GitHub Actions app; code-owner review only on protected paths; security verdict is a commit status; negative tests in M0/M1 (sections 5, 16). |
| Change 2 (identity, untrusted principals; public vs private) | Resolved. Login-ID allowlist on every consumed issue, comment, label event and PR; v0 answers only via the inbox, so GitHub comments are never an input; repo public vs private is Decision 1 with cost (sections 4, 10, 17). |
| Change 3 (smoke vs CI ordering) | Resolved. The smoke runs inside Actions against a locally started container (`docker run` + Playwright). Preview deploy happens after merge, for humans only, and is not a gate (sections 5, 8). |
| Change 4 (budget, console limit, metered M0) | Resolved. Caps replaced with the reviewer's table; one console limit ($16); M0 is a metered three-task experiment with cent-level reconciliation; truncation removed (sections 3, 6, 13). |
| Change 5 (lease/fencing, off-request-thread loop) | Resolved. DB lease with fencing token; agent loop in a worker thread; `/healthz` touches no DB or loop state. Also found: during a rolling surge both pods hold DB connections, so the pool is 2 per pod, not 3 (section 2). |
| Change 6 (30-min cap vs CI waits) | Resolved by the recommended split: a run is resumable steps, persisted per CI round; the run ends while waiting; a token-free poller resumes it. CI rounds cap lowered to 6 (section 3). |
| Change 7 (Postgres specifics) | Resolved: pool 2, `verify-full` with `${{ db.ca_cert }}`, URL scheme rewrite, 50 MB read-only handling, retention job, Alembic migration in M0 acceptance (section 2). |
| Change 8 (role-scoped write paths) | Resolved: builder `projects/<slug>/` only; enforced on the final staged diff; `.github/`, symlinks, `..` rejected (section 11). |
| Change 9 (narrow self-enhancement) | Resolved by deferral plus narrowing: v0 self-enhancement is a human-run report (friction table + proposed diff in a draft issue). Reviewer/verifier prompts and the untrusted wrapper are protected; evidence is signatures only; automation and fixture runner deferred to after M2 (section 9). |
| Change 10 (honest app-slot plan) | Resolved: 4 of 5 slots (orchestrator, 2 prod, 1 shared preview), delete-in-finally plus orphan sweep, admin raise as a pre-M0 task, node headroom checked with sre, not `NODE_CPU_CAP_M` (section 2). |
| Change 11 (A/B fixture meaningless) | Resolved: deterministic offline kit lint/regression gates any kit change; one live fixture run is a smoke only; no pass/fail on token counts (section 9). |
| Change 12 (inbox security) | Resolved: CSRF, cookie flags, lockout, escaping, redaction in UI transcripts (section 10). |
| Change 13 (missing Chas prerequisites) | Resolved: section 16 now lists Kaisen image build/first deploy, approved signup, browser-minted key, second identity, repo decision, package flips. |
| Change 14 (false statements) | Resolved: `NODE_CPU_CAP_M` removed (it is the per-app override ceiling, `config.py:174-178`); "versions API" replaced by `image-history` + `rollback`; console limit contradiction fixed; egress corrected above. |
| Change 15 (pin model, 429 accounting) | Resolved: `KAISEN_MODEL` is a full model ID, recorded per event; 429/backoff rules and accounting in section 6. |
| Claims 1.6, 1.12, 1.13 (backups, cache vs truncation, unmeasured sizes) | Backups: M0 checks the platform backup setting for the Kaisen app (USER_GUIDE 387: new apps start on/off per platform setting) and turns it on if off. Truncation removed. Sizes measured in M0. |
| Claim 1.9 (ports, UID, bandwidth) | Added to the generated-Dockerfile rules (section 8). |
| Claim 1.10 (GHCR private by default) | **External**; handled as three manual flips rehearsed in M0 (section 7). |
| Section 7 "minimal viable path" | Adopted with one change: the verifier role is dropped and its job (independent tests, smoke) moves into CI. Rejected: nothing. Deferred: verifier, docs role, digest, GitHub-comment answers, kit-improver automation, fixture runner, M3 plug-ins. |

## 1. Goals and non-goals

Goals
- The orchestrator itself runs as one PicoPaaS app, inside the platform caps.
- From a brief, it plans, builds, security-reviews and deploys an app; no human dispatching.
- Humans only approve spend/risk and answer questions, through one inbox.
- Every run is metered, with hard dollar/token caps and a kill switch.
- Self-improvement in v0 is advisory: Kaisen reports friction and proposes a diff; Chas applies it.

Non-goals (v0)
- Multi-tenancy, billing, signup, vault per customer.
- A runner tier or pods (not available under PicoPaaS caps).
- Cloud-account credentials of any kind. Never supported.
- Agent edits to orchestrator code, workflows, `brief.yaml`, `CODEOWNERS` (Chas, by hand).
- Apps needing more than one container, SMTP, private images, or any secret.

## 2. Architecture

```
 Chas -- browser --> [Kaisen orchestrator on PicoPaaS] <-- DATABASE_URL (pg, 50MB, 5 conns)
                        | worker thread (agent loop) + poller + inbox UI + /metrics
          +-------------+------------------+
          v             v                  v
   api.anthropic.com  GitHub API/git    platform.picopaas.xyz API
   (tool-use loop)    (one repo,        (deploy, status, image-history, rollback)
                       as kaisen-bot)
                        |
                  GitHub Actions in the same repo (pull_request, contents: read):
                  tests, coverage, gitleaks, docker build + Playwright smoke (local container)
                  GitHub Actions on push to main: build + push image to ghcr.io
```

Workspace repo (`KAISEN_BRIEF_REPO`, e.g. `<org>/kaisen-work`) holds: `brief.yaml`, `kit/`, `projects/<slug>/`, `.github/workflows/` (Chas-authored only), `CODEOWNERS`. Issues, PRs and labels are the work queue, but only from allowlisted logins (section 4).

The Kaisen image itself lives in a different repo (`~/source/kaisen`). That repo has its own workflow that builds and pushes `ghcr.io/<owner>/kaisen:<sha>`; Chas flips the package public once, does the first `picopaas apply`, and redeploys by hand (v0: orchestrator never deploys itself).

Orchestrator process (Python 3.12, FastAPI, `anthropic` SDK, SQLAlchemy 2, Alembic):
- **Main thread:** FastAPI inbox, `/healthz` (static 200, no DB, no loop state), `/metrics`.
- **Worker thread:** lease holder; scheduler tick every 60 s; runs one step at a time. Blocking calls (clone, gitleaks, model, GitHub) never run on the event loop, so liveness probes (3 failures x 5 s, USER_GUIDE 425-430) cannot kill a run.
- **Lease and fencing:** `lease(id=1, holder, token, expires_at)` in Postgres. A pod acquires by compare-and-swap, increments `token`, renews every 10 s (expiry 40 s), releases on SIGTERM. The token is stored on every `runs`/`events` row, and the worker re-checks the lease before every model call, every push and every deploy; a lost lease aborts the step at the next boundary. Reason: non-volume apps use `RollingUpdate maxSurge 1, maxUnavailable 0` (`k8s_manifests.py:608-611`), so every Kaisen redeploy runs two pods at once. A standby pod only polls the lease.
- **Postgres:** platform role has `CONNECTION LIMIT 5` (USER_GUIDE 300). Because of the surge, two pods coexist, so the SQLAlchemy pool is `pool_size=2, max_overflow=0, pool_pre_ping=True` per pod (4 total, 1 spare for admin psql). Connect with `sslmode=verify-full`, writing `${{ db.ca_cert }}` to `/tmp/db-ca.pem`; rewrite `postgres://` to `postgresql+psycopg://`. Schema through an Alembic migration (M0 acceptance), not `create_all`.
- **50 MB cap:** at 50 MB the role goes read-only and sessions are closed (USER_GUIDE 488, 230). Retention job: transcripts 14 days, events 60 days, prune (DELETE) when usage passes 35 MB, before any insert. Metering is fail-closed: if the events insert fails, the run stops. Reconnect after prune.
- **Backups:** M0 checks that backups are on for the app (platform setting, USER_GUIDE 387) and runs one restore drill of the audit DB before M1.

Truth hierarchy: GitHub is work state; the DB is audit/metering state; losing the DB loses history, not work.

Deployment spec (app `kaisen`):
```yaml
apiVersion: picopaas/v1
name: kaisen
image: ghcr.io/<owner>/kaisen:<sha>   # public, non-root numeric USER 10001, <512MB
port: 8080
healthCheck: { enabled: true, path: /healthz }
resources: { db: true, db_engine: pg }
env:
  ENVIRONMENT: prod
  KAISEN_BRIEF_REPO: <org>/kaisen-work
  KAISEN_MODEL: <pinned full model id>
  KAISEN_ALLOWED_GITHUB_IDS: "<chas numeric id>,<kaisen-bot numeric id>"
  KAISEN_RUN_USD_CAP: "2"
  KAISEN_DAILY_USD_CAP: "8"
secrets: [ANTHROPIC_API_KEY, GITHUB_TOKEN, PICOPAAS_API_KEY, KAISEN_ADMIN_PASSWORD, SECRET_KEY]
```
Caps: 384 MB RAM, 200m CPU (`DEFAULT_CPU_LIMIT_M`), 250 MB scratch, image under 512 MB, one replica, ports 1024-65535, 20 Mbit/s outbound (fine). Image size (~150 MB) and RSS (~120 MB) are estimates; M0 measures both.

**App slots (corrects v1).** A normal account is capped at `MAX_APPS_PER_USER`=5 (`config.py:133`); `DEFAULT_APP_LIMIT` is 1, so the dedicated user needs an approved signup, an admin raising `app_limit` to 5, and Chas logging in as that user in a browser to mint a full key (keys can only be minted in a browser session, API.md:133-138). Plan: orchestrator + 2 prod apps + **one** shared preview slot = 4 of 5; one slot stays free for a leaked app. Preview is deleted in a `finally` block; a boot-time and hourly sweep lists apps and deletes any `kaisen-preview` not owned by an active run. A 403 at the limit raises an inbox item, not a retry. The earlier claim "5 apps x 200m against `NODE_CPU_CAP_M`" is withdrawn: that constant is the per-app override ceiling. Pre-M0 task: sre reports current node CPU/memory requests (64m/128Mi per app, `config.py:168-172`) against the node pool and confirms four more apps fit. The dedicated account holds no other apps, ever (section 11).

## 3. How agents run: decision

Unchanged: a direct Anthropic Messages API tool-use loop, not the Claude Code CLI or Agent SDK (image +~40 MB vs +~250 MB, RAM ~30 MB vs 300-500 MB, no shell in the container with every secret, exact metering, explicit caching). Sizes remain estimates until M0 measures them.

The container cannot run `npm install` or `pytest`; all execution is in GitHub Actions.

**Resumable steps (replaces v1 blocking loop).** A run is a sequence of persisted steps; the process never waits on CI.
1. Step `build`: fresh context = system prompt + kit (cached) + issue + current diff of the branch + the latest structured CI summary (if any). Tools: `list_files`, `read_file`, `grep`, `write_file`, `delete_file`, `commit_and_push`, `ask_human`, `finish`. Ends when the builder calls `commit_and_push`: the step is closed, the issue goes to `state:ci-wait` with `{pr, sha, round}` in the DB.
2. The token-free poller (60 s, GitHub API only) watches the Actions result for that sha. On completion it stores a structured CI summary (job names, status, last 60 lines of each failed job, control characters stripped, 4 KB cap per job) and queues the next step. No model call occurs while waiting.
3. Green: scm step (code), then security-reviewer step, then merge. Red: next `build` step with the summary, up to **6 CI rounds** per issue.
4. Because each step starts from a rebuilt fresh context, no earlier message is rewritten or truncated; the cached prefix stays valid and the 5-minute cache TTL (External) cannot expire mid-wait. Cache is warm only inside a step; the cost model assumes a cache write at the start of each step.
5. Limits per step: `max_tokens`, 25 turns, 10 minutes wall clock of active work. Per issue: 6 CI rounds, 6 hours elapsed including queue time (then `blocked`), same-tool-call repeat detector (3 identical calls).
6. `ask_human` ends the step; the answer (from the inbox only) starts a new step. Restart cost (re-reading) is counted in the budget.
7. `KAISEN_MODEL` is a full pinned model ID (no alias), recorded on every event; `KAISEN_MODEL_REVIEW` may differ. Prices in one `config.py` table; unknown model means refuse to run. Changing the model ID is a human act and resets comparisons.

If a task truly needs a shell, add a Phase-2 runner tier (separate request).

## 4. Work state, roles and principals

Labels: `project:<slug>`, `role:<name>`, `state:ready|building|ci-wait|review|blocked|done`, `needs-approval`, `human`.

**Allowlisted principals.** The orchestrator consumes an issue, PR, comment, label event or review only if the actor's numeric GitHub user ID is in `KAISEN_ALLOWED_GITHUB_IDS` (Chas and `kaisen-bot`). Everything else is ignored and counted in `kaisen_foreign_input_total`. A state-label change by anyone but the bot or Chas is a no-op and raises an alarm inbox item. Compare IDs (`user.id`), not names. In v0 no decision is taken from GitHub comments: approvals and answers come only through the inbox UI, giving a single input surface. GitHub issues labelled `human` are a read-only mirror for notification. Negative test: a comment "approved" from a non-allowlisted account changes nothing (M1 acceptance).

Roles (prompts in `kit/roles/*.md`):
- planner: reads `brief.yaml`, writes the issue breakdown: **at most 5 issues for the checklist and 8 for the game**, each with an acceptance test and files in scope. No code.
- builder: implements one issue on a branch, tests first; writes README for its project.
- security-reviewer: reads every PR diff (never its author); checks authz, injection, secrets, dependencies, Dockerfile user. Blocks via commit status `kaisen/security` (failure with a comment), never edits.
- scm: not an agent; orchestrator code: gitleaks on the staged diff, path-scope check, push, open PR, merge when gates are green.
- Deferred to after M2: verifier, docs, digest.

Pipeline per issue: `ready` -> builder -> push -> `ci-wait` -> CI green -> security-reviewer -> (if `needs-approval`, inbox) -> scm merge -> post-merge image build -> preview deploy -> Chas promotes in the inbox -> prod.

## 5. Gates

Enforced by code or CI. The merge ruleset is configured by Chas (section 16), not by Kaisen.
1. Tests pass (`test` job in `ci.yml`, workflow trigger `pull_request`).
2. Coverage floor from `brief.yaml` (default 80% lines; job `coverage`).
3. gitleaks, no allowlist (job `secrets`); also fails if `.gitleaksignore` exists or `gitleaks:allow` appears; also run by scm before each push.
4. **Smoke inside Actions** (job `smoke`): build the project's image, `docker run` it on the runner, Playwright drives it (page loads, key interaction works, no console errors). This is a required check on the PR, and it is the only smoke gate. The PicoPaaS preview is for human viewing after merge and is not a gate.
5. Security verdict: commit status `kaisen/security` = success on the PR head sha (a commit status, because a PR review by the PR's own author is not allowed and Check Runs need a GitHub App; **External**).
6. Human `needs-approval` (inbox) for: new paid resource, DB or volume, large new dependency, any `brief.yaml` change, any protected path, every production promotion in v0.
7. Ruleset on `main` (Chas configures): PR required; required status checks `test`, `coverage`, `secrets`, `smoke` **bound to the GitHub Actions app as source**, and `kaisen/security`; code-owner review required, with `CODEOWNERS` listing only `.github/**`, `CODEOWNERS`, `brief.yaml`, `kit/protected/**`, `kit/roles/*review*`, `kit/untrusted*`, `fixtures/**` owned by Chas (so ordinary PRs need no human review, protected-path PRs do, and the bot cannot approve its own PR); **no bypass actors**; block force-push and deletion of `main`; restrict deletion of branches matching `kaisen/*` not required.
8. Workflow hardening: top-level `permissions: contents: read`; `ci.yml` never has `packages: write` or any secret; the image push lives in `release.yml`, triggered only on `push` to `main`, with `packages: write` on that job. Fork PR workflows require approval for all outside contributors. The bot's PAT has no Workflows scope, so GitHub itself refuses any push touching `.github/`; scm also rejects such a diff in code (section 11).
9. **Negative tests (M0/M1 acceptance):** (a) a deliberately red PR opened by `kaisen-bot` cannot be merged via the API using the bot token; (b) a PR editing `.github/workflows/ci.yml` is refused at push and, if forced via Chas's token in the test, requires Chas's code-owner review; (c) a PR touching `brief.yaml` cannot merge without Chas; (d) forging a commit status named `test` is not accepted as the required check.

Residual risk, stated plainly: a compromised orchestrator can forge `kaisen/security` and merge a CI-green non-protected change; it cannot weaken a gate or touch protected paths.

## 6. Budget and kill switch

Caps (all configurable; raising a cap is a human action in the UI only):

| Scope | Cap |
|---|---|
| Run (one step sequence for an issue) | $2 (planner/security-reviewer steps $1) |
| Day (UTC day; reset at 00:00 UTC; stored with the event timestamp) | $8 |
| Anthropic console spend limit (the one outer limit) | $16/day, i.e. 2 x the daily cap (if the console offers only a monthly limit, set $100 monthly = whole-v0 cap; **External**) |
| Checklist project | $30 |
| Invaders project | $40 |
| v0 self-enhancement reports total | $10 |
| Whole v0 | $100, then a human re-approves |

v1 inconsistencies ($10 vs $20 console; $3 run) are removed; these figures are the single source (`brief.yaml` and `config.py` defaults read from them).

- Meter every API response's `usage` by kind (fresh input, cache write, cache read, output) into `events` with computed USD and the pinned model ID.
- Cap hit stops the run at the next call boundary, labels the issue `blocked`, posts an inbox item.
- Retries: 429/5xx use exponential backoff with jitter, max 4 tries; each retry counts as an event; 3 consecutive 429s pause the scheduler for 10 minutes and raise an inbox item. GitHub API: respect `Retry-After` and secondary rate limits; poller budget is below 1,000 requests/hour of the 5,000 per-token limit.
- Guards: turn cap, step wall clock, CI-round cap, repeat detector, per-issue elapsed cap.
- Kill switch: `Pause all` in the UI and `KAISEN_PAUSED=1`. Paused means nothing is claimed, running steps stop at the next call.

## 7. Secrets

| Name | Where | Required from Chas | Minimum scope |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | PicoPaaS secret on `kaisen` only | yes | dedicated key, console limit $16/day |
| `GITHUB_TOKEN` | PicoPaaS secret on `kaisen` only | yes | fine-grained PAT owned by `kaisen-bot`, resource owner = the org, one repo only: Contents RW, Pull requests RW, Issues RW, Commit statuses RW, Actions Read, Metadata Read. **No Workflows, no admin** |
| `PICOPAAS_API_KEY` | PicoPaaS secret on `kaisen` only | yes | `full` key minted in a browser by the dedicated PicoPaaS user; the account holds only Kaisen's apps |
| `KAISEN_ADMIN_PASSWORD` | PicoPaaS secret | Chas picks | 20+ characters |
| `SECRET_KEY` | PicoPaaS secret | Chas generates | session signing |
| (none) | GitHub Actions secrets | no | CI uses only the automatic workflow token; `PICOPAAS_API_KEY` is never in GitHub |

Rules: env vars read once at startup, never in prompts, tool results, logs, events or UI (redaction filter for any held secret value, applied to the UI transcript view too). The model has no tool that reveals or sends them. `deploy` is orchestrator code only. Generated apps get no secrets in v0 (a hard rule, not a default).

Open prerequisite (**External**): GHCR packages are believed private on first push with no API to flip them. Flips needed: `kaisen`, `kaisen-work-checklist`, `kaisen-work-invaders`, and a throwaway package for the M0 rehearsal; each blocks the first preview deploy (422 from `/apps/spec` for a non-public image, API.md:227). Kaisen raises an inbox item at the first deploy of each.

## 8. Deploying generated apps

- Each project has a `picopaas.yaml`. `release.yml` (push to `main`) builds `ghcr.io/<owner>/<repo>-<slug>:<sha>` (SHA tag, never `latest`) after merge.
- Dockerfile rules baked into the kit and checked by the security-reviewer: numeric `USER 10001` (a named USER maps to UID 10001 only by convention and causes file-ownership failures, USER_GUIDE 33); listen on 1024-65535 (e.g. `nginx-unprivileged` on 8080); compressed layers under 512 MB; no DB, volume or secret without `needs-approval`.
- After merge and image build the orchestrator deploys to the single shared `kaisen-preview` app via `POST /apps/spec`, reads `hostname` from the response, and posts the URL to the inbox with a Promote button. Promote deploys the same SHA image to `kaisen-<slug>` (prod). Both prod promotions are human-approved in v0.
- Rollback: the platform already rolls back a never-healthy image automatically (event `rollback`, API.md:195). Kaisen does not duplicate it. It watches `GET /apps/{id}/events` and raises an inbox item on `rollback`. A manual rollback is an inbox action using `GET /apps/{id}/image-history` and `POST /apps/{id}/rollback {digest}` (rolling back stops auto-update for that tag; the usage guide notes this).
- Egress for generated apps follows the platform (all TCP except SMTP); generated apps are hostile code with internet access, which is why they receive no secrets.

## 9. Self-enhancement (v0: advisory)

Goal: notice repeated friction and propose a kit change without any automated self-modification.
1. Detect: each run end records `friction` events with a normalised signature (`ci_fail:missing-dockerfile-user`, `gate_fail:coverage`, `human-question:repeat`). Only signature strings and numeric counters are stored for this purpose; no raw log text, issue text or dependency output reaches the report, so an attacker cannot steer it.
2. Report: after M1 and after M2, Kaisen files one draft issue labelled `kaisen-report` containing a friction table (signature, count, cost) and a proposed minimal kit diff as text. Budget: $10 total for v0. No PR is opened.
3. Apply: Chas applies the diff by hand. Any `kit/` change must pass the **offline kit gate** in CI, which uses no model: kit lint (required sections present, byte-size and file-count limits of 3 files / 4 KB per change), a hash check that protected prompts and the `<untrusted>` wrapper text are unchanged unless the PR is Chas's, and a prompt-regression check against recorded transcripts for schema validity.
4. Protected set (CODEOWNERS and code): gate definitions, budget rules, security policy, `kit/roles/*review*`, the untrusted-wrapper text, tool descriptions.
5. Seeded-vulnerability regression: a small set of seeded vulnerable diffs the security-reviewer must block is run once per M-milestone live ($1 smoke, not a token-count pass/fail). Nightly fixture runs and the kit-improver role are deferred until measured runs exist.

## 10. Inbox

- Inbox (`/inbox`): approve/deny, answer free text, see context (issue link, diff link, cost so far). Sources: `needs-approval`, `ask_human`, budget stops, rollbacks, alarms, reports. "Taste review" is defined: after the preview deploy, Chas opens the preview URL and answers accept or reject with free text; it is one inbox item per project.
- Security: session cookie `HttpOnly; Secure; SameSite=Strict`, CSRF token on every POST, login rate limit with lockout (5 failures, 15 minutes; PicoPaaS's limiter is not inherited), all untrusted strings (issue titles, `ask_human` text, CI summaries) escaped on render, no inline script, strict CSP, redaction on the transcript view.
- Notification: GitHub issue mirror notifies Chas by GitHub's own email (PicoPaaS blocks SMTP). Mirror is write-only from Kaisen.
- Spend page `/spend` (by day, project, role) replaces the digest in v0.

## 11. Security

- Prompt injection: issue text, repo files, dependency READMEs and CI logs are untrusted data wrapped in `<untrusted>`; this is a request, not a control. Real controls are tool scope and gates. `get_ci_result` content is length-capped and control characters stripped.
- **Write scopes, enforced on the staged diff** in `commit_and_push`: after normalising (`git diff --cached --name-status --no-renames`, reject mode 120000 symlinks, reject `..`, absolute paths and case-folded matches), builder may touch `projects/<slug>/` only. Nobody (agent) may touch `.github/`, `CODEOWNERS`, `brief.yaml`, `fixtures/`, `kit/`. Orchestrator rejects the push before it leaves the process.
- **Egress (corrected):** the platform does not restrict by host. The fixed host list in the HTTP client (`api.anthropic.com`, `api.github.com`, `github.com`, `platform.picopaas.xyz`, `ghcr.io`) is a convention that prevents the model's tools from fetching arbitrary URLs; it does not contain a compromised process. v0 therefore has no `web_fetch` tool and no code path that sends data to a non-listed host.
- **Token blast radius, stated honestly:** GitHub PAT: junk branches/PRs, spend up to caps; cannot merge red PRs, edit workflows or settings, or bypass the ruleset (no bypass actors, not admin). PicoPaaS full key: can create, update, delete and read logs/env of every app on that account, including redeploying `kaisen` itself with another image; the control is that the dedicated account holds nothing else (tested: M0 lists the account's apps and asserts only Kaisen's). Anthropic key: bounded by the console limit.
- Generated apps get no secrets (hard rule); they run next to nothing of value.
- No cloud-account tokens (DigitalOcean, Spaces) are ever given to Kaisen.
- Dependencies pinned; security-reviewer checks new packages (LLM reading attacker-written text, so large additions need `needs-approval`); Dependabot enabled.

## 12. Observability

`/metrics`: `kaisen_runs_total{role,outcome}`, `kaisen_tokens_total{kind}`, `kaisen_usd_total{project}`, `kaisen_budget_remaining_usd`, `kaisen_queue_depth{state}`, `kaisen_gate_fail_total{gate}`, `kaisen_foreign_input_total`, `kaisen_lease_held`, `kaisen_paused`. Structured JSON logs to stdout, secrets redacted. Run page shows the transcript (redacted). Retention per section 2.

## 13. Milestones (minimal viable path)

All costs below are placeholders until M0 measures them; the gate is the measured number.

### M0 - metered hello-world (budget $5)
Build: skeleton service, Alembic schema, login, pause button, `/healthz`, `/metrics`, lease, pool of 2, image and its own release workflow, `picopaas.yaml`, the metering code, and the experiment below.
Experiment: three fixed micro-tasks against the real model with the exact tool loop: (a) read-only summarise a 200-line repo, (b) write one file and complete one CI round trip, (c) `ask_human` and resume. Record tokens by kind, turns and wall clock for each.
Acceptance (every item reviewers flagged is a test here):
- Deployed on PicoPaaS; `/healthz` 200; image size and RSS measured and published (targets 512 MB / 250 MB).
- Measured cost table (by kind, per task) and wall-clock table published; projected M1 cost computed. **M1 starts only if projected M1 <= 70% of the checklist budget ($21);** else shrink scope.
- Cost reconciles to the cent with the Anthropic console per batch, with cache-write and cache-read lines separately matching (a missing cache-write line item is a failure).
- A redeploy of Kaisen mid-run: the second pod does not start a run until it holds the lease, and an old-pod step aborts at the next boundary (lease/fencing test with both pods alive).
- Pool never exceeds 2 per pod; `too many connections` never appears during surge; `verify-full` connects; retention job runs; DB read-only handling tested by filling to the cap on a copy.
- `KAISEN_RUN_USD_CAP=0.01` stops a run; pause stops the scheduler; no secret value appears in logs or UI (greps).
- Liveness: a 60-second blocking step does not fail `/healthz`.
- Ruleset negative tests 9(a) to 9(d) pass; non-allowlisted comment is ignored.
- GHCR flip rehearsed with a throwaway image; Kaisen self-deploy rehearsed.
- Backups on for the app; restore drill of the audit DB completed.
- Account audit: the dedicated PicoPaaS user lists only Kaisen's apps; sre confirms node headroom for four more apps.

### M1 - checklist app (budget $30; "M1-lite")
Build: planner (<= 5 issues), builder, security-reviewer, scm, gates 1-9, resumable steps, single shared preview slot, human-approved prod promotion, inbox, spend page.
Acceptance:
- From a brief ("checklist web app: add, tick, delete, persists in browser storage; no backend DB") with no human input except approvals, the repo has issues, merged PRs, tests, and the app is live at a PicoPaaS URL via the preview then promote path.
- Every merged PR had `test`, `coverage`, `secrets`, `smoke` (inside Actions) green and `kaisen/security` success; no PR was merged with a red or missing check; ruleset showed no bypass use.
- Max 3 inbox items besides promotions and 1 taste review; at most 6 CI rounds per issue; no run exceeded $2; project spend <= $30; whole-day spend <= $8; zero gitleaks findings over history.
- Wall-clock per issue reported including CI queue time; no step exceeded 10 minutes of active work.
- Non-allowlisted issue, comment and label events were ignored (seeded in the test).
- Preview slot freed after every run (orphan sweep finds nothing); account never exceeded 4 apps.

### M2 - space invaders (budget $40; "M2-lite")
Build: game brief via the same loop; advisory self-enhancement report; offline kit gate in CI.
Acceptance:
- Game loads, ship moves/shoots, invaders advance, score and game over work; Playwright smoke plays a scripted round inside Actions; live on PicoPaaS after promotion.
- At most 8 issues, project spend <= $40, v0 total <= $100.
- A `kaisen-report` issue exists with a signature-only friction table and a proposed diff; Chas applies it by hand; the offline kit gate passes on that PR and rejects a seeded PR that edits the security-reviewer prompt.
- Seeded-vulnerability regression: the reviewer blocks every seeded diff.

### M3 - after M2
Not specified here. Written after M2 using measured numbers and the real third goal. Entry criterion: M2 shipped within budget. It will say what the third goal tests (DB or volume conflicts with the "no DB, no volume" default), stack presets, per-project secret slots and role plug-ins.

## 14. Test plan

- Orchestrator: pytest, SQLite in-memory, real login flow, no auth mocks; Postgres-specific behaviour (pool, lease, retention) covered by a Postgres CI service in M0.
- Anthropic, GitHub, PicoPaaS clients behind interfaces with fakes; the fake model scripts tool-use sequences (success, tool error, loop, budget exhaustion, 429).
- Unit: budget math, path scoping on staged diffs (symlink, `..`, case-fold, rename), redaction, label state machine, lease/fencing, allowlist, signature normaliser, loop guards, CI summary sanitiser.
- Contract: recorded GitHub/PicoPaaS fixtures. Fixtures use values that do not look like secrets (`not-the-password`).
- Live: M0 experiment, M1 and M2 runs; negative tests of section 5.9.
- Coverage floor 85% on the orchestrator; gitleaks in pre-commit and CI.

## 15. Risks

1. Remote-only execution is slow and token-costly. Mitigation: 6-round cap, <= 5/8 issues, measured at M0, projected-cost gate.
2. Model cost unknown. Mitigation: metered M0, caps, one console limit.
3. GHCR visibility needs manual flips. Mitigation: rehearsal in M0, inbox item; Docker Hub fallback.
4. Prompt injection. Mitigation: tool scope, allowlisted principals, ruleset, independent reviewer, human gates.
5. Compromised orchestrator replaces itself via the full key. Mitigation: the account holds nothing else; accepted residual.
6. Deploy mid-run. Mitigation: lease/fencing, runs resumable from DB state; boot sweeps orphaned `building` issues and leaked previews.
7. PicoPaaS app limit and node headroom. Mitigation: 4 of 5 slots, sre check pre-M0.
8. 50 MB DB. Mitigation: retention job, fail-closed metering, backups.
9. GitHub or Anthropic rate limits. Mitigation: backoff and accounting (section 6).

## 16. What Chas must provide

Pre-M0 (about 45 minutes, in this order):
1. **PicoPaaS dedicated user:** sign up a new account (e.g. `kaisen@...`); as admin, approve it and set its `app_limit` to 5 (hard max is `MAX_APPS_PER_USER` 5, so no config override is needed); log in as that user in a browser and mint a `full` API key. This user must hold no apps other than Kaisen's. Ask sre to confirm node headroom for four more apps.
2. **GitHub machine user:** create a second GitHub account `kaisen-bot` (GitHub permits one machine account per person; email alias OK). Enable 2FA. Note its numeric user ID and yours (`gh api users/<login> --jq .id`).
3. **Free GitHub organization** `<org>` (owned by you). Create repo `<org>/kaisen-work`. Visibility per Decision 1. Add `kaisen-bot` as a collaborator/team member with **Write** (not Admin, not Maintain).
4. **Fine-grained PAT** owned by `kaisen-bot`, resource owner = `<org>` (the org may need to allow fine-grained PATs and approve the token), repository access = `kaisen-work` only; permissions: Contents RW, Pull requests RW, Issues RW, Commit statuses RW, Actions Read, Metadata Read; no Workflows, no Administration. Secret name `GITHUB_TOKEN`. (**External**: if the org cannot issue fine-grained PATs, tell the builder; fallback is a classic PAT with `repo` only on the dedicated bot account.)
5. **CODEOWNERS and workflows by hand:** add `ci.yml` (jobs `test`, `coverage`, `secrets`, `smoke`; `permissions: contents: read`; no secrets), `release.yml` (push to `main`; `packages: write` only on the build job) and `CODEOWNERS` (your login owns `.github/**`, `CODEOWNERS`, `brief.yaml`, `kit/protected/**`, `kit/roles/*review*`, `kit/untrusted*`, `fixtures/**`). The builder drafts these in M0; you review and commit them.
6. **Ruleset on `main`** (Settings > Rules > Rulesets, target `main`): enforcement Active; **bypass list empty**; require pull request; require review from code owners, 1 approval; required status checks `test`, `coverage`, `secrets`, `smoke` with the source set to **GitHub Actions**, plus `kaisen/security`; block force pushes; restrict deletions. Also Settings > Actions: workflow permissions read-only by default; require approval for outside collaborators' workflow runs. After setup, run the negative tests in section 5.9 (the builder supplies a script).
7. **Secrets to set on the `kaisen` PicoPaaS app** (via `picopaas apply` secrets or the UI), exactly these names: `ANTHROPIC_API_KEY` (dedicated key, console limit $16/day), `GITHUB_TOKEN` (bot PAT above), `PICOPAAS_API_KEY` (dedicated user's full key), `KAISEN_ADMIN_PASSWORD` (20+ chars), `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Non-secret env: `KAISEN_BRIEF_REPO`, `KAISEN_MODEL` (the pinned model ID the builder gives you), `KAISEN_ALLOWED_GITHUB_IDS`. No GitHub Actions secrets are needed.
8. **Kaisen image, once:** push `~/source/kaisen` to its own GitHub repo (the builder supplies `release.yml`); after the first workflow run, GitHub > Packages > `kaisen` > Package settings > Change visibility > Public. Then run the first `picopaas apply`. Redeploys of Kaisen are yours.
9. **GHCR package flips:** each of `kaisen-work-checklist`, `kaisen-work-invaders` (and the throwaway M0 package) > Change visibility > Public when the inbox asks (three/four flips total).
10. **PicoPaaS admin overrides/approvals:** only the `app_limit` raise to 5 for the dedicated user and approving its signup. No platform config change, no new paid resource, no request in `docs/requests/` is required. Enable backups for `kaisen` if the platform default is off.
11. Two short briefs (checklist, invaders) and, later, the third goal. Approvals: every production promotion, every `brief.yaml` change, every `.github/` and protected-path PR (all as GitHub code-owner reviews by you), and cap raises.
12. Anthropic console: confirm the spend limit can be set at $16/day (or monthly $100) on the dedicated key and any org-level usage policy allows an agent loop (**External**).

## 17. Decisions Chas must make

1. **Repo visibility: public or private.** Recommend public: Actions minutes are free (**External**: private-repo free minutes are limited and the 6-round CI design can burn them in days), and exposure is covered by the ID allowlist, no fork-PR workflow runs and no secrets in CI. Choose private only if you will not publish briefs and generated code; then cost and cap Actions minutes first.
2. **Second identity: machine user in a free org (recommended) vs a GitHub App.** The machine user is a 20-minute setup and fits PAT code already specified; an App gives Check Runs and per-install tokens but needs JWT code and a private key as a secret. Recommend machine user for v0, App when Kaisen goes multi-tenant.
3. **Spend ceilings.** Recommend accepting: run $2, day $8, console $16, checklist $30, invaders $40, whole v0 $100. Alternative is lower caps with a smaller scope; do not raise them before M0 data exists.
4. **Prod promotion approval: every promotion (recommended in v0) or only the first per project.** Every promotion costs one click and keeps a human between merged code and the public URL.
5. **Dedicated PicoPaaS user (recommended) vs your admin account.** Dedicated keeps the full-key blast radius to Kaisen's four apps; admin would allow limit above 5 but exposes all your apps to a stolen key. Recommend dedicated, with 4 of 5 slots used.
