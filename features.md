# Features

Every feature has an entry here (see `.agents/skills/features-md/SKILL.md`). Status: `planned` → `built` → `verified`.
Requirement IDs: R1–R15 are the original requirements, A–K the approved changes, Q1–Q6 the answers (see `docs/PLAN.md`).

## Core (lead)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Plan | built | docs/PLAN.md | read it | A–K |
| Config + DEMO/LIVE modes | verified | app/config.py | `pytest tests/test_config.py`; run the DEMO Compose stack | R13 |
| SQLite state machine + delivery dedupe store | verified | app/db.py, app/models.py | `pytest tests/test_db.py`; `pytest tests/test_e2e_demo.py` | R1, R7 |
| ACU ledger (caps 5/15, ceiling 120 across all issues) | verified | app/budget.py | `pytest tests/test_budget.py`; `pytest tests/test_e2e_demo.py` | R6, B |
| Metrics JSON | verified | app/metrics.py, app/main.py | `curl localhost:8000/metrics.json`; `pytest tests/test_e2e_demo.py` | R7, K |
| Playbooks as code (text) | verified | playbooks/*.md | read them; start the DEMO Compose stack | R8 |
| Schema drift check (local copy vs playbook schema) | verified | app/schema_check.py, app/pipeline.py | `pytest tests/test_main.py`; start the DEMO Compose stack | E |
| features.md rule, skill, PR template checkbox | built | features.md, .agents/skills/features-md/SKILL.md, .github/pull_request_template.md | read them | R10, F |
| DEMO seed data (real issues #1–#5) + scripted outcomes | verified | app/demo/seed_issues.json, app/demo/scenarios.json | `pytest tests/test_e2e_demo.py` | G |

## GitHub side (child A)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Webhook intake (HMAC, redelivery dedupe, fork-only) | verified | app/webhook.py | `pytest tests/test_webhook.py`; `pytest tests/test_e2e_demo.py` | R1, Q6 |
| Periodic sweep + local POST /sweep | verified | app/sweep.py | `curl -X POST localhost:8000/sweep`; `pytest tests/test_e2e_demo.py` | R1, Q4 |
| GitHub client + seeded fake | verified | app/github_client.py, app/fake_github.py | `pytest tests/test_github_client.py`; `pytest tests/test_e2e_demo.py` | R13, G |
| Issue actions (auto-create labels, comment, label, close) | verified | app/issue_actions.py | `pytest tests/test_issue_actions.py`; `pytest tests/test_e2e_demo.py` | R3, R7, Q2 |
| Signed webhook simulate script | verified | scripts/simulate_webhook.py | `python scripts/simulate_webhook.py --issue 1`; `pytest tests/test_e2e_demo.py` | R15 |

## Devin side (child B)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Devin v3 client + scripted fake | verified | app/devin_client.py, app/fake_devin.py | `pytest tests/test_devin_client.py`; `pytest tests/test_e2e_demo.py` | R5, R13 |
| Playbook resolution by title + overrides + schema from GET playbook | verified | app/playbooks.py | `pytest tests/test_playbooks.py`; `pytest tests/test_e2e_demo.py` | R5, E |
| Triage with read-only PR rejection | verified | app/triage.py | `pytest tests/test_triage.py`; `pytest tests/test_e2e_demo.py` | R2 |
| Prompt fencing + reachability-evidence-standards skill | built | app/prompts.py | `pytest tests/test_prompts.py` | R11, F |
| Router (per issue) | verified | app/router.py | `pytest tests/test_router.py`; `pytest tests/test_e2e_demo.py` | R3, C |
| Fix session, "PR opened", never archived | verified | app/fix.py | `pytest tests/test_fix.py`; `pytest tests/test_e2e_demo.py` | R4, D |
| Pipeline worker | verified | app/pipeline.py | `pytest tests/test_pipeline.py`; `pytest tests/test_e2e_demo.py` | R2–R4 |
| Stuck-session escalation (SHOULD) | built | app/escalation.py | `pytest tests/test_escalation.py` | SHOULD |
| Optional devin_mode per stage (SHOULD) | verified | app/config.py, app/triage.py, app/fix.py | `pytest tests/test_devin_mode.py`; `pytest tests/test_e2e_demo.py` | K |
| Playbook bootstrap script (SHOULD) | built | scripts/bootstrap_playbooks.py | `pytest tests/test_bootstrap.py` | R8 |

## Ops side (child C)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Board (incl. mode + acus_consumed per session) | verified | app/dashboard.py, app/templates/ | open http://127.0.0.1:8000/; `pytest tests/test_e2e_demo.py` | R7, K |
| Preflight (set/missing/invalid, schema drift) | verified | app/preflight.py | `python -m app.preflight`; run the DEMO Compose stack | R13, E |
| Dockerfile (ECR base, non-root, healthcheck) | verified | Dockerfile | `docker compose up` | R12 |
| Compose (127.0.0.1:8000) + .dockerignore | verified | docker-compose.yml, .dockerignore | `docker compose config`; `docker compose up` | R11, R12, Q5 |
| CI (ruff, pytest, docker build, gitleaks) | built | .github/workflows/ci.yml | GitHub Actions | R11 |

## Integration (lead)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| E2E DEMO run (startup, webhooks, budget, routing, modes) | verified | tests/test_e2e_demo.py | `pytest tests/test_e2e_demo.py` | R15 |
| README (diagram, run, simulate, PAT perms, decisions, limits, Automation appendix, next steps) | planned | README.md | read it | R15, I, Q1 |

## BONUS (only on explicit go)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Upstream sync + FORK_CHANGELOG.md via PR | planned | — | — | R9, J |
