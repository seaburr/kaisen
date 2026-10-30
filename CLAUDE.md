# Kaisen repo: Claude Code context

**Read first:** `../picopaas/docs/KAISEN_OPS.md` (seaburr/picopaas). It is the operations guide for both repositories.

What this repo is now (2026-10-30):
- **The agent kit** (`agents/`, `process/`, `templates/`, `LESSONS.md`, `README.md`): the reusable agent-factory pattern
  extracted from PicoPaaS. Kit changes belong here.
- **The retired standalone Kaisen service** (`service/`, `runtime/`, `docs/SPEC*.md`, `docs/BUILDER_V0.md`): Kaisen first
  ran as its own tenant app; it now lives **inside PicoPaaS** (`picopaas/app/kaisen/`, Preview). Do not add features to
  `service/`. Product work on Kaisen happens in seaburr/picopaas.

Rules carried over from PicoPaaS: only the `scm-security` agent commits and pushes; gitleaks on every commit, never an
allowlist (change fixture values instead); never commit secrets. The standalone test instance and its previews run on
the PicoPaaS test user account; delete them only after asking Chas.
