from datetime import date

from app.changelog import prepend, render_section
from app.models import UpstreamCommit


def _commit(subject, sha="a" * 40, *, merge=False):
    return UpstreamCommit(sha=sha, subject=subject, is_merge=merge)


def test_render_section_groups_conventional_types_in_order():
    commits = (
        _commit("chore: tidy"),
        _commit("other wording"),
        _commit("feat(api): add endpoint"),
        _commit("docs: explain endpoint"),
        _commit("fix: handle empty response"),
        _commit("feat!: remove old endpoint"),
        _commit("perf: cache results"),
        _commit("refactor: simplify parser"),
        _commit("test: add coverage"),
        _commit("build: update package"),
        _commit("ci: run checks"),
        _commit("revert: revert old behavior"),
        _commit("fix: behavior BREAKING CHANGE"),
    )

    section = render_section(commits, (), "b" * 40, "c" * 40, date(2026, 10, 2))

    positions = [
        section.index("### Breaking changes"),
        section.index("### feat"),
        section.index("### fix"),
        section.index("### perf"),
        section.index("### refactor"),
        section.index("### docs"),
        section.index("### test"),
        section.index("### build"),
        section.index("### ci"),
        section.index("### chore"),
        section.index("### revert"),
        section.index("### Other"),
    ]
    assert positions == sorted(positions)
    assert "## Upstream sync 2026-10-02 (bbbbbbb..ccccccc)" in section
    assert "remove old endpoint" in section
    assert "behavior BREAKING CHANGE" in section
    assert "other wording" in section


def test_render_section_skips_merge_and_own_changelog_commits():
    commits = (
        _commit("Merge pull request #1", merge=True),
        _commit("docs(fork-changelog): update changelog"),
        _commit("fix: retained commit", sha="d" * 40),
    )

    section = render_section(commits, (), "b" * 40, "c" * 40, date(2026, 10, 2))

    assert "retained commit" in section
    assert "Merge pull request" not in section
    assert "docs(fork-changelog)" not in section


def test_render_section_escapes_subject_and_links_only_full_hex_sha():
    commits = (
        _commit("feat: <img src=x> [click](https://evil) &", sha="e" * 40),
        _commit("fix: strange sha", sha="g123456"),
    )

    section = render_section(commits, (), "b" * 40, "c" * 40, date(2026, 10, 2))

    assert "&lt;img src=x&gt;" in section
    assert r"\[click\]\(https://evil\)" in section
    assert "&amp;" in section
    assert f"[eeeeeee](https://github.com/apache/superset/commit/{'e' * 40})" in section
    assert "[g123456]" not in section
    assert " (g123456)" in section


def test_render_section_lists_requirement_file_changes_only():
    section = render_section(
        (_commit("fix: update dependencies"),),
        ("requirements/base.txt", "requirements/development.txt", "docs/readme.md"),
        "b" * 40,
        "c" * 40,
        date(2026, 10, 2),
    )

    assert "### Dependency changes" in section
    assert "requirements/base.txt" in section
    assert "requirements/development.txt" in section
    assert "docs/readme.md" not in section


def test_render_section_marks_absent_requirement_changes():
    section = render_section(
        (_commit("fix: update code"),),
        ("requirements/nested/base.txt", "README.md"),
        "b" * 40,
        "c" * 40,
        date(2026, 10, 2),
    )

    assert "_No requirements/*.txt changes._" in section


def test_render_section_flags_incomplete_dependency_file_list():
    section = render_section(
        (),
        ("requirements/base.txt",),
        "b" * 40,
        "c" * 40,
        date(2026, 10, 2),
        files_truncated=True,
    )

    assert "### Dependency changes" in section
    assert "_Dependency list may be incomplete: GitHub compare returned its 300-file cap._" in section


def test_prepend_adds_new_section_after_deduplicated_header():
    section = "## Upstream sync 2026-10-02 (aaaaaaa..bbbbbbb)\n\n- change"

    result = prepend("# Fork changelog\n\n## Older sync\n", section)

    assert result.startswith("# Fork changelog\n\n" + section)
    assert result.count("# Fork changelog") == 1
    assert result.endswith("\n\n## Older sync\n")


def test_prepend_creates_header_when_file_is_missing():
    section = "## Upstream sync 2026-10-02 (aaaaaaa..bbbbbbb)\n\n- change"

    assert prepend(None, section) == "# Fork changelog\n\n" + section + "\n"


def test_prepend_does_not_duplicate_an_existing_range():
    section = "## Upstream sync 2026-10-02 (aaaaaaa..bbbbbbb)\n\n- change"
    existing = "# Fork changelog\n\n" + section + "\n\n## Older sync\n"

    result = prepend(existing, section)

    assert result.count("## Upstream sync 2026-10-02") == 1
    assert result.endswith("\n\n## Older sync\n")
