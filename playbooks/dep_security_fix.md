# Playbook: Dependency Security Fix (dmonroym0/superset)

## Overview
Fix one `[Security]` GitHub issue on dmonroym0/superset by upgrading a single vulnerable Python or npm dependency. The diff is limited to that package. Prove there are no regressions against the unit-test baseline, and open a PR that closes the issue.

## What's Needed From User
- Issue number N (e.g. `#3`). The issue body has the package, ecosystem (pip/npm), current -> fixed version, CVEs, and the affected files. Read it with `gh issue view N --repo dmonroym0/superset`.

## Procedure
1. Set up every new shell with `source ~/.nvm/nvm.sh && nvm use 24.16.0 && source ~/venvs/superset/bin/activate`, then `cd ~/repos/superset && git checkout master && git pull`.
2. Read issue N and record the package, ecosystem, current/fixed versions, CVEs, and files.
3. Confirm the vulnerability still exists at HEAD:
   - pip: `uv pip install pip-audit` if it's missing. Strip the `-e`/`.` lines from `requirements/*.txt` into a temp file and run `pip-audit -r <tmp> --no-deps --disable-pip`.
   - npm: run `npm audit --package-lock-only` in the directory that owns the lockfile (superset-frontend, superset-frontend/cypress-base, superset-embedded-sdk, superset-websocket).
   - If the vulnerability is gone, comment on the issue with the command and output (`gh issue comment N --repo dmonroym0/superset`) and stop.
4. Record the baseline on master before changing anything:
   - pip: `PYTHONPATH=$PWD SUPERSET_TESTENV=true SUPERSET_SECRET_KEY=not-a-secret pytest -n auto --dist loadfile -p no:cacheprovider -q -rfE -o filterwarnings=default ./tests/common ./tests/unit_tests > baseline.log`. Save the counts and the warnings-summary section.
   - npm: run the owning package's test command (`npm test` in superset-embedded-sdk or superset-websocket). For superset-frontend, run only the jest tests that import the package: `npx jest --max-workers=3 --workerIdleMemoryLimit=4G --silent <paths>`.
5. Branch with `git checkout -b devin/$(date +%s)-bump-<pkg>` and upgrade:
   - pip: `./scripts/uv-pip-compile.sh --upgrade-package <pkg>==<version>`. If Docker Hub returns 429, rerun as `RUNNING_IN_DOCKER=1 ./scripts/uv-pip-compile.sh --upgrade-package <pkg>==<version>` inside the activated Python 3.11 venv, and say so in the PR.
   - npm: bump a direct dependency in package.json. For a transitive one, add or raise an `overrides` entry. Then regenerate the lockfile in that directory and reinstall with `npm ci`.
6. Check the diff scope with `git diff --stat` and `git diff`. Allowed changes:
   - the target package
   - sub-dependencies that its new version requires or no longer needs (added or removed pins)
   - `# via` comment lines
   - for npm, the matching `overrides`/package.json line

   Revert any unrelated lockfile churn. If `pyproject.toml` or a `.in` constraint blocks the version, see step 9.
7. Verify by rerunning the step-4 command on the branch:
   - Compare passed/skipped/xfailed and the failures to the baseline.
   - Diff the warnings summary against the baseline. Investigate any new `DeprecationWarning`/`FutureWarning` that comes from the upgraded package.
   - For pip, also run the CI coverage gates: `pytest --cov=superset/sql/ ./tests/unit_tests/sql/ --cov-fail-under=100` and `pytest --cov=superset/semantic_layers/ ./tests/unit_tests/semantic_layers/ --cov-fail-under=100`.
8. If a regression appears, stop and report it in the PR or issue rather than working around it (see Forbidden Actions).
9. If another installed package (e.g. a pytest plugin) or a `pyproject.toml`/`.in` upper bound blocks the target version, split the work into stacked PRs:
   - Bottom PR: the highest compatible version, branched from master.
   - Next PR: the target version plus the minimal unblocking change, based on the bottom PR's branch. That change is either the blocker's minimum compatible bump or the raised upper bound in pyproject.toml (e.g. `<26` -> `<27`).
   - Run step 7 for each PR.

   Create both with `git_create_pr`, then group them with `git_stack`. Example: pytest 7.4.4 -> 8.4.2 -> 9.0.3, where the second PR adds pytest-asyncio 1.3.0.
10. Commit only the requirement/lockfile changes and push. Open the PR **as a draft**, titled `fix(deps): bump <pkg> from <old> to <new> (<CVE>)`, using the repo PR template, with:
    - `Closes #N`
    - the CVEs/GHSAs
    - old -> new versions, including changed sub-dependencies
    - the exact commands run, including whether the Docker fallback was used
    - a before/after table of passed/skipped/xfailed/failed and new warnings
11. Watch CI with `git_pr_checks`. Fix real failures. Explain every failure you don't fix in a PR comment.
12. Leave the PR(s) in draft. The user publishes them after review. Report the structured output.

## Specifications
- The vulnerability is gone from `pip-audit`/`npm audit` on the branch.
- The diff only touches the target package, its direct sub-dependencies, and the matching override/constraint line.
- Unit results match the baseline: 20,392 total = 20,345 passed / 45 skipped / 2 xfailed. Drift to 20,347 / 43 / 2 is normal. There are no new failures and no new warnings from the upgraded package.
- CI is green, or every failure is explained in the PR.
- The PR closes issue N and is still a draft.
- Structured output is provided: issue, package, versions, verdict, PR URLs, before/after counts.

## Advice and Pointers
- `pytest.ini` has `filterwarnings = ignore`, which hides every warning.
  - Don't use a blanket `-W error`: the suite aborts while importing `tests/conftest.py` (SQLAlchemy `SADeprecationWarning`, exit 4, 0 tests run).
  - Use `-o filterwarnings=default` and diff the warnings summary (about 9.6k warnings from about 316 unique sources on master).
  - To check pytest's own deprecations, use `-W error::pytest.PytestDeprecationWarning`.
- On this fork, `dependency-review` always fails with "Dependency review is not supported on this repository". It's a repo setting, so cite it as the explanation.
- `check-python-deps` reruns `uv-pip-compile.sh` in Docker and fails if the pins differ. It confirms that a `RUNNING_IN_DOCKER=1` local compile matches.
- In superset-frontend, `npm install` prunes a nested dompurify entry from package-lock.json. Revert that hunk, and use `npm ci` for installs.
- If counts drift by +/-2 between passed and skipped, rerun alone before blaming the change. A concurrent pre-commit install can cause the drift.

## Forbidden Actions
- Skipping, xfailing, or deleting tests, or loosening assertions.
- Changing production code (`superset/`, `superset-frontend/src/`) to make a test pass.
- Running the full superset-frontend jest suite (it OOM-kills the VM). Run targeted tests only.
- Bumping unrelated packages or running a blanket `--upgrade`.
- Pushing to master.
- Marking a PR ready for review, or merging it. The user publishes after reviewing.
- Raising a pyproject.toml bound in the same PR as the base upgrade (it goes in its own stacked PR).
