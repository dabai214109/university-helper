"""Failure-contract tests for the Adapter and Yanxi answer providers."""

from unittest.mock import patch

import pytest
import requests

from app.services.course.chaoxing.answer_base import Tiku, TikuFallback
from app.services.course.chaoxing.answer_providers import TikuAdapter, TikuYanxi

_TOKEN_SENTINEL = "TIKU_PROVIDER_TOKEN_SENTINEL"
_RESPONSE_SENTINEL = "TIKU_PROVIDER_RESPONSE_SENTINEL"
_QUESTION = {"title": "中国的首都是哪里？", "type": "single", "options": "A.北京\nB.上海"}


class _Response:
    def __init__(self, payload=None, status_code=200, text=_RESPONSE_SENTINEL):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


def _logs(mock_logger):
    return "\n".join(str(call.args[0]) for call in mock_logger.mock_calls if call.args)


def _adapter():
    provider = TikuAdapter()
    provider.api = f"https://93.184.216.34/query?token={_TOKEN_SENTINEL}"
    return provider


def _yanxi():
    provider = TikuYanxi()
    provider.api = f"https://yanxi.example.invalid/query?token={_TOKEN_SENTINEL}"
    provider._token = _TOKEN_SENTINEL
    return provider


@pytest.mark.parametrize(
    ("provider_factory", "request_path"),
    [
        (_adapter, "app.services.course.chaoxing.answer_providers.adapter.requests.post"),
        (_yanxi, "app.services.course.chaoxing.answer_providers.yanxi.requests.get"),
    ],
)
@pytest.mark.parametrize(
    "request_error",
    [
        requests.exceptions.ConnectionError("connection failed"),
        requests.exceptions.Timeout("request timed out"),
        requests.exceptions.RequestException("request failed"),
    ],
)
def test_network_failures_return_none_without_logging_error_details(provider_factory, request_path, request_error):
    provider = provider_factory()

    with (
        patch(request_path, side_effect=request_error),
        patch(f"{request_path.rsplit('.', 2)[0]}.logger") as mock_logger,
    ):
        assert provider._query(_QUESTION) is None

    logs = _logs(mock_logger)
    assert provider.name in logs
    assert _TOKEN_SENTINEL not in logs
    assert _RESPONSE_SENTINEL not in logs
    assert str(request_error) not in logs


@pytest.mark.parametrize(
    ("provider_factory", "request_path"),
    [
        (_adapter, "app.services.course.chaoxing.answer_providers.adapter.requests.post"),
        (_yanxi, "app.services.course.chaoxing.answer_providers.yanxi.requests.get"),
    ],
)
def test_invalid_json_returns_none_without_logging_response(provider_factory, request_path):
    provider = provider_factory()
    invalid_json = ValueError(f"invalid response: {_RESPONSE_SENTINEL}")

    with (
        patch(request_path, return_value=_Response(invalid_json)),
        patch(f"{request_path.rsplit('.', 2)[0]}.logger") as mock_logger,
    ):
        assert provider._query(_QUESTION) is None

    logs = _logs(mock_logger)
    assert provider.name in logs
    assert _TOKEN_SENTINEL not in logs
    assert _RESPONSE_SENTINEL not in logs


@pytest.mark.parametrize(
    ("provider_factory", "request_path"),
    [
        (_adapter, "app.services.course.chaoxing.answer_providers.adapter.requests.post"),
        (_yanxi, "app.services.course.chaoxing.answer_providers.yanxi.requests.get"),
    ],
)
def test_http_failure_returns_none_without_logging_response(provider_factory, request_path):
    provider = provider_factory()

    with (
        patch(request_path, return_value=_Response(status_code=503)),
        patch(f"{request_path.rsplit('.', 2)[0]}.logger") as mock_logger,
    ):
        assert provider._query(_QUESTION) is None

    logs = _logs(mock_logger)
    assert provider.name in logs
    assert "503" in logs
    assert _TOKEN_SENTINEL not in logs
    assert _RESPONSE_SENTINEL not in logs


def test_adapter_success_preserves_answer_format():
    provider = _adapter()
    response = _Response({"answer": {"bestAnswer": ["北京", "Beijing"]}})

    with patch("app.services.course.chaoxing.answer_providers.adapter.requests.post", return_value=response):
        assert provider._query(_QUESTION) == "北京\nBeijing"


def test_yanxi_success_preserves_answer_and_updates_remaining_times():
    provider = _yanxi()
    response = _Response({"code": 1, "data": {"answer": " 北京 ", "times": 97}})

    with patch("app.services.course.chaoxing.answer_providers.yanxi.requests.get", return_value=response):
        assert provider._query(_QUESTION) == "北京"

    assert provider._times == 97


def test_yanxi_rotates_to_the_next_token_when_the_config_is_invalid():
    """配置失效（被重置/过期）以前被当成「未命中」，换 token 的逻辑根本不会触发。

    线上表现就是每道题都记一句「未命中」，明明配了 token 却全部失败。
    """
    provider = _yanxi()
    provider._conf = {"tokens": "expired,good"}
    provider._token_index = 0
    provider._token = "expired"
    rotated: list[str] = []

    def fake_load_token():
        rotated.append(provider._conf["tokens"].split(",")[provider._token_index])
        provider._token = provider._conf["tokens"].split(",")[provider._token_index]

    provider.load_token = fake_load_token
    expired = _Response(
        {
            "code": 0,
            "data": {"answer": "⚠️此题库配置已经无效 (或被重置)！请重新复制题库配置"},
            "message": "请求失败",
        }
    )
    good = _Response({"code": 1, "data": {"answer": "北京", "times": 90}})

    with patch(
        "app.services.course.chaoxing.answer_providers.yanxi.requests.get",
        side_effect=[expired, good],
    ):
        assert provider._query(_QUESTION) == "北京"

    assert rotated == ["good"], "失效后必须换到下一个 token 再试"


