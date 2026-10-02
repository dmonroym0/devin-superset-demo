# Features

Every feature has an entry here (see `.agents/skills/features-md/SKILL.md`). Status: `planned` → `built` → `verified`.
Requirement IDs: R1–R15 are the original requirements, A–K the approved changes, Q1–Q6 the answers (see `docs/PLAN.md`).

## Core (lead)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Plan | built | docs/PLAN.md | read it | A–K |
| Config + DEMO/LIVE database isolation | verified | app/config.py, app/db.py, app/main.py | `python -m pytest tests/test_main.py::test_demo_and_live_use_distinct_databases_in_same_data_dir tests/test_main.py::test_startup_rejects_database_claimed_by_another_mode tests/test_db.py::test_claim_mode_rejects_legacy_database_with_issue_data tests/test_db.py::test_claim_mode_stamps_empty_database_and_allows_same_mode_with_data` | R6, R13 |
| SQLite state machine + delivery dedupe store | verified | app/db.py, app/models.py | `pytest tests/test_db.py`; `pytest tests/test_e2e_demo.py` | R1, R7 |
| Atomic session settlement + issue transition | verified | app/db.py, app/fix.py, app/triage.py, app/escalation.py | `python -m pytest tests/test_fix.py::test_failed_atomic_pr_transition_keeps_fix_session_active_for_retry` | R7 |
| ACU ledger (caps 5/15, ceiling 120 across all issues) | verified | app/budget.py | `pytest tests/test_budget.py`; `pytest tests/test_e2e_demo.py` | R6, B |
| Metrics JSON | verified | app/metrics.py, app/main.py | `curl localhost:8000/metrics.json`; `pytest tests/test_e2e_demo.py` | R7, K |
| Playbooks as code (text) | verified | playbooks/*.md | read them; start the DEMO Compose stack | R8 |
| Schema drift check (local copy vs playbook schema) | verified | app/schema_check.py, app/pipeline.py | `pytest tests/test_main.py`; start the DEMO Compose stack | E |
| features.md rule, skill, PR template checkbox | built | features.md, .agents/skills/features-md/SKILL.md, .github/pull_request_template.md | read them | R10, F |
| DEMO seed data (real issues #1–#5) + scripted outcomes | verified | app/demo/seed_issues.json, app/demo/scenarios.json | `pytest tests/test_e2e_demo.py` | G |

## GitHub side (child A)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Webhook intake (HMAC, redelivery dedupe, fork-only) | verified | app/webhook.py, app/fake_github.py | `pytest tests/test_webhook.py`; `pytest tests/test_webhook.py::test_demo_webhook_keeps_existing_closed_issue_state`; `pytest tests/test_webhook.py::test_demo_webhook_updates_existing_title_and_body_preserving_state_and_labels`; `pytest tests/test_webhook.py::test_demo_webhook_adds_trigger_label_to_unknown_issue`; `pytest tests/test_e2e_demo.py` | R1, Q6 |
| Periodic sweep + local POST /sweep | verified | app/sweep.py | `curl -X POST localhost:8000/sweep`; `pytest tests/test_e2e_demo.py` | R1, Q4 |
| GitHub client + seeded fake | verified | app/github_client.py, app/fake_github.py | `pytest tests/test_github_client.py`; `pytest tests/test_e2e_demo.py` | R13, G |
| URL destination allowlist + Markdown escaping | verified | app/issue_actions.py, app/dashboard.py | `python -m pytest tests/test_issue_actions.py::test_triage_comment_omits_unsafe_urls tests/test_issue_actions.py::test_triage_comment_renders_allowlisted_fork_links tests/test_issue_actions.py::test_triage_comment_escapes_markdown_model_text tests/test_dashboard.py::test_dashboard_renders_database_rows_safely` | R3, F1 |
| Issue actions (auto-create labels, comment, label, close) | verified | app/issue_actions.py | `pytest tests/test_issue_actions.py`; `pytest tests/test_e2e_demo.py` | R3, R7, Q2 |
| Signed webhook simulate script | verified | scripts/simulate_webhook.py | `python scripts/simulate_webhook.py --issue 1`; `pytest tests/test_e2e_demo.py` | R15 |

## Devin side (child B)

| Name | Status | Files | How to see it | Requirement |
|---|---|---|---|---|
| Devin v3 client + scripted fake | verified | app/devin_client.py, app/fake_devin.py | `pytest tests/test_devin_client.py`; `pytest tests/test_e2e_demo.py` | R5, R13 |
| Playbook resolution by title + overrides + schema from GET playbook | verified | app/playbooks.py | `pytest tests/test_playbooks.py`; `pytest tests/test_e2e_demo.py` | R5, E |
| Triage with read-only PR rejection and cancellation re-check | verified | app/triage.py, app/fix.py, app/models.py, app/db.py, app/metrics.py, app/templates/board.html, app/webhook.py | `python -m pytest tests/test_triage.py::test_closed_queued_triage_issue_is_cancelled_before_session tests/test_triage.py::test_queued_triage_issue_without_trigger_label_is_cancelled tests/test_fix.py::test_fix_without_trigger_label_is_cancelled_before_session tests/test_webhook.py::test_relabelled_cancelled_issue_is_revived_by_webhook` | R2, R4 |
| Prompt fencing + reachability-evidence-standards skill | built | app/prompts.py | `pytest tests/test_prompts.py` | R11, F |
| Router (per issue, issue-CVE allowlist) | verified | app/router.py, app/models.py, app/issue_actions.py, app/prompts.py | `python -m pytest tests/test_router.py::test_unlisted_reachable_cve_does_not_route_to_fix tests/test_router.py::test_only_issue_listed_cves_can_qualify tests/test_router.py::test_issue_without_cve_ids_requires_human_review tests/test_prompts.py::test_fix_prompt_excludes_unlisted_cves_from_triage_output` | R1, R3, C |
| Fix session, "PR opened", never archived | verified | app/fix.py | `pytest tests/test_fix.py`; `pytest tests/test_e2e_demo.py` | R4, D |
| Ambiguous session-create accounting + restart recovery | verified | app/triage.py, app/fix.py, app/budget.py, app/db.py, app/pipeline.py | `python -m pytest tests/test_triage.py::test_ambiguous_triage_create_keeps_reservation_and_does_not_retry tests/test_fix.py::test_ambiguous_fix_create_keeps_reservation_and_does_not_retry tests/test_pipeline.py::test_restart_preserves_ambiguous_unattached_reservation` | R5 |
| Fix-budget retry preserves TRIAGED state | verified | app/fix.py, README.md, docs/PLAN.md | `python -m pytest tests/test_fix.py::test_fix_budget_refusal_keeps_triaged` | F3 |
| Pipeline worker | verified | app/pipeline.py | `pytest tests/test_pipeline.py`; `pytest tests/test_e2e_demo.py` | R2–R4 |
| Stuck-session escalation, including hard timeout during poll failures | verified | app/escalation.py, app/pipeline.py, app/triage.py, app/fix.py | `python -m pytest tests/test_pipeline.py::test_triage_hard_timeout_escalates_on_transient_poll_error tests/test_pipeline.py::test_fix_hard_timeout_escalates_on_transient_poll_error` | R8, SHOULD |
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
| Upstream sync + FORK_CHANGELOG.md via PR | verified | `app/config.py`, `app/models.py`, `app/interfaces.py`, `app/github_client.py`, `app/fake_github.py`, `app/demo/upstream.json`, `app/changelog.py`, `app/db.py`, `app/upstream_sync.py`, `app/request_security.py`, `app/sweep.py`, `app/main.py`, `app/metrics.py`, `app/dashboard.py`, `app/templates/board.html`, `README.md`, `.env.example`, `tests/test_changelog.py`, `tests/test_github_client.py`, `tests/test_upstream_sync.py`, `tests/test_e2e_demo.py` | Run `pytest -q tests/test_changelog.py tests/test_github_client.py tests/test_upstream_sync.py tests/test_e2e_demo.py`. Key coverage: `test_render_section_groups_conventional_types_in_order`, `test_get_file_decodes_content_and_returns_none_for_missing_file`, `test_create_changelog_pr_updates_existing_file_with_contents_sha`, `test_upstream_merge_creates_changelog_pr_and_advances_sha`, `test_conflict_creates_one_issue_while_existing_issue_is_open`, `test_sync_endpoint_rejects_forwarded_for`, `test_sync_endpoint_returns_busy_when_background_sync_holds_lock`, `test_merge_upstream_error_returns_502`, `test_conflict_reuses_open_matching_issue_from_github`, `test_demo_upstream_scenario_has_eight_representative_commits`, and `test_demo_end_to_end` verifies `/metrics.json`. | R9, J |
