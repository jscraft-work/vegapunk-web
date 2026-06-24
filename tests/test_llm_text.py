"""low-tier LLM 응답 정리(clean_title / parse_tag_list) 단위 테스트."""

from app.llm_text import clean_title, parse_tag_list


def test_clean_title_plain():
    assert clean_title("이직 준비 메모") == "이직 준비 메모"
    assert clean_title('"따옴표 제목"') == "따옴표 제목"
    assert clean_title("  여러 줄\n둘째 줄  ") == "여러 줄"  # 첫 줄만


def test_clean_title_json_object():
    assert clean_title('{"title": "연봉 협상"}') == "연봉 협상"
    assert clean_title('{"제목": "오이 재배"}') == "오이 재배"


def test_clean_title_status_envelope():
    # 회귀: 상태 봉투에서 "completed"가 아니라 실제 본문을 골라야 한다.
    assert clean_title('{"status":"completed","output":"실제 제목"}') == "실제 제목"
    assert clean_title('{"status": "completed", "answer": "면접 후기"}') == "면접 후기"
    # 본문 키가 먼저 와도 동작.
    assert clean_title('{"result":"이력서 정리","status":"ok"}') == "이력서 정리"


def test_clean_title_code_fence():
    assert clean_title('```json\n{"title": "도커 정리"}\n```') == "도커 정리"
    assert clean_title("```\n그냥 제목\n```") == "그냥 제목"


def test_clean_title_nested_and_list():
    assert clean_title('{"output": {"title": "중첩 제목"}}') == "중첩 제목"
    assert clean_title('["배열 제목"]') == "배열 제목"


def test_clean_title_empty():
    assert clean_title("") == ""
    assert clean_title('{"status": "completed"}') == ""  # 본문 없음 → 빈 제목


def test_parse_tag_list_comma_and_newline():
    assert parse_tag_list("커리어, 이직, 연봉") == ["커리어", "이직", "연봉"]
    assert parse_tag_list("a\nb\nc") == ["a", "b", "c"]
    assert parse_tag_list("x, x, y") == ["x", "y"]  # 중복 제거


def test_parse_tag_list_json():
    assert parse_tag_list('["농사", "오이"]') == ["농사", "오이"]
    assert parse_tag_list('{"tags": ["커리어", "이직"]}') == ["커리어", "이직"]
    assert parse_tag_list('```json\n["도커", "배포"]\n```') == ["도커", "배포"]


def test_parse_tag_list_empty():
    assert parse_tag_list("") == []
    assert parse_tag_list("[]") == []
