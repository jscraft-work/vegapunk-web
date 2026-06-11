"""런타임 조절 가능한 앱 설정 (인메모리 캐시 + Redis 백업).

- 값은 인메모리 `_cache`에서 즉시 읽힌다(search 등 IO 없이 → 즉시반영).
- 변경 시 Redis에 백업해 재시작/재배포에도 유지.
- 단일 워커 전제(멀티워커면 매 접근 Redis 읽기나 pub/sub 필요).
- Redis 미가용(dev/test, MemoryStore)이면 메모리에만 유지(영속 X).
"""

from __future__ import annotations

_DEFAULTS: dict[str, float] = {
    "vec_dist_threshold": 0.18,  # 검색 관련성 거리 임계(작을수록 엄격)
}
_PREFIX = "setting:"
_cache: dict[str, float] = dict(_DEFAULTS)


def get(key: str) -> float:
    return _cache.get(key, _DEFAULTS[key])


def all_settings() -> dict[str, float]:
    return dict(_cache)


async def load(redis) -> None:
    """Redis에 저장된 값을 인메모리 캐시로 로드(없으면 기본값 유지)."""
    if redis is None:
        return
    for key in _DEFAULTS:
        try:
            raw = await redis.get(_PREFIX + key)
        except Exception:  # noqa: BLE001 — Redis 미가용 → 기본값 유지
            return
        if raw is None:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            _cache[key] = float(raw)
        except (TypeError, ValueError):
            pass


async def update(redis, key: str, value: float) -> None:
    """캐시 즉시 갱신(즉시반영) + Redis 백업(영속)."""
    if key not in _DEFAULTS:
        raise KeyError(key)
    _cache[key] = float(value)
    if redis is None:
        return
    try:
        await redis.set(_PREFIX + key, str(value))
    except Exception:  # noqa: BLE001 — Redis 없으면 메모리에만
        pass
