# Kaisen: reusable agent-factory kit

Extracted from the PicoPaaS project. It packages the process, agent definitions and doc templates that let a
Claude Code session (the "manager") run a small team of subagents on a software project with a written contract,
a ledger, human approval gates and a single committer.

This is a kit of files to copy. Repository: github.com/seaburr/kaisen.

> **Status (2026-10-30):** Kaisen the *product* (build an app from a brief, deploy it to PicoPaaS) now lives inside
> PicoPaaS as a Preview feature (seaburr/picopaas, `app/kaisen/`). The `service/` and `runtime/` folders here are the
> retired standalone version, kept as history. Operations guide for both repos: `picopaas/docs/KAISEN_OPS.md`.
> Claude Code context for this repo: `CLAUDE.md`.

## Tree
```
README.md  LESSONS.md
agents/            12 generic agent definitions (copy to <project>/.claude/agents/)
process/WAVE.md    roles registry, ledger format, rules, feedback-wave workflow
templates/
  BRIEF.md AGENTS.md (single source) CLAUDE.md (pointer) TASKS.md (spec -> tasks)
  docs/{QUESTIONS.md,LEDGER.md,SECRETS_POLICY.md,requests/0001-example.md}
  .githooks/pre-commit        gitleaks on staged changes
```


## Start a new project
1. `mkdir myproj && cd myproj && git init`
2. Copy `templates/BRIEF.md`, `templates/AGENTS.md`, `templates/CLAUDE.md`, `templates/TASKS.md`, `templates/docs/`, `templates/.githooks/` into it; copy
   `process/WAVE.md` to `process/WAVE.md` (or `docs/PROCESS.md`; fix links) and `agents/*.md` to `.claude/agents/`.
3. Replace `{{placeholders}}` (`grep -rn '{{' .`): PROJECT, HUMAN, project_slug, domain, config_file, repo_url, kubeconfig_path.
4. In each agent file, edit the "Project-specific" section for your stack; drop roles you do not need and update the registry in WAVE.md.
5. `git config core.hooksPath .githooks`; install gitleaks. Add a CI step failing if `.gitleaksignore` exists.
6. Write the brief, then start the manager session: read BRIEF.md, CLAUDE.md, QUESTIONS.md, LEDGER.md.

## What stays as-is (works today)
- BRIEF.md contract plus Amendments log; request protocol (what / why / monthly cost); QUESTIONS.md as the single human inbox.
- Prose ledger with START/DONE lines and the role registry.
- The single-committer loop: `scm-security` is the only agent that commits and pushes, runs gitleaks, watches CI and deploys.
  Dependabot-steward may merge PRs and push PR branches only.
- No-allowlist secret rule and the test house style (see LESSONS.md).
- The manager-driven feedback wave in process/WAVE.md (run by hand).

## Planned (do NOT exist yet)
Per the factory assessment, these are goals, not features of this kit:
- Smaller parameterized roster (planner, builder, verifier, security-reviewer, docs) distributed as a plugin.
- A `/wave` skill with fixed phases and enforced exit gates.
- Issues plus an append-only `events.jsonl` as state, with "Now / Next" generated instead of hand-written.
- PR-per-issue with security review on every change (not per wave).
- A staging environment instead of pushing straight to production.
- A headless orchestrator so the human is not the dispatcher.
- Repeatable checks (Playwright smoke, coverage floor) moved into CI; Dependabot auto-merge for green patch/minor.
