# STATE: what exists on 2026-10-02 (submission audit)

Snapshot taken from `main @ f662d83` (dmonroym0/devin-superset-demo) and the fork dmonroym0/superset, read-only. Every row cites its evidence. GitHub states come from `gh issue view`, `gh pr list --json`, `gh api .../timeline` and the PR check summaries, captured on 2026-10-02 around 08:00–08:20 UTC. The fork sections (issues, PRs, upstream sync) were refreshed at ~17:30 UTC after the LIVE run.

## 1. The fork: dmonroym0/superset

### Issues

Refreshed 2026-10-02 ~17:30 UTC from `gh api repos/dmonroym0/superset/issues/N` (+ `/events`, `/comments`). This supersedes the 08:20 snapshot, which predates the LIVE run.

| # | Title | Package (ecosystem) | State | Labels | Open PR? |
|---|---|---|---|---|---|
| 1 | [Security] Upgrade jaraco-context 6.0.1 -> 6.1.0 | jaraco-context (pip, dev) | open | security | **yes: #6** (open, ready for review) |
| 2 | [Security] Upgrade python-multipart 0.0.29 -> 0.0.31 | python-multipart (pip) | **closed, not_planned** (08:27:11, by the service) | security, devin:fixplease, devin:low-priority | no |
| 3 | [Security] Upgrade urllib3 2.7.0 -> 2.8.0 | urllib3 (pip) | open | security, devin:fixplease, devin:pr-opened | **yes: #9** (draft, opened by the service's fix session) |
| 4 | [Security] Upgrade pytest 7.4.4 -> 9.0.3 | pytest (pip, dev) | open | security | **yes: #7 and #8** (open drafts, stacked) |
| 5 | [Security] Upgrade fast-uri 3.1.7 -> 3.1.8 | fast-uri (npm, dev) | open | security | no |

LIVE evidence. The service acts as the PAT owner, so its GitHub actor is `dmonroym0`; Devin's own triage comments are by `devin-ai-integration[bot]`.

- **#2:** `devin:fixplease` added 08:22:43 → `devin:in-progress` 08:22:48 → Devin triage comment 08:25:58 → service comment "### devin-superset-demo triage / Route: Not reachable: closing as low priority" 08:27:07 → `devin:low-priority` 08:27:08 → closed `not_planned` 08:27:11. This is the service's not-reachable path (`app/issue_actions.py`), seconds apart and in that order.
- **#3:** `devin:fixplease` 08:28:51 → `devin:in-progress` 08:29:17 → Devin triage comment 08:33:22 → service route comment "Fix: opening a Devin fix session" 08:34:26 → PR #9 opened 08:39:44 by `devin-ai-integration[bot]` (session https://app.devin.ai/sessions/56160d44a4c843c3a49e949cfa7070a8) → service comment "<https://github.com/dmonroym0/superset/pull/9> Opened, not done" 08:40:01 → `devin:pr-opened` 08:40:02, `devin:in-progress` removed 08:40:03.
- **#1, #4, #5:** never labelled `devin:fixplease`. #1 and #4 were skipped on purpose: they already have PRs and there is no duplicate-PR guard (https://github.com/dmonroym0/devin-superset-demo/issues/10).

### Pull requests

Checks are per head commit and were not re-run after the upstream sync. "Mergeable" is GitHub's `mergeable` / `mergeable_state` read after the sync.

