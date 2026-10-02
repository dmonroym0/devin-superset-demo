# Features

Every feature has an entry here (see `.agents/skills/features-md/SKILL.md`). Status: `planned` → `built` → `verified`.
Requirement IDs: R1–R15 are the original requirements, A–K the approved changes, Q1–Q6 the answers (see `docs/PLAN.md`).

## Core (lead)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Plan | built | docs/PLAN.md | read it | A–K |
| Config + DEMO/LIVE modes | built | app/config.py | `pytest tests/test_config.py` | R13 |
| SQLite state machine + delivery dedupe store | built | app/db.py, app/models.py | `pytest tests/test_db.py` | R1, R7 |
| ACU ledger (caps 5/15, ceiling 120 across all issues) | built | app/budget.py | `pytest tests/test_budget.py` | R6, B |
| Metrics JSON | built | app/metrics.py, app/main.py | `curl localhost:8000/metrics.json` | R7, K |
| Playbooks as code (text) | built | playbooks/*.md | read them | R8 |
| Schema drift check (local copy vs playbook schema) | built | app/schema_check.py | `pytest tests/test_schema_check.py` | E |
| features.md rule, skill, PR template checkbox | built | features.md, .agents/skills/features-md/SKILL.md, .github/pull_request_template.md | read them | R10, F |
| DEMO seed data (real issues #1–#5) + scripted outcomes | built | app/demo/seed_issues.json, app/demo/scenarios.json | read them | G |

## GitHub side (child A)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Webhook intake (HMAC, redelivery dedupe, fork-only) | built | app/webhook.py | `pytest tests/test_webhook.py` | R1, Q6 |
| Periodic sweep + local POST /sweep | built | app/sweep.py | `curl -X POST localhost:8000/sweep` | R1, Q4 |
| GitHub client + seeded fake | built | app/github_client.py, app/fake_github.py | `pytest tests/test_github_client.py` | R13, G |
| Issue actions (auto-create labels, comment, label, close) | built | app/issue_actions.py | `pytest tests/test_issue_actions.py` | R3, R7, Q2 |
| Signed webhook simulate script | built | scripts/simulate_webhook.py | `python scripts/simulate_webhook.py --issue 1` | R15 |

## Devin side (child B)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Devin v3 client + scripted fake | planned | app/devin_client.py, app/fake_devin.py | `pytest tests/test_devin_client.py` | R5, R13 |
| Playbook resolution by title + overrides + schema from GET playbook | planned | app/playbooks.py | `pytest tests/test_playbooks.py` | R5, E |
| Triage with read-only PR rejection | planned | app/triage.py | `pytest tests/test_triage.py` | R2 |
| Prompt fencing + reachability-evidence-standards skill | planned | app/prompts.py | `pytest tests/test_prompts.py` | R11, F |
| Router (per issue) | planned | app/router.py | `pytest tests/test_router.py` | R3, C |
| Fix session, "PR opened", never archived | planned | app/fix.py | `pytest tests/test_fix.py` | R4, D |
| Pipeline worker | planned | app/pipeline.py | `pytest tests/test_pipeline.py` | R2–R4 |
| Stuck-session escalation (SHOULD) | planned | app/escalation.py | `pytest tests/test_escalation.py` | SHOULD |
| Optional devin_mode per stage (SHOULD) | planned | app/config.py, app/triage.py, app/fix.py | `pytest tests/test_devin_mode.py` | K |
| Playbook bootstrap script (SHOULD) | planned | scripts/bootstrap_playbooks.py | `pytest tests/test_bootstrap.py` | R8 |

## Ops side (child C)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Board (incl. mode + acus_consumed per session) | built | app/dashboard.py, app/templates/ | open http://127.0.0.1:8000/ | R7, K |
| Preflight (set/missing/invalid, schema drift) | built | app/preflight.py | `python -m app.preflight` | R13, E |
| Dockerfile (ECR base, non-root, healthcheck) | built | Dockerfile | `docker compose up` | R12 |
| Compose (127.0.0.1:8000) + .dockerignore | built | docker-compose.yml, .dockerignore | `docker compose config` | R11, R12, Q5 |
| CI (ruff, pytest, docker build, gitleaks) | built | .github/workflows/ci.yml | GitHub Actions | R11 |

## Integration (lead)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| E2E DEMO run | planned | tests/e2e/ | `pytest tests/e2e` | R15 |
| README (diagram, run, simulate, PAT perms, decisions, limits, Automation appendix, next steps) | planned | README.md | read it | R15, I, Q1 |

## BONUS (only on explicit go)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Upstream sync + FORK_CHANGELOG.md via PR | planned | — | — | R9, J |
