"""管理后台的 Grok Bot 页：条目的生成与删除、呼名目标、热词、登录流程接口。全部用临时配置文件和假的登录服务。"""

import base64
import json
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.admin import config_store, pages
from core.agent import asr_text, llm_router
from core.agent.grokbot_client import Credentials
from core.agent.grokbot_client import _sleep as real_sleep  # conftest 把 asyncio.sleep 改成了不等待的桩

CUSTOM_YAML = """\
language: zh
selected_module:
  LLM: ClaudeCLI
  Intent: nointent
LLM:
  ClaudeCLI:
    type: claude_cli
    model: haiku
call_names:
  enabled: true
  reply: 我在
  models:
    ClaudeCLI: [claude, cloud]
"""


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    p = tmp_path / ".config.yaml"
    p.write_text(CUSTOM_YAML, encoding="utf-8")
    return p


def merged(path):
    return config_store.load_merged(custom_path=path)


def jwt(exp_in):
    claims = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + exp_in}).encode()).rstrip(b"=").decode()
    return f"h.{claims}.s"


# ---------- 条目 ----------

def test_save_grok_bots_creates_updates_removes(cfg_path):
    bots = [{"id": "id-wukong", "name": "孙悟空"}, {"id": "id-daiyu", "name": "林 黛玉!"}]
    changed = pages.save_grok_bots({"bots": bots}, merged(cfg_path), cfg_path)
    assert sorted(changed) == ["GrokBot_孙悟空", "GrokBot_林黛玉"]
    m = merged(cfg_path)
    assert m["LLM"]["GrokBot_孙悟空"] == {"type": "grok_bot", "bot_id": "id-wukong", "bot_name": "孙悟空"}
    assert m["LLM"]["GrokBot_林黛玉"]["bot_name"] == "林 黛玉!"
    d = pages.describe_grok_bot(m)
    assert [e["bot_id"] for e in d["entries"]] == ["id-wukong", "id-daiyu"]
    # 新勾选的 Bot 自动用名字做称呼；"林 黛玉!" 有空格标点，不能当称呼
    assert d["entries"][0]["aliases"] == ["孙悟空"] and d["entries"][1]["aliases"] == []
    assert m["call_names"]["models"]["ClaudeCLI"] == ["claude", "cloud"]  # 原有的称呼不受影响
    assert pages.describe_llm(m)["callable"] == {"ClaudeCLI": ["claude", "cloud"], "GrokBot_孙悟空": ["孙悟空"]}
    # 没变化
    assert pages.save_grok_bots({"bots": bots}, m, cfg_path) == []
    # 改名：条目名不变，bot_name 更新
    assert pages.save_grok_bots({"bots": [{"id": "id-wukong", "name": "大圣"}, bots[1]]}, m, cfg_path) == ["GrokBot_孙悟空"]
    m = merged(cfg_path)
    assert m["LLM"]["GrokBot_孙悟空"]["bot_name"] == "大圣"
    # 给它配了称呼，再取消勾选：条目和称呼一起删
    pages.save_call_names({"models": [{"entry": "ClaudeCLI", "aliases": ["cloud"]},
                                      {"entry": "GrokBot_孙悟空", "aliases": ["孙悟空", "大圣"]}]}, m, cfg_path)
    assert pages.save_grok_bots({"bots": [bots[1]]}, merged(cfg_path), cfg_path) == ["GrokBot_孙悟空"]
    m = merged(cfg_path)
    assert "GrokBot_孙悟空" not in m["LLM"] and "GrokBot_孙悟空" not in m["call_names"]["models"]
    assert m["LLM"]["ClaudeCLI"]["model"] == "haiku"  # 别的条目不受影响


