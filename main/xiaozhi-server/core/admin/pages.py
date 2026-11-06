"""管理后台各页面的读取与保存逻辑（大模型、语音识别、语音合成、意图识别）。

和 config_store 的通用条目编辑不同，这里按页面组织：只暴露后台要管理的内容，
并对取值做校验（例如思考力度只能是 low / medium / high），错误信息直接给用户看。
所有函数只读写配置文件，不碰运行中的服务；热加载由 AdminHandler 负责。
"""

import re
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from config.config_loader import get_project_dir
from core.admin import catalog, config_store, plugin_settings
from core.agent import asr_text
from core.utils import language

CLAUDE_CLI_ENTRY = "ClaudeCLI"
CLAUDE_EFFORT_VALUES = {"", "low", "medium", "high", "xhigh", "max"}
CLAUDE_PERMISSION_VALUES = {"default", "acceptEdits", "plan", "auto"}
TOOL_NAME = re.compile(r"^[A-Za-z][\w:.*()\-]*$")
URL = re.compile(r"^https?://\S+$")


def _selected(merged: dict, section: str):
    return (merged.get("selected_module") or {}).get(section)


def _entry(merged: dict, section: str, name: str) -> dict:
    entry = (merged.get(section) or {}).get(name)
    return entry if isinstance(entry, dict) else {}


def _text(value) -> str:
    return "" if config_store.is_placeholder(value) else str(value).strip()


# ---------- 大模型 ----------

def _url_field(entry: dict) -> str:
    """上游有的条目用 url，有的用 base_url（openai 类型两个都认，base_url 优先）。"""
    return "url" if "url" in entry and "base_url" not in entry else "base_url"


def describe_vendor(vendor: dict, merged: dict) -> dict:
    entry = _entry(merged, "LLM", vendor["entry"])
    key = entry.get("api_key")
    base_url = _text(entry.get(_url_field(entry))) or vendor["base_url"]
    model = _text(entry.get("model_name")) or (vendor["models"][0] if vendor["models"] else "")
    key_set = not config_store.is_placeholder(key)
    exists = bool(entry) and entry.get("type") == "openai"
    return {
        "id": vendor["id"],
        "label": vendor["label"],
        "entry": vendor["entry"],
        "base_url": base_url,
        "model": model,
        "models": vendor["models"],
        "hint": vendor.get("hint", ""),
        "key_set": key_set,
        "key_hint": config_store.mask_key(key),
        # 自定义服务（本机代理、LM Studio）可能不需要 Key
        "configured": exists and bool(base_url and model) and (key_set or vendor["id"] == "custom"),
        "conflict": bool(entry) and entry.get("type") != "openai",
    }


def llm_kind(merged: dict, name: str = None) -> str:
    name = name or _selected(merged, "LLM")
    if config_store.entry_type(merged, "LLM", name) == "claude_cli":
        return "cli"
    if config_store.entry_type(merged, "LLM", name) == "grok_bot":
        return "grok"
    if name in catalog.VENDOR_BY_ENTRY and config_store.entry_type(merged, "LLM", name) == "openai":
        return "api"
    return "other"


def claude_level(entry: dict) -> str:
    """从配置反推权限级别；工具组合和三档都对不上时返回 custom（手动改过配置）。"""
    mode = entry.get("permission_mode") or "default"
    tools = [str(t) for t in entry.get("allowed_tools") or []]
    core = sorted(t for t in tools if t not in catalog.CLAUDE_WEB_TOOLS)
    for level in catalog.CLAUDE_LEVELS:
        if level["mode"] == mode and core == sorted(level["tools"]):
            return level["value"]
    return "custom"


def claude_web(entry: dict) -> bool:
    tools = entry.get("allowed_tools") or []
    return all(t in tools for t in catalog.CLAUDE_WEB_TOOLS)


def describe_claude_cli(merged: dict) -> dict:
    entry = _entry(merged, "LLM", CLAUDE_CLI_ENTRY)
    values = {}
    for name, value in entry.items():
        if name == "type":
            continue
        kind = config_store.field_kind(name, value)
        if kind in ("text", "number", "bool"):
            values[name] = "" if kind == "text" and config_store.is_placeholder(value) else value
        elif kind == "list":
            values[name] = [str(v) for v in value or []]
    return {
        "exists": entry.get("type") == "claude_cli",
        "values": values,
        "level": claude_level(entry),
        "web": claude_web(entry),
        "options": {
            "model": catalog.CLAUDE_MODELS,
            "effort": catalog.CLAUDE_EFFORTS,
            "level": [{k: lv[k] for k in ("value", "label", "desc")} for lv in catalog.CLAUDE_LEVELS],
            "tools": catalog.CLAUDE_TOOLS,
        },
    }


def describe_llm(merged: dict) -> dict:
    selected = _selected(merged, "LLM")
    kind = llm_kind(merged, selected)
    vendor = catalog.VENDOR_BY_ENTRY.get(selected) if kind == "api" else None
    calls = describe_call_names(merged)
    return {
        "selected": selected,
        "kind": kind,
        "vendor": vendor["id"] if vendor else None,
        "claude": describe_claude_cli(merged),
        "vendors": [describe_vendor(v, merged) for v in catalog.LLM_VENDORS],
        "grok": {"bots": grok_entries(merged),
                 "selected": next((g for g in grok_entries(merged) if g["entry"] == selected), None)},
        # 配了称呼的模型都能叫到，"默认"只是设备刚连上时用的那个；页面据此标出每个模型的称呼
        "call_names_enabled": calls["enabled"],
        "callable": {m["entry"]: m["aliases"] for m in calls["models"] if m["aliases"]},
    }


# ---------- 大模型：Grok Bot ----------

GROK_BOT_PREFIX = "GrokBot_"
GROK_NAME_BAD = re.compile(r"[^\w一-鿿]+")


def grok_entries(merged: dict) -> list:
    """配置里已勾选的 Grok Bot：每个一个 type 为 grok_bot 的 LLM 条目。"""
    result = []
    for name, entry in (merged.get("LLM") or {}).items():
        if isinstance(entry, dict) and entry.get("type") == "grok_bot":
            result.append({"entry": str(name), "bot_id": str(entry.get("bot_id") or "").strip(),
                           "bot_name": str(entry.get("bot_name") or "").strip() or str(name)})
    return result


