"""语音清除聊天记录："清除聊天记录"之类的话先问一遍，说"确定"才清（chat_history.clear，和聊天页设置里的按钮同一个接口）。

在呼名之后、意图识别和大模型之前处理，所以 Claude Code、Grok Bot 也能用。指令、确认和回复都不写进对话。
问了之后 CONFIRM_SECONDS 秒内：说确认的话就清，说否定的话就不清，说别的就当没问过、照常交给模型。
"""

import asyncio
import json
import re
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.utils import language

TAG = __name__

CONFIRM_SECONDS = 30
# [xiaozhi-hub] zh 保留原话术；en 话术按网关 language 取（language.msg）
ASK = "确定要清除全部聊天记录吗？清除后我会忘掉之前聊过的内容。说确定就清除"
DONE = "好的，聊天记录已经清除了"
KEPT = "好的，不清除"
ASK_EN = "Clear all the chat history? I'll forget what we've talked about. Say yes to go ahead."
DONE_EN = "Done — the chat history is cleared."
KEPT_EN = "Okay, leaving it as it is."

_STRIP = re.compile(r"[\s，。！？、,.!?；;：:~～\"'“”‘’…]+")
_VERB = "(?:清除|清空|删除|删掉|清掉|清理|抹掉|删了|清了)"
_OBJ = "(?:全部|所有|一下|我们的|咱们的|我的|和你的|跟你的)?(?:聊天|对话|历史|会话|消息)(?:记录|历史|内容)?"
# "清除聊天记录"、"把聊天记录清空"、"聊天记录全部删掉"；前面带否定的不算（"别清除聊天记录"）
_CLEAR = re.compile(rf"(?:^|[^不别])(?:{_VERB}{_OBJ}|把?{_OBJ}(?:都|全部|全)?{_VERB})")
_CONFIRM = re.compile(r"^(?:嗯|对|是|好|行|可以|没问题|确定|确认|清除|清空|删除|删吧|清吧|好的|是的|对的|确定清除|确认清除|清除吧|删掉吧)+(?:吧|啊|呀|的)?$")
_CANCEL = re.compile(r"(?:不要|不用|别|算了|取消|不清|不删|再想想|等一下|先不)")

# 英文：标点和多余空格去掉、保留单词间空格；动词和宾语隔着几个词也算（"clear all my chat history"）
_STRIP_EN_PUNCT = re.compile(r"[^\w\s'-]+")
_CLEAR_EN = re.compile(
    r"\b(?:clear|delete|remove|erase|wipe|reset|purge|forget|dump)\b(?:\s+[\w'-]+){0,4}?\s+"
    r"(?:chats?|conversations?|hist(?:ory)|memor(?:y)|messages?)\b"
    r"|\b(?:chats?|conversations?|history|memory)\b(?:\s+[\w'-]+){0,3}?\s+"
    r"(?:clear|cleared|delete|deleted|remove|removed|erase|erased|wiped?|reset|purged?)\b"
    r"|\bforget\s+(?:everything|it\s+all|all\s+of\s+(?:it|this|them))\b"
)
_CLEAR_EN_NEG = re.compile(r"\b(?:don'?t|not|never|no|cancel|stop|avoid)\b")
_CONFIRM_EN = re.compile(
    r"^(?:y(?:es|eah|eh|a|ep|up)?|uh\s?huh|sure|ok(?:ay)?|alright|do\s+it|go\s+ahead|yes\s+please|please\s+do"
    r"|confirm(?:ed)?|clear|delete|remove|reset|wipe|clear\s+it|delete\s+it|remove\s+it|reset\s+it)"
    r"(?:\s+(?:it|now|please))?$"
)
_CANCEL_EN = re.compile(r"\b(?:no|nah|nope|cancel|don'?t|do\s+not|never\s+mind|hold\s+on|wait|keep|not\s+now)\b")


def normalize(text: str, lang: str = language.ZH) -> str:
    if lang == language.EN:
        return _STRIP_EN_PUNCT.sub(" ", text or "").lower().strip()
    return _STRIP.sub("", text or "").lower()


