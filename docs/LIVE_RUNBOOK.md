# LIVE RUNBOOK (for Daniel; not run during the audit)

LIVE mode was **not** run in this audit. Everything here comes from the code and README, with file references. Anything that hasn't been seen to work is marked *unverified*.

## 0. What LIVE does to the fork
At startup the service creates any missing `devin:*` labels on `dmonroym0/superset` (`app/main.py:45`, `app/models.py:80-95`). After that, it only touches the issues you label with `devin:fixplease`:
- adds and removes the `devin:*` status labels;
- posts comments;
- closes issues it finds not reachable, as `not_planned` (`app/issue_actions.py:131-201`).

Devin fix sessions open PRs on the fork. Upstream sync is **off** in LIVE unless you set `UPSTREAM_SYNC_ENABLED=true` (`app/config.py:105`). Leave it off.

## 1. GitHub token: minimum permissions
Use a fine-grained PAT that can reach `dmonroym0/superset` only (README "GitHub token: minimum permissions"):

| Permission | Access | Needed by |
|---|---|---|
| Metadata | Read | always |
| Issues | Read and write | `list_open_issues_with_label`, `get_issue`, `ensure_labels`, `add_labels`, `remove_label`, `create_comment`, `close_issue` (`app/github_client.py:126-212`). `scripts/demo_reset.py` needs the same. |
| Contents, Pull requests | **none** with sync off | used only by upstream sync (`merge_upstream`, `compare`, `get_file`, `create_changelog_pr`, `app/github_client.py:222-356`) |

Devin key: use a runtime API key for the org. It does **not** need ManageOrgPlaybooks (README). The playbooks must already exist with the titles in `.env.example`, or set `PLAYBOOK_TRIAGE_ID` / `PLAYBOOK_FIX_ID`.

## 2. Load keys from macOS Keychain (nothing written to disk)
One-time setup. Each command prompts for the value, so it never lands in shell history:
```bash
security add-generic-password -a "$USER" -s forkfix-github-token -w
security add-generic-password -a "$USER" -s forkfix-devin-key   -w
```
For each LIVE session, in one terminal:
```bash
export GITHUB_TOKEN="$(security find-generic-password -a "$USER" -s forkfix-github-token -w)"
export DEVIN_API_KEY="$(security find-generic-password -a "$USER" -s forkfix-devin-key -w)"
export DEVIN_ORG_ID="org-..."          # not a secret
unset GITHUB_WEBHOOK_SECRET            # sweep only; nothing public needed
```
**Don't** use the README's `cp .env.example .env` route for this. That writes the keys to disk. Also, `docker-compose.yml` only passes `APP_MODE` plus the `.env` file, so keys exported in your shell **won't reach the container** through `docker compose up`. Run the image directly instead. `-e NAME` with no value copies the variable from your shell, so the value never appears in the command line:
```bash
docker compose build
docker run -d --name forkfix-live -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --security-opt no-new-privileges:true \
  -v forkfix-live-data:/data \
  -e APP_MODE=live -e GITHUB_TOKEN -e DEVIN_API_KEY -e DEVIN_ORG_ID \
  -e SWEEP_INTERVAL_S=60 -e TRIAGE_ACU_CAP=5 -e FIX_ACU_CAP=15 -e ACU_CEILING=60 \
  devin-superset-demo-forkfix:latest
```
`ACU_CEILING=60` is enough for the order in §4 (5 + 20 + 20 = 45). The default is 120.

