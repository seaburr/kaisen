# {{PROJECT}}: Claude Code context

{{One-line description, caps and hosting summary.}}

**`BRIEF.md` is the non-negotiable contract. Read it before doing anything.** Then read:
- `docs/LEDGER.md`: what is done and next (start at "Now / Next")
- `docs/ARCHITECTURE.md`: design decisions and cost model
- `docs/QUESTIONS.md`: {{HUMAN}}'s file. Read it first every session; carry answers into docs/requests, move answered
  items to Resolved. Every new question for {{HUMAN}} goes here.
- `docs/requests/`: infra requests (what / why / monthly cost). Never provision without an approved request.
- `process/WAVE.md`: roles, ledger format, rules, feedback-wave workflow

## Hard rules
- Budget: {{target}}. Any new paid resource needs a request.
- Tokens are scarce. Small chunks; ledger line at START and END of each chunk; prefer `sonnet` subagents for implementation.
- Only the `scm-security` agent commits and pushes. Others leave changes in the working tree and record them in the ledger.
- Secret scanning: every clone runs `git config core.hooksPath .githooks` (gitleaks on staged changes). Never add
  `.gitleaksignore`, inline `gitleaks:allow`, or allowlists; CI fails if `.gitleaksignore` exists. Test fixtures use
  values that do not look like secrets (e.g. `not-the-password`): a finding means change the value.
- Never commit secrets: no `.env`, kubeconfig, `*.tfvars`, `*.tfstate`, or tokens.

## Layout
{{tree}}

## Commands
{{setup / run / test / migrate / frontend}}
