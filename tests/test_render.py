from core import render


def test_citation_suffix_dedup_and_ranges():
    cites = [
        {"type": "page_location", "start_page_number": 3, "end_page_number": 4},
        {"type": "page_location", "start_page_number": 3, "end_page_number": 4},
        {"type": "page_location", "start_page_number": 5, "end_page_number": 7},
        {"type": "char_location", "start_char_index": 0},
    ]
    assert render.citation_suffix(cites) == " [p.3](page:3) [p.5–6](page:5)"
    assert render.citation_suffix(None) == ""


def test_history_groups_tool_steps_under_one_claude_section(tmp_path):
    msgs = [
        {"role": "user", "content": [{"type": "paper_document"}, {"type": "text", "text": "问题一"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "想一想", "signature": "s"},
            {"type": "text", "text": "先跑个实验"},
            {"type": "tool_use", "id": "t1", "name": "run_python",
             "input": {"purpose": "验证", "code": "print('```')"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": [{"type": "text", "text": "输出 42"}]}]},
        {"role": "assistant", "content": [{"type": "text", "text": "答案是 42",
                                           "citations": [{"type": "page_location", "start_page_number": 2,
                                                          "end_page_number": 3}]}]},
        {"role": "user", "content": [{"type": "text", "text": "问题二"}]},
    ]
    md = render.render_history(msgs, str(tmp_path))
    assert md.count(render.CLAUDE_HEADER) == 1
    assert md.count(render.USER_HEADER) == 2
    assert "输出 42" in md and "[p.2](page:2)" in md
    assert "想一想" not in md
    assert "````python" in md                     # 代码里有 ``` 时换更长的围栏
    assert "想一想" in render.render_history(msgs, str(tmp_path), show_thinking=True)