def grok_entry_name(bot_name: str, bot_id: str, taken: dict) -> str:
    """条目名：GrokBot_<Bot 名>（只留字母、数字、下划线和汉字）；重名时带上 id 的前几位。"""
    base = GROK_BOT_PREFIX + (GROK_NAME_BAD.sub("", bot_name or "") or bot_id[:8])
    name = base
    while name in taken and taken[name] != bot_id:
        name = f"{base}_{bot_id[:4]}" if name == base else f"{name}_"
    return name


def describe_grok_bot(merged: dict) -> dict:
    """已勾选的 Bot，以及每个 Bot 在呼名页配的称呼（没配的为空，页面上提示去配）。"""
    from core.agent import call_names

    aliases = {str(k): call_names._aliases(v) for k, v in ((merged.get("call_names") or {}).get("models") or {}).items()}
    bots = grok_entries(merged)
    for b in bots:
        b["aliases"] = aliases.get(b["entry"], [])
    return {"entries": bots, "default": _selected(merged, "LLM")}


def _default_grok_alias(bot_name: str, taken: set) -> str:
    """新勾选的 Bot 自动用它的名字做称呼（呼名页可改）；名字不能当称呼（有空格标点、太长、被占用）时不加。"""
    from core.agent import call_names

    alias = (bot_name or "").strip().lower()
    if not alias or ALIAS_BAD.search(alias) or not 2 <= len(alias) <= 20 or alias in call_names._PREFIXES or alias in taken:
        return ""
    return alias


def save_grok_bots(payload: dict, merged: dict, path: Path = None) -> list:
    """按 payload["bots"]（[{"id", "name"}]）同步条目：新勾选的建条目并自动配上 Bot 名字作称呼，
    取消的删条目（连同它的称呼）。

    返回修改了的条目名。正在作为默认模型使用的 Bot 不能取消。"""
    from core.agent import call_names

    raw = payload.get("bots")
    if not isinstance(raw, list):
        raise ValueError(language.msg(merged, "Malformed Bot list", "Bot 列表格式不对"))
    wanted = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(language.msg(merged, "Malformed Bot list", "Bot 列表格式不对"))
        bot_id = str(item.get("id") or "").strip()
        if not bot_id:
            raise ValueError(language.msg(merged, "A Bot entry is missing its id", "Bot 缺少 id"))
        wanted[bot_id] = str(item.get("name") or "").strip() or bot_id[:8]
    current = {g["bot_id"]: g for g in grok_entries(merged)}
    taken = {g["entry"]: g["bot_id"] for g in grok_entries(merged)}
    taken.update({name: "" for name in (merged.get("LLM") or {}) if name not in taken})
    selected = _selected(merged, "LLM")
    cn_cfg = merged.get("call_names") or {}
    effective_models = {str(k): call_names._aliases(v) for k, v in (cn_cfg.get("models") or call_names.DEFAULT_MODELS).items()}
    taken_aliases = {a for aliases in effective_models.values() for a in aliases}
    changed = []
    with config_store.edit_custom(path) as data:
        llm = data.setdefault("LLM", CommentedMap())
        for bot_id, name in wanted.items():
            old = current.get(bot_id)
            if old and old["bot_name"] == name:
                continue
            entry_name = old["entry"] if old else grok_entry_name(name, bot_id, taken)
            taken[entry_name] = bot_id
            block = llm.get(entry_name)
            if not isinstance(block, CommentedMap):
                block = CommentedMap()
                llm[entry_name] = block
            block["type"] = "grok_bot"
            block["bot_id"] = bot_id
            block["bot_name"] = name
            changed.append(entry_name)
            alias = _default_grok_alias(name, taken_aliases) if not old else ""
            if alias:
                # 自定义文件里还没有称呼表时，先把当前生效的（默认只有 Claude）抄进去，再加这个 Bot
                cn = data.setdefault("call_names", CommentedMap())
                models = cn.get("models")
                if not isinstance(models, CommentedMap):
                    models = CommentedMap((k, CommentedSeq(v)) for k, v in effective_models.items())
                    cn["models"] = models
                models[entry_name] = CommentedSeq([alias])
                taken_aliases.add(alias)
        for bot_id, old in current.items():
            if bot_id in wanted:
                continue
            if old["entry"] == selected:
                raise ValueError(language.msg(
                    merged, f"{old['bot_name']} is the current default model — switch to another one on the "
                            "Models page before unchecking it",
                    f"{old['bot_name']} 正在作为默认模型使用，先到“大模型”页换掉再取消"))
            llm.pop(old["entry"], None)
            models = (data.get("call_names") or {}).get("models")
            if isinstance(models, dict):
                models.pop(old["entry"], None)
            changed.append(old["entry"])
    return changed


def _check_positive_number(name: str, raw, merged: dict = None) -> None:
    try:
        if float(str(raw).strip()) <= 0:
            raise ValueError
    except ValueError:
        raise ValueError(language.msg(merged, f"{name} must be a number greater than 0",
                                      f"{name} 需要是大于 0 的数字"))


def _check_non_negative_number(name: str, raw, merged: dict = None) -> None:
    try:
        if float(str(raw).strip()) < 0:
            raise ValueError
    except ValueError:
        raise ValueError(language.msg(merged, f"{name} must be 0 or a positive number",
                                      f"{name} 需要是 0 或正数"))


