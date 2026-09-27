"""假的 Anthropic 客户端：按剧本返回流式事件和最终消息，记录每次请求参数。"""

import copy
from types import SimpleNamespace


class FakeBlock:
    def __init__(self, d):
        self._d = d
        self.type = d["type"]

    def to_dict(self, mode="python"):
        return copy.deepcopy(self._d)


def text(t, citations=None):
    d = {"type": "text", "text": t}
    if citations:
        d["citations"] = citations
    return d


def tool_use(name, inp, id_="toolu_01ABCDEF"):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def message(blocks, stop="end_turn", usage=None):
    u = usage or {}
    return SimpleNamespace(
        content=[FakeBlock(b) for b in blocks],
        stop_reason=stop,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=u.get("input", 10), output_tokens=u.get("output", 5),
                              cache_read_input_tokens=u.get("cache_read", 0),
                              cache_creation_input_tokens=u.get("cache_write", 0)),
    )


class FakeStream:
    def __init__(self, msg, on_iter=None):
        self._msg = msg
        self._on_iter = on_iter

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self._msg.content:
            yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(
                type=b.type, name=b._d.get("name", "")))
            if self._on_iter:
                self._on_iter()
            if b.type == "text":
                yield SimpleNamespace(type="text", text=b._d["text"])

    def get_final_message(self):
        return self._msg


class FakeClient:
    """script: 列表，每项是 message(...)、异常实例，或 (message, on_iter 回调)。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class _Messages:
            def stream(self, **params):
                outer.requests.append(copy.deepcopy(params))
                item = outer.script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, tuple):
                    return FakeStream(*item)
                return FakeStream(item)

        self.beta = SimpleNamespace(messages=_Messages())
