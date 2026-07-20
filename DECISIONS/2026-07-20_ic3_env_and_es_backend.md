# IC-3 가용분 — env 일원화 + 상품 ES 재추출 백엔드 + 유저 K=100

날짜: 2026-07-20 · 근거:
[fable_doc/plans/2026-07-19_input_connectors_readiness.md](../fable_doc/plans/2026-07-19_input_connectors_readiness.md)
§7 사용자 확정 · IC-1·IC-2 준비분 완료 위에 얹는 "접근 확정분" 구현.

## 결정 요약

1. **env 일원화**: GraphRapping 자체 git-ignored `.env`(루트)로 DB 관련 환경변수를
   전부 관리. `.env.example`(플레이스홀더만) 커밋. 로딩은 **명시적 opt-in**.
2. **상품 마스터 = ES9 재추출 백엔드**: `scripts/fetch_product_catalog_es.py` —
   전량 export → **기존 `refresh_product_catalog` 체인 재사용**(검증·diff·landing).
3. **유저 커넥터 자격증명 env 이관**: os.environ 우선 → 기존 personal-agent `.env`
   경로 fallback(무파괴). 최초 로드 **K=100**.
4. **리뷰 백엔드 = 대기**: 구현하지 않음(근거 아래).

## 1. env 일원화

### 로더 (`src/common/env_file.py`)
- `parse_env_text`(순수) + `load_env_file(path=".env", *, override=False, environ=None)`.
  ~30줄 자체 파서 — **python-dotenv 의존성 추가 안 함**(base 의존성 불변).
- **우선순위 = 이미 설정된 os.environ 우선**(`override=False` 기본): 셸/CI export가
  항상 `.env`를 이긴다 → 어떤 실행 환경과도 충돌 없음.
- **opt-in 지점**: 커넥터 스크립트 3종(`fetch_user_profiles_pg`,
  `fetch_review_triples`(IC-2 기존), `fetch_product_catalog_es`)의 `main()` 시작부에서만
  호출. **import 부수효과 금지** — 라이브러리 import는 파일을 읽지 않는다.
- `environ` 주입 인자: 테스트가 명시 매핑을 넘겨 실 os.environ을 오염시키지 않고
  검증(테스트 격리 유지).

### 서버 미적용 (보류)
- 이번엔 **스크립트만** `.env`를 로드. `src/web/server.py`에는 미적용.
- 이유: 데모/테스트 환경 오염 방지. 서버는 이미 명시적 os.environ 게이트로 동작하고,
  conftest가 GRAPHRAPPING_* env를 격리한다. 서버 `.env` 로드는 별도 결정으로 보류.

### `.gitignore`
- `.env`·`.env.*` 무시(기존) + `!.env.example` 예외 추가(템플릿은 커밋 대상).

### `.env.example` 변수 블록
- 상품 ES: `ES_CLOUD_URL`·`ES_CLOUD_KEY`·`ES_AMORE_INDEX`·`ES_INNI_INDEX`.
- 유저 Azure PG: `AIBE_DB_URL/PORT/NM/USER/PW/SCHEMA`.
- GraphRapping PG: `GRAPHRAPPING_DATABASE_URL`.
- 미래 규약(주석): AWS 표준 체인(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
  `AWS_REGION`/`AWS_PROFILE`), `SNOWFLAKE_*`.

## 2. 상품 ES 재추출 백엔드

### 접속 (recommend-agent 실코드 패턴)
- 플레인 REST: `POST {ES_CLOUD_URL}/{index}/_search`,
  헤더 `Authorization: ApiKey {ES_CLOUD_KEY}` + `Content-Type: application/json`.
- 인덱스 env 2종: `ES_AMORE_INDEX`(031)·`ES_INNI_INDEX`(036). 기본 export =
  **두 인덱스의 중복제거 합집합**. ES `_source` 필드가 카탈로그 컬럼과 동일 네이밍
  → 필드 리매핑 불요(`mockdata/product_catalog_es.json`이 곧 이 export).

### 페이지네이션 방식 = search_after (PIT/scroll 없이)
- 정렬 `sort=[{<고유키>: asc}, {_doc: asc}]`(기본 고유키 =
  `ONLINE_PROD_SERIAL_NUMBER` = 서빙 SKU id = refresh diff 키, 카탈로그 내 고유).
  마지막 hit의 `sort` 배열을 그대로 다음 `search_after`로 에코 → 정렬 키 개수와
  무관하게 동작. 짧은/빈 페이지에서 종료.
