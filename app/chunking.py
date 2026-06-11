"""본문을 검색용 청크로 분할.

1차: 마크다운 헤딩(`#`~`######`)과 빈 줄 기준 문단으로 나눈다.
2차: 한 청크가 너무 길면(``MAX_CHUNK_CHARS``) 줄/문장 경계로 추가 분할한다.
코드블록(```)은 분할 도중 쪼개지지 않게 한 덩이로 보존한다.
빈/공백 청크는 제거하고 원문 순서를 유지한다.
"""

from __future__ import annotations

import re

# 한 청크의 대략적 상한(문자 기준). 수백 토큰 ≈ 약 500자.
MAX_CHUNK_CHARS = 500

_HEADING = re.compile(r"^#{1,6}\s")
# 문장 경계: 마침표/물음표/느낌표(반각·전각) 뒤 공백.
_SENT = re.compile(r"(?<=[.!?。！？])\s+")


def _split_blocks(body: str) -> list[tuple[str, bool]]:
    """본문을 (텍스트, is_code) 블록 리스트로 분할.

    헤딩·빈 줄을 경계로 삼되, 펜스 코드블록은 한 덩이(is_code=True)로 묶는다.
    """
    blocks: list[tuple[str, bool]] = []
    cur: list[str] = []
    in_fence = False

    def flush(is_code: bool = False) -> None:
        if cur:
            text = "\n".join(cur).strip()
            if text:
                blocks.append((text, is_code))
            cur.clear()

    for line in body.split("\n"):
        stripped = line.strip()

        # 펜스 진입: 직전 텍스트 블록을 닫고 코드블록을 새로 시작.
        if not in_fence and stripped.startswith("```"):
            flush()
            in_fence = True
            cur.append(line)
            continue

        # 펜스 내부: 닫는 펜스를 만날 때까지 그대로 누적.
        if in_fence:
            cur.append(line)
            if stripped.startswith("```"):
                in_fence = False
                flush(is_code=True)
            continue

        # 일반 텍스트.
        if stripped == "":
            flush()
        elif _HEADING.match(line):
            flush()
            cur.append(line)
        else:
            cur.append(line)

    # 닫히지 않은 펜스는 코드블록으로 간주해 보존.
    flush(is_code=in_fence)
    return blocks


def _split_long(text: str) -> list[str]:
    """``MAX_CHUNK_CHARS``를 넘는 텍스트를 줄/문장 경계로 그리디 분할."""
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= MAX_CHUNK_CHARS:
            units.append(line)
        else:
            units.extend(p for p in _SENT.split(line) if p)

    chunks: list[str] = []
    buf = ""
    for u in units:
        if buf and len(buf) + 1 + len(u) > MAX_CHUNK_CHARS:
            chunks.append(buf)
            buf = u
        else:
            buf = u if not buf else f"{buf} {u}"
    if buf:
        chunks.append(buf)

    # 경계 없는 초장문(단일 토큰 등)은 하드 슬라이스로 상한 보장.
    result: list[str] = []
    for c in chunks:
        if len(c) <= MAX_CHUNK_CHARS:
            result.append(c)
        else:
            result.extend(
                c[i : i + MAX_CHUNK_CHARS] for i in range(0, len(c), MAX_CHUNK_CHARS)
            )
    return result


def split_into_chunks(body: str) -> list[str]:
    """본문을 청크 리스트로 분할(원문 순서 유지, 빈 청크 제거)."""
    out: list[str] = []
    for text, is_code in _split_blocks(body):
        if is_code or len(text) <= MAX_CHUNK_CHARS:
            out.append(text)
        else:
            out.extend(_split_long(text))
    return [c for c in (s.strip() for s in out) if c]
