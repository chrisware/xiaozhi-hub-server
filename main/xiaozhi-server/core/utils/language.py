# [xiaozhi-hub] Hub-level language setting: top-level `language: en|zh` in the config
# (English is the default in these forks; Chinese upstream stays available).
#
# This module does two things:
#   1. Resolves the hub language and the LLM reply-language word ("English" / "中文")
#      used in the prompt template.
#   2. Fills the English variants of the hub's own canned strings (persona, error and
#      farewell replies, exit commands, Claude CLI fillers, Edge voice) into the merged
#      config — but ONLY for keys the user file (data/.config.yaml) does not set itself.
#      Anything the operator wrote always wins; setting `language: zh` changes nothing.
from collections.abc import Mapping

EN = "en"
ZH = "zh"

# The word injected into {{language}} of the base prompt template
REPLY_LANGUAGE = {EN: "English", ZH: "中文"}


def get_language(config) -> str:
    lang = str((config or {}).get("language") or EN).strip().lower()
    return lang if lang in (EN, ZH) else EN


def reply_language(config) -> str:
    return REPLY_LANGUAGE[get_language(config)]


def msg(config, en: str, zh: str) -> str:
    """Pick a user-facing string by hub language."""
    return zh if get_language(config) == ZH else en


# Path into the merged config -> English default, applied when language is "en".
EN_DEFAULTS: dict[tuple, object] = {
    ("prompt",): (
        "You are a concise, friendly voice assistant on a little voice device: you chat, "
        "answer questions, and get things done.\n"
        "Talk naturally, like a reliable mate — answer straight, no beating around the bush, "
        "no long lectures.\n"
    ),
    ("exit_commands",): ["exit", "goodbye", "stop listening"],
    ("system_error_response",): "I'm a bit busy right now — let's try again in a moment.",
    ("end_prompt", "reply"): "Haven't heard you in a while, so I'll rest now. Give me a shout when you need me.",
    ("end_prompt", "prompt"): (
        "End this conversation now. Say a short, warm goodbye in one or two sentences "
        "(start with something like \"it's been good talking to you\")."
    ),
    ("module_test", "test_sentences"): [
        "Hello, tell me a bit about yourself",
        "What's the weather like today?",
        "Summarise how quantum computing works in about 100 words",
    ],
    ("TTS", "EdgeTTS", "voice"): "en-NZ-MollyNeural",
}

# Per agent-type defaults, applied to every LLM entry of that type whose key the user
# file does not set (fillers etc. are per-entry in the template, e.g. GrokBot_xxx).
AGENT_DEFAULTS = {
    "claude_cli": {"first_reply_filler": "Right, let me have a look",
                   "progress_reply": "Still working on it"},
    "grok_bot": {"first_reply_filler": "Right, handing that to the Grok Bot",
                 "voice_hint": "Your reply will be spoken aloud: keep it conversational and "
                               "short, no lists, tables or code"},
}


def _user_set(user_config, path: tuple) -> bool:
    """True when the user file explicitly sets this path (even to an empty value)."""
    node = user_config
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return False
        node = node[part]
    return True


def _set_path(config: dict, path: tuple, value) -> None:
    node = config
    for part in path[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[path[-1]] = value


def apply_language_defaults(config: dict, user_config) -> dict:
    """Overlay the English defaults onto the merged config (in place) when language=en."""
    if get_language(config) != EN:
        return config
    for path, value in EN_DEFAULTS.items():
        if _user_set(user_config, path):
            continue
        _set_path(config, path, value)
    user_llm = user_config.get("LLM") if isinstance(user_config, Mapping) else None
    for entry_name, entry in (config.get("LLM") or {}).items():
        if not isinstance(entry, dict):
            continue
        defaults = AGENT_DEFAULTS.get(entry.get("type"))
        if not defaults:
            continue
        user_entry = user_llm.get(entry_name) if isinstance(user_llm, Mapping) else None
        for key, value in defaults.items():
            if isinstance(user_entry, Mapping) and key in user_entry:
                continue
            entry.setdefault(key, value)
    return config