def save_claude_cli(payload: dict, merged: dict, path: Path = None) -> list:
    """校验后写入 ClaudeCLI 条目，返回实际修改的字段。"""
    entry = _entry(merged, "LLM", CLAUDE_CLI_ENTRY)
    if entry.get("type") != "claude_cli":
        raise KeyError(CLAUDE_CLI_ENTRY)
    payload = dict(payload or {})
    # 权限级别和联网开关换算成 permission_mode + allowed_tools
    if "level" in payload or "web" in payload:
        level_value = payload.pop("level", None) or claude_level(entry)
        web = bool(payload.pop("web", claude_web(entry)))
        if level_value == "custom":
            tools = [t for t in entry.get("allowed_tools") or [] if t not in catalog.CLAUDE_WEB_TOOLS]
            mode = entry.get("permission_mode") or "default"
        else:
            level = catalog.CLAUDE_LEVEL_BY_VALUE.get(level_value)
            if level is None:
                raise ValueError(language.msg(merged, "Permission level must be read-only, editable or Auto",
                                              "权限级别只能是 只读、可编辑、Auto"))
            tools, mode = list(level["tools"]), level["mode"]
        payload["allowed_tools"] = tools + (catalog.CLAUDE_WEB_TOOLS if web else [])
        payload["permission_mode"] = mode
    if "model" in payload:
        model = str(payload["model"] or "").strip()
        if model and not re.fullmatch(r"[\w.\-\[\]]+", model):
            raise ValueError(language.msg(
                merged, "Model names may only contain letters, digits, dots and dashes, e.g. haiku or "
                        "claude-sonnet-5-5",
                "模型名称只能包含字母、数字、点和横线，例如 haiku 或 claude-sonnet-5-5"))
    if "effort" in payload and str(payload["effort"] or "").strip() not in CLAUDE_EFFORT_VALUES:
        raise ValueError(language.msg(merged, "Thinking effort must be low, medium, high, xhigh or max",
                                      "思考力度只能是 低、中、高、超高、最高"))
    if "permission_mode" in payload and str(payload["permission_mode"] or "").strip() not in CLAUDE_PERMISSION_VALUES:
        raise ValueError(language.msg(merged, "Unsupported permission mode", "权限模式不支持这个值"))
    if "workdir" in payload and not str(payload["workdir"] or "").strip():
        raise ValueError(language.msg(merged, "The working directory cannot be empty", "工作目录不能为空"))
    if "allowed_tools" in payload:
        tools = payload["allowed_tools"]
        items = tools if isinstance(tools, list) else re.split(r"[,，\s]+", str(tools))
        items = [str(t).strip() for t in items if str(t).strip()]
        bad = [t for t in items if not TOOL_NAME.match(t)]
        if bad:
            raise ValueError(language.msg(merged, f"Bad tool names: {', '.join(bad)}",
                                          f"工具名不对：{', '.join(bad)}"))
        payload["allowed_tools"] = items
    # 这几项填 0 有特别含义（见 claude_cli.py）：单轮、无响应超时 0 是不限制，空闲关闭 0 是每轮结束就关进程，
    # 自动接续 0 是不自动接续，后台最长 0 是不转后台（打断就停）
    for name in ("turn_timeout_seconds", "stall_timeout_seconds", "idle_timeout_seconds", "resume_window_minutes",
                 "background_max_seconds", "background_after_seconds"):
        if name in payload:
            _check_non_negative_number(name, payload[name], merged)
    if "filler_delay_seconds" in payload:
        _check_positive_number("filler_delay_seconds", payload["filler_delay_seconds"], merged)
    raw = str(payload.get("progress_interval_seconds", "")).strip()
    if raw:  # 留空表示用默认值，0 表示关闭进度提示
        try:
            if float(raw) < 0:
                raise ValueError
        except ValueError:
            raise ValueError(language.msg(
                merged, "The progress-note interval must be 0 or a positive number (0 disables it)",
                "进度提示间隔需要是 0 或正数，0 表示关闭"))
    return config_store.update_entry("LLM", CLAUDE_CLI_ENTRY, payload, merged, path)


