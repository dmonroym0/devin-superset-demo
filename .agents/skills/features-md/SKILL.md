---
name: features-md
description: The features.md rule for devin-superset-demo. Use whenever you add, change, or verify a feature in this repo.
---

# features.md rule

Every feature has an entry in `features.md` at the repo root. A feature PR without its entry is not done.

Each entry has:
- **Name**
- **Status**: `planned`, `built`, or `verified`
- **Files**: the paths that implement it
- **How to see it**: a command, URL, or test that shows it working
- **Requirement**: the requirement or change ID it covers (e.g. `R1`, `C`)

Rules:
1. Update `features.md` in the same PR as the code. Not in a follow-up.
2. `built` means the code and its tests are merged into the branch. `verified` means it was checked end to end (DEMO run, curl, or e2e test) and the "How to see it" step works.
3. Only edit the section you own. Don't reorder other sections.
4. Tick the `features.md` checkbox in the PR template only after the entry is updated.