- **scroll 대비 장단**:
  - 장점: 서버측 scroll 컨텍스트를 열지/누수/정리하지 않음(stateless), ES9-forward
    (scroll은 deep pagination에서 비권장).
  - 단점: 스냅샷 격리 없음 — export 도중 카탈로그가 변형되면 행 누락/중복 가능.
    상품 마스터는 저변동 + 하류 계약·baseline diff가 큰 불일치를 잡으므로 수용.
    엄격 격리가 필요해지면 PIT+search_after로 전환.
  - `--sort-field`로 정렬 키 override(ES 매핑상 기본 키가 non-sortable일 때;
    예: `.keyword` 서브필드). **라이브 스모크는 메인 세션 몫**(서브에이전트 무접속).

### 체인 재사용
- export(`EsCatalogReader.read()` → `_source` dict 리스트) → **그대로**
  `refresh_product_catalog(records, baseline, date)` 호출. 검증·diff·staging landing·
  매니페스트는 IC-2 코드 재사용(중복 구현 0). HTTP 레이어(`FetchFn`)는 주입 가능 —
  목 테스트만, 라이브 접속 없음.
- 신규 의존성 없음: **stdlib `urllib.request`**(httpx는 query-llm extra라 base에 없음).

## 3. 유저 커넥터 env 이관 + K=100

- `resolve_db_credentials(env_path, environ=None) -> (creds, source)`:
  ① os.environ(= GraphRapping `.env` 로드 후) 4개 필수 키 전부 있으면 사용
  (`source="environ"`) → ② 없으면 기존 `load_db_credentials(env_path)`(personal-agent
  `.env`) fallback(`source="env_file(fallback)"`). **무파괴 이관**: GraphRapping `.env`가
  없어도 기존 파일 경로로 동작, 반대로 personal-agent `.env`가 없어도 GraphRapping
  `.env`/셸로 동작.
- `main()` 시작부 `load_env_file()` opt-in 호출. 자격증명 실값은 **로그/출력 금지** —
  `source` 라벨만 출력.
- `--limit` 기본값 `DEFAULT_LIMIT=100`(상한 500 불변). 기존 테스트는
  `_limit_type` 파싱만 검증(argparse 기본값 비의존) → 무영향.

## 4. 리뷰 백엔드 대기 (구현 안 함) — 근거

inference-gerter 정찰(Fable) 요약:
- SageMaker 배치 파이프라인: `gerter_ner_step` → `gerter_bee_step`. **relation 스텝은
  합류 예정**(사용자 확인 필요).
- 산출: Snowflake NER/BEE 테이블(`SNF_{OWN,EXTN,GLOBAL}_NER_TBL_NAME`) + S3
  TransformOutput 양쪽.
- relation 합류 후 최종 rs.jsonl 형태/위치가 확정되어야 백엔드 구현 가능. 인터페이스
  (`ReviewTripleReader`)는 IC-2에서 준비 완료 → 확정 시 File 백엔드 옆에 S3/Snowflake
  백엔드만 추가하면 됨. **지금 구현은 추측 코드가 되므로 대기.**

## 무파괴 계약 준수

- `.env` 파일 자체 미생성(자격증명 0). 산출물 = `.env.example`(플레이스홀더)+코드.
- 기존 픽스처·스냅샷 diff 0, 기존 테스트 무수정 통과. 127.0.0.1:8123 무접촉,
  `run_906_full_load_db.py` 불가침, 라이브 ES/PG 무접속(목 테스트만).

## 완료 보고

- 신규: `src/common/env_file.py`, `scripts/fetch_product_catalog_es.py`, `.env.example`,
  `tests/test_env_file.py`, `tests/test_product_es_connector.py`,
  `tests/test_user_creds_resolution.py`, 본 문서.
- 수정: `scripts/fetch_user_profiles_pg.py`(env-first creds 해석·K=100·opt-in .env),
  `.gitignore`(`!.env.example`), `README.md`(env 표 블록 + `.env` 안내).
- 게이트: ruff/mypy/pytest — (실행 결과는 메인 세션 검증 후 갱신).
