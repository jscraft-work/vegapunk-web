from app.chunking import MAX_CHUNK_CHARS, split_into_chunks


def test_heading_paragraph_split():
    body = "# 제목\n\n첫 문단이다.\n\n둘째 문단이다."
    chunks = split_into_chunks(body)
    assert len(chunks) >= 2
    assert any("첫 문단" in c for c in chunks)
    assert any("둘째 문단" in c for c in chunks)


def test_long_secondary_split():
    long_para = " ".join(["짧은 문장이다."] * 200)  # 500자 초과, 빈 줄 없음
    chunks = split_into_chunks(long_para)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)


def test_code_block_preserved():
    code = "```python\nfor i in range(10):\n    print(i)\n```"
    body = f"설명 문단.\n\n{code}\n\n다음 문단."
    chunks = split_into_chunks(body)
    # 코드블록이 한 덩이로 그대로 보존되어야 한다.
    assert any(c == code for c in chunks)


def test_empty_chunks_removed():
    body = "\n\n   \n\n실제 내용.\n\n\n"
    assert split_into_chunks(body) == ["실제 내용."]