def save_vendor(vendor_id: str, payload: dict, merged: dict, path: Path = None) -> list:
    """保存某个 API 厂商的接口地址、模型和 Key。条目不存在时按 OpenAI 兼容类型新建。"""
    vendor = catalog.VENDOR_BY_ID.get(vendor_id)
    if vendor is None:
        raise KeyError(vendor_id)
    entry = _entry(merged, "LLM", vendor["entry"])
    if entry and entry.get("type") != "openai":
        raise ValueError(language.msg(
            merged, f"{vendor['entry']} in the config is not OpenAI-compatible; rename or delete it in the "
                    "config file",
            f"配置里的 {vendor['entry']} 不是 OpenAI 兼容类型，请在配置文件里改名或删掉它"))
    payload = payload or {}
    base_url = str(payload.get("base_url") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not URL.match(base_url):
        raise ValueError(language.msg(merged, "The endpoint URL must start with http:// or https://",
                                      "接口地址需要以 http:// 或 https:// 开头"))
    if not model:
        raise ValueError(language.msg(merged, "Please fill in a model name", "请填写模型名称"))

    url_field = _url_field(entry)
    changes = {}
    if base_url != _text(entry.get(url_field)):
        changes[url_field] = base_url
    if model != _text(entry.get("model_name")):
        changes["model_name"] = model
    if api_key:
        if any(c.isspace() for c in api_key):
            raise ValueError(language.msg(merged, "The API Key cannot contain spaces or newlines",
                                          "API Key 里不能有空格或换行"))
        if api_key != entry.get("api_key"):
            changes["api_key"] = api_key
    if not entry:
        changes = {"type": "openai", **changes}
        changes.setdefault("api_key", "")
    if not changes:
        return []
    with config_store.edit_custom(path) as data:
        target = data.setdefault("LLM", CommentedMap()).setdefault(vendor["entry"], CommentedMap())
        for field, value in changes.items():
            target[field] = value
    return [f for f in changes if f != "type"]


def vendor_test_config(vendor_id: str, payload: dict, merged: dict) -> dict:
    """测试连接用的 LLM 配置：页面上还没保存的值优先，Key 留空时用已保存的。"""
    vendor = catalog.VENDOR_BY_ID.get(vendor_id)
    if vendor is None:
        raise KeyError(vendor_id)
    entry = _entry(merged, "LLM", vendor["entry"])
    payload = payload or {}
    base_url = str(payload.get("base_url") or "").strip() or _text(entry.get(_url_field(entry))) or vendor["base_url"]
    model = str(payload.get("model") or "").strip() or _text(entry.get("model_name"))
    api_key = str(payload.get("api_key") or "").strip() or _text(entry.get("api_key"))
    if not URL.match(base_url):
        raise ValueError(language.msg(merged, "The endpoint URL must start with http:// or https://",
                                      "接口地址需要以 http:// 或 https:// 开头"))
    if not model:
        raise ValueError(language.msg(merged, "Please fill in a model name", "请填写模型名称"))
    if not api_key and vendor_id != "custom":
        raise ValueError(language.msg(merged, "Please enter an API Key first", "请先填写 API Key"))
    config = {k: v for k, v in entry.items() if k not in ("url", "base_url")}
    config.update({"type": "openai", "base_url": base_url, "model_name": model,
                   "api_key": api_key or "not-needed", "max_tokens": 20, "timeout": 20})
    return config


def desired_intent(merged: dict, llm_name: str, path: Path = None):
    """切换大模型后意图识别应该是什么：Claude CLI 只能用 nointent；
    API 模型恢复用户上次在 API 模式下的选择，没有选过时用 function_call。"""
    if config_store.entry_type(merged, "LLM", llm_name) == "claude_cli":
        return "nointent"
    pref = (config_store.load_custom(path).get("admin") or {}).get("api_intent")
    if pref in catalog.INTENT_MODES:
        return pref
    return "function_call"


# ---------- 语音识别 ----------

# sherpa-onnx 各 model_type 需要的文件，第一个是主模型文件（用来判断有没有下载完整）。
# 和 core/providers/asr/sherpa_onnx_local.py 的 EXTRA_MODEL_FILES 保持一致；那边会导入 sherpa_onnx 等重的库，后台不直接引用
SHERPA_MODEL_FILES = {
    "sense_voice": ["model.int8.onnx", "tokens.txt"],
    "paraformer": ["model.int8.onnx", "tokens.txt"],
    "fire_red_asr": ["encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"],
    "fire_red_asr_ctc": ["model.int8.onnx", "tokens.txt"],
    "funasr_nano": ["llm.int8.onnx", "encoder_adaptor.int8.onnx", "embedding.int8.onnx", "Qwen3-0.6B"],
    "qwen3_asr": ["decoder.int8.onnx", "encoder.int8.onnx", "conv_frontend.onnx", "tokenizer"],
    "transducer": ["encoder*.onnx", "decoder*.onnx", "joiner*.onnx", "tokens.txt"],
}
SHERPA_MODEL_LABELS = {
    "sense_voice": "SenseVoice",
    "paraformer": "Paraformer",
    "fire_red_asr": "FireRedASR2-AED",
    "fire_red_asr_ctc": "FireRedASR2-CTC",
    "funasr_nano": "Fun-ASR-Nano",
    "qwen3_asr": "Qwen3-ASR",
    "transducer": "Zipformer Transducer",
}


def _find_model_file(model_dir: Path, name: str):
    """name 带 * 时按通配符找，优先 int8；找不到返回 None。"""
    if "*" not in name:
        path = model_dir / name
        return path if path.exists() else None
    matches = sorted(model_dir.glob(name), key=lambda p: (".int8." not in p.name, p.name))
    return matches[0] if matches else None


def asr_model_check(asr_entry: dict, merged: dict = None):
    """本地 ASR 的模型文件检查；不是本地模型时返回 None。"""
    if asr_entry.get("type") != "sherpa_onnx_local":
        return None
    files = SHERPA_MODEL_FILES.get(asr_entry.get("model_type") or "sense_voice", SHERPA_MODEL_FILES["sense_voice"])
    model_dir = Path(get_project_dir()) / (asr_entry.get("model_dir") or "")
    model = _find_model_file(model_dir, files[0]) or model_dir / files[0]
    size = model.stat().st_size if model.exists() else 0
    absent = [f for f in files if _find_model_file(model_dir, f) is None]
    ok = size > 100 * 1024 * 1024 and not absent
    if size and not absent:
        detail = f"{model.name} {size // (1024 * 1024)}MB"
    elif absent:
        detail = language.msg(merged, f"missing {model_dir / absent[0]}", f"缺少 {model_dir / absent[0]}")
    else:
        detail = language.msg(merged, f"{model} is incomplete", f"{model} 文件不完整")
    return {"ok": ok, "detail": detail}


def asr_label(entry: dict, merged: dict = None) -> str:
    if entry.get("type") != "sherpa_onnx_local":
        return language.msg(merged, "Cloud or another ASR service", "云端或其他识别服务")
    model_type = entry.get("model_type") or "sense_voice"
    name = SHERPA_MODEL_LABELS.get(model_type, model_type)
    if "x-asr" in str(entry.get("model_dir") or "").lower():
        name = language.msg(merged, "X-ASR (Chinese + English)", "X-ASR 中英混合")
    return language.msg(merged, f"Local ASR · {name} (sherpa-onnx), works offline",
                        f"本地识别 · {name}（sherpa-onnx），不需要联网")


def asr_choice(merged: dict) -> str:
    """当前用的是后台能选的哪一种：local（本地 X-ASR）、doubao，或 other（手动改了配置）。"""
    name = _selected(merged, "ASR")
    entry = _entry(merged, "ASR", name)
    if name == catalog.LOCAL_ASR_ENTRY and entry.get("type") == "sherpa_onnx_local":
        return "local"
    if name == catalog.DOUBAO_ASR_ENTRY and entry.get("type") == "doubao_stream":
        return "doubao"
    return "other"


def describe_asr(merged: dict) -> dict:
    name = _selected(merged, "ASR")
    entry = _entry(merged, "ASR", name)
    info = [{"label": language.msg(merged, "Model folder", "模型目录"),
             "value": str(entry.get("model_dir") or "")}] if entry.get("model_dir") else []
    if entry.get("model_type"):
        info.append({"label": language.msg(merged, "Model type", "模型类型"),
                     "value": str(entry["model_type"])})
    local = _entry(merged, "ASR", catalog.LOCAL_ASR_ENTRY)
    return {
        "selected": name,
        "label": asr_label(entry, merged),
        "info": info,
        "model": asr_model_check(entry, merged),
        "choice": asr_choice(merged),
        "local": {"exists": local.get("type") == "sherpa_onnx_local", "label": asr_label(local, merged),
                  "model": asr_model_check(local, merged)},
        "doubao": describe_doubao(merged),
        "vad": describe_vad(merged),
        "hotwords": describe_hotwords(merged),
        "corrections": describe_corrections(merged),
        "recent": asr_text.recent(),
    }


# ---------- 豆包流式语音识别 ----------
# 新版火山引擎控制台只给一个 API Key；旧版控制台给 APP ID + Access Token。填了 API Key 就按新版鉴权

def describe_doubao(merged: dict) -> dict:
    entry = _entry(merged, "ASR", catalog.DOUBAO_ASR_ENTRY)
    key, token = entry.get("api_key"), entry.get("access_token")
    key_set, token_set = not config_store.is_placeholder(key), not config_store.is_placeholder(token)
    appid = _text(entry.get("appid"))
    resource = _text(entry.get("resource_id")) or catalog.DOUBAO_RESOURCES[0]["value"]
    exists = entry.get("type") == "doubao_stream"
    return {
        "entry": catalog.DOUBAO_ASR_ENTRY,
        "auth": "app" if not key_set and appid else "key",
        "key_set": key_set,
        "key_hint": config_store.mask_key(key),
        "appid": appid,
        "token_set": token_set,
        "token_hint": config_store.mask_key(token),
        "resource_id": resource,
        "resources": catalog.DOUBAO_RESOURCES,
        "configured": exists and (key_set or bool(appid and token_set)),
        "conflict": bool(entry) and not exists,
    }


def _doubao_values(payload: dict, entry: dict, merged: dict = None) -> dict:
    """校验页面提交的值，返回 {auth, api_key, appid, access_token, resource_id}；密钥留空时用已保存的。"""
    payload = payload or {}
    auth = str(payload.get("auth") or "key")
    if auth not in ("key", "app"):
        raise ValueError(language.msg(
            merged, "Authentication must be API Key or APP ID + Access Token",
            "鉴权方式只能是 API Key 或 APP ID + Access Token"))
    resource = (str(payload.get("resource_id") or "").strip() or _text(entry.get("resource_id"))
                or catalog.DOUBAO_RESOURCES[0]["value"])
    if resource not in catalog.DOUBAO_RESOURCE_VALUES:
        raise ValueError(language.msg(merged, "Unsupported resource ID", "不支持这个资源 ID"))
    values = {"auth": auth, "resource_id": resource}
    for field, label in (("api_key", "API Key"), ("access_token", "Access Token")):
        raw = str(payload.get(field) or "").strip()
        if any(c.isspace() for c in raw):
            raise ValueError(language.msg(merged, f"The {label} cannot contain spaces or newlines",
                                          f"{label} 里不能有空格或换行"))
        values[field] = raw or _text(entry.get(field))
    values["appid"] = str(payload.get("appid") or "").strip() or _text(entry.get("appid"))
    if auth == "key" and not values["api_key"]:
        raise ValueError(language.msg(merged, "Please enter an API Key", "请填写 API Key"))
    if auth == "app":
        if not re.fullmatch(r"[A-Za-z0-9]+", values["appid"]):
            raise ValueError(language.msg(merged, "Please enter the APP ID (letters and digits only)",
                                          "请填写 APP ID（只有字母和数字）"))
        if not values["access_token"]:
            raise ValueError(language.msg(merged, "Please enter the Access Token", "请填写 Access Token"))
    return values


def save_doubao(payload: dict, merged: dict, path: Path = None) -> list:
    """保存豆包的鉴权和资源 ID，返回修改的字段。用 APP ID 鉴权时清掉 API Key（有 API Key 时优先用它）。"""
    entry = _entry(merged, "ASR", catalog.DOUBAO_ASR_ENTRY)
    if entry and entry.get("type") != "doubao_stream":
        raise ValueError(language.msg(
            merged, f"{catalog.DOUBAO_ASR_ENTRY} in the config is not Doubao streaming ASR; rename or delete "
                    "it in the config file",
            f"配置里的 {catalog.DOUBAO_ASR_ENTRY} 不是豆包流式识别，请在配置文件里改名或删掉它"))
    values = _doubao_values(payload, entry, merged)
    wanted = {"resource_id": values["resource_id"]}
    if values["auth"] == "key":
        wanted["api_key"] = values["api_key"]
    else:
        wanted.update(appid=values["appid"], access_token=values["access_token"], api_key="")
    changes = {k: v for k, v in wanted.items() if v != _text(entry.get(k))}
    if not entry:
        changes = {"type": "doubao_stream", "enable_multilingual": False, "end_window_size": 200,
                   "output_dir": "tmp/", **changes}
    if not changes:
        return []
    with config_store.edit_custom(path) as data:
        target = data.setdefault("ASR", CommentedMap()).setdefault(catalog.DOUBAO_ASR_ENTRY, CommentedMap())
        for field, value in changes.items():
            target[field] = value
    return [f for f in changes if f in wanted]


def doubao_test_config(payload: dict, merged: dict) -> dict:
    """测试连接用的配置：页面上还没保存的值优先。"""
    entry = _entry(merged, "ASR", catalog.DOUBAO_ASR_ENTRY)
    values = _doubao_values(payload, entry, merged)
    config = {k: v for k, v in entry.items() if k != "type"}
    config.update(resource_id=values["resource_id"], api_key=values["api_key"] if values["auth"] == "key" else "",
                  appid=values["appid"], access_token=values["access_token"])
    return config


# ---------- 热词 ----------
# 所有支持热词的识别共用一份，存在配置顶层的 asr_hotwords（见 core/agent/asr_text.py）
HOTWORD_MAX = 200
HOTWORD_BAD = re.compile(r"[,，\n\r\t]")
hotwords_supported = asr_text.hotwords_supported


def describe_hotwords(merged: dict) -> dict:
    entry = _entry(merged, "ASR", _selected(merged, "ASR"))
    configured = asr_text.hotwords_of(merged)
    seen = {w.lower() for w in configured}
    result = {
        "supported": hotwords_supported(entry),
        "words": configured,
        "auto": [w for w in asr_text.call_name_hotwords(merged) if w.lower() not in seen],
        "dropped": [],
    }
    if entry.get("type") == "doubao_stream":
        multilingual = str(entry.get("enable_multilingual", False)).lower() != "false"
        _, result["dropped"] = asr_text.doubao_hotwords(asr_text.effective_hotwords(entry, merged), multilingual)
    return result


def save_hotwords(words, merged: dict, path: Path = None) -> list:
    """把热词整个写进 data/.config.yaml 的 asr_hotwords，同时删掉旧版本存在识别条目里的。返回修改的字段。"""
    if not hotwords_supported(_entry(merged, "ASR", _selected(merged, "ASR"))):
        raise ValueError(language.msg(
            merged, "The current speech recognition doesn't support hotwords (X-ASR and Doubao streaming ASR do)",
            "当前语音识别不支持热词（支持的有 X-ASR、豆包流式语音识别）"))
    if not isinstance(words, list):
        raise ValueError(language.msg(merged, "Hotword list format is wrong", "热词格式不对"))
    result = []
    for w in words:
        w = re.sub(r"\s+", " ", str(w)).strip()
        if not w:
            continue
        if HOTWORD_BAD.search(w) or len(w) > 40:
            raise ValueError(language.msg(
                merged, f'Hotword "{w}" cannot contain commas or newlines, and must be at most 40 characters',
                f"热词“{w}”不能包含逗号或换行，长度不超过 40 个字"))
        if w.lower() not in {x.lower() for x in result}:
            result.append(w)
    if len(result) > HOTWORD_MAX:
        raise ValueError(language.msg(
            merged, f"At most {HOTWORD_MAX} hotwords; too many slow recognition down and cause false matches",
            f"热词最多 {HOTWORD_MAX} 个；太多会让识别变慢，也更容易误认"))
    if result == asr_text.hotwords_of(merged) and "asr_hotwords" in merged:
        return []
    with config_store.edit_custom(path) as data:
        data["asr_hotwords"] = CommentedSeq(result)
        for entry in (data.get("ASR") or {}).values():
            if isinstance(entry, dict):
                entry.pop("hotwords", None)
    return ["hotwords"]


# ---------- 识别后纠错替换 ----------

def describe_corrections(merged: dict) -> list:
    table = asr_text.corrections_of(merged)
    return [{"target": str(t), "aliases": asr_text._aliases(v)} for t, v in table.items()]


def save_corrections(items, merged: dict, path: Path = None) -> list:
    """校验后把替换表整个写进 data/.config.yaml 的 asr_corrections。返回修改的字段。"""
    if not isinstance(items, list):
        raise ValueError(language.msg(merged, "Replacement list format is wrong", "替换表格式不对"))
    table, owner = {}, {}
    for item in items:
        target = re.sub(r"\s+", " ", str((item or {}).get("target") or "")).strip()
        aliases = [re.sub(r"\s+", " ", a) for a in asr_text._aliases((item or {}).get("aliases"))]
        if not target and not aliases:
            continue
        if not target:
            raise ValueError(language.msg(
                merged, f'What should "{aliases[0]}" be corrected to? Please fill in the correct form',
                f"“{aliases[0]}”要改成什么？请填写正确写法"))
        if len(target) > 40:
            raise ValueError(language.msg(
                merged, f'The correct form "{target}" is too long — keep it within 40 characters',
                f"正确写法“{target}”太长了，不超过 40 个字"))
        clean = []
        for a in aliases:
            if len(a) < 2 or len(a) > 40:
                raise ValueError(language.msg(
                    merged, f'The misrecognized form "{a}" needs 2–40 characters; too short risks changing '
                            "ordinary speech",
                    f"认错的写法“{a}”需要 2～40 个字；太短容易误改普通的话"))
            if a.lower() == target.lower():
                continue  # 和正确写法一样，不用替换
            other = owner.get(a.lower())
            if other and other != target:
                raise ValueError(language.msg(
                    merged, f'"{a}" maps to both "{other}" and "{target}"; keep only one',
                    f"“{a}”同时要改成“{other}”和“{target}”，只能保留一个"))
            owner[a.lower()] = target
            if a.lower() not in {x.lower() for x in clean}:
                clean.append(a)
        if not clean:
            raise ValueError(language.msg(
                merged, f'"{target}" has no misrecognized forms to replace',
                f"“{target}”还没有认错的写法"))
        table.setdefault(target, [])
        table[target] += [a for a in clean if a.lower() not in {x.lower() for x in table[target]}]
    if table == {t: list(v) for t, v in asr_text.corrections_of(merged).items()}:
        return []
    with config_store.edit_custom(path) as data:
        data["asr_corrections"] = CommentedMap((t, CommentedSeq(v)) for t, v in table.items())
    return ["asr_corrections"]


# ---------- 说完停顿时长（VAD） ----------
# 服务端 VAD 安静超过这么久就判定一句话说完，交给语音识别。
# 流式识别（type 以 _stream 结尾）由识别服务自己断句：豆包会用这个时长（end_window_size），其他的不起作用。
VAD_SILENCE_FIELD = "min_silence_duration_ms"
VAD_SILENCE_RANGE = (200, 5000)


def describe_vad(merged: dict) -> dict:
    name = _selected(merged, "VAD")
    entry = _entry(merged, "VAD", name)
    asr = _entry(merged, "ASR", _selected(merged, "ASR"))
    return {
        "selected": name,
        "supported": entry.get("type") == "silero",
        "silence_ms": int(entry.get(VAD_SILENCE_FIELD) or 1000),
        "range": list(VAD_SILENCE_RANGE),
        "asr_stream": str(asr.get("type") or "").endswith("_stream") and asr.get("type") != "doubao_stream",
        "asr_doubao": asr.get("type") == "doubao_stream",
    }


def save_vad_silence(raw, merged: dict, path: Path = None):
    """校验后写入当前 VAD 条目的停顿时长，返回写入的毫秒数；和原来一样时返回 None。"""
    name = _selected(merged, "VAD")
    entry = _entry(merged, "VAD", name)
    if entry.get("type") != "silero":
        raise ValueError(language.msg(
            merged, "The current voice detector isn't SileroVAD; only SileroVAD's pause length can be set here",
            "当前语音检测不是 SileroVAD，后台只支持设置 SileroVAD 的停顿时长"))
    low, high = VAD_SILENCE_RANGE
    try:
        value = int(str(raw).strip())
        if not low <= value <= high:
            raise ValueError
    except ValueError:
        raise ValueError(language.msg(merged, f"The pause length must be a whole number between {low} and "
                                              f"{high} (milliseconds)",
                                      f"停顿时长需要是 {low}～{high} 之间的整数（毫秒）"))
    if value == entry.get(VAD_SILENCE_FIELD):
        return None
    with config_store.edit_custom(path) as data:
        data.setdefault("VAD", CommentedMap()).setdefault(name, CommentedMap())[VAD_SILENCE_FIELD] = value
    return value


# ---------- 语音合成 ----------

# 中文音色的分组名按后台语言显示；英文音色的分组本来就是英文，直接透出
VOICE_GROUP_EN = {"普通话": "Mandarin", "方言：东北话": "Dialect: Northeastern",
                  "方言：陕西话": "Dialect: Shaanxi", "粤语": "Cantonese", "台湾": "Taiwan"}


def voice_group_label(group: str, merged: dict) -> str:
    return language.msg(merged, VOICE_GROUP_EN.get(group, group), group)


def describe_tts(merged: dict) -> dict:
    name = _selected(merged, "TTS")
    entry = _entry(merged, "TTS", name)
    voices = [v if language.get_language(merged) == "zh" else {**v, "group": voice_group_label(v["group"], merged)}
              for v in catalog.EDGE_VOICES]
    return {
        "selected": name,
        "type": entry.get("type", name),
        "voice": str(entry.get("voice") or ""),
        "voices": voices,
    }


def save_tts_voice(voice: str, merged: dict, path: Path = None) -> list:
    name = _selected(merged, "TTS")
    if _entry(merged, "TTS", name).get("type") != "edge":
        raise ValueError(language.msg(
            merged, "The current speech synthesis isn't EdgeTTS; only EdgeTTS voices can be set here",
            "当前语音合成不是 EdgeTTS，后台只支持设置 EdgeTTS 的音色"))
    if voice not in catalog.EDGE_VOICE_VALUES:
        raise ValueError(language.msg(merged, "Unknown voice", "不支持这个音色"))
    return config_store.update_entry("TTS", name, {"voice": voice}, merged, path)


# ---------- 意图识别 ----------

def plugin_configured(merged: dict, plugin: dict) -> bool:
    """插件需要的设置是否已填写（上游默认值是"你的…"或"mk-xxx"这类占位文本）。
    后台能设置的插件看必填字段，其余的看 plugins.<config>.api_key。"""
    if plugin.get("settings"):
        return plugin_settings.configured(merged, plugin["settings"])
    key = ((merged.get("plugins") or {}).get(plugin["config"]) or {}).get("api_key")
    return not config_store.is_placeholder(key) and not str(key).strip().lower().endswith("-xxx")


def _localized_plugin(merged: dict, p: dict) -> dict:
    """A plugin list entry with label/desc in the hub language."""
    q = {k: v for k, v in p.items() if not k.endswith("_en")}
    q["label"] = language.msg(merged, p.get("label_en", p["label"]), p["label"])
    q["desc"] = language.msg(merged, p.get("desc_en", p["desc"]), p["desc"])
    return q


def describe_intent(merged: dict) -> dict:
    name = _selected(merged, "Intent")
    mode = config_store.entry_type(merged, "Intent", name)
    functions = [str(f) for f in _entry(merged, "Intent", "function_call").get("functions") or []]
    return {
        "selected": name,
        "mode": mode if mode in catalog.INTENT_MODES else "other",
        "locked": llm_kind(merged) == "cli",
        "plugins": [{**_localized_plugin(merged, p), "enabled": p["name"] in functions,
                     "needs_config": bool(p.get("config")) and not plugin_configured(merged, p)}
                    for p in catalog.PLUGINS],
        "always_on": [_localized_plugin(merged, p) for p in catalog.ALWAYS_ON_PLUGINS],
        "extra": [f for f in functions if f not in catalog.PLUGIN_NAMES],
    }


def intent_entry_name(merged: dict, mode: str) -> str:
    """意图识别模式对应的条目名（上游里条目名和类型同名，用户改过名时按类型找）。"""
    intents = merged.get("Intent") or {}
    if isinstance(intents.get(mode), dict) and intents[mode].get("type", mode) == mode:
        return mode
    for name, entry in intents.items():
        if isinstance(entry, dict) and entry.get("type") == mode:
            return name
    raise KeyError(mode)


def save_intent_plugins(functions: list, merged: dict, path: Path = None) -> list:
    """保存 function_call 的插件列表。后台不认识的插件（用户手动加的）原样保留。"""
    if not isinstance(functions, list):
        raise ValueError(language.msg(merged, "Plugin list format is wrong", "插件列表格式不对"))
    unknown = [f for f in functions if f not in catalog.PLUGIN_NAMES]
    if unknown:
        raise ValueError(language.msg(merged, f"Unknown plugins: {', '.join(map(str, unknown))}",
                                      f"不认识的插件：{', '.join(map(str, unknown))}"))
    name = intent_entry_name(merged, "function_call")
    current = [str(f) for f in _entry(merged, "Intent", name).get("functions") or []]
    wanted = [p["name"] for p in catalog.PLUGINS if p["name"] in functions]
    wanted += [f for f in current if f not in catalog.PLUGIN_NAMES]
    if wanted == current:
        return []
    with config_store.edit_custom(path) as data:
        target = data.setdefault("Intent", CommentedMap()).setdefault(name, CommentedMap())
        target["functions"] = CommentedSeq(wanted)
    return ["functions"]


def remember_api_intent(mode: str, path: Path = None) -> None:
    """记住 API 模式下用户选的意图识别，切回 API 模型时恢复。"""
    if (config_store.load_custom(path).get("admin") or {}).get("api_intent") == mode:
        return
    with config_store.edit_custom(path) as data:
        data.setdefault("admin", CommentedMap())["api_intent"] = mode


# ---------- 呼名 ----------

ALIAS_BAD = re.compile(r"[\s,，.。!！?？、~～;；:：\"'“”‘’]")


def call_targets(merged: dict) -> list:
    """可以配称呼的模型：本机 Claude Code，以及已经填好的 API 厂商。

    defaults 是添加这个模型时预填的称呼：本机 Claude Code 用真人录音实测的几种写法，
    API 厂商只填英文厂商名；Claude API 的 claude 已给本机，自定义没有固定名字，都不预填。"""
    from core.agent import call_names

    result = []
    if config_store.entry_type(merged, "LLM", CLAUDE_CLI_ENTRY) == "claude_cli":
        result.append({"entry": CLAUDE_CLI_ENTRY,
                       "label": language.msg(merged, "Claude (local Claude Code)", "Claude（本机 Claude Code）"),
                       "defaults": list(call_names.DEFAULT_MODELS[CLAUDE_CLI_ENTRY])})
    for g in grok_entries(merged):
        if g["bot_id"]:
            result.append({"entry": g["entry"],
                           "label": language.msg(merged, f"{g['bot_name']} (Grok Bot)",
                                                 f"{g['bot_name']}（Grok Bot）"),
                           "defaults": [g["bot_name"].lower()]})
    for vendor in catalog.LLM_VENDORS:
        if describe_vendor(vendor, merged)["configured"]:
            result.append({"entry": vendor["entry"],
                           "label": language.msg(merged, f"{vendor['label']} (API)",
                                                 f"{vendor['label']}（API）"),
                           "defaults": [] if vendor["id"] in ("claude", "custom") else [vendor["id"]]})
    return result


def describe_call_names(merged: dict) -> dict:
    from core.agent import call_names, llm_router

    cfg = merged.get("call_names") or {}
    raw = cfg.get("models") or call_names.DEFAULT_MODELS
    targets = {t["entry"]: t for t in call_targets(merged)}
    models = []
    for key, value in raw.items():
        entry = llm_router.entry_name(str(key))
        if any(m["entry"] == entry for m in models):
            continue
        t = targets.get(entry)
        models.append({"entry": entry, "label": t["label"] if t else entry, "aliases": call_names._aliases(value),
                       "ready": t is not None})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "reply": str(cfg.get("reply") or call_names.default_reply(merged)),
        "models": models,
        "targets": list(targets.values()),
        "default": _selected(merged, "LLM"),
        "prefixes": ["嗨", "嘿", "hi", "hey"],
    }


