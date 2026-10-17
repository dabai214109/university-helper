from typing import Any


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _as_csv_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ",".join(items)
    return str(value).strip()


def normalize_tiku_config(raw_config: Any) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        return {}

    config: dict[str, Any] = dict(raw_config)

    token_text = _as_csv_text(config.get("tokens") or config.get("token"))
    if token_text:
        config["tokens"] = token_text

    # 前端只有一个「题库 Token」输入框，但各个 provider 读的是不同的键：
    #   言溪 / Like      -> tokens
    #   硅基流动          -> siliconflow_key
    #   AI 智能答题       -> key
    # 不路由的话，用户把硅基流动的 key 填进「Token」，到了引擎里
    # self._conf["siliconflow_key"] 直接 KeyError，provider 被静默禁用。
    # 只有用户没显式提供对应键时才路由，避免覆盖高级用户的手工配置。
    if token_text:
        provider_names = [n.strip() for n in str(config.get("provider") or "").split(",") if n.strip()]
        if "SiliconFlow" in provider_names and not config.get("siliconflow_key"):
            config["siliconflow_key"] = token_text
        if "AI" in provider_names and not config.get("key"):
            config["key"] = token_text

    cover_rate = config.get("coverage_threshold", config.get("cover_rate"))
    if cover_rate not in (None, ""):
        config["cover_rate"] = _as_float(cover_rate, default=0.8, minimum=0.0, maximum=1.0)

    judge_mapping = config.get("judge_mapping")
    if isinstance(judge_mapping, dict):
        true_list = _as_csv_text(judge_mapping.get("correct"))
        false_list = _as_csv_text(judge_mapping.get("wrong"))
        if true_list:
            config["true_list"] = true_list
        if false_list:
            config["false_list"] = false_list

    if "submit_mode" in config:
        submit_mode = str(config.get("submit_mode") or "").strip().lower()
        if submit_mode:
            config["submit"] = "true" if submit_mode in {"submit", "auto", "direct"} else "false"

    # 部分题库必须带 token 才能用。provider 可能是逗号分隔的**回退链**
    # （前端默认就是 "TikuYanxi,TikuGo"），所以必须逐个判断，不能拿整串去
    # 比对集合 —— 那样默认链永远匹配不上，结果是一个无 token 的言溪被留在
    # 链首，每次查询都失败一次后才回退到下一个题库。
    provider = str(config.get("provider") or "").strip()
    token_required_providers = {"TikuYanxi", "TikuLike"}
    if provider and not token_text:
        names = [name.strip() for name in provider.split(",") if name.strip()]
        kept = [name for name in names if name not in token_required_providers]
        if len(kept) != len(names):
            # 丢掉需要 token 的那几个；全被丢掉就整个移除 provider。
            if kept:
                config["provider"] = ",".join(kept)
            else:
                config.pop("provider", None)

    return config
