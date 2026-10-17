from app.services.course.chaoxing.payload_mapper import normalize_tiku_config


def test_normalize_tiku_config_maps_frontend_fields():
    normalized = normalize_tiku_config(
        {
            "provider": "TikuYanxi",
            "token": "token-a,token-b",
            "coverage_threshold": 0.9,
            "judge_mapping": {"correct": ["对", "正确"], "wrong": ["错", "错误"]},
            "submit_mode": "submit",
        }
    )

    assert normalized["provider"] == "TikuYanxi"
    assert normalized["tokens"] == "token-a,token-b"
    assert normalized["cover_rate"] == 0.9
    assert normalized["true_list"] == "对,正确"
    assert normalized["false_list"] == "错,错误"
    assert normalized["submit"] == "true"


def test_normalize_tiku_config_disables_token_provider_without_token():
    normalized = normalize_tiku_config(
        {
            "provider": "TikuYanxi",
            "token": "",
            "coverage_threshold": 0.75,
            "submit_mode": "save",
        }
    )

    assert "provider" not in normalized
    assert "tokens" not in normalized
    assert normalized["cover_rate"] == 0.75
    assert normalized["submit"] == "false"


# ── 题库配置：三处修复的回归测试 ──────────────────────────────────────────────


def test_judgement_lists_merge_with_defaults_instead_of_replacing():
    """填「对,正确,是」不能把 T/True 挤掉，否则题库答 T 会退化成随机选择。

    这个断言必须走真正的判断路径（judgement_select），而不是只看列表内容 ——
    只看列表的话，旧代码在「用户没配」时返回默认值也能蒙混过关。
    """
    from app.services.course.chaoxing.answer_base import Tiku

    tiku = Tiku()
    tiku._conf = {
        "provider": "TikuGo",
        "true_list": "对,正确,是",
        "false_list": "错,错误,否",
    }
    tiku.init_tiku()

    # 用户配置生效
    assert tiku.judgement_select("是") is True
    assert tiku.judgement_select("否") is False
    # 默认的英文/符号写法仍然认得 —— 这正是旧代码丢掉的部分
    assert tiku.judgement_select("T") is True
    assert tiku.judgement_select("True") is True
    assert tiku.judgement_select("F") is False
    assert tiku.judgement_select("False") is False


def test_judgement_merge_handles_empty_user_config():
    from app.services.course.chaoxing.answer_base import _merge_judgement_list

    assert _merge_judgement_list(None, ["正确", "对"]) == ["正确", "对"]
    assert _merge_judgement_list("", ["正确", "对"]) == ["正确", "对"]


def test_judgement_merge_ignores_whitespace_and_blanks():
    from app.services.course.chaoxing.answer_base import _merge_judgement_list

    merged = _merge_judgement_list(" 是 , ,否 ", ["正确", "对"])

    assert merged == ["是", "否", "正确", "对"]


def test_token_check_handles_a_comma_separated_provider_chain():
    """默认链是 "TikuYanxi,TikuGo"；无 token 时必须把言溪摘掉、留下 GO。"""
    normalized = normalize_tiku_config(
        {"provider": "TikuYanxi,TikuGo", "token": "", "coverage_threshold": 0.9}
    )

    assert normalized.get("provider") == "TikuGo"


def test_token_check_drops_provider_entirely_when_all_need_a_token():
    normalized = normalize_tiku_config(
        {"provider": "TikuYanxi,TikuLike", "token": ""}
    )

    assert "provider" not in normalized


def test_token_check_keeps_everything_when_a_token_is_supplied():
    normalized = normalize_tiku_config(
        {"provider": "TikuYanxi,TikuGo", "token": "abc"}
    )

    assert normalized["provider"] == "TikuYanxi,TikuGo"
    assert normalized["tokens"] == "abc"


def test_siliconflow_gets_the_key_from_the_shared_token_field():
    """前端只有一个 Token 框，硅基流动读的是 siliconflow_key。"""
    normalized = normalize_tiku_config({"provider": "SiliconFlow", "token": "sk-test"})

    assert normalized["siliconflow_key"] == "sk-test"
    assert normalized["tokens"] == "sk-test"


def test_ai_gets_the_key_from_the_shared_token_field():
    normalized = normalize_tiku_config({"provider": "AI", "token": "sk-ai"})

    assert normalized["key"] == "sk-ai"


def test_explicit_key_is_not_overwritten_by_the_shared_token():
    """高级用户手填 siliconflow_key 时，路由不能覆盖它。"""
    normalized = normalize_tiku_config(
        {"provider": "SiliconFlow", "token": "shared", "siliconflow_key": "explicit"}
    )

    assert normalized["siliconflow_key"] == "explicit"


def test_no_token_means_no_key_routing():
    normalized = normalize_tiku_config({"provider": "AI", "token": ""})

    assert "key" not in normalized
    assert "siliconflow_key" not in normalized


def test_task_store_encrypts_ai_keys():
    """API Key 是凭据，落库前必须加密 —— 漏了就是明文入库。"""
    from app.services.course.task_store import _SENSITIVE_FIELDS

    for field in ("key", "siliconflow_key", "tokens"):
        assert field in _SENSITIVE_FIELDS, f"{field} 必须加进敏感字段清单"