def test_yanxi_survives_a_second_exhaustion_attempt():
    """第一次用尽后 index 停在 len；第二次换 token 曾越过守卫抛 IndexError。

    那个 IndexError 没人接，直接把调用它的章节整个炸掉 —— 线上表现为一门课
    28 个章节全部 "Chapter processing crashed"。
    """
    provider = _yanxi()
    provider._conf = {"tokens": "only-expired"}
    provider._token_index = 0
    provider._token = "only-expired"
    expired = _Response(
        {"code": 0, "data": {"answer": "⚠️此题库配置已经无效 (或被重置)！"}, "message": "请求失败"}
    )

    with patch(
        "app.services.course.chaoxing.answer_providers.yanxi.requests.get",
        return_value=expired,
    ):
        assert provider._query(_QUESTION) is None   # 第一次：用尽
        assert provider._query(_QUESTION) is None   # 第二次：不能抛异常
        assert provider._query(_QUESTION) is None

    assert provider._tokens_exhausted is True


def test_yanxi_stops_retrying_once_the_tokens_are_exhausted():
    """用尽后不该每题再打一次请求，白白增加延迟。"""
    provider = _yanxi()
    provider._conf = {"tokens": "only-expired"}
    provider._token_index = 0
    provider._token = "only-expired"
    expired = _Response(
        {"code": 0, "data": {"answer": "⚠️此题库配置已经无效 (或被重置)！"}, "message": "请求失败"}
    )

    with patch(
        "app.services.course.chaoxing.answer_providers.yanxi.requests.get",
        return_value=expired,
    ) as mock_get:
        provider._query(_QUESTION)
        calls_after_first = mock_get.call_count
        provider._query(_QUESTION)
        provider._query(_QUESTION)

    assert mock_get.call_count == calls_after_first, "用尽后不应再发起请求"


def test_load_token_past_the_end_raises_permission_error_not_index_error():
    """守卫必须用 >=。

    轮换会先自增再取 token，所以「第二次用尽」时下标是 len+1。用 == 判断会
    放行，于是 token_list[len+1] 抛 IndexError —— 调用方只接 PermissionError，
    IndexError 就一路冒到章节层。
    """
    provider = _yanxi()
    provider._conf = {"tokens": "a,b"}
    provider._token_index = 5

    with pytest.raises(PermissionError):
        provider.load_token()


def test_yanxi_reports_exhausted_tokens_instead_of_a_plain_miss():
    """所有 token 都不可用时，日志要能指向真正的原因和操作。"""
    provider = _yanxi()
    provider._conf = {"tokens": "only-expired"}
    provider._token_index = 0
    provider._token = "only-expired"
    expired = _Response(
        {"code": 0, "data": {"answer": "⚠️此题库配置已经无效 (或被重置)！"}, "message": "请求失败"}
    )

    with (
        patch(
            "app.services.course.chaoxing.answer_providers.yanxi.requests.get",
            return_value=expired,
        ),
        patch("app.services.course.chaoxing.answer_providers.yanxi.logger") as mock_logger,
    ):
        assert provider._query(_QUESTION) is None

    logs = _logs(mock_logger)
    assert "tk.enncy.cn" in logs, "要告诉用户去哪里重新获取配置"
    assert "未命中" not in logs, "配置失效不该被报成未命中"
    assert _TOKEN_SENTINEL not in logs


@pytest.mark.parametrize(
    ("provider_factory", "request_path", "payload"),
    [
        (_adapter, "app.services.course.chaoxing.answer_providers.adapter.requests.post", {}),
        (_yanxi, "app.services.course.chaoxing.answer_providers.yanxi.requests.get", {"code": 1, "data": {}}),
    ],
)
def test_missing_response_fields_are_provider_misses_without_response_logging(provider_factory, request_path, payload):
    provider = provider_factory()

    with (
        patch(request_path, return_value=_Response(payload)),
        patch(f"{request_path.rsplit('.', 2)[0]}.logger") as mock_logger,
    ):
        assert provider._query(_QUESTION) is None

    logs = _logs(mock_logger)
    assert provider.name in logs
    assert _TOKEN_SENTINEL not in logs
    assert _RESPONSE_SENTINEL not in logs


class _FallbackHit(Tiku):
    def __init__(self):
        super().__init__()
        self.name = "fallback-hit"

    def _query(self, q_info):
        return "A"


@pytest.mark.parametrize(
    ("provider_factory", "request_path"),
    [
        (_adapter, "app.services.course.chaoxing.answer_providers.adapter.requests.post"),
        (_yanxi, "app.services.course.chaoxing.answer_providers.yanxi.requests.get"),
    ],
)
def test_external_failure_allows_fallback_to_next_provider(provider_factory, request_path):
    provider = provider_factory()
    fallback = TikuFallback([provider, _FallbackHit()])
    fallback.DISABLE = False

    with patch(request_path, side_effect=requests.exceptions.ConnectionError("offline")):
        assert fallback._query(_QUESTION) == "A"
