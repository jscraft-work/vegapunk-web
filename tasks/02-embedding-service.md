# Task 02: 임베딩 서비스

## 목표
텍스트를 multilingual-e5-large(1024차원) 벡터로 바꾸는 로컬 임베딩 모듈을 만든다. e5 prefix 규약(`passage:`/`query:`)을 강제하고, 여러 텍스트를 한 번에 처리하는 배치 임베딩을 제공한다. 이후 인덱싱(03)·검색(04)·distill(08)이 공통으로 사용한다.

## 선행 조건
- Task 01 완료(프로젝트 구조·설정).

## 구현 상세

### 2.1 fastembed 래퍼 (`app/embedding.py`)
- 의존성 추가: `fastembed`.
- ⚠️ **버전 고정 필수**: fastembed는 버전에 따라 `multilingual-e5-large`의 풀링 방식이 바뀐다(0.5.1 이하 CLS, 이후 mean pooling). 인덱싱(03)과 검색(04)이 **다른 풀링으로 임베딩하면 같은 벡터 공간이 아니게 되어 검색이 조용히 망가진다**. `pyproject.toml`에 정확한 버전을 고정하고(`uv.lock` 커밋), 12장 Docker도 동일 버전을 쓴다. 현재 기준 mean pooling(0.8.x)을 사용하며, e5 원논문 권장도 mean이다.
- 모델 싱글톤: `intfloat/multilingual-e5-large` 를 모듈 최초 사용 시 1회 로드(앱 기동 블로킹 방지를 위해 lazy 초기화; 무거우므로 전역 1개만).
- 핵심 API (둘 다 prefix를 **함수 내부에서** 붙인다 — 호출자가 빠뜨리지 못하게):
  - `embed_passages(texts: list[str]) -> list[list[float]]` — 각 텍스트에 `"passage: "` 접두 후 배치 임베딩.
  - `embed_query(text: str) -> list[float]` — `"query: "` 접두 후 단건 임베딩.
- fastembed는 동기/CPU 바운드 → FastAPI 비동기 컨텍스트에서 호출 시 `anyio.to_thread.run_sync`로 감싸는 `aembed_passages`/`aembed_query` 비동기 래퍼도 제공.
- 반환 벡터는 길이 1024 검증(assert/예외). 빈 리스트 입력 → 빈 리스트 반환(호출자 가드 단순화).

### 2.2 정규화 정책
- e5는 코사인 유사도 사용 → 벡터 정규화 여부를 fastembed 기본 동작에 맞춤. 04의 검색이 `vector_cosine_ops`(`<=>`)를 쓰므로 추가 정규화 불필요(코사인은 크기 무관). 정책을 모듈 docstring에 명시.

### 2.3 모델 다운로드 캐시
- fastembed 모델 캐시 경로를 설정(환경변수 `FASTEMBED_CACHE`, 기본 `./.fastembed_cache`). 12장 Docker에서 이미지에 미리 받아두기 위함(메모만, 실제 Docker는 12에서).

## 완료 기준

### 자동 검증 (테스트)
- [ ] `tests/test_embedding.py::test_dim` — `embed_query("안녕")` 길이 1024.
- [ ] `tests/test_embedding.py::test_batch` — `embed_passages(["a","b","c"])` 가 3개, 각 1024.
- [ ] `tests/test_embedding.py::test_prefix_effect` — 같은 문장을 passage/query로 임베딩한 결과가 서로 다름(prefix가 실제로 반영됨을 검증; 벡터 불일치 확인).
- [ ] `tests/test_embedding.py::test_empty` — `embed_passages([])` → `[]`.

### 수동 검증
- [ ] 최초 실행 시 모델 다운로드가 캐시 경로에 받아지고, 2회차는 재다운로드 없음.

**검증 실행 명령어**: `uv run pytest tests/test_embedding.py -q`

## 참고사항
- **prefix를 호출자에게 맡기지 않는다** — 03/04/08이 각각 붙이면 한 곳만 빠져도 검색이 조용히 망가진다. 반드시 이 모듈이 책임진다.
- **fastembed 버전이 바뀌면 풀링이 달라질 수 있다** — 색인된 벡터와 질의 벡터의 풀링이 어긋나면 검색 결과가 무의미해진다. 버전 업그레이드 시 전체 재인덱싱이 필요할 수 있음을 기억할 것(2.1 참고).
- 모델 로드는 수 초~수십 초 걸릴 수 있음. 테스트는 세션 스코프 fixture로 1회만 로드.
- 다음(03)은 `aembed_passages`로 청크들을 배치 임베딩한다.
