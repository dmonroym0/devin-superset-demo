import httpx
import pytest
import respx

from app.config import Settings
from app.devin_client import DevinError, HttpDevinClient, build_devin_client
from app.fake_devin import FakeDevin
from app.models import SessionRequest

BASE = "https://devin.test"
ORG = "/v3/organizations/org-test"
KEY = "test-key-not-real"


def client() -> HttpDevinClient:
    settings = Settings.from_env(
        {"APP_MODE": "live", "DEVIN_API_BASE": BASE, "DEVIN_API_KEY": KEY, "DEVIN_ORG_ID": "org-test"}
    )
    return HttpDevinClient(settings, retry_backoff_s=0)


SESSION = {
    "session_id": "devin-1",
    "url": "https://app.devin.ai/sessions/1",
    "status": "running",
    "status_detail": "working",
    "pull_requests": [{"pr_url": "https://github.com/o/r/pull/1", "pr_state": "open"}],
    "acus_consumed": 2.5,
    "structured_output": {"a": 1},
    "tags": ["issue-1"],
    "updated_at": 1700000000,
}


def test_build_devin_client_modes(tmp_path):
    assert isinstance(build_devin_client(Settings.from_env({})), FakeDevin)
    live = build_devin_client(Settings.from_env({"APP_MODE": "live", "DEVIN_ORG_ID": "org-test"}))
    assert isinstance(live, HttpDevinClient)


@respx.mock(base_url=BASE)
async def test_create_and_get_session(respx_mock):
    create = respx_mock.post(f"{ORG}/sessions").mock(return_value=httpx.Response(200, json=SESSION))
    respx_mock.get(f"{ORG}/sessions/devin-1").mock(return_value=httpx.Response(200, json=SESSION))
    devin = client()
    request = SessionRequest(
        "p", "t", "pb-1", 5, tags=("issue-1",), structured_output_schema={"type": "object"}
    )
    info = await devin.create_session(request)
    sent = create.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {KEY}"
    assert httpx.Response(200, content=sent.content).json() == request.to_payload()
    info = await devin.get_session("devin-1")
    assert info.pr_urls == ("https://github.com/o/r/pull/1",)
    assert (info.status, info.status_detail, info.acus_consumed) == ("running", "working", 2.5)
    assert (
        info.structured_output == {"a": 1} and info.tags == ("issue-1",) and info.updated_at == 1700000000.0
    )
    await devin.aclose()


@respx.mock(base_url=BASE)
async def test_message_and_archive(respx_mock):
    message = respx_mock.post(f"{ORG}/sessions/devin-1/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    archive = respx_mock.post(f"{ORG}/sessions/devin-1/archive").mock(
        return_value=httpx.Response(200, json=SESSION)
    )
    devin = client()
    await devin.send_message("devin-1", "hello")
    await devin.archive_session("devin-1")
    assert httpx.Response(200, content=message.calls.last.request.content).json() == {"message": "hello"}
    assert archive.called


@respx.mock(base_url=BASE)
async def test_list_playbooks_paginates(respx_mock):
    route = respx_mock.get(f"{ORG}/playbooks").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [{"playbook_id": "pb-1", "title": "A"}],
                    "has_next_page": True,
                    "end_cursor": "c1",
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": [{"playbook_id": "pb-2", "title": "B", "macro": "!b"}],
                    "has_next_page": False,
                },
            ),
        ]
    )
    playbooks = await client().list_playbooks()
    assert [p.playbook_id for p in playbooks] == ["pb-1", "pb-2"]
    assert "after" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["after"] == "c1"


@respx.mock(base_url=BASE)
async def test_get_playbook_maps_schema(respx_mock):
    respx_mock.get(f"{ORG}/playbooks/pb-1").mock(
        return_value=httpx.Response(
            200,
            json={"playbook_id": "pb-1", "title": "T", "macro": "!t", "structured_output_schema": {"x": 1}},
        )
    )
    playbook = await client().get_playbook("pb-1")
    assert (playbook.macro, playbook.structured_output_schema) == ("!t", {"x": 1})


@respx.mock(base_url=BASE)
async def test_retries_once_on_retryable_status(respx_mock):
    route = respx_mock.get(f"{ORG}/sessions/devin-1").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=SESSION)]
    )
    assert (await client().get_session("devin-1")).session_id == "devin-1"
    assert route.call_count == 2


@respx.mock(base_url=BASE)
async def test_second_failure_raises(respx_mock):
    route = respx_mock.get(f"{ORG}/sessions/devin-1").mock(return_value=httpx.Response(503))
    with pytest.raises(DevinError) as info:
        await client().get_session("devin-1")
    assert route.call_count == 2 and info.value.status_code == 503


@respx.mock(base_url=BASE)
async def test_error_has_no_key_and_no_retry_on_4xx(respx_mock):
    route = respx_mock.post(f"{ORG}/sessions").mock(return_value=httpx.Response(401, text=f"bad key {KEY}"))
    with pytest.raises(DevinError) as info:
        await client().create_session(SessionRequest("p", "t", "pb", 5))
    assert route.call_count == 1
    assert KEY not in str(info.value) and KEY not in repr(info.value)
    assert (info.value.status_code, info.value.method) == (401, "POST")
