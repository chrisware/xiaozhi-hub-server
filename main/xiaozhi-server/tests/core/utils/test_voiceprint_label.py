"""「未知说话人」标签随网关语言走，且两种标签都被比较处认可。"""

from core.utils.voiceprint_provider import UNKNOWN_SPEAKERS, VoiceprintProvider


def _make(hub_config):
    # url 为空 → 不发网络请求，只验证标签
    return VoiceprintProvider({"url": "", "speakers": ["1, Alice"]}, hub_config)


def test_unknown_speaker_label_follows_language():
    assert _make({"language": "en"}).unknown_speaker == "Unknown speaker"
    assert _make({"language": "zh"}).unknown_speaker == "未知说话人"
    assert _make(None).unknown_speaker == "Unknown speaker"  # fork 默认 en


def test_both_labels_in_compare_set():
    assert set(("未知说话人", "Unknown speaker")) <= set(UNKNOWN_SPEAKERS)
