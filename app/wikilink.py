"""본문에서 위키링크 `[[제목]]` 추출.

`[[제목|별칭]]` 형식이면 제목부만 취한다. 코드블록(```)·인라인코드(`)
내부의 링크는 무시한다. 순서를 유지하며 중복을 제거한다.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`]*`")
_LINK = re.compile(r"\[\[([^\[\]]+)\]\]")


def extract_links(body: str) -> list[str]:
    # 코드(펜스→인라인 순)를 먼저 제거해 그 안의 링크를 배제.
    cleaned = _FENCE.sub("", body)
    cleaned = _INLINE.sub("", cleaned)

    seen: set[str] = set()
    out: list[str] = []
    for m in _LINK.finditer(cleaned):
        title = m.group(1).split("|", 1)[0].strip()
        if title and title not in seen:
            seen.add(title)
            out.append(title)
    return out
