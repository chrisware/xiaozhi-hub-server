"""Tests for the recursive config merge in config/config_loader.py."""
from config.config_loader import merge_configs


def test_merge_simple_override():
    default = {"a": 1, "b": 2}
    custom = {"b": 99}
    assert merge_configs(default, custom) == {"a": 1, "b": 99}


def test_merge_adds_new_key():
    default = {"a": 1}
    custom = {"b": 2}
    assert merge_configs(default, custom) == {"a": 1, "b": 2}


def test_merge_recursive_dict():
    default = {"x": {"a": 1, "b": 2}}
    custom = {"x": {"b": 99}}
    assert merge_configs(default, custom) == {"x": {"a": 1, "b": 99}}


def test_merge_deep_recursive():
    default = {"a": {"b": {"c": 1, "d": 2}}}
    custom = {"a": {"b": {"c": 99}}}
    assert merge_configs(default, custom) == {"a": {"b": {"c": 99, "d": 2}}}


def test_merge_replaces_non_dict_with_dict():
    default = {"a": 1}
    custom = {"a": {"nested": True}}
    assert merge_configs(default, custom) == {"a": {"nested": True}}


def test_merge_replaces_dict_with_non_dict():
    default = {"a": {"nested": True}}
    custom = {"a": 1}
    assert merge_configs(default, custom) == {"a": 1}


def test_merge_does_not_mutate_defaults():
    default = {"a": {"b": 1}}
    custom = {"a": {"c": 2}}
    merge_configs(default, custom)
    assert default == {"a": {"b": 1}}  # unchanged

# ---------- [xiaozhi-hub] language=en 的英文话术默认值 ----------

from core.utils import language

_ZH_DEFAULT = {
    "prompt": "你是一个简洁、友好的语音助手。\n",
    "exit_commands": ["退出", "关闭"],
    "system_error_response": "现在有点忙，我们稍后再试吧。",
    "end_prompt": {"enable": True, "reply": "好一会儿没听到你说话，我先休息啦。"},
    "TTS": {"EdgeTTS": {"type": "edge", "voice": "zh-CN-XiaoxiaoNeural"}},
}


def test_language_en_overlay_fills_defaults():
    merged = merge_configs(dict(_ZH_DEFAULT, language="en"), {})
    language.apply_language_defaults(merged, {})
    assert merged["exit_commands"] == ["exit", "goodbye", "stop listening"]
    assert merged["TTS"]["EdgeTTS"]["voice"] == "en-NZ-MollyNeural"
    assert merged["prompt"].startswith("You are a concise")
    assert "稍后再试" not in merged["system_error_response"]
    assert "enable" in merged["end_prompt"]  # 只覆盖 reply/prompt，不丢兄弟键


def test_language_overlay_respects_user_values():
    custom = {"exit_commands": ["goodbye"], "TTS": {"EdgeTTS": {"voice": "zh-CN-YunxiNeural"}}}
    merged = merge_configs(dict(_ZH_DEFAULT, language="en"), custom)
    language.apply_language_defaults(merged, custom)
    assert merged["exit_commands"] == ["goodbye"]
    assert merged["TTS"]["EdgeTTS"]["voice"] == "zh-CN-YunxiNeural"  # 用户选的音色不动


def test_language_zh_changes_nothing():
    merged = merge_configs(dict(_ZH_DEFAULT, language="zh"), {})
    language.apply_language_defaults(merged, {})
    assert merged == dict(_ZH_DEFAULT, language="zh")


def test_reply_language_word():
    assert language.reply_language({"language": "en"}) == "English"
    assert language.reply_language({"language": "zh"}) == "中文"
    assert language.reply_language({}) == "English"  # 本 fork 默认英文


def test_language_en_overlay_fills_agent_entries():
    default = {"language": "en",
               "LLM": {"ClaudeCLI": {"type": "claude_cli"},
                       "GrokBot_x": {"type": "grok_bot", "first_reply_filler": "custom one"}}}
    custom = {"LLM": {"GrokBot_x": {"first_reply_filler": "custom one"}}}
    merged = merge_configs(default, custom)
    language.apply_language_defaults(merged, custom)
    assert merged["LLM"]["ClaudeCLI"]["first_reply_filler"].startswith("Right")
    assert merged["LLM"]["ClaudeCLI"]["progress_reply"] == "Still working on it"
    assert merged["LLM"]["GrokBot_x"]["first_reply_filler"] == "custom one"  # 用户值优先
    assert "spoken aloud" in merged["LLM"]["GrokBot_x"]["voice_hint"]


def test_language_zh_agent_entries_untouched():
    default = {"language": "zh", "LLM": {"ClaudeCLI": {"type": "claude_cli"}}}
    merged = merge_configs(default, {})
    language.apply_language_defaults(merged, {})
    assert "first_reply_filler" not in merged["LLM"]["ClaudeCLI"]