def test_save_grok_bots_name_collision_and_validation(cfg_path):
    bots = [{"id": "id-1", "name": "小助手"}, {"id": "id-2", "name": "小 助手"}]
    changed = pages.save_grok_bots({"bots": bots}, merged(cfg_path), cfg_path)
    assert "GrokBot_小助手" in changed and "GrokBot_小助手_id-2" in changed
    # 称呼"小助手"给了第一个；第二个的名字带空格，不自动配
    assert merged(cfg_path)["call_names"]["models"].get("GrokBot_小助手") == ["小助手"]
    assert "GrokBot_小助手_id-2" not in merged(cfg_path)["call_names"]["models"]
    # 名字和已有称呼撞了：不自动配
    pages.save_grok_bots({"bots": [{"id": "id-1", "name": "小助手"}, {"id": "id-2", "name": "小 助手"}, {"id": "id-3", "name": "Claude"}]},
                         merged(cfg_path), cfg_path)
    assert "GrokBot_Claude" not in merged(cfg_path)["call_names"]["models"]
    with pytest.raises(ValueError):
        pages.save_grok_bots({"bots": "x"}, merged(cfg_path), cfg_path)
    with pytest.raises(ValueError):
        pages.save_grok_bots({"bots": [{"name": "没有id"}]}, merged(cfg_path), cfg_path)