def is_clear(text: str, lang: str = language.ZH) -> bool:
    if lang == language.EN:
        t = normalize(text, lang)
        if not t or len(t.split()) > 7 or _CLEAR_EN_NEG.search(t):
            return False
        return bool(_CLEAR_EN.search(t))
    t = normalize(text)
    return len(t) <= 20 and bool(_CLEAR.search(t))


def is_confirm(text: str, lang: str = language.ZH) -> bool:
    t = normalize(text, lang)
    if not t:
        return False
    if lang == language.EN:
        return not _CANCEL_EN.search(t) and bool(_CONFIRM_EN.match(t))
    return not _CANCEL.search(t) and bool(_CONFIRM.match(t))


def is_cancel(text: str, lang: str = language.ZH) -> bool:
    t = normalize(text, lang)
    return bool((_CANCEL_EN if lang == language.EN else _CANCEL).search(t))


def phrases(lang: str) -> dict:
    return ({"ask": ASK_EN, "done": DONE_EN, "kept": KEPT_EN} if lang == language.EN
            else {"ask": ASK, "done": DONE, "kept": KEPT})


def result_text(result: dict, lang: str = language.ZH) -> str:
    """清除结果的语音回复。聊过 Claude Code / Grok Bot 时说一句它们那边没动（清的只是本机的记录）。"""
    agents = result.get("agents") or []
    done = phrases(lang)["done"]
    if agents:
        if lang == language.EN:
            listed = " and ".join(agents) if len(agents) <= 2 else ", ".join(agents[:-1]) + f", and {agents[-1]}"
            return f"{done} The conversations over on {listed} haven't been touched."
        return f"{done}。{'和'.join(agents)}自己那边的对话没有动"
    return done


async def handle(conn: "ConnectionHandler", text: str):
    """返回 None 表示这句话已经处理完；否则返回要继续交给模型的文字。"""
    if text.lstrip().startswith("{"):
        return text
    lang = language.get_language(conn.config)
    ph = phrases(lang)
    pending = getattr(conn, "chat_clear_asked", 0)
    if pending:
        conn.chat_clear_asked = 0
        if time.monotonic() - pending < CONFIRM_SECONDS:
            if is_confirm(text, lang):
                result = await clear_now(conn)
                await _notify_cleared(conn)
                await _speak(conn, text, result_text(result, lang))
                return None
            if is_cancel(text, lang):
                await _speak(conn, text, ph["kept"])
                return None
    if is_clear(text, lang):
        conn.logger.bind(tag=TAG).info(f"语音清除聊天记录，先确认：{text}")
        if await _speak(conn, text, ph["ask"]):
            conn.chat_clear_asked = time.monotonic()
            return None
    return text


async def clear_now(conn: "ConnectionHandler") -> dict:
    from core.admin import connections
    from core.agent import chat_history, llm_router

    device = chat_history.device_of(conn)
    if not device:
        # 没有 device-id 的连接没存过记录：只清这个连接的对话
        chat_history.clear_dialogues([conn])
        return {"deleted": 0, "agents": []}
    online = [c for c in (connections.get(i["session_id"]) for i in connections.snapshot()
                          if i["device_id"] == device) if c is not None]
    if conn not in online:
        online.append(conn)
    config = llm_router._server_config(conn)
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: chat_history.clear(device, config, connections=online))


async def _notify_cleared(conn: "ConnectionHandler") -> None:
    """告诉设备记录清掉了（带屏幕的设备据此清空聊天页）。不认识这条消息的设备会忽略它。"""
    try:
        await conn.websocket.send(json.dumps({"type": "chat_history", "state": "cleared",
                                              "session_id": conn.session_id}))
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"通知设备聊天记录已清除失败: {e}")


async def _speak(conn: "ConnectionHandler", heard: str, reply: str) -> bool:
    """不经过大模型直接回一句，也不写进对话。TTS 还没就绪时返回 False。"""
    from core.handle.sendAudioHandle import send_stt_message
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

    deadline = time.monotonic() + 3
    while not conn.tts and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    if not conn.tts:
        return False
    await send_stt_message(conn, heard)
    conn.client_abort = False
    conn.sentence_id = str(uuid.uuid4().hex)
    conn.tts.store_tts_text(conn.sentence_id, reply)
    conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.FIRST,
                                              content_type=ContentType.ACTION))
    conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=reply)
    conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.LAST,
                                              content_type=ContentType.ACTION))
    return True
