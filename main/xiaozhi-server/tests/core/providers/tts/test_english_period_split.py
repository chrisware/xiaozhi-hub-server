# [xiaozhi-hub] ASCII "." 加入流式分段：英文句子要在句号处切段合成（首包延迟），
# 但小数（3.5）、省略号（...）和缓冲区末尾还没看清下文的句号不算句子结尾。
import pytest

from core.providers.tts.base import TTSProviderBase


class _TTS(TTSProviderBase):
    async def text_to_speak(self, text, output_file):
        return None


def _segments(text, step, first_sentence=True, stop=True):
    tts = object.__new__(_TTS)
    tts.punctuations = ("。", "？", "?", "！", "!", "；", ";", "：", ".")
    tts.first_sentence_punctuations = ("，", "~", "、", ",", "。", "？", "?", "！", "!", "；", ";", "：", ".")
    tts.tts_text_buff, tts.processed_chars = [], 0
    tts.is_first_sentence, tts.tts_stop_request = first_sentence, False
    out = []
    for i in range(0, len(text), step):
        tts.tts_text_buff.append(text[i:i + step])
        segment = tts._get_segment_text()
        if segment:
            out.append(segment)
    if stop:
        tts.tts_stop_request = True
        rest = tts._get_segment_text()
        if rest:
            out.append(rest)
    return out


@pytest.mark.parametrize("step", [1, 4])
def test_english_sentences_split_at_periods(step):
    assert _segments("The weather today is fine. Have a nice day outside.", step) == [
        "The weather today is fine",
        "Have a nice day outside",
    ]


@pytest.mark.parametrize("step", [1, 3])
def test_decimal_point_never_splits(step):
    # 3.5 和 10.30 中间的点不切；句尾的点 flush 时收走
    assert _segments("The total is 3.5 litres. I'll round it to four.", step) == [
        "The total is 3.5 litres",
        "I'll round it to four",
    ]
    assert _segments("Pi is roughly 3.14159.", step) == ["Pi is roughly 3.14159"]


def test_ellipsis_does_not_split_mid_stream():
    # 省略号的点不切；后面真正的句号才切
    assert _segments("Wait... that is odd. Here comes another proper sentence.", 1) == [
        "Wait... that is odd",
        "Here comes another proper sentence",
    ]


def test_short_answer_joins_the_next_sentence():
    # "Yes." 太短（<MIN_SEGMENT_CHARS），并进下一句一起合成
    assert _segments("Yes. That is all settled now.", 1, first_sentence=False) == [
        "Yes. That is all settled now",
    ]


def test_mixed_zh_en_splits_on_both_scripts():
    # 首段"好的。"太短，按短段合并规则并进后面英文句一起切出
    assert _segments("好的。The time is 10.30 pm sharp tonight.", 1) == [
        "好的。The time is 10.30 pm sharp tonight",
    ]


def test_period_at_buffer_end():
    # 字母后的句号一到就切（句子结束了）；数字后面（可能是 "10." + "5"）等到 flush 才切
    assert _segments("Costs 5 dollars.", 1, stop=False) == ["Costs 5 dollars"]
    assert _segments("The value is 10.", 1, stop=False) == []
    assert _segments("The value is 10.", 1) == ["The value is 10"]


def test_tts_one_sentence_splits_english_period():
    from unittest.mock import MagicMock
    from core.providers.tts.dto.dto import ContentType

    class _Q(list):
        def put(self, item):
            self.append(item)

    tts = object.__new__(_TTS)
    tts.tts_text_queue = _Q()
    conn = MagicMock()
    conn.sentence_id = "sid-1"
    TTSProviderBase.tts_one_sentence(tts, conn, ContentType.TEXT,
                                     content_detail="Total is 3.5 kg. Done now.")
    parts = [m.content_detail for m in tts.tts_text_queue]
    # re.split 把标点作为独立片段入队（消费端再拼回来）：小数点不切，句子点切
    assert "Total is 3.5 kg" in parts
    assert "." in parts and " Done now" in parts
    assert not any(p == "Total is 3" for p in parts)  # 小数没被切开
