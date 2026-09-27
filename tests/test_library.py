import pytest

from core.library import Library
from tests.conftest import make_pdf


def test_add_and_list(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    pdf = make_pdf(tmp_path / "a.pdf")
    p = lib.add_pdf(pdf, {"title": "Paper A", "arxiv_id": "1706.03762v7", "authors": ["X", "Y"],
                          "published": "2017-06-12"})
    assert p.id == "arxiv-1706.03762v7"
    assert [q.id for q in lib.list_papers()] == [p.id]
    assert "arXiv:1706.03762v7" in p.display_subtitle() and "2017" in p.display_subtitle()
    # 同一 PDF / 同一 arXiv 号（不同版本）都识别为重复
    assert lib.find_duplicate(pdf_path=pdf).id == p.id
    assert lib.find_duplicate(arxiv_id="1706.03762v5").id == p.id
    # 同名不冲突
    p2 = lib.add_pdf(make_pdf(tmp_path / "b.pdf", pages=3), {"title": "Paper A", "arxiv_id": "1706.03762v7"})
    assert p2.id == "arxiv-1706.03762v7-2"


def test_rejects_non_pdf(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    f = tmp_path / "x.pdf"
    f.write_text("hello")
    with pytest.raises(ValueError):
        lib.add_pdf(str(f))
    assert lib.list_papers() == []


def test_sessions_and_notes(tmp_path):
    lib = Library(str(tmp_path / "lib"))
    p = lib.add_pdf(make_pdf(tmp_path / "a.pdf"))
    assert p.title == "a"
    s = p.new_session("skim", "bg", web_tools=True)
    assert p.list_sessions() == []          # 没保存前不出现
    s.messages.append({"role": "user", "content": [{"type": "paper_document"},
                                                   {"type": "text", "text": "这篇论文的主要贡献是什么？请详细说明"}]})
    s.save()
    [loaded] = p.list_sessions()
    assert loaded.data["style"] == "skim" and loaded.data["web_tools"] is True
    assert loaded.display_title().startswith("这篇论文的主要贡献")
    p.rename("新名字")
    assert lib.get(p.id).title == "新名字"
    assert p.list_sessions()[0].data["paper_title"] == "a"     # 会话里的标题快照不变
    p.write_notes("# n")
    assert p.read_notes() == "# n"
    loaded.delete()
    assert p.list_sessions() == []
    lib.delete(p)
    assert lib.list_papers() == []
