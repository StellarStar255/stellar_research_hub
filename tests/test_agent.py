import json
import os

import pytest

from core import agent
from core.library import Library
from tests.conftest import make_pdf
from tests.fakes import FakeClient, message, text, tool_use

SETTINGS = {"model": "claude-opus-5", "effort": "high", "timeout": 60, "python": ""}


@pytest.fixture
def session(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    paper = lib.add_pdf(make_pdf(tmp_path / "attn.pdf"), {"title": "Attention Is All You Need"})
    return paper.new_session("tutor", background="研一，熟悉 PyTorch", web_tools=False)


class Recorder(agent.Callbacks):
    def __init__(self, approve=True, reason="", cancel_after=None):
        self.texts, self.statuses, self.notes, self.approvals = [], [], [], []
        self._approve, self._reason = approve, reason
        self.cancel_flag = False

    def on_text(self, d):
        self.texts.append(d)

    def on_status(self, t):
        self.statuses.append(t)

    def cancelled(self):
        return self.cancel_flag

    def approve_code(self, purpose, code):
        self.approvals.append((purpose, code))
        return self._approve, self._reason

    def save_note(self, title, content):
        self.notes.append((title, content))
        return "已写入笔记"


def test_simple_turn_attaches_paper_once_and_caches(session):
    client = FakeClient([message([text("核心是自注意力。")]), message([text("因为方差。")])])
    cb = Recorder()
    assert agent.run_turn(client, session, "这篇讲什么？", SETTINGS, cb) == "done"
    assert agent.run_turn(client, session, "为什么除以 √d？", SETTINGS, cb) == "done"

    first = session.messages[0]["content"]
    assert first[0] == {"type": "paper_document"}          # 历史里只存占位
    assert session.messages[2]["content"] == [{"type": "text", "text": "为什么除以 √d？"}]

    req = client.requests[1]
    doc = req["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    assert doc["citations"] == {"enabled": True}
    assert doc["cache_control"]["ttl"] == "1h"
    assert doc["title"] == "Attention Is All You Need"
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["thinking"]["type"] == "adaptive"
    assert "研一" in req["system"] and "Attention Is All You Need" in req["system"]
    # 前缀逐字节一致：第二次请求的第一条消息与第一次完全相同
    assert json.dumps(client.requests[0]["messages"][0], sort_keys=True) == \
        json.dumps(req["messages"][0], sort_keys=True)
    assert client.requests[0]["system"] == req["system"]
    assert "".join(cb.texts) == "核心是自注意力。因为方差。"
    assert session.data["usage"]["input"] == 20
    with open(session.path, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert len(saved["messages"]) == 4
    assert "data" not in json.dumps(saved)                   # PDF 没进 JSON


def test_run_python_tool_loop_returns_output_and_figure(session):
    code = ("import matplotlib.pyplot as plt\n"
            "plt.plot([1, 2, 3], [1, 4, 9])\n"
            "plt.show()\n"
            "print('sum =', 1 + 2)\n")
    client = FakeClient([
        message([text("我来验证。"), tool_use("run_python", {"purpose": "画图", "code": code})],
                stop="tool_use"),
        message([text("结果如图。")]),
    ])
    cb = Recorder()
    assert agent.run_turn(client, session, "做个实验", SETTINGS, cb) == "done"
    assert cb.approvals == [("画图", code)]

    result = session.messages[2]["content"][0]
    assert result["type"] == "tool_result" and not result.get("is_error")
    assert "sum = 3" in result["content"][0]["text"]
    imgs = [c for c in result["content"] if c["type"] == "local_image"]
    assert len(imgs) == 1
    assert os.path.exists(os.path.join(session.paper.experiments_dir, imgs[0]["path"]))

    # 第二次请求里图片已展开成 image 块
    sent = client.requests[1]["messages"][2]["content"][0]["content"]
    assert sent[1]["type"] == "image" and sent[1]["source"]["media_type"] == "image/png"


def test_denied_code_is_reported_to_model(session):
    client = FakeClient([
        message([tool_use("run_python", {"purpose": "x", "code": "print(1)"})], stop="tool_use"),
        message([text("好的，不运行。")]),
    ])
    cb = Recorder(approve=False, reason="先别跑")
    agent.run_turn(client, session, "试试", SETTINGS, cb)
    res = session.messages[2]["content"][0]
    assert res["is_error"] and "先别跑" in res["content"][0]["text"]
    assert not os.path.isdir(os.path.join(session.paper.experiments_dir, ".runs"))


def test_save_note_goes_through_callback(session):
    client = FakeClient([
        message([tool_use("save_note", {"title": "要点", "content": "- 缩放点积"})], stop="tool_use"),
        message([text("记好了。")]),
    ])
    cb = Recorder()
    agent.run_turn(client, session, "记下来", SETTINGS, cb)
    assert cb.notes == [("要点", "- 缩放点积")]


def test_invalid_tool_input_not_executed(session):
    client = FakeClient([
        message([tool_use("run_python", {"purpose": "x"})], stop="tool_use"),
        message([text("抱歉。")]),
    ])
    cb = Recorder()
    agent.run_turn(client, session, "试试", SETTINGS, cb)
    assert cb.approvals == []
    assert session.messages[2]["content"][0]["is_error"]


def test_truncated_tool_call_gets_error_result(session):
    client = FakeClient([
        message([tool_use("run_python", {"purpose": "x", "code": "print("})], stop="max_tokens"),
    ])
    cb = Recorder()
    assert agent.run_turn(client, session, "试试", SETTINGS, cb) == "done"
    assert cb.approvals == []
    # 每个 tool_use 都有配对的 tool_result，历史仍然合法
    assert session.messages[-1]["content"][0]["tool_use_id"] == "toolu_01ABCDEF"


def test_api_error_on_first_call_rolls_back(session):
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIConnectionError(request=req)
    client = FakeClient([err])
    with pytest.raises(agent.AgentError, match="网络"):
        agent.run_turn(client, session, "你好", SETTINGS, Recorder())
    assert session.messages == []
    assert not os.path.exists(session.path)                  # 新会话失败不留空文件


def test_refusal_raises_and_rolls_back(session):
    client = FakeClient([message([], stop="refusal")])
    with pytest.raises(agent.AgentError, match="拒绝"):
        agent.run_turn(client, session, "你好", SETTINGS, Recorder())
    assert session.messages == []


def test_cancel_mid_stream_rolls_back(session):
    cb = Recorder()

    def cancel():
        cb.cancel_flag = True
    client = FakeClient([(message([text("一半")]), cancel)])
    assert agent.run_turn(client, session, "你好", SETTINGS, cb) == "cancelled"
    assert session.messages == []


def test_sanitize_mid_output_fallback():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": "partial"},
        {"type": "tool_use", "id": "a", "name": "run_python", "input": {}},
        {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {}},
        {"type": "fallback", "from": {"model": "claude-opus-5"}, "to": {"model": "claude-opus-4-8"}},
        {"type": "thinking", "thinking": "", "signature": "t"},
        {"type": "text", "text": "rest"},
    ]
    out = agent.sanitize_assistant_content(blocks)
    assert [b["type"] for b in out] == ["text", "thinking", "text"]
    assert agent.sanitize_assistant_content(blocks[:3]) == blocks[:3]


def test_build_request_model_specifics(session):
    class C:
        class beta:
            class messages:
                @staticmethod
                def stream(*, model, fallbacks=None, **kw):
                    pass
    session.messages.append({"role": "user", "content": [{"type": "paper_document"},
                                                         {"type": "text", "text": "q"}]})
    p = agent.build_request(session, {"model": "claude-opus-5"}, C())
    assert p["fallbacks"] == "default" and p["betas"] == [agent.FALLBACK_BETA]
    p = agent.build_request(session, {"model": "claude-opus-5", "base_url": "https://gw"}, C())
    assert "fallbacks" not in p and p["betas"] == []
    p = agent.build_request(session, {"model": "claude-haiku-4-5"}, C())
    assert "thinking" not in p and "output_config" not in p
    session.data["web_tools"] = True
    p = agent.build_request(session, {"model": "claude-sonnet-5"}, C())
    assert {t.get("type") for t in p["tools"]} >= {"web_search_20260209", "web_fetch_20260209"}


def test_append_note_format():
    out = agent.append_note("# 我的笔记\n", "注意力", "正文")
    assert out.startswith("# 我的笔记\n\n## 注意力\n")
    assert out.rstrip().endswith("正文")
    assert agent.append_note("", "", "x").startswith("## 笔记")
