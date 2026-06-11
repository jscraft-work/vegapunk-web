"""테스트 공용 헬퍼: 인증 우회 + LLM 주입 앱 빌더."""

from __future__ import annotations

from app.deps import require_user
from app.llm import get_llm
from app.main import create_app
from app.session import MemoryStore

FAKE_USER = {"id": 1, "email": "tester@example.com", "name": "테스터"}


def build_app(pool, *, llm=None, user=None):
    """인증을 우회(require_user override)한 테스트 앱. 필요시 FakeLLM 주입."""
    app = create_app()
    app.state.pool = pool
    app.state.session_store = MemoryStore()
    app.dependency_overrides[require_user] = lambda: user or FAKE_USER
    if llm is not None:
        app.dependency_overrides[get_llm] = lambda: llm
    return app