def _clean_aliases(raw, label: str, taken: dict, merged: dict = None) -> list:
    from core.agent import call_names

    if not isinstance(raw, list):
        raise ValueError(language.msg(merged, "Alias list format is wrong", "称呼格式不对"))
    aliases = []
    for item in raw:
        alias = str(item or "").strip().lower()
        if not alias or alias in aliases:
            continue
        if ALIAS_BAD.search(alias):
            raise ValueError(language.msg(merged, f'Alias "{alias}" cannot contain spaces or punctuation',
                                          f"称呼“{alias}”里不能有空格或标点"))
        if not 2 <= len(alias) <= 20:
            raise ValueError(language.msg(merged, f'Alias "{alias}" must be 2–20 characters long',
                                          f"称呼“{alias}”要在 2 到 20 个字之间"))
        if alias in call_names._PREFIXES:
            raise ValueError(language.msg(merged, f'"{alias}" is a greeting prefix and cannot be an alias',
                                          f"“{alias}”是开头的招呼语，不能当称呼"))
        if alias in taken:
            raise ValueError(language.msg(
                merged, f'Alias "{alias}" is set for both {taken[alias]} and {label}; an alias can only '
                        "belong to one model",
                f"称呼“{alias}”同时给了 {taken[alias]} 和 {label}，只能给一个模型"))
        taken[alias] = label
        aliases.append(alias)
    if not aliases:
        raise ValueError(language.msg(
            merged, f"{label} needs at least one alias; delete the model instead if you don't want to call it",
            f"{label} 至少要有一个称呼，不用的话请删掉这个模型"))
    return aliases


