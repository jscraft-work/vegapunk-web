from app.wikilink import extract_links


def test_basic():
    assert extract_links("보라 [[A]] 그리고 [[B]].") == ["A", "B"]


def test_alias_takes_title():
    assert extract_links("[[제목|별칭]] 참고") == ["제목"]


def test_ignore_code():
    body = "텍스트 [[진짜]] `[[인라인무시]]`\n```\n[[블록무시]]\n```"
    assert extract_links(body) == ["진짜"]


def test_dedup_preserves_order():
    assert extract_links("[[A]] [[A]] [[B]] [[A]]") == ["A", "B"]
