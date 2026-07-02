# vegapunk v2 — MCP 입구 추가 (델타)

## 개요
이미 동작 중인 vegapunk 웹앱(FastAPI + Postgres/pgvector/pg_bigm + openclaw + fastembed) **위에**, claude.ai(웹·폰)·Claude Code용 **원격 MCP 입구**를 더한다. 새 앱이 아니라 기존 서비스 계층 위의 증분이다.

목적: vegapunk가 못 하는 것(이미지·시각화·웹검색)을 직접 만들지 않고 claude.ai에 위임하고, **같은 노트 저장소**(같은 DB·같은 `user_id`)를 claude.ai/Claude Code에서 저장·검색하게 한다. 1순위 용도는 **Claude Code 연동**(대화에서 나온 결정을 저장 → 코드로 잇는 순환).

## 핵심 설계 (2층)
```
LLM 오케스트레이션 층 ── 입구마다 다름
  웹(HTTP) 입구: openclaw로 직접 (다시쓰기·답변·요약·게이트)
  MCP 입구    : Claude.ai가 수행 → 서버는 전부 생략
─────────────────────────────────────────────
데이터·검색·병합 층 ── 완전 공유 (LLM 없는 부분만)
  DB · 검색코어 · find_merge_target · ingest · 인덱싱 · 임베딩
```
- **MCP 입구는 서버측 LLM을 0회 호출한다.** distill 글쓰기·답변·요약은 전부 Claude.ai가 한다. 서버는 LLM 없는 함수(검색·매칭·저장)만 도구로 노출.
- MCP 도구는 **기존 서비스 함수를 호출하는 얇은 래퍼.** 검색/병합/인덱싱을 새로 짜지 않는다.

## 인증 구조 (왜 AS가 필요한가)
- 커스텀 커넥터는 **OAuth 서버 하나**만 가리킨다. Claude는 github/kakao/google을 모르고 **vegapunk만** 본다.
- 멀티 신원(kakao+github+google을 한 계정으로 link)을 지원하려면, "여러 신원=동일인"을 아는 **aggregator가 vegapunk뿐**이다 → vegapunk가 OAuth **인가서버(AS)** 가 되어 Claude에게 자기 토큰을 발급해야 한다.
- 기존 kakao/github OAuth는 **상류 인증**(사람이 누구인지)으로 그대로 재사용. AS는 그 결과를 Claude가 이해하는 토큰으로 **번역**.

## 데이터 통합 경계
- **노트만 통합**(같은 `user_id`로 모임). **대화는 통합 안 함**(vegapunk 대화는 vegapunk에, claude.ai 대화는 claude.ai에). claude.ai 대화 자동저장 금지 — "골라서 저장".

## 공통 규약 (v1 README 규약 + v2 추가)
- 기존 [tasks/README.md](../README.md)의 기술스택·레이어 경계·마이그레이션 규약을 그대로 따른다.
- **서버측 LLM 0 (MCP 경로)**: MCP 도구 안에서 openclaw/Claude API를 부르지 않는다.
- **user_id 스코프 누락 금지**: 모든 MCP 도구·쿼리는 인증된 `user_id`로 스코프. 멀티유저 데이터 누수는 치명적.
- **로직 복제 금지**: 기존 `search`/`ingest`/`distill_match`/`indexing`/`embedding` 서비스 함수를 호출만.
- **빈 결과 환각 금지**: 검색이 비면 도구가 빈 결과를 명확히 반환하고, 도구 설명에 "결과 없으면 추측 말 것" 명시.
- **민감 접근수단 저장 금지**: 비번·계좌번호·API 키는 저장하지 않는다(도구 설명·운영 원칙).
- **같은 프로세스 마운트**: MCP는 기존 FastAPI 프로세스에 `/mcp`로 마운트(DB 풀·fastembed 싱글톤·서비스 함수 공유). 별도 프로세스 금지(자원 중복).

## 태스크 목록
| # | 태스크 | 설명 | 상태 |
|---|--------|------|------|
| 01 | [계정 모델 (멀티 신원)](01-account-model.md) | users↔identities 분리, find-or-create, link_account, 병합+툼스톤, user_id 스코프 점검 | ✅ |
| 02 | [OAuth 2.1 인가서버](02-oauth-authorization-server.md) | authlib AS, 디스커버리·PKCE·토큰 발급, Claude 콜백, Redis 토큰 저장 | ✅ |
| 03 | [MCP 서버 마운트 & 도구](03-mcp-server-and-tools.md) | `/mcp` 마운트, 도구 8종, search 게이트 플래그 | ✅ |
| 04 | [배포 & 커넥터 연결 & 교차검증](04-deploy-and-connect.md) | 공개 노출, 커넥터 등록, 웹↔claude.ai 노트 공유 검증 | ✅ |
| 05 | [연결 동의 화면](05-consent-screen.md) | authorize 코드 발급 전 항상 동의 화면(어느 계정에 연결하는지) | ⬜ |
| 06 | [Google OAuth provider](06-google-provider.md) | github/kakao 패턴으로 Google(OIDC) 추가 | ⬜ |
| 07 | [공개 엔드포인트 하드닝](07-public-endpoint-hardening.md) | rate limit, 토큰 revoke, link 토큰 TTL | ⬜ |
| 08 | [Admin 콘솔 + 웹앱 재편](08-admin-console.md) | 계정·토큰 관리 + 지식 관리, Admin 우선 / Chat 보조 | ⬜ |

## 의존 순서
**완료(프로덕션 배포됨)**: 01(계정) → 02(AS) → 03(도구) → 04(배포·연결). github+kakao 멀티신원 링크까지 실검증 완료.

**남음**: 05(동의 화면)·06(Google)은 인증 흐름 마감 → 07(하드닝) → 08(Admin 콘솔로 웹앱 재편). 05·06·07은 독립적이라 순서 자유, 08은 01·07 위에 올림.

나중(태스크 밖): 민감정보 암호화, 복구 코드, primary_email 변경 UI.