def save_call_names(payload: dict, merged: dict, path: Path = None) -> list:
    """保存呼名设置，返回修改了的项。取值不对时抛出 ValueError。

    payload["models"] 是 [{"entry": 条目名, "aliases": [...]}]。新加的模型必须已经配置好；
    原来就有、后来没配置好的模型可以保留（叫到时设备会提示还没配置好）。
    """
    from core.agent import call_names

    current = describe_call_names(merged)
    enabled = bool(payload.get("enabled", current["enabled"]))
    reply = str(payload.get("reply", current["reply"]) or "").strip() or call_names.default_reply(merged)
    if len(reply) > 40:
        raise ValueError(language.msg(merged, "The reply is too long — keep it under 40 characters",
                                      "回复太长了，请控制在 40 个字以内"))
    raw = payload.get("models")
    if raw is None:
        raw = [{"entry": m["entry"], "aliases": m["aliases"]} for m in current["models"]]
    if not isinstance(raw, list):
        raise ValueError(language.msg(merged, "Model list format is wrong", "模型列表格式不对"))
    known = {m["entry"]: m["label"] for m in current["models"]}
    known.update({t["entry"]: t["label"] for t in current["targets"]})
    allowed = {t["entry"] for t in current["targets"]} | {m["entry"] for m in current["models"]}
    models, taken = {}, {}
    for item in raw:
        entry = str((item or {}).get("entry") or "") if isinstance(item, dict) else ""
        if entry not in allowed:
            raise ValueError(language.msg(
                merged, f'Model "{entry}" isn\'t set up yet — fill it in on the Models page first',
                f"模型“{entry}”还没配置好，请先到“大模型”页填好再来"))
        if entry in models:
            raise ValueError(language.msg(merged, f"{known[entry]} appears twice",
                                          f"{known[entry]} 重复了"))
        models[entry] = _clean_aliases(item.get("aliases"), known[entry], taken, merged)
    if enabled and not models:
        raise ValueError(language.msg(merged, "Give at least one model an alias",
                                      "至少要给一个模型配称呼"))

    old_models = {m["entry"]: m["aliases"] for m in current["models"]}
    changed = [k for k, new, old in (("enabled", enabled, current["enabled"]), ("reply", reply, current["reply"]),
                                     ("models", list(models.items()), list(old_models.items()))) if new != old]
    if not changed:
        return []
    with config_store.edit_custom(path) as data:
        cfg = data.setdefault("call_names", CommentedMap())
        cfg["enabled"] = enabled
        cfg["reply"] = reply
        cfg["models"] = CommentedMap((k, CommentedSeq(v)) for k, v in models.items())
    return changed


# ---------- 界面语言 ----------

def describe_language(merged: dict) -> dict:
    """后台语言设置：value 是当前生效的语言，label 本身按当前语言显示。"""
    return {"language": {
        "value": language.get_language(merged),
        "label": language.msg(merged, "Interface language", "界面语言"),
        "hint": language.msg(
            merged, "Switches the console and the text the hub sends. Reopen the page to refresh its language; "
                    "devices pick up the new wording when they reconnect",
            "切换后台界面和网关发出的文字。页面重新打开后换界面语言；设备重新连接后用上新文字"),
        "options": [{"value": o["value"], "label": o["label"]} for o in catalog.LANGUAGES],
    }}


def save_language(payload: dict, merged: dict, path: Path = None) -> list:
    """把界面语言写进 data/.config.yaml 顶层的 language。返回修改了的项。"""
    value = str(payload.get("language") or "").strip().lower()
    if value not in {o["value"] for o in catalog.LANGUAGES}:
        raise ValueError(language.msg(merged, "Language must be English or 中文", "语言只能选 English 或 中文"))
    if language.get_language(merged) == value:
        return []
    with config_store.edit_custom(path) as data:
        data["language"] = value
    return ["language"]