| # | Title | State | Head → base | Latest CI (head) | Failing jobs | Mergeable after sync | Linked issue | Devin session (from body) |
|---|---|---|---|---|---|---|---|---|
| 6 | chore(deps): bump jaraco-context from 6.0.1 to 6.1.0 (CVE-2026-23949) | **open** (not draft, not merged) | devin/1790836671-bump-jaraco-context → master | 50 passed, 0 failed | none | yes, `clean` (1 ahead / 45 behind) | Closes #1 | https://app.devin.ai/sessions/98aa8416df4946948c56758fe753b547 |
| 7 | chore(deps-dev): bump pytest from 7.4.4 to 8.4.2 | **open, draft** | devin/1790839809-pytest-8.4.2 → master | 49 passed, **2 failed** | `test-postgres (current)`, `test-postgres-required` (`TestSavedQueryApi::test_related_saved_query`, documented in the body as a Postgres-only row-order failure left for a maintainer) | yes, `unstable` (no conflict; `unstable` = the 2 failing checks) | Refs #4 | https://app.devin.ai/sessions/629a2b09418543aab6861d405bb20357 |
| 8 | chore(deps-dev): bump pytest from 8.4.2 to 9.0.3 and pytest-asyncio to 1.3.0 | **open, draft** (stacked on #7) | devin/1790840562-pytest-9.0.3 → devin/1790839809-pytest-8.4.2 | 44 passed, 0 failed | none | yes, `clean` (its base is #7's branch, which the sync didn't touch) | Refs #4 | https://app.devin.ai/sessions/629a2b09418543aab6861d405bb20357 |
| 9 | fix(deps): bump urllib3 from 2.7.0 to 2.8.0 (CVE-2026-97688) | **open, draft** | devin/1790930323-bump-urllib3 → master | 52 passed, 0 failed | none | yes, `clean` | issue #3 (LIVE fix session) | https://app.devin.ai/sessions/56160d44a4c843c3a49e949cfa7070a8 |
| 10 | docs(fork-changelog): upstream sync d2fb52a..0fdfd66 | **open** | devin/fork-changelog-0fdfd6660e0a → master | 35 passed, **1 failed** | `License Check`: `FORK_CHANGELOG.md` has no Apache license header (Superset's `scripts/check_license.sh`; https://github.com/dmonroym0/devin-superset-demo/issues/26) | yes, `unstable` (the failing check) | — (BONUS upstream sync, opened by the service as `dmonroym0`) | — |

**No fork PR is merged. No CVE is fixed on fork `master`.**

### Upstream sync and fork `master`

The LIVE upstream sync (15:31:13 UTC) moved fork `master` from d2fb52ac83 to 0fdfd6660e, the upstream commit "docs(databases): add ClickHouse Managed Postgres (#44870)". `compare d2fb52ac83...0fdfd6660e` is 45 ahead / 0 behind, and `compare apache/superset:master...dmonroym0:master` is 0 ahead. So `master` holds **only upstream commits: no merge commit and no fork-only commit**. The board's "merged upstream changes" is the label for a successful `merge-upstream` call (`app/i18n/en.json`), not a merge commit. The changelog went to PR #10 on its own branch, never to `master`.

Effect on open PRs: none conflict. #6 and #9 each touch only `requirements/*.txt` and are `clean`. #7 touches `requirements/development.txt` and two test fixtures, with no conflict; its `unstable` state is the pre-existing Postgres failure. #8 is based on #7's branch, which the sync didn't move. Their checks still reflect the pre-sync base.

### Claims checked on GitHub

| Claim (from the brief) | Finding | Evidence |
|---|---|---|
| PR #6 bumps jaraco-context for CVE-2026-23949 with the repo's compile script; tests match the baseline; Docker Hub 429 fallback disclosed | **True.** The PR body shows `RUNNING_IN_DOCKER=1 ./scripts/uv-pip-compile.sh --upgrade-package jaraco-context==6.1.0`, says Docker Hub rate-limited the image pull, and has a before/after table (baseline 20,345 passed / 45 skipped / 2 xfailed). | PR #6 body |
| #7/#8 split because pytest-asyncio 0.23.8 caps pytest below 9; #8 would fix CVE-2025-71176 once merged | **True.** PR #7: "pytest-asyncio 0.23.8 (`pytest>=7,<9`, which is what holds this step at 8.x)", "CVE-2025-71176 is not fixed by this PR". PR #8 bumps to 9.0.3 + pytest-asyncio 1.3.0. Both are open. | PR #7, PR #8 bodies |
| pytest 8 collection order exposed two integration fixtures leaking DB state; only the teardown changed | **True in substance.** PR #7, "Integration-test fixture fixes": (1) `_clean_security` in a migration test never restored the Security view menus; (2) `fixtures/users.py` never deleted the `dar` role. Commit de5894e "test: delete leaked 'dar' role in user-group fixtures". | PR #7 body |
| Postgres-only row-order failure documented and left for a maintainer | **True.** PR #7, "Not fixed: `queries/saved_queries/api_tests.py::TestSavedQueryApi::test_related_saved_query` (Postgres only)". It is still failing on #7's CI. | PR #7 body + checks |
| Suite re-run with -W error because pytest.ini hides warnings | **True, but narrower.** The rerun used `-W error::pytest.PytestRemovedIn9Warning -W error::pytest.PytestDeprecationWarning`, not a blanket `-W error`. The stated reason is "`pytest.ini` has `filterwarnings = ignore`". | PR #7 table, PR #8 table |
| urllib3 issue triaged reachable; python-multipart not reachable in the default configuration | **True.** Issue #3 comment: CVE-2026-97688 and -97689 "Reachable (reproduced locally)", -97687 not reachable. Issue #2 comment: "none of the three CVEs are reachable through this repo's code paths", with the caveat that a deployment using `OAuthProvider`/`OAuthProxy` would make two of them reachable. | dmonroym0/superset#3 comment 5926719039, #2 comment 5926732081 |

**Difference from your notes:** the issue #3 triage comment says CVE-2026-97688 (deflate hang) was "reproduced locally". Your notes say it did not reproduce. The comment also doesn't mention the ALERT_REPORTS / ALERT_REPORT_WEBHOOK feature flags. It only mentions `ALERT_REPORTS_WEBHOOK_ALLOW_INTERNAL_HOSTS`. Both points match your account that Devin missed the flags. The DEMO scenario for #3 (`app/demo/scenarios.json`) encodes -97688 as `REACHABLE, high`, copied from that comment.

## 2. The demo repo: dmonroym0/devin-superset-demo

### Modules (`app/`, from each module docstring)

| Module | What it does |
|---|---|
| `__main__.py` | runs uvicorn on HOST:PORT |
| `config.py` | env-backed `Settings`; `Secret` wrapper keeps values out of repr/str; DEMO defaults |
| `models.py` | shared types, issue states, the 7 managed labels (`MANAGED_LABELS`, lines 88-94) |
| `interfaces.py` | client protocols and the `Deps` bundle |
| `db.py` | SQLite: `meta`, `deliveries`, `issues`, `sessions`, `ledger`, `events`, `upstream_syncs` |
| `main.py` | app factory: schema init, mode claim, label bootstrap, playbook resolution, background tasks |
| `webhook.py` | `POST /webhooks/github`: 1 MiB cap, HMAC, delivery-ID dedupe, event/label/repo filters |
| `sweep.py` | periodic sweep (`SWEEP_INTERVAL_S`) and `POST /sweep` |
| `request_security.py` | local-only guard for `/sweep` and `/sync-upstream` (CIDR allowlist, rejects forwarded headers) |
| `pipeline.py` | worker tick: SEEN/QUEUED_BUDGET → TRIAGING → TRIAGED → FIXING → terminal |
| `triage.py` | read-only triage sessions, PR rejection, structured-output parse, archive |
| `router.py` | pure per-issue routing |
| `fix.py` | fix sessions, PR detection; never archived |
| `escalation.py` | soft-timeout nudge, hard-timeout or suspension → needs-human; `archive_triage` |
| `budget.py` | atomic ACU reservation ledger with a global ceiling |
| `prompts.py` | prompts with nonce-fenced untrusted text |
| `playbooks.py` | resolve playbooks by ID override or exact title; check the triage schema |
| `schema_check.py` | diff the local schema copy against the playbook's attached schema |
| `issue_actions.py` | comments, labels, close, URL allowlist, Markdown escaping |
| `github_client.py` / `fake_github.py` | LIVE GitHub REST client / in-memory DEMO fake seeded from `app/demo/seed_issues.json` |
| `devin_client.py` / `fake_devin.py` | LIVE Devin API v3 client / scripted DEMO fake driven by `app/demo/scenarios.json` |
| `metrics.py`, `dashboard.py`, `templates/board.html` | `/metrics.json` and the board |
| `preflight.py` | `python -m app.preflight`: env + read-only API checks, prints only status words |
| `upstream_sync.py`, `changelog.py` | BONUS: scheduled upstream merge and `FORK_CHANGELOG.md` PR |

### HTTP endpoints

| Method | Path | File | Notes |
|---|---|---|---|
| GET | `/` | app/dashboard.py:49 | board (HTML) |
| GET | `/healthz` | app/main.py:68 | `{"status":"ok","mode":...}` |
| GET | `/metrics.json` | app/main.py:72 | counts, automation rate, median time to PR, ACUs, sessions, upstream sync |
| POST | `/webhooks/github` | app/webhook.py:17 | signed `issues.labeled` only |
| POST | `/sweep` | app/sweep.py:51 | local-only manual sweep |
| POST | `/sync-upstream` | app/upstream_sync.py:277 | BONUS, local-only |

### Scripts

| Script | What it does |
|---|---|
| `scripts/simulate_webhook.py` | sends a signed `issues.labeled` payload (stdlib only; defaults to the DEMO secret) |
| `scripts/bootstrap_playbooks.py` | dry run by default; `--apply` creates only the playbooks whose title is missing (lines 3-7, 77-81) |
| `python -m app.preflight` | preflight module (not under scripts/) |

### DEMO scenarios (from `app/demo/scenarios.json`)

| Issue | Triage output (scripted) | Fix | Expected route |
|---|---|---|---|
| #1 jaraco-context | CVE-2026-23949 REACHABLE medium | fake PR /pull/101 | PR opened (illustrative; see #12) |
| #2 python-multipart | 3 × NOT_REACHABLE high | — | comment, low-priority, close |
| #3 urllib3 | -97687 NOT_REACHABLE; -97688, -97689 REACHABLE high | fake PR /pull/103 | PR opened |
| #4 pytest | CVE-2025-71176 REACHABLE medium | — | needs-human (major bump 7.4.4 → 9.0.3) |
| #5 fast-uri | CVE-2026-86472 UNKNOWN low | — | needs-human (illustrative; see #12) |
| synthetic 9001 | triage returns a PR (/pull/9001) | — | rejected → needs-human + `devin:triage-rejected` |
| synthetic 9002 | session never settles | — | soft nudge, then hard timeout → needs-human |
| any other number | "no scripted scenario" UNKNOWN (`fake_devin.py:30`) | — | needs-human |

### Environment variables (`app/config.py`, `.env.example`)

`APP_MODE` (demo), `GITHUB_API_BASE`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET` (DEMO default `demo-only-not-a-secret`; LIVE empty → webhook 503), `DEVIN_API_BASE`, `DEVIN_API_KEY`, `DEVIN_ORG_ID`, `PLAYBOOK_TRIAGE_ID`, `PLAYBOOK_FIX_ID`, `PLAYBOOK_TRIAGE_TITLE` ("CVE Reachability Triage (Read-Only)"), `PLAYBOOK_FIX_TITLE` ("Dependency Security Fix (superset)"), `TRIAGE_ACU_CAP` (5), `FIX_ACU_CAP` (15), `ACU_CEILING` (120), `SWEEP_INTERVAL_S` (300), `SWEEP_ALLOWED_CIDRS` (127.0.0.0/8, ::1/128, 172.16.0.0/12), `POLL_INTERVAL_S` (DEMO 1, LIVE 15), `SOFT_TIMEOUT_S` (1800), `HARD_TIMEOUT_S` (5400), `DEVIN_MODE_TRIAGE`, `DEVIN_MODE_FIX`, `UPSTREAM_SYNC_ENABLED` (DEMO true, LIVE false), `UPSTREAM_SYNC_INTERVAL_S` (86400), `UPSTREAM_SYNC_BRANCH` (master), `DEMO_UPSTREAM_SCENARIO` (merge), `DATA_DIR` (/data), `DB_PATH`, `HOST` (0.0.0.0), `PORT` (8000).

### Labels the service creates (`app/models.py:88-94`, created at startup by `app/main.py:45`)

`devin:fixplease` (trigger), `devin:in-progress`, `devin:pr-opened`, `devin:needs-human`, `devin:low-priority`, `devin:queued-budget`, `devin:triage-rejected`.

What the service does to an issue (`app/issue_actions.py:130-203`): it adds `devin:in-progress`, posts a triage comment, then does one of three things. FIX: comment with the PR URL(s) ("Opened, not done"), add `devin:pr-opened`. Needs-human: add `devin:needs-human` (+ `devin:triage-rejected`). Not reachable: add `devin:low-priority`, **close as not_planned**. In each case it removes `devin:in-progress`. Queued budget adds `devin:queued-budget` plus one comment. It never removes `devin:fixplease`.

### CI

`.github/workflows/ci.yml` has three jobs: `test` (ruff check, ruff format --check, pytest), `gitleaks` (gitleaks-action@v2, full history), and `docker` (build, non-root check, /healthz). Latest run on `main @ f662d83`: **success** (2026-10-02T08:04Z). Every CI run in `gh run list --limit 15` is success.

### Docs, skills, playbooks

- Docs: `README.md`, `docs/PLAN.md` (approved plan, A–K), `features.md`, `LICENSE` (MIT), `.github/pull_request_template.md` (features.md checkbox, line 11). This audit adds `docs/STATE.md`, `docs/TIMELINE.md`, `docs/DEVIN_USAGE.md`, `docs/TEST_REPORT.md` and `docs/LIVE_RUNBOOK.md`.
- Repo skills: `.agents/skills/features-md/SKILL.md`.
- Playbooks as code: `playbooks/cve_triage.md` (read-only triage), `playbooks/dep_security_fix.md` (covers pip **and npm**: step 5, "regenerate the lockfile in that directory and reinstall with `npm ci`"; step 10 opens the PR as a draft).

## 3. Requested vs built

Status key: built / partly built / missing / unverified.

| Requested | Status | Evidence |
|---|---|---|
| issues.labeled webhook with HMAC + periodic sweep + redelivery dedupe | built | app/webhook.py:39 (`compare_digest`), :67-76 (duplicate), :85 (label); app/sweep.py:47; TEST_REPORT §3 |
| Read-only triage using "CVE Reachability Triage (Read-Only)", structured output required, PR URL rejected in code | built | app/triage.py:151 (`structured_output_required=True`), :238-246 (reject on `pr_urls`); app/prompts.py:40 (read-only line) |
| Routing: reachable first; major / unknown / low → human; not_reachable → comment + close low priority | built | app/router.py:94-118, :119+; app/issue_actions.py:150-155; TEST_REPORT §5 |
| Fix sessions using "Dependency Security Fix (superset)"; PR = opened, not done | built | app/fix.py; app/issue_actions.py:158-163 ("Opened, not done") |
| Devin API v3 only; playbook IDs by title at startup, env overrides | built | app/devin_client.py:99 (`/v3/organizations/...`); `rg "/v1/\|/v2/" app scripts` finds nothing; app/playbooks.py:21-38 |
| ACU cap per session (5 / 15) and ceiling (120) | built | app/config.py:94-96; app/budget.py:10-14; DEMO ledger 5×5 + 2×15 = 55 / 120 |
| Comments + labels; dashboard; metrics endpoint | built | app/issue_actions.py; app/dashboard.py:49; app/main.py:72 |
| Playbooks as code + create-if-missing bootstrap | built | playbooks/*.md; scripts/bootstrap_playbooks.py:3-7, 79 |
| features.md + PR template checkbox | built | features.md; .github/pull_request_template.md:11 |
| Untrusted issue text fenced in prompts | built | app/prompts.py:22-28, 43-44; TEST_REPORT §8 |
| No secrets in logs, images or git; .dockerignore; gitleaks in CI | built | .dockerignore:1-6; ci.yml:20-26; gitleaks 36 commits, no leaks; TEST_REPORT §8 |
| Python 3.11, SQLite, non-root image + healthcheck, public.ecr.aws base | built | Dockerfile:1, 14, 16; pyproject; app/db.py |
| DEMO with zero keys | built | `docker compose up --build` with no .env; TEST_REPORT §2 |
| Preflight never prints values | built | app/preflight.py `_report`; canary test, 0 hits (TEST_REPORT §7) |
| Tests; signed-webhook simulate script; README diagram; MIT license | built | 265 tests; scripts/simulate_webhook.py:56; README.md:16 (mermaid); LICENSE |
| Route per issue; fix if any CVE reachable ≥ medium and bump not major; other verdicts in comment | built | app/router.py:101-118; app/issue_actions.py:48-93 (table of qualifying + others) |
| Archive triage only, never fix | built | app/escalation.py:61-62 (`if row.stage is not Stage.TRIAGE ... return`); app/fix.py:1; DEMO db: triage archived=1 ×5, fix archived=0 ×2 |
| Bind 0.0.0.0 in container; publish 127.0.0.1 in compose | built | Dockerfile:13; docker-compose.yml:5 |
| No personal IDs in .env.example | built | .env.example: `DEVIN_ORG_ID=org-xxxxxxxx`, empty keys |
| Sweep interval env var + local manual sweep | built | app/config.py:97; app/sweep.py:51-53 |
| Playbook schema is the source of truth; preflight fails if local copy differs | built | app/playbooks.py:41-42; app/preflight.py:176-189 |
| Triage prompt applies reachability-evidence-standards; features-md skill | built | app/prompts.py:39; .agents/skills/features-md/SKILL.md |
| DEMO seeds the fork's real issues and replays what really happened | **partly built** | Seeds are real (app/demo/seed_issues.json). Outcomes for #1 and #5 are made up (README.md:52, :56). Gap issue https://github.com/dmonroym0/devin-superset-demo/issues/12 |
| Session tags | built | app/triage.py:33, 44-45 (`devin-superset-demo`, `issue-N`, `stage-X`); used at triage.py:149, fix.py:134 |
| README section comparing with Devin Automations; "Next steps" | built | README.md:174, :183 |
| Optional DEVIN_MODE_TRIAGE / DEVIN_MODE_FIX; dashboard column for mode and ACUs | built (values not validated: https://github.com/dmonroym0/devin-superset-demo/issues/13) | app/config.py:165-166; app/models.py:169-170; board.html:125-127 |
| docs/PLAN.md committed | built | docs/PLAN.md |
| BONUS upstream sync | **exists** (merged in PR #6 of this repo, not a gap) | app/upstream_sync.py; README.md:122; DEMO `/metrics.json` shows `last_outcome: merged` against a fake PR /pull/900 |

Gap issues filed: https://github.com/dmonroym0/devin-superset-demo/issues/12 (only one requested item is partly built). Other findings are in `docs/TEST_REPORT.md`.
