import io
import json
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest
from web import agent, codex_tools


@pytest.fixture
def paper(tmp_path):
    return SimpleNamespace(root=str(tmp_path), kind="paper", id="paper-1", title="Test", meta={})


def fake_process(monkeypatch, events=()):
    calls = []
    class Proc:
        returncode = 0
        stdout = io.StringIO("".join(json.dumps(e) + "\n" for e in events))
        def poll(self): return 0
        def wait(self): return 0
    monkeypatch.setattr(agent.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)) or Proc())
    class Thread:
        def __init__(self, target, args, **kwargs): self.target, self.args = target, args
        def start(self): self.target(*self.args)
    monkeypatch.setattr(agent.threading, "Thread", Thread)
    return calls


def test_codex_send_resume_and_custom_model(paper, monkeypatch):
    calls = fake_process(monkeypatch, [
        {"type": "thread.started", "thread_id": "codex-session"},
        {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": "回答"}},
        {"type": "turn.completed", "usage": {"input_tokens": 123}},
    ])
    cid = agent.send(paper, None, "问题", provider="codex", model="my-model")
    chat = agent.load(paper, cid)
    assert chat["provider"] == "codex" and chat["model"] == "my-model"
    assert chat["codex_session"] == "codex-session"
    assert chat["messages"][-1]["status"] == "done"
    cmd = calls[0][0]
    assert cmd[cmd.index("--model") + 1] == "my-model"
    assert "--ignore-user-config" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    servers = next(x for x in cmd if x.startswith("mcp_servers="))
    assert tomllib.loads(servers)["mcp_servers"]["research"]["required"]
    agent.send(paper, cid, "追问", model="another-model")
    assert "resume" in calls[1][0] and "codex-session" in calls[1][0]
    assert agent.load(paper, cid)["model"] == "another-model"


def test_switch_backends_replays_history(paper, monkeypatch):
    calls = fake_process(monkeypatch, [{"type": "system", "subtype": "init", "session_id": "old-claude"},
                                     {"type": "result", "subtype": "success", "result": "原回答"}])
    cid = agent.send(paper, None, "原问题", provider="claude", model="")
    agent.send(paper, cid, "换后端", provider="codex", model="custom")
    assert "原问题" in calls[-1][0][-1] and "原回答" in calls[-1][0][-1]
    agent.send(paper, cid, "再换回来", provider="claude", model="sonnet")
    assert "--resume" not in calls[-1][0]
    assert "换后端" in calls[-1][0][2]


def test_missing_cli_does_not_save_running_message(paper, monkeypatch):
    def missing(*args, **kwargs): raise FileNotFoundError("missing")
    monkeypatch.setattr(agent.subprocess, "Popen", missing)
    with pytest.raises(RuntimeError, match="安装并登录"):
        agent.send(paper, None, "hi", provider="codex")
    assert agent.list_chats(paper) == []


@pytest.mark.parametrize("provider,model", [("other", "x"), ("codex", "--foo"), ("claude", "a b")])
def test_invalid_selection(provider, model):
    with pytest.raises(ValueError): agent.selection(provider, model)


def test_mcp_steps_and_failed_turn(paper):
    msg = {"status": "running", "parts": [], "draft": ""}
    chat = {}
    for event in ["item.started", "item.completed"]:
        agent._handle_codex(chat, msg, {"type": event, "item": {
            "id": "write", "type": "mcp_tool_call", "tool": "Write",
            "arguments": {"file_path": "experiments/test.py"},
            "result": {"content": [{"type": "text", "text": "saved"}]}}}, paper.root)
    assert len(msg["parts"]) == 1 and msg["parts"][0]["status"] == "done"
    agent._handle_codex(chat, msg, {"type": "turn.failed", "error": {"message": "invalid model"}}, paper.root)
    assert msg["status"] == "error" and msg["error"] == "invalid model"


def test_write_allowlist_traversal_and_symlinks(paper, tmp_path):
    codex_tools.call(paper.root, "paper", paper.id, "Write", {"file_path": "experiments/test.py", "content": "print(1)"})
    assert (tmp_path / "experiments/test.py").read_text() == "print(1)"
    for path in ["meta.json", "../escape.py", "report.md", "/tmp/escape.py"]:
        with pytest.raises(ValueError): codex_tools.file_path(paper.root, path, "paper", write=True)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "experiments/link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError): codex_tools.file_path(paper.root, "experiments/link/escape.py", "paper", write=True)
    with pytest.raises(ValueError): codex_tools.file_path(paper.root, "../../etc/passwd", "paper")


def test_pdf_read_has_text_and_rendered_pages(tmp_path, qapp):
    from tests.conftest import make_pdf
    path = make_pdf(tmp_path / "paper.pdf")
    result = codex_tools.read_pdf(path, "1-2")
    assert len([c for c in result if c["type"] == "image"]) == 2
    assert any("Page 1" in c.get("text", "") for c in result)
    with pytest.raises(ValueError): codex_tools.read_pdf(path, "3")


def test_topic_import_and_report_require_confirmation(tmp_path, monkeypatch):
    from core import topics
    monkeypatch.setattr(topics, "get", lambda oid: SimpleNamespace(meta={"plan_confirmed": False}))
    for tool, args in [("Import", {"ref": "1706.03762"}), ("Write", {"file_path": "report.md", "content": "x"})]:
        with pytest.raises(ValueError, match="确认"):
            codex_tools.call(tmp_path, "topic", "topic-1", tool, args)


def test_stdio_mcp_protocol(paper):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "Write", "arguments": {"file_path": "meta.json", "content": "bad"}}},
    ]
    proc = subprocess.run([sys.executable, codex_tools.__file__, paper.root, "paper", paper.id],
                          input="\n".join(json.dumps(r) for r in requests), text=True, capture_output=True, check=True)
    results = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(results) == 3
    assert results[0]["result"]["capabilities"] == {"tools": {}}
    assert results[-1]["result"]["isError"]


def test_api_provider_model_and_validation(paper, monkeypatch):
    from fastapi.testclient import TestClient
    from web import server
    monkeypatch.setattr(server, "_ctx", lambda kind, oid: paper)
    calls = []
    monkeypatch.setattr(server.agent, "send", lambda *args, **kw: calls.append((args, kw)) or "chat-id")
    with TestClient(server.app) as client:
        response = client.post("/api/papers/paper-1/chats", json={"message": "test", "provider": "codex", "model": "custom-model"})
        assert response.status_code == 200 and response.json()["id"] == "chat-id"
        assert calls[-1][1] == {"provider": "codex", "model": "custom-model"}
        assert client.post("/api/papers/paper-1/chats", json={"message": "test", "provider": "other"}).status_code == 422
        config = client.get("/api/agent-config").json()
        assert {p["id"] for p in config["providers"]} == {"claude", "codex"}
        def invalid(*args, **kwargs): raise ValueError("bad model")
        monkeypatch.setattr(server.agent, "send", invalid)
        assert client.post("/api/papers/paper-1/chats", json={"message": "test"}).status_code == 400


def test_auto_followup_inherits_chat_backend(paper, monkeypatch):
    calls = fake_process(monkeypatch, [{"type": "turn.completed"}])
    cid = agent.send(paper, None, "question", provider="codex", model="my-model")
    agent.send(paper, cid, "experiment output", auto=True)
    assert calls[-1][0][0] == agent.CODEX
    assert agent.load(paper, cid)["model"] == "my-model"
    assert agent.load(paper, cid)["messages"][-2]["auto"]
