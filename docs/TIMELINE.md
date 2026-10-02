# TIMELINE (oldest first, UTC)

Sources: `gh issue list` / `gh pr list --json createdAt,mergedAt`, PR comment/review timestamps, `git log`. "Session" links come from PR bodies and comments. **No fork PR is merged** as of 2026-10-02 21:50 UTC.

| When (UTC) | Repo | Event | Why it mattered |
|---|---|---|---|
| 2026-10-01 06:33:19 | fork | Issue #1 opened: jaraco-context 6.0.1 → 6.1.0 (`security`) by `devin-ai-integration[bot]` (Devin session https://app.devin.ai/sessions/3f181bfa2dfa4592b98eb60c30ee56b9, "Create Security Upgrade Issues") | |
| 2026-10-01 06:33:20 | fork | Issue #2 opened: python-multipart 0.0.29 → 0.0.31 by `devin-ai-integration[bot]`, same session | |
| 2026-10-01 06:33:21 | fork | Issue #3 opened: urllib3 2.7.0 → 2.8.0 by `devin-ai-integration[bot]`, same session | |
| 2026-10-01 06:33:23 | fork | Issue #4 opened: pytest 7.4.4 → 9.0.3 by `devin-ai-integration[bot]`, same session | |
| 2026-10-01 06:35:51 | fork | Issue #5 opened: fast-uri 3.1.7 → 3.1.8 by `devin-ai-integration[bot]`, same session | These five issues are the DEMO seed data. All five were opened by Devin's GitHub app, not by a person or by this service. |
| 2026-10-01 06:51:44 | fork | PR #6 opened (jaraco-context bump, Closes #1). Session https://app.devin.ai/sessions/98aa8416df4946948c56758fe753b547 | First Devin dependency fix. It used the repo's compile script and disclosed the Docker Hub 429 fallback. |
| 2026-10-01 06:51:47 | fork | PR #6: Devin PR-monitoring comment ("I'll fix CI failures and address comments…") | Shows PR monitoring was on. |
| 2026-10-01 06:57:01 | fork | PR #6: Devin Review, "No Issues Found" | |
| 2026-10-01 07:23:28 | fork | Issue #3 triage comment: CVE-2026-97688/-97689 reachable, -97687 not. Session https://app.devin.ai/sessions/4041fab2f8c748a687b7000ed15466f0 | The reachable verdict that DEMO scenario #3 replays. Per Daniel's notes, it missed two off-by-default feature flags, which became the org skill. |
| 2026-10-01 07:24:15 | fork | Issue #2 triage comment: none of 3 CVEs reachable (with an OAuthProvider caveat). No session link in the comment. | The not-reachable verdict that DEMO scenario #2 replays. |
| 2026-10-01 07:44:55 | fork | PR #7 opened (pytest 7.4.4 → 8.4.2, draft, Refs #4). Session https://app.devin.ai/sessions/629a2b09418543aab6861d405bb20357 | Step 1 of a stacked major upgrade. It fixed two leaking integration fixtures. |
| 2026-10-01 07:44:58 | fork | PR #7: PR-monitoring comment; 07:49:47 Devin Review "No Issues Found" | |
| 2026-10-01 07:54:41 | fork | PR #8 opened (pytest 9.0.3 + pytest-asyncio 1.3.0, draft, stacked on #7). Same session | pytest-asyncio 0.23.8 caps pytest < 9, so the CVE fix needed a second PR. |
| 2026-10-01 07:54:45 | fork | PR #8: PR-monitoring comment | |
| 2026-10-01 07:30:01 | fork | Issue #4 comment on the pytest plan (5926823085); follow-ups 07:57:16 and 09:47:37. Session 629a2b09… | Documents the -W error reasoning and the collection-order change. |
| 2026-10-01 21:18:52 | demo | Initial commit (5da01e1) | |
| 2026-10-02 04:03:30 | demo | `docs: add approved plan (docs/PLAN.md)` (215dc4e) | The approved plan with Daniel's changes A–K is the build contract. |
| 2026-10-02 04:08:26 | demo | Scaffold + core contracts commit (28d7e44) on devin/1790913600-forkfix | The shared contracts let three child sessions build in parallel. |
| 2026-10-02 04:27:53 | demo | PR #1 opened (child: ops: board, preflight, Docker, CI). Session https://app.devin.ai/sessions/bac9bfb7c8ae4e5e9886f583838fb079 | Child session 1 of 3. |
| 2026-10-02 04:30:28 | demo | PR #2 opened (child: Devin side). Session https://app.devin.ai/sessions/a41d505f4343436c92eb16adc5a76e4a | Child session 2 of 3. |
| 2026-10-02 04:39:45 | demo | PR #3 opened (child: GitHub side). Session https://app.devin.ai/sessions/d88cc9c7a7174d3e95b41dd05617a625 | Child session 3 of 3. |
| 2026-10-02 04:50:42 | demo | PRs #1, #2, #3 merged into devin/1790913600-forkfix | Integration branch assembled. |
| 2026-10-02 04:52:09 | demo | PR #4 opened (integration → main). Session https://app.devin.ai/sessions/1ef366ee7b8a477b81656cd968b0f58e | The main deliverable. |
| 2026-10-02 04:52:39 | demo | PR #5 opened (fixes for Devin Review findings on #3) | |
| 2026-10-02 04:56–05:14 | demo | PR #4: Devin Review rounds with inline findings (router.py, escalation.py, triage.py, config.py, main.py) | Automated review found real defects before merge. |
| 2026-10-02 05:27:48 | demo | PR #5: Daniel asks for #5 to be integrated and re-tested before #4 merges (comment 5946136553) | Human gate on merge order. |
| 2026-10-02 05:29:03 | demo | PR #4: Daniel says #4 stays unmerged until #5 is integrated (comment 5946147924) | |
| 2026-10-02 05:30:58 | demo | PR #5 merged into the integration branch | |
| 2026-10-02 06:22:01 | demo | PR #4: Daniel requests 4 changes (comment 5946664259): route only on issue-listed CVEs (he reproduced CVE-2026-1111 routing to FIX from CVE-2026-9999), persist the nudge after delivery, allowlist URL hosts, re-check cancellation before spending ACUs | **The key human-judgment moment.** Daniel found a hallucinated-CVE routing bug and a phishing-URL bug that Devin and Devin Review had not flagged. |
| 2026-10-02 06:48:50 | demo | PR #4: Devin reports all 8 changes + 3 follow-ups pushed (fbd65ce … 90ec4bb), each with a failing-first test | |
| 2026-10-02 07:06:49 | demo | PR #4: DEMO delta re-test evidence comment | |
| 2026-10-02 07:16:22 | demo | PR #6 opened (BONUS upstream sync), same session | BONUS work. Whether it was approved is unverified (no approval comment on GitHub); Daniel merged it. |
| 2026-10-02 07:16:26 | demo | PR #6: PR-monitoring comment; Devin Review rounds 07:19–08:03 | |
| 2026-10-02 07:53:01 | demo | PR #4: final DEMO end-to-end evidence @ 3d3d8f7 | |
| 2026-10-02 07:55:17 | demo | PR #4 merged to main | MUST + SHOULD service on main. |
| 2026-10-02 08:00:39 | demo | PR #6: final DEMO evidence @ 6d856ba (fixes a blank changelog link after a no-op sync) | |
| 2026-10-02 08:03:58 | demo | PR #6 merged to main (f662d83); CI success 08:04 | State audited here. |
| 2026-10-02 08:12:38–41 | demo | Issues #7, #8, #9 filed (cross-origin POST, stale session status, features.md README row) | Pre-audit findings, P2. |
| 2026-10-02 08:18:26–36 | demo | Audit issues #10–#16 filed (see TEST_REPORT) | #10 (duplicate-PR risk) changes the LIVE runbook. |
| 2026-10-02 08:22:43 | fork | LIVE: `devin:fixplease` added to #2 | First real run of the service against the fork. |
| 2026-10-02 08:25:58 | fork | LIVE triage comment on #2 by `devin-ai-integration[bot]`: 3 × not reachable | |
| 2026-10-02 08:27:11 | fork | #2 closed `not_planned` by the service after its "Not reachable" comment (08:27:07) and `devin:low-priority` (08:27:08) | Triage saved a fix session: no CVE reachable. |
| 2026-10-02 08:28:51 | fork | LIVE: `devin:fixplease` added to #3; `devin:in-progress` 08:29:17 | |
| 2026-10-02 08:33:22 | fork | LIVE triage of #3: comment 5948272855 by `devin-ai-integration[bot]` from read-only triage session https://app.devin.ai/sessions/482f049dcce9483486d53c6d77a46eb3 (HEAD d2fb52ac83): -97688, -97689 reachable, -97687 not | The verdict the router acted on. |
| 2026-10-02 08:34:26 | fork | Service route comment on #3: "Fix: opening a Devin fix session" | Two reachable CVEs, minor bump → FIX. |
| 2026-10-02 08:39:44 | fork | PR #9 opened (urllib3 2.7.0 → 2.8.0, draft) by `devin-ai-integration[bot]` from the LIVE fix session https://app.devin.ai/sessions/56160d44a4c843c3a49e949cfa7070a8; service "Opened, not done" comment 08:40:01, `devin:pr-opened` 08:40:02 | First PR opened end-to-end by the service. |
| 2026-10-02 15:31:13 | fork | LIVE upstream sync: `master` d2fb52ac83 → 0fdfd6660e (45 upstream commits, no merge commit) | PRs #6–#9 still mergeable, no conflicts. |
| 2026-10-02 15:31:17 | fork | PR #10 opened: `FORK_CHANGELOG.md` for d2fb52a..0fdfd66 | `License Check` fails: the file lacks the Apache header (https://github.com/dmonroym0/devin-superset-demo/issues/26). |
| 2026-10-02 19:02:20 | fork | Second LIVE upstream sync: `master` 0fdfd6660e → bc3698b5d6 (12 upstream commits, no merge commit) | PRs #6–#9 still mergeable, no conflicts. |
| 2026-10-02 19:02:24 | fork | PR #11 opened: `FORK_CHANGELOG.md` for 0fdfd66..bc3698b | Same `License Check` failure as #10 (https://github.com/dmonroym0/devin-superset-demo/issues/26). |
| 2026-10-02 21:40:14–21:41:34 | fork | PR #9 marked ready for review by `dmonroym0`, then back to draft | Re-triggered #9's checks. |
| now | fork | PRs #6–#11 still **open**; issue #2 closed by the service; #1, #3, #4, #5 open | No CVE is fixed on fork master. |
