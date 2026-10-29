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
`DATABASE_CA_CERT` (PEM text, optional), `WORKER_ENABLED=0` to turn the background worker off.

## Test
```bash
python -m pytest -q
```
Tests use SQLite in memory, the real setup+login flow, and fakes for the model and GitHub.

## Deploy (what Chas does)
1. `picopaas apply` with `image: ghcr.io/<owner>/kaisen:<sha>`, port 8080, `healthCheck /healthz`, `resources: {db: true, db_engine: pg}`, secret `SECRET_KEY` (see `picopaas.yaml`).
2. Read the setup code from the app logs, open `/setup`, set the password.
3. Settings: paste Anthropic API key, pick model, set caps, optionally a GitHub repo + fine-grained token. Use the **Test** buttons.
4. New project: name + brief. Press Build. Watch the run page; answer questions in the inbox; download or push.
