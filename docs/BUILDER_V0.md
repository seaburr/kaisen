# Kaisen builder v0: in-app setup (supersedes parts of SPEC.md v2)

Status: 2026-10-29, written from Chas's answer in PicoPaaS `docs/QUESTIONS.md`:
> Kaisen is configured by the customer in-app: a web form once deployed, then it goes to work. Focus on getting the
> builder going; wire up what it needs to test itself later. The user plugs in the repo Kaisen writes back to, OR
> just downloads the code as a tarball.

Where this file and `SPEC.md` disagree, this file wins for v0. Everything in SPEC.md that is not mentioned here
(CI gates, ruleset, planner/reviewer roles, preview/promote, GHCR flips, self-enhancement) is **deferred** to M1+.

## What changes vs SPEC v2

| SPEC v2 | Builder v0 |
|---|---|
| Five PicoPaaS secrets (ANTHROPIC_API_KEY, GITHUB_TOKEN, PICOPAAS_API_KEY, KAISEN_ADMIN_PASSWORD, SECRET_KEY) | **One** deploy-time secret: `SECRET_KEY`. Everything else is entered in the Settings form and stored Fernet-encrypted in the DB (key derived from SECRET_KEY). |
| Admin password in env | First-run claim: on boot with no admin, Kaisen logs a one-time setup code to stdout (readable in PicoPaaS logs). `/setup` asks for that code + a new password (12+ chars). Code is single-use, expires when claimed. |
| One fixed work repo, GitHub is the work state | Workspace files live in the DB (`project_files`). Output per project: **Download .tar.gz** (always available) and/or **Push to GitHub** (repo + token from Settings or per project). |
| Work queue from GitHub issues/labels | Work queue is in-app: create a project with a brief; the builder runs it. No GitHub input surface at all in v0 (so no principal allowlist needed yet). |
| CI as the only test runner | No test execution in v0. The builder writes tests alongside code; running them is the next milestone. |

## Deploy (what Chas does)
1. `picopaas apply` with `image: ghcr.io/<owner>/kaisen:<sha>`, port 8080, `healthCheck /healthz`, `resources: {db: true, db_engine: pg}`, secret `SECRET_KEY`.
2. Read the setup code from the app logs, open `/setup`, set the password.
3. Settings: paste Anthropic API key, pick model, set caps, optionally a GitHub repo + fine-grained token. Use the **Test** buttons.
4. New project: name + brief. Press Build. Watch the run page; answer questions in the inbox; download or push.

## Settings form (all editable later; secrets write-only, UI shows only "set, ends in …abcd")
- Anthropic API key (secret). Test = `models.retrieve(model)`.
- Model: default `claude-opus-5-5`; choices from the price table in `kaisen/config.py` (Opus 5.5, Sonnet 5.5, Haiku 5.5). Unknown model = refuse to run.
- Effort: low/medium/high (default medium).
- Caps (USD): per run (default 2), per day (default 8), per project (default 30). Pause-all switch.
- GitHub (optional): default repo `owner/name`, default branch, token (secret, fine-grained: Contents RW + Pull requests RW on that repo). Test = GET repo and check `permissions.push`.
- PicoPaaS API URL + key (optional, unused until deploy wiring; stored now so the form is complete).

## Builder
- Run = one worker-thread job per project build. Resumable steps persisted in DB; `ask_human` ends a step, the inbox answer starts the next.
- Direct Anthropic Messages API manual tool-use loop (no Agent SDK). Adaptive thinking (default), `output_config.effort`, prompt caching on system+tools, stream with `get_final_message()`. No forced `tool_choice` (400 on Opus 5.5). Check `stop_reason` (`refusal`, `max_tokens`) before running tools.
- Tools: `list_files`, `read_file`, `write_file`, `delete_file`, `ask_human`, `finish`. Workspace = this project's DB files only.
- Path rules: relative, normalised, no `..`, no absolute, no `.git/`, no `.github/` (v0), max 200 KB per file, max 300 files and 5 MB per project (DB cap is 50 MB).
- Limits per step: 40 turns, 15 min active wall clock, repeat detector (3 identical calls). Caps checked before every model call; hit = stop, inbox item.
- Metering: every response's `usage` (input, cache write, cache read, output) → `events` with USD from the price table and the model id. Metering insert failure stops the run (fail closed).
- Prompts: system prompt from `kaisen/prompts/builder.md` (stable, cached). The brief is untrusted user data in a `<brief>` wrapper. Generated-app rules baked in: Dockerfile with numeric `USER 10001`, port 1024-65535, image < 512 MB, README, tests next to code, no secrets.

## Output
- **Tarball:** `GET /projects/{id}/download` streams `<slug>.tar.gz` built from DB files (in-memory, safe names only).
- **GitHub push:** button on the project page. Git Data API: blobs → tree → commit → update/create branch `kaisen/<slug>` (or the configured branch), optionally open a PR. Empty repo: seed with the Contents API first. Token never logged; 401/403/404 shown as plain errors.

## Platform constraints carried over from SPEC v2 (still apply)
- `/healthz` static 200, no DB. Agent loop in a worker thread.
- DB lease with fencing token (rolling update runs two pods); standby pod does not run jobs.
- Postgres: pool_size 2, max_overflow 0, pre_ping; `postgres://` → `postgresql+psycopg://`; `sslmode=verify-full` with the CA cert when `DATABASE_CA_CERT` is set. SQLite locally and in tests. Alembic migrations.
- Inbox/UI security: HttpOnly Secure SameSite=Strict session cookie, CSRF on every POST, login lockout (5 failures / 15 min), escaped output, strict CSP, no inline script, secret redaction in transcripts/logs.

## Next (not v0)
Run tests (GitHub Actions on the pushed branch, CI summary fed back as a step), security-reviewer role, deploy to PicoPaaS preview, planner role, GHCR flips.
