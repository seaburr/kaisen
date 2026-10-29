# Secret scanning: the no-allowlist rule

- Install gitleaks (`brew install gitleaks`) and run once per clone: `git config core.hooksPath .githooks`.
  The `.githooks/pre-commit` hook runs `gitleaks protect --staged` and refuses to commit if gitleaks is missing.
- A finding is fixed by changing the value so it is not secret-shaped, never by suppressing it.
- Never add `.gitleaksignore`, inline `gitleaks:allow` comments, or a config allowlist. CI should fail if
  `.gitleaksignore` exists (add a step: `test ! -f .gitleaksignore`).
- Test fixtures use obviously fake values (e.g. `not-the-password`).
- Real secrets live in repo/CI secrets or the platform secret store; local ones in `~/.<project>.env` outside the repo.
