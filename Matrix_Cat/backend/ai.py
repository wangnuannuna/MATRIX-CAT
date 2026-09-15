# -*- coding: utf-8 -*-
"""AI 文案优化:把用户的大白话/草稿改写成小红书爆款(标题/正文/话题)。

**默认走国内免费的智谱 GLM-4-Flash**(OpenAI 兼容接口,httpx 直连,不吃 VPN)。
换供应商:MATRIXCAT_AI_PROVIDER = zhipu(默认) / dashscope / siliconflow / moonshot / deepseek / claude / openai。
key:MATRIXCAT_AI_KEY(通用),或各家专属环境变量(如 ZHIPU_API_KEY);claude 用 MATRIXCAT_ANTHROPIC_API_KEY。
模型:MATRIXCAT_AI_MODEL 覆盖,留空用供应商默认。
"""
import json
import logging
import os

from .config import settings

logger = logging.getLogger(__name__)

# 供应商注册表:base=chat/completions 全路径、默认模型、专属 key 环境变量、展示名、免费领 key 地址。
_PROVIDERS = {
    "zhipu": {
        "base": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash", "key_env": "ZHIPU_API_KEY",
        "label": "智谱 GLM-4-Flash(免费)", "signup": "bigmodel.cn(免费注册领 key)"},
    "dashscope": {
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-turbo", "key_env": "DASHSCOPE_API_KEY",
        "label": "通义千问 qwen-turbo(有免费额度)", "signup": "dashscope.console.aliyun.com"},
    "siliconflow": {
        "base": "https://api.siliconflow.cn/v1/chat/completions",
        "model": "Qwen/Qwen2.5-7B-Instruct", "key_env": "SILICONFLOW_API_KEY",
        "label": "硅基流动 Qwen2.5-7B(免费)", "signup": "siliconflow.cn(送额度)"},
    "moonshot": {
        "base": "https://api.moonshot.cn/v1/chat/completions",
        "model": "moonshot-v1-8k", "key_env": "MOONSHOT_API_KEY",
        "label": "Kimi moonshot(有免费额度)", "signup": "platform.moonshot.cn"},
    "deepseek": {
        "base": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek(极便宜·非免费)", "signup": "platform.deepseek.com"},
}

_SYSTEM = """你是资深小红书爆款文案操盘手。用户给你一段内容草稿或原始想法,你把它打磨成能"爆流"的小红书图文文案。

要求:
- 标题:不超过 20 字,强钩子(好奇/痛点/利益点),可带 1-2 个 emoji,别为了标题党编造虚假信息。
- 正文:口语、真诚、有画面感和情绪价值;短句分段、适度 emoji;结尾自然引导互动(评论/收藏/点关注)。别写成生硬广告。
- 话题:3-6 个,贴合内容 + 平台高流量标签,不要带 # 号,用数组返回。
- 保持用户原意与真实信息,绝不编造不存在的事实、数据、体验。
- 全部中文输出。

只返回一个 JSON 对象,形如:{"title":"...","body":"...","topics":["...","..."]},不要有 JSON 以外的任何文字。"""


class AIError(Exception):
    """AI 优化出错,消息是给前端看的人话。"""


def _provider() -> str:
    return (settings.ai_provider or "zhipu").strip().lower()


def _cfg():
    """当前供应商配置。claude 返回 None(单独分支);openai 用自定义地址。"""
    p = _provider()
    if p == "claude":
        return None
    if p == "openai":
        return {"base": (settings.ai_base_url or "").rstrip("/"),
                "model": settings.ai_model or "gpt-3.5-turbo", "key_env": "OPENAI_API_KEY",
                "label": "OpenAI 兼容(自定义)", "signup": ""}
    return _PROVIDERS.get(p)


def _openai_key(cfg) -> str:
    return settings.ai_key or (os.getenv(cfg["key_env"]) if cfg else "") or ""


def available() -> bool:
    if _provider() == "claude":
        return bool(settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
    cfg = _cfg()
    return bool(cfg and cfg.get("base") and _openai_key(cfg))


def info() -> dict:
    """给前端:当前供应商 / 模型 / 是否已配 key / 免费领 key 地址。"""
    p = _provider()
    if p == "claude":
        return {"provider": "claude", "label": "Claude(海外·需付费)",
                "model": settings.ai_model or "claude-opus-4-8",
                "available": available(), "signup": "platform.claude.com"}
    cfg = _cfg() or {}
    return {"provider": p, "label": cfg.get("label", p),
            "model": settings.ai_model or cfg.get("model", ""),
            "available": available(), "signup": cfg.get("signup", "")}


def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t[:4].lower() == "json":
            t = t[4:].strip()
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        t = t[s:e + 1]
    return json.loads(t)


def _normalize(data: dict) -> dict:
    topics = data.get("topics") or []
    if not isinstance(topics, list):
        topics = [str(topics)]
    return {"title": str(data.get("title") or "").strip(),
            "body": str(data.get("body") or "").strip(),
            "topics": [str(t).lstrip("#").strip() for t in topics if str(t).strip()]}


def optimize_content(raw: str, platform: str = "xhs", kind: str = "image") -> dict:
    """把大白话 raw 改写成 {title, body, topics}。失败抛 AIError(人话)。"""
    raw = (raw or "").strip()
    if not raw:
        raise AIError("先写点想发的内容,再让 AI 优化")
    if len(raw) > 4000:
        raw = raw[:4000]
    kind_cn = "视频" if kind == "video" else "图文"
    user = (f"平台:小红书。内容类型:{kind_cn}。\n\n"
            f"我的内容草稿:\n{raw}\n\n请把它打磨成能爆流的小红书文案,只返回 JSON。")

    if _provider() == "claude":
        return _via_claude(user)
    cfg = _cfg()
    if not cfg or not cfg.get("base"):
        raise AIError(f"未知/未配置 AI 供应商:{_provider()}(可选 zhipu/dashscope/siliconflow/moonshot/deepseek/claude)")
    key = _openai_key(cfg)
    if not key:
        raise AIError(f"{cfg['label']} 未配置:设 MATRIXCAT_AI_KEY(或 {cfg['key_env']})后重启后端。免费领 key:{cfg['signup']}")
    return _via_openai_compat(cfg, key, user)


def _via_openai_compat(cfg, key, user) -> dict:
    """OpenAI 兼容 /chat/completions。境内站直连(trust_env=False)绕开 VPN。"""
    import httpx

    model = settings.ai_model or cfg["model"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "temperature": 0.85, "max_tokens": 2000,
               "messages": [{"role": "system", "content": _SYSTEM},
                            {"role": "user", "content": user}],
               "response_format": {"type": "json_object"}}

    def _post(pl):
        with httpx.Client(timeout=60, trust_env=False) as c:
            return c.post(cfg["base"], headers=headers, json=pl)

    try:
        r = _post(payload)
        if r.status_code == 400:                    # 有的家不认 response_format,去掉重试一次
            payload.pop("response_format", None)
            r = _post(payload)
    except Exception as e:  # noqa: BLE001
        raise AIError(f"连不上 {cfg['label']}:{str(e)[:120]}")

    if r.status_code == 401 or r.status_code == 403:
        raise AIError(f"{cfg['label']} key 无效/无权限,检查 MATRIXCAT_AI_KEY")
    if r.status_code == 429:
        raise AIError(f"{cfg['label']} 调用太频繁或额度用尽,稍后再试")
    if r.status_code != 200:
        raise AIError(f"{cfg['label']} 报错 {r.status_code}:{_err_msg(r)}")

    try:
        content = r.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        raise AIError("AI 返回结构异常,请重试")
    try:
        return _normalize(_extract_json(content))
    except Exception:  # noqa: BLE001
        raise AIError("AI 返回不是有效 JSON,请重试")


def _err_msg(r) -> str:
    try:
        d = r.json()
        e = d.get("error") or d
        return str((e.get("message") if isinstance(e, dict) else None) or d)[:160]
    except Exception:  # noqa: BLE001
        return (r.text or "")[:160]


def _via_claude(user) -> dict:
    """Claude(anthropic SDK)。仅 provider=claude 时走这。"""
    try:
        import anthropic
    except ImportError:
        raise AIError("后端没装 anthropic SDK(pip install anthropic)")
    key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY") or ""
    if not key:
        raise AIError("Claude 未配置:设 MATRIXCAT_ANTHROPIC_API_KEY 后重启后端")
    schema = {"type": "object", "properties": {
        "title": {"type": "string"}, "body": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}}},
        "required": ["title", "body", "topics"], "additionalProperties": False}
    try:
        resp = anthropic.Anthropic(api_key=key).messages.create(
            model=settings.ai_model or "claude-opus-4-8", max_tokens=6000, system=_SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": "medium"},
            messages=[{"role": "user", "content": user}])
    except Exception as e:  # noqa: BLE001
        raise AIError(f"Claude 调用失败:{type(e).__name__}: {str(e)[:120]}")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    try:
        return _normalize(_extract_json(text))
    except Exception:  # noqa: BLE001
        raise AIError("AI 返回格式异常,请重试")
