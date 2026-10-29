# Kaisen builder v0

A small web service that turns a project brief into a set of files using the Anthropic API, keeps the files in its
database, and lets you download them as a tarball or push them to GitHub. Contract: `../docs/BUILDER_V0.md`.

## Run locally
```bash
cd service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p data
ENVIRONMENT=dev alembic upgrade head
ENVIRONMENT=dev uvicorn kaisen.main:app_factory --factory --reload --port 8080
```
The one-time setup code is printed to stdout at startup. Open http://localhost:8080/setup.
Config: `SECRET_KEY` (required unless `ENVIRONMENT` is dev/test), `DATABASE_URL` (default `sqlite:///./data/kaisen.db`),
`DATABASE_CA_CERT` (PEM text, optional), `WORKER_ENABLED=0` to turn the background worker off,
`KAISEN_RUNTIME_IMAGE` (runtime image for previews; baked into the Kaisen image by release.yml via the `RUNTIME_IMAGE` build arg, the env var overrides it).

## Test
```bash
python -m pytest -q
```
Tests use SQLite in memory, the real setup+login flow, and fakes for the model and GitHub.

## Deploy (what Chas does)
1. `picopaas apply` with `image: ghcr.io/<owner>/kaisen:<sha>`, port 8080, `healthCheck /healthz`, `resources: {db: true, db_engine: mysql}`, env `DATABASE_URL: ${{ db.url }}` and `DATABASE_CA_CERT: ${{ db.ca_cert }}`, secret `SECRET_KEY` (see `picopaas.yaml`).
2. Read the setup code from the app logs, open `/setup`, set the password.
3. Settings: paste Anthropic API key, pick model, set caps. Under **Output** choose *Download only* or *Download and push to GitHub*; for pushes give the repository URL and a fine-grained token (the page has step-by-step instructions). Use the **Test** buttons.
4. New project: name + brief. Press Build. Watch the run page; answer questions in the inbox; download or push.

## Live previews
Kaisen deploys each project on PicoPaaS as app `kaisen-<project>` using the generic runtime image built from `../runtime/`
(no image build per project). Settings > PicoPaaS: URL (default `https://platform.picopaas.xyz`), a full-access API key
(Account > API keys), optional PicoPaaS project slug, and Kaisen's public URL (captured from the admin's browser, editable;
https is required outside dev). The runtime downloads `GET /bundles/<token>.tar.gz` (unauthenticated, the token is only stored
hashed, the latest 3 per project stay valid), verifies the sha256, installs deps and runs the project per its `kaisen.json`.
The builder's `deploy_preview` tool deploys and reports status, HTTP code and logs; a finished build auto-deploys; the project
page has the preview link, **Deploy now** and **Tell Kaisen what to change** (starts a fresh run from the files, last summary
and your feedback). New PicoPaaS accounts may run 1 app (Kaisen itself): use a project with free slots or ask your admin.
`runtime/` is not built by the tests; `tests/test_runtime_boot.py` covers the pure parts of `boot.py`.

## Docker builds locally
Start Docker (`open -a Docker` on macOS), then from the repo root:
```bash
docker build -t kaisen-runtime:local runtime/                                   # the preview runtime (~460 MB)
docker build --build-arg RUNTIME_IMAGE=kaisen-runtime:local -t kaisen:local service/   # Kaisen itself (~335 MB)
runtime/smoke-local.sh ~/Downloads/<project>.tar.gz   # run a downloaded project like PicoPaaS would; KEEP=1 to poke at it
```
Apple silicon builds arm64 by default; `PLATFORM=linux/amd64 runtime/smoke-local.sh ...` matches the cluster (slower).