## 3. Preflight (read-only)
```bash
docker run --rm -e APP_MODE=live -e GITHUB_TOKEN -e DEVIN_API_KEY -e DEVIN_ORG_ID \
  devin-superset-demo-forkfix:latest python -m app.preflight; echo "rc=$?"
```
Expect every line to say `set`, the playbook schema check to pass, and `preflight: ok` with `rc=0`. Any `missing` or `invalid` gives `rc=1`, and the container won't start (README §LIVE). Preflight never prints values (TEST_REPORT §9). Check the `DEVIN_MODE_*` spelling yourself, because preflight doesn't check it ([#13](https://github.com/dmonroym0/devin-superset-demo/issues/13)).

## 4. Which fork issues, in what order
| Order | Fork issue | Why it's safe | Expected route (*unverified*) | ACU committed |
|---|---|---|---|---|
| 1 | #2 python-multipart 0.0.29 → 0.0.31 | no open PR; patch bump | not reachable → comment, `devin:low-priority`, closed. The earlier Devin comment on #2 reached the same verdict. | 5 (triage) |
| 2 | #3 urllib3 2.7.0 → 2.8.0 | no open PR; minor bump | 2 reachable → fix session → **PR opened**, `devin:pr-opened` | 5 + 15 |
| 3 (optional) | #5 fast-uri 3.1.7 → 3.1.8 (npm) | no open PR; patch bump | if reachable: fix → PR; otherwise `devin:needs-human` | 5 (+15) |
| **skip** | #1 jaraco-context | fork PR [#6](https://github.com/dmonroym0/superset/pull/6) already open | would start a second fix session and open a duplicate PR | — |
| **skip** | #4 pytest | fork PRs [#7](https://github.com/dmonroym0/superset/pull/7) / [#8](https://github.com/dmonroym0/superset/pull/8) already open; also a major bump | duplicate risk | — |

#1 and #4 stay out until [#10](https://github.com/dmonroym0/devin-superset-demo/issues/10) (duplicate-PR guard) is fixed. Nothing in `app/` checks for an existing PR (TEST_REPORT §8).

**npm lockfiles:** yes, they're covered. `playbooks/dep_security_fix.md` says to "regenerate the lockfile in that directory and reinstall with npm ci" for each `package-lock.json` the issue names.

Label **one issue at a time**: add `devin:fixplease` in the GitHub UI. With `SWEEP_INTERVAL_S=60` the service picks it up within a minute. To skip the wait, run `curl -X POST localhost:8000/sweep`.

## 5. What the board should show, and roughly when
Board: `http://localhost:8000/`. Metrics: `curl -s localhost:8000/metrics.json`. The service checks Devin every 15 s in LIVE (`app/config.py:98`).

| Time after labelling (*estimates, unverified*) | Board state | Fork labels |
|---|---|---|
| ≤ 1 min | `seen` → `triaging` | `devin:in-progress` |
| ~5–20 min | `triaged`, then `not_reachable`, `needs_human` or `fixing` | low-priority (closed), needs-human, or still in-progress |
| ~15–45 min more (fix) | `pr_opened` | `devin:pr-opened`, and a comment with the PR link |

If you record, record DEMO for the walkthrough. Then show a LIVE issue that has already finished, rather than waiting on camera.

## 6. ACU caps
- Triage is capped at `TRIAGE_ACU_CAP` (5) and fix at `FIX_ACU_CAP` (15). Each is sent as `max_acu_limit` on session create (`app/triage.py:148`, `app/fix.py`).
- The ledger commits the cap up front and refuses to start a session that would push the total over `ACU_CEILING`. The issue then goes to `queued_budget` with label `devin:queued-budget`.
- The board's "ACUs consumed 0.0" means "not reported", not "free" ([#17](https://github.com/dmonroym0/devin-superset-demo/issues/17)). Check the real usage on Devin's Usage page.

## 7. A session that's stuck
Handled automatically (`app/escalation.py`):
- At `SOFT_TIMEOUT_S` (1800 s), the service sends the session one nudge message.
- At `HARD_TIMEOUT_S` (5400 s), or when a session errors or hits its ACU limit, the issue goes to `needs_human` with a comment giving the reason.

To act by hand:
1. Open the session from the board link (tags `forkfix`, `issue-<n>`, `stage-<triage|fix>`), then message or stop it in Devin.
2. To stop all spend at once, run `docker stop forkfix-live`. Sessions that are already running keep going until their cap, so stop them in Devin too.

## 8. Reset between takes
```bash
python scripts/demo_reset.py 2 3 5                    # dry run: shows labels/comments/close it would undo
python scripts/demo_reset.py 2 3 5 --apply --remove-trigger
python scripts/demo_reset.py 2 --apply --recreate     # also opens a fresh copy (same title/body, original labels)
```
- The script only touches the issue numbers you pass. It removes only `devin:*` service labels, and keeps `devin:fixplease` unless you pass `--remove-trigger`.
- It deletes only comments that you posted and that carry a service marker, and it reopens an issue only if you closed it as `not_planned` and it has `devin:low-priority`.
- It reads `GITHUB_TOKEN` from the environment and never prints it.
- It doesn't close PRs that Devin opened. Close those on GitHub yourself.

The service's SQLite database remembers issues it has already processed. Before labelling the **same** issue number again, remove it:
```bash
docker rm -f forkfix-live && docker volume rm forkfix-live-data
```
This also resets the ACU ledger. Alternatively, use `--recreate` and label the new copy.
