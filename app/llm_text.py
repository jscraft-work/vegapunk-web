"""low-tier LLM 응답 텍스트 정리.

openclaw low-tier 모델이 한 줄 제목/태그 지시를 어기고 가끔 JSON(`{"title":..}`),
코드펜스(```...```), 또는 **상태 봉투**(`{"status":"completed","output":".."}`)로
감싸 반환한다. 그 원문(또는 "completed" 같은 상태 단어)이 그대로 저장되던 버그를 막는다.
"""

from __future__ import annotations

import json

# 내용으로 볼 만한 키(우선순위 순). 상태 봉투의 실제 본문이 이 중 하나에 담긴다.
_CONTENT_KEYS = (
    "title", "제목", "text", "summary", "output", "result", "content",
    "answer", "response", "message", "value", "tags", "태그",
)
# 폴백에서 건너뛸 메타/상태 키.
_META_KEYS = {
    "status", "state", "type", "role", "id", "finish_reason",
    "model", "level", "code", "ok", "success", "reason",
}
# 값이 이 상태 단어뿐이면 내용이 아님(예: "completed").
_STATUS_WORDS = {
    "completed", "complete", "success", "succeeded", "ok", "done",
    "finished", "failed", "error", "pending", "running", "null", "none",
}


def _strip_fence(t: str) -> str:
    """```json ... ``` 코드펜스를 벗긴다."""
    if t.startswith("```"):
        lines = [ln for ln in t.splitlines() if not ln.strip().startswith("```")]
        return "\n".join(lines).strip()
    return t


def _is_status(s: str) -> bool:
    return s.strip().lower() in _STATUS_WORDS


def _coerce_str(obj) -> str | None:
    """JSON 값에서 사람이 읽을 문자열 하나를 끌어낸다(상태 단어/메타 키는 회피)."""
    if isinstance(obj, str):
        return obj if obj.strip() and not _is_status(obj) else None
    if isinstance(obj, list):
        for item in obj:
            v = _coerce_str(item)
            if v:
                return v
        return None
    if isinstance(obj, dict):
        for key in _CONTENT_KEYS:
            if key in obj:
                v = _coerce_str(obj[key])
                if v:
                    return v
        # 폴백: 메타/상태가 아닌 첫 문자열 값.
        for k, v in obj.items():
            if str(k).lower() in _META_KEYS:
                continue
            cv = _coerce_str(v)
            if cv:
                return cv
        return None
    return None


def _coerce_list(obj) -> list[str] | None:
    """JSON 값에서 문자열 리스트를 끌어낸다(태그용)."""
    if isinstance(obj, list):
        out = [x.strip() for x in obj if isinstance(x, str) and x.strip() and not _is_status(x)]
        return out or None
    if isinstance(obj, dict):
        for key in _CONTENT_KEYS:
            if key in obj:
                got = _coerce_list(obj[key])
                if got:
                    return got
        return None
    return None


def _parse_json(t: str):
    if t[:1] in "{[":
        try:
            return json.loads(t)
        except ValueError:
            return None
    return None


def clean_title(raw: str) -> str:
    """LLM 제목 응답 → 안전한 한 줄 제목."""
    t = _strip_fence((raw or "").strip())
    if not t:
        return ""
    obj = _parse_json(t)
    if obj is not None:
        t = _coerce_str(obj) or ""
    # 첫 줄만, 감싼 따옴표 제거.
    t = t.splitlines()[0].strip() if t.strip() else ""
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'`":
        t = t[1:-1].strip()
    return t


def parse_tag_list(raw: str) -> list[str]:
    """LLM 태그 응답 → 태그 문자열 리스트(쉼표/줄바꿈 또는 JSON 배열/객체)."""
    t = _strip_fence((raw or "").strip())
    if not t:
        return []
    obj = _parse_json(t)
    if obj is not None:
        items = _coerce_list(obj)
        if items is not None:
            return _dedupe(items)
    # 일반 텍스트: 쉼표/줄바꿈 분리.
    return _dedupe(s.strip() for s in t.replace("\n", ",").split(","))


def _dedupe(items) -> list[str]:
    """공백/중복 제거, 순서 보존."""
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        s = s.strip().strip("\"'`[]").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
