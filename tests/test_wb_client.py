import httpx
import pytest

from wb_web.wb.client import RetryPolicy, WildberriesClient
from wb_web.wb.errors import (
    WildberriesAuthenticationError,
    WildberriesRateLimitError,
    WildberriesResponseError,
)


def make_client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, follow_redirects=False)
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = WildberriesClient("test-token", http_client=http, sleep=record_sleep, **kwargs)
    client._recorded_sleeps = sleeps  # expose for assertions
    return client, sleeps


def seller_payload():
    return {"name": "ИП Тестов", "sid": "seller-1", "tin": "123", "country": "KG"}


def feedback_payload(feedback_id="fb1", *, with_answer=False, rating=5, text="отличный товар"):
    payload = {
        "id": feedback_id,
        "text": text,
        "pros": "качество",
        "cons": "",
        "productValuation": rating,
        "createdDate": "2026-11-10T10:00:00Z",
        "productDetails": {"nmId": 42, "productName": "Кроссовки", "supplierArticle": "SKU-1"},
    }
    if with_answer:
        payload["answer"] = {"text": "уже ответили", "state": "published", "editable": False}
    return payload


@pytest.mark.asyncio
async def test_seller_info_sends_raw_token() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=seller_payload())

    client, _ = make_client(handler)
    info = await client.get_seller_info()
    assert info.name == "ИП Тестов"
    assert info.seller_id == "seller-1"
    assert seen["auth"] == "test-token"  # raw, no Bearer


@pytest.mark.asyncio
async def test_401_raises_authentication_error() -> None:
    client, _ = make_client(lambda req: httpx.Response(401, json={"errorText": "bad token"}))
    with pytest.raises(WildberriesAuthenticationError):
        await client.get_seller_info()


@pytest.mark.asyncio
async def test_429_retries_with_retry_after_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"errorText": "slow down"})
        return httpx.Response(200, json=seller_payload())

    client, sleeps = make_client(handler)
    info = await client.get_seller_info()
    assert info.seller_id == "seller-1"
    assert calls["n"] == 2
    assert sleeps == [3.0]


@pytest.mark.asyncio
async def test_429_exhausts_and_raises() -> None:
    client, _ = make_client(
        lambda req: httpx.Response(429, json={"errorText": "slow down"}),
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01),
    )
    with pytest.raises(WildberriesRateLimitError):
        await client.get_seller_info()


@pytest.mark.asyncio
async def test_answer_feedback_already_answered_posts_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # no POST must happen
        return httpx.Response(200, json={"error": False, "data": feedback_payload(with_answer=True)})

    client, _ = make_client(handler)
    result = await client.answer_feedback("fb1", "Спасибо за отзыв!")
    assert result.already_answered is True
    assert result.published is False


@pytest.mark.asyncio
async def test_answer_feedback_success() -> None:
    state = {"posted": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["posted"] = True
            assert request.headers["Authorization"] == "test-token"
            return httpx.Response(204)
        return httpx.Response(200, json={"error": False, "data": feedback_payload()})

    client, _ = make_client(handler)
    result = await client.answer_feedback("fb1", "Спасибо за отзыв! Будем лучше.")
    assert state["posted"] is True
    assert result.published is True


@pytest.mark.asyncio
async def test_answer_feedback_ambiguous_timeout_reconciles_via_recheck() -> None:
    state = {"stage": "before", "posted_text": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["stage"] = "after"
            state["posted_text"] = "да да да да да"
            raise httpx.TimeoutException("read timed out")
        if state["stage"] == "after":
            answered = feedback_payload()
            answered["answer"] = {"text": "да да да да да", "state": "published", "editable": False}
            return httpx.Response(200, json={"error": False, "data": answered})
        return httpx.Response(200, json={"error": False, "data": feedback_payload()})

    client, _ = make_client(handler)
    result = await client.answer_feedback("fb1", "да да да да да")
    assert result.published is True
    assert result.recovered_after_ambiguous_error is True


@pytest.mark.asyncio
async def test_list_feedbacks_parses_page() -> None:
    payload = {
        "error": False,
        "data": {
            "feedbacks": [feedback_payload("fb1"), feedback_payload("fb2", rating=1)],
            "countUnanswered": 2,
        },
    }
    client, _ = make_client(lambda req: httpx.Response(200, json=payload))
    page = await client.list_feedbacks(is_answered=False, take=10, skip=0)
    assert len(page.feedbacks) == 2
    assert page.feedbacks[0].product.nm_id == 42
    assert page.feedbacks[1].rating == 1
    assert page.unanswered_count == 2


@pytest.mark.asyncio
async def test_feedback_missing_id_raises_response_error() -> None:
    payload = {"error": False, "data": {"text": "нет id"}}
    client, _ = make_client(lambda req: httpx.Response(200, json=payload))
    with pytest.raises(WildberriesResponseError):
        await client.get_feedback("fb1")
