import importlib.util
import json
import sys
from pathlib import Path

import respx

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("demo_reset", ROOT / "scripts" / "demo_reset.py")
reset = importlib.util.module_from_spec(spec)
sys.modules["demo_reset"] = reset
spec.loader.exec_module(reset)

BASE = "https://api.github.test"
REPO = "/repos/dmonroym0/superset"
TOKEN = "ghp_test_token_not_real_123456"
ENV = {"GITHUB_TOKEN": TOKEN, "GITHUB_API_BASE": BASE}
ME = {"login": "daniel"}


def _issue(number, *, labels, state="open", state_reason=None, closed_by=None):
    return {
        "number": number,
        "title": f"[Security] issue {number}",
        "body": f"body of {number}",
        "labels": [{"name": name} for name in labels],
        "state": state,
        "state_reason": state_reason,
        "closed_by": {"login": closed_by} if closed_by else None,
    }


COMMENTS = [
    {
        "id": 11,
        "user": ME,
        "body": "### devin-superset-demo triage\n\n...\n\n_Automated by devin-superset-demo._",
    },
    {"id": 12, "user": ME, "body": "Needs a human: major version bump"},
    {"id": 13, "user": ME, "body": "My own note, keep it"},
    {"id": 14, "user": {"login": "someone"}, "body": "Needs a human: quoted by someone else"},
]


def _mock_reads(router, number, issue, comments=COMMENTS):
    router.get("/user").respond(json=ME)
    router.get(f"{REPO}/issues/{number}").respond(json=issue)
    router.get(f"{REPO}/issues/{number}/comments").respond(json=comments)


def _run(router, argv, capsys):
    code = reset.main(argv, env=ENV)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    return code, out.out


def test_dry_run_makes_only_get_requests(capsys):
    closed = _issue(
        2,
        labels=["security", "devin:fixplease", "devin:low-priority"],
        state="closed",
        state_reason="not_planned",
        closed_by="daniel",
    )
    with respx.mock(base_url=BASE, assert_all_called=True) as router:
        _mock_reads(router, 2, closed)
        code, out = _run(router, ["2"], capsys)
        methods = {call.request.method for call in router.calls}
    assert code == 0
    assert methods == {"GET"}
    assert "remove label devin:low-priority" in out
    assert "reopen" in out
    assert "dry run: nothing changed" in out


def test_apply_undoes_only_service_labels_comments_and_close(capsys):
    closed = _issue(
        2,
        labels=["security", "devin:fixplease", "devin:low-priority", "devin:in-progress"],
        state="closed",
        state_reason="not_planned",
        closed_by="daniel",
    )
    with respx.mock(base_url=BASE, assert_all_called=True) as router:
        _mock_reads(router, 2, closed)
        low = router.delete(f"{REPO}/issues/2/labels/devin%3Alow-priority").respond(200, json=[])
        prog = router.delete(f"{REPO}/issues/2/labels/devin%3Ain-progress").respond(404)
        c11 = router.delete(f"{REPO}/issues/comments/11").respond(204)
        c12 = router.delete(f"{REPO}/issues/comments/12").respond(204)
        reopen = router.patch(f"{REPO}/issues/2").respond(json={})
        code, _ = _run(router, ["2", "--apply"], capsys)
        deleted = {call.request.url.path for call in router.calls if call.request.method == "DELETE"}
    assert code == 0
    assert low.called and prog.called and c11.called and c12.called
    assert json.loads(reopen.calls.last.request.content) == {"state": "open"}
    assert f"{REPO}/issues/comments/13" not in deleted
    assert f"{REPO}/issues/comments/14" not in deleted
    assert not any("fixplease" in path or "security" in path for path in deleted)


def test_issue_closed_by_someone_else_is_not_reopened(capsys):
    closed = _issue(
        3, labels=["devin:low-priority"], state="closed", state_reason="not_planned", closed_by="maintainer"
    )
    with respx.mock(base_url=BASE, assert_all_called=True) as router:
        _mock_reads(router, 3, closed, comments=[])
        router.delete(f"{REPO}/issues/3/labels/devin%3Alow-priority").respond(200, json=[])
        code, out = _run(router, ["3", "--apply"], capsys)
        patched = any(call.request.method == "PATCH" for call in router.calls)
    assert code == 0
    assert "reopen" not in out
    assert not patched


def test_remove_trigger_and_recreate_copy(capsys):
    issue = _issue(5, labels=["security", "devin:fixplease", "devin:needs-human"])
    with respx.mock(base_url=BASE, assert_all_called=True) as router:
        _mock_reads(router, 5, issue, comments=[])
        router.delete(f"{REPO}/issues/5/labels/devin%3Afixplease").respond(200, json=[])
        router.delete(f"{REPO}/issues/5/labels/devin%3Aneeds-human").respond(200, json=[])
        create = router.post(f"{REPO}/issues").respond(
            201, json={"number": 42, "html_url": "https://github.com/dmonroym0/superset/issues/42"}
        )
        code, out = _run(router, ["5", "--apply", "--remove-trigger", "--recreate"], capsys)
    assert code == 0
    assert json.loads(create.calls.last.request.content) == {
        "title": "[Security] issue 5",
        "body": "body of 5",
        "labels": ["security"],
    }
    assert "created https://github.com/dmonroym0/superset/issues/42" in out


def test_only_requested_issues_are_read_or_changed(capsys):
    with respx.mock(base_url=BASE, assert_all_called=True, assert_all_mocked=True) as router:
        _mock_reads(router, 4, _issue(4, labels=["security"]), comments=[])
        code, out = _run(router, ["4", "4", "--apply"], capsys)
        paths = {call.request.url.path for call in router.calls}
    assert code == 0
    assert "nothing to undo" in out
    assert paths == {"/user", f"{REPO}/issues/4", f"{REPO}/issues/4/comments"}


def test_missing_token_exits_2_and_http_error_never_prints_token(capsys):
    assert reset.main(["1"], env={}) == 2
    assert "GITHUB_TOKEN: missing" in capsys.readouterr().err
    with respx.mock(base_url=BASE) as router:
        router.get("/user").respond(401)
        code = reset.main(["1"], env=ENV)
    out = capsys.readouterr()
    assert code == 1
    assert "-> 401" in out.err
    assert TOKEN not in out.out + out.err
