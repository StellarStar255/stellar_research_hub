import pytest

from core import arxiv

SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All
      You Need</title>
    <summary>  The dominant sequence transduction models ...  </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
  </entry>
</feed>"""


@pytest.mark.parametrize("text,expected", [
    ("1706.03762", "1706.03762"),
    ("1706.03762v7", "1706.03762v7"),
    ("arXiv:2106.09685", "2106.09685"),
    ("https://arxiv.org/abs/2106.09685v2", "2106.09685v2"),
    ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762"),
    ("https://arxiv.org/pdf/1706.03762v5", "1706.03762v5"),
    ("https://huggingface.co/papers/2502.16161", "2502.16161"),
    ("hep-th/9901001", "hep-th/9901001"),
    ("https://example.com/paper.pdf", None),
    ("hello world", None),
    ("", None),
])
def test_parse_arxiv_id(text, expected):
    assert arxiv.parse_arxiv_id(text) == expected


def test_parse_atom():
    m = arxiv.parse_atom(SAMPLE)
    assert m["arxiv_id"] == "1706.03762v7"
    assert m["title"] == "Attention Is All You Need"
    assert m["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert m["published"] == "2017-06-12"
    assert m["abstract"].startswith("The dominant")


def test_parse_atom_empty():
    assert arxiv.parse_atom(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>') is None


def test_fetch_rejects_garbage():
    with pytest.raises(ValueError, match="认不出来"):
        arxiv.fetch("not a paper")