def test_auto_alias_when_no_call_names_section(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text("language: zh\nselected_module:\n  LLM: ClaudeCLI\nLLM:\n  ClaudeCLI:\n    type: claude_cli\n", encoding="utf-8")
    pages.save_grok_bots({"bots": [{"id": "id-1", "name": "孙悟空"}]}, merged(p), p)
    models = merged(p)["call_names"]["models"]
    assert models["GrokBot_孙悟空"] == ["孙悟空"] and models["ClaudeCLI"] == ["claude", "cloud", "clod", "clot", "克劳德"]


def test_cannot_remove_default_model(cfg_path):
    pages.save_grok_bots({"bots": [{"id": "id-1", "name": "孙悟空"}]}, merged(cfg_path), cfg_path)
    config_store.select_entry("LLM", "GrokBot_孙悟空", merged(cfg_path), cfg_path)
    with pytest.raises(ValueError, match="默认模型"):
        pages.save_grok_bots({"bots": []}, merged(cfg_path), cfg_path)
    assert pages.describe_llm(merged(cfg_path))["kind"] == "grok"
    assert pages.describe_llm(merged(cfg_path))["grok"]["selected"]["bot_name"] == "孙悟空"


def test_call_targets_hotwords_and_router(cfg_path):
    pages.save_grok_bots({"bots": [{"id": "id-1", "name": "孙悟空"}]}, merged(cfg_path), cfg_path)
    m = merged(cfg_path)
    targets = {t["entry"]: t for t in pages.call_targets(m)}
    assert targets["GrokBot_孙悟空"]["label"] == "孙悟空（Grok Bot）" and targets["GrokBot_孙悟空"]["defaults"] == ["孙悟空"]
    pages.save_call_names({"models": [{"entry": "ClaudeCLI", "aliases": ["cloud"]},
                                      {"entry": "GrokBot_孙悟空", "aliases": ["孙悟空"]}]}, m, cfg_path)
    m = merged(cfg_path)
    assert asr_text.call_name_hotwords(m) == ["Claude", "孙悟空"]
    assert llm_router.model_ready(m, "GrokBot_孙悟空") is True
    assert llm_router.model_ready({"LLM": {"GrokBot_x": {"type": "grok_bot", "bot_id": ""}}}, "GrokBot_x") is False
    assert llm_router.model_label("GrokBot_孙悟空") == "孙悟空"
    assert pages.describe_grok_bot(m)["entries"][0]["aliases"] == ["孙悟空"]


# ---------- 接口 ----------

class FakeAuth:
    """假的 api2.cursor.sh：只有登录轮询；EnsureSandBox 返回 500（登录后读 Bot 列表失败只记警告）。"""

    def __init__(self):
        self.approved = False
        self.app = web.Application()
        self.app.add_routes([web.get("/auth/poll", self.poll),
                             web.post("/aiserver.v1.GrokBotService/EnsureSandBox", self.ensure)])

    async def poll(self, request):
        if not self.approved:
            return web.Response(status=404)
        return web.json_response({"accessToken": jwt(3600), "refreshToken": "r"})

    async def ensure(self, request):
        return web.Response(status=500, text="no box")


@pytest.fixture
async def client(cfg_path, tmp_path):
    auth = FakeAuth()
    auth_server = TestServer(auth.app)
    await auth_server.start_server()
    app = web.Application()
    config = {"language": "zh", "selected_module": {"LLM": "ClaudeCLI", "Intent": "nointent"}, "LLM": {"ClaudeCLI": {"type": "claude_cli"}},
              "call_names": {}}
    handler = ah.AdminHandler(config, ws_server=None, config_path=cfg_path, grokbot_credentials=tmp_path / "creds.json")
    handler.grokbot.backend = f"http://127.0.0.1:{auth_server.port}"
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        c.handler, c.auth = handler, auth
        yield c
    await handler.grokbot.close()
    await auth_server.close()


async def test_grokbot_endpoints(client):
    d = await (await client.get("/admin/api/grokbot")).json()
    assert d["ok"] and d["account"]["logged_in"] is False and d["entries"] == [] and d["login"]["pending"] is False
    assert "accessToken" not in json.dumps(d)

    r = await (await client.post("/admin/api/grokbot/login", json={})).json()
    assert "/loginDeepControl?" in r["url"] and r["login"]["pending"]
    again = await (await client.post("/admin/api/grokbot/login", json={})).json()
    assert again["url"] == r["url"]  # 等待期间再点登录不会换地址

    client.auth.approved = True
    for _ in range(300):
        d = await (await client.get("/admin/api/grokbot")).json()
        if not d["login"]["pending"]:
            break
        await real_sleep(0.02)
    assert d["login"]["pending"] is False and d["login"]["result"]["ok"] and d["account"]["logged_in"]
    assert "accessToken" not in json.dumps(d)
    creds = Credentials(client.handler.grokbot.creds.path)
    assert creds.logged_in

    # 勾选 Bot → 条目生成并推到运行中的配置；呼名页能看到它
    r = await (await client.post("/admin/api/grokbot/bots", json={"bots": [{"id": "id-1", "name": "孙悟空"}]})).json()
    assert r["changed"] == ["GrokBot_孙悟空"]
    assert client.handler.config["LLM"]["GrokBot_孙悟空"]["bot_id"] == "id-1"
    cn = await (await client.get("/admin/api/call-names")).json()
    assert any(t["entry"] == "GrokBot_孙悟空" for t in cn["targets"])
    assert (await client.post("/admin/api/grokbot/bots", json={"bots": "bad"})).status == 400

    # 读取 Bot 列表失败：假服务没有云端电脑
    r = await client.post("/admin/api/grokbot/bots/refresh", json={})
    assert r.status == 502 and "读取 Bot 列表失败" in (await r.json())["error"]

    # 退出登录：凭证删除
    d = await (await client.post("/admin/api/grokbot/logout", json={})).json()
    assert d["account"]["logged_in"] is False and not creds.path.exists()


async def test_grokbot_requires_login(cfg_path, tmp_path):
    app = web.Application()
    handler = ah.AdminHandler({"selected_module": {}, "LLM": {}}, config_path=cfg_path, grokbot_credentials=tmp_path / "c.json")
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        assert (await c.get("/admin/api/grokbot")).status == 401
        assert (await c.post("/admin/api/grokbot/login", json={})).status == 401
    await handler.grokbot.close()


async def test_login_not_pending_once_approved_even_if_bot_list_is_slow(tmp_path):
    """批准后任务还要读 Bot 列表（可能要唤醒云端电脑几分钟）：页面不该继续显示"等待批准"，取消也不该盖掉成功。"""
    import asyncio
    from core.admin.grokbot import GrokBotAdmin
    from core.agent.grokbot_client import _sleep

    admin = GrokBotAdmin(tmp_path / "c.json")
    gate = asyncio.Event()

    class FakeClient:
        def start_login(self):
            from core.agent.grokbot_client import LoginFlow
            return LoginFlow(uuid="u", verifier="v", url="https://cursor.com/loginDeepControl?x")

        async def poll_login(self, flow, timeout):
            admin.creds.save({"accessToken": jwt(3600), "refreshToken": "r"})
            return True

        conn = None

    admin._client = FakeClient()

    async def slow_fetch():
        await gate.wait()
        return []

    admin.fetch_bots = slow_fetch
    await admin.start_login()
    for _ in range(50):
        if admin._approved:
            break
        await _sleep(0.02)
    st = admin.status()
    assert st["login"]["pending"] is False and st["login"]["result"]["ok"] and st["account"]["logged_in"]
    await admin.cancel_login()  # 用户此时点"取消"
    assert admin.status()["login"]["result"]["ok"] is True  # 不会被盖成"已取消"
    gate.set()
