# Phase 1.2 — glb 채널 identity 전략 사전 결정

작성일: 2026-07-08 · **확정: 2026-07-10 (사용자 승인 — D안)** · 관련 계획: `fable_doc/03_improvement_plan.md` §Phase 1.2 (이슈 B2, `fable_doc/02_issues_assessment.md`)

이 문서는 **glb(Amazon/Sephora) 온보딩 자체를 실행하지 않는다.** 온보딩 구현은
`fable_doc/03_improvement_plan.md` Phase 5 백로그(표 중 "다국어 사전 구조 개편 +
glb 온보딩", 착수 조건 "1.2의 glb identity 전략 결정 선행")이며, 이 문서는 그
착수 조건인 **전략 사전 결정**만 산출한다. 코드/테스트/설정 변경 없음.

## 배경

- `mockdata/SCHEMA_RS_JSONL.md` §3 (48-58행) product_id 출처 표: own은
  `ecp_onln_prd_srno`/`intg_onln_prd_cd_vl`, extn은 `std_prd_cd`(=`rd_goods`,
  실제 상품코드)를 쓰지만 **glb만 `std_prd_cd`의 실제 값이 `a.prod_nm`(상품명)이다**
  ("상품명이 코드로 사용 (주의)"). §9-3 (261-273행)의 Snowflake 매핑도 동일하게
  `product_id ← std_prd_cd ← a.prod_nm`를 "**상품명이 코드로 사용** (주의!)"로
  재확인한다. §10 상품 마스터 연동 키 표(277-285행)에서도 own/extn은 실제 코드
  컬럼으로 조인하지만 glb만 조인 키가 `prod_nm`이다.
- 위험(계획 문서 원문): 상품명이 바뀌면 다른 상품으로 취급되고, 동명 상품이
  충돌하며, `source_channel + source_key_type + source_product_id` composite
  identity(`docs/architecture/db_consumer_contract.md` §3)가 오염될 수 있다.
- 재적재 비용이 큰 이유: `src/common/ids.py`의 `make_product_iri(product_id)`는
  `product_id`의 순수 함수(`f"product:{product_id}"`)이고, `canonical_fact`
  target 연결·`wrapped_signal.target_product_id`·`agg_product_signal.target_product_id`·
  `serving_product_profile.product_id`가 전부 이 값에서 파생된다. **identity
  발급 규칙을 온보딩 후에 바꾸면 glb 슬라이스 전체를 재처리해야 한다** — 그래서
  계획 문서가 "온보딩 **전에** 결정"을 요구한다.
- 이번 브랜치에서 Phase 1.1(source identity collision 처리 일관화)이 이미
  구현되어 `src/db/contract_validator.py`에 일반화된 collision 검증
  (`_count_source_identity_collision_violations`, 392-463행)이 들어왔다. Phase
  1.2 결정은 이 신규 검증기가 실제로 무엇을 잡고 무엇을 못 잡는지 확인한 뒤
  내려야 한다 (아래 조사결과 4번).

## 현행 계약 조사 결과

### 1. 리뷰→상품 타겟 해석은 이미 "정확매칭 실패 시 퍼지매칭"으로 동작한다

`src/jobs/run_daily_pipeline.py` `_match_product_by_source_id`(72-84행)는
`record.source_product_id`가 기존 `ProductIndex.exact`(product_id 문자열
집합, own ES 상품마스터의 `ONLINE_PROD_SERIAL_NUMBER` 기반 — 숫자형 문자열)와
**정확히 일치**할 때만 채택한다(`process_review`, 194행: `match =
_match_product_by_source_id(record.source_product_id, product_index)`). glb의
`source_product_id`(상품명 원문)는 own의 숫자형 product_id와 절대 일치하지
않으므로 이 단계는 사실상 항상 실패하고, `match_product(brnd_nm, prod_nm,
index)`(`src/link/product_matcher.py`)의 브랜드+이름 퍼지 체인으로 자동
폴백한다(196행). 즉 **오늘 코드 기준으로도 glb 리뷰의 "상품 타겟" 자체는 이미
B안과 동일한 경로를 탄다** — 이번 결정이 정해야 하는 것은 이 매칭 결과를
**product identity로 어떻게 기록/승격할지**이다.

`product_matcher.py`(134-149행)의 임계값 처리는 이미 "매칭 성공(고신뢰)분만
승격, 나머지는 quarantine" 정책을 그대로 구현하고 있다:

```python
if best_pid and best_score >= FUZZY_AUTO_ACCEPT:      # ≥0.93 → auto accept
    return MatchResult(..., match_status=MatchStatus.FUZZY, ...)
if best_pid and best_score >= FUZZY_MANUAL_REVIEW:     # 0.80~0.93
    # Could be correct but needs human review — still quarantine for safety
    return MatchResult(..., match_status=MatchStatus.QUARANTINE, ...)
```

`run_daily_pipeline.py` 199행 `if match.match_status != MatchStatus.QUARANTINE
and match.matched_product_id:`가 이 결과를 그대로 소비해 QUARANTINE이면
`quarantine.quarantine_product_match(...)`(203-209행)로 보낸다. 즉 B안이
요구하는 "성공분만 수용, 미매칭 quarantine" 정책은 **이미 구현되어 있고 신규
코드가 필요 없다.**

### 2. brnd_nm은 신뢰할 수 없는 입력이다 — glb에서 위험이 증폭됨

`mockdata/SCHEMA_RS_JSONL.md` §2 (38행): `brnd_nm`은 "명시적 source
브랜드명(**추가 예정**). 없으면 null/누락으로 두며 `prd_nm` token에서 만들지
않는다". `product_matcher.py`의 fuzzy 단계(116-133행)는:

```python
brand_norm = normalize_text(_safe_text(brand_name_raw))
...
for pid, pname in index.exact.items():
    pid_brand = index.brands.get(pid, "")
    if brand_norm and pid_brand and brand_norm != pid_brand:
        continue   # brand_norm이 빈 문자열이면 이 줄 자체가 평가되지 않음
```

`brand_norm`이 빈 문자열이면 브랜드 필터가 아예 적용되지 않고 **카탈로그 전체
브랜드를 대상으로 상품명만으로** 유사도를 계산한다. 이는 glb 전용 결함이
아니라 브랜드가 빈 모든 입력에 이미 존재하는 구조적 위험이지만, `brnd_nm`이
"추가 예정"인 glb에서 실제로 발현될 가능성이 가장 높다.

### 3. 신규 collision validator는 "교차 소스 병합"만 탐지한다 — 사각지대 존재

`src/db/contract_validator.py::_count_source_identity_collision_violations`
(392-463행)의 `unmarked_shared_source_id` 체크는 다음 조건일 때만 위반으로
잡는다(442행):

```sql
GROUP BY source_product_id
HAVING COUNT(DISTINCT product_id) > 1
   AND COUNT(DISTINCT (source_channel, source_key_type)) > 1   -- 서로 다른 채널/key_type일 때만
```

`tests/test_source_identity_collision.py`의
`test_pg_unmarked_shared_source_product_id_is_invalid`(476-497행)가 이를
정확히 재현한다: product_id `A`(channel=031, key_type=`ecp_onln_prd_srno`)와
`B`(channel=036, key_type=`chn_prd_cd`)가 같은 `source_product_id="shared"`를
가리킬 때만 위반 처리된다. 이 검증기는 실제 운영 데이터에서 관측된 사례
(`docs/architecture/db_consumer_contract.md` 117-119행: `product_id="35119"`가
own 031/036 두 채널을 병합한 사례, `SOURCE_KEY_COLLISION`으로 마킹됨)를 잡기
위해 설계됐다.

**동일 channel·동일 key_type 내부**(예: 둘 다 `channel=amazon,
key_type=PRODUCT_NAME_KEY`)에서 서로 다른 두 실제 상품이 같은 정규화 키로
매핑되는 경우는 `COUNT(DISTINCT (source_channel, source_key_type))`가 1이므로
**구조적으로 탐지되지 않는다.** 게다가 product_id가 그 정규화 키에서 1:1로
파생된다면 애초에 `product_master` row가 하나만 생성되므로 DB에는 "충돌의
흔적"조차 남지 않는다 — `src/db/repos/product_repo.py`
`upsert_product_master`(16-76행)가 `ON CONFLICT (product_id) DO UPDATE SET`로
전체 컬럼을 덮어쓰기 때문에(33-56행) 나중에 적재된 리뷰의 상품 정보가 이전
정보를 조용히 대체한다. **이는 A안(정규화 상품명 키)을 채택할 경우 Phase
1.1의 collision validator만으로는 안전하지 않다는 뜻이다.**

### 4. 정규화 도구는 "기호 정규화"를 지원하지 않는다

`src/common/text_normalize.py`의 `normalize_text`(11-16행)는 NFC 정규화 +
lowercase + 공백 축약만 수행한다. `strip_brand_prefixes`(19-29행)는 괄호
`(...)` 내용 제거와 브랜드 접두어 제거만 한다. 계획 문서가 A안에 요구하는
"정규화 상품명 키(공백/기호/대소문자 정규화)"의 "기호 정규화"는 기존 함수
재사용만으로는 충족되지 않으며, Phase 5 구현 시 신규 정규화 로직이 필요하다.

### 5. glb 채널은 오늘 이미 "부분적으로" 흘러들어온다 — 명시적 차단 없음

`src/loaders/rs_jsonl_loader.py`의 `_channel_to_site`(216-230행)는 이미
`"amazon": "Amazon", "sephora": "Sephora"` 매핑을 갖고 있고, `_source_key_type`
(242-248행)은 own 채널(031/036/039/048)만 처리해 그 외(extn/glb 포함)는
`None`을 반환한다. glb 레코드를 거부하는 코드는 없다 — 즉 rs.jsonl 입력에
glb 레코드가 섞여 들어오면 오늘도 `source_channel="amazon"/"sephora"`,
`source_key_type=None`, `source_product_id=<상품명 원문>`으로 그대로
`review_raw`에 적재된다(조사 1번의 폴백 매칭 경로를 타면서). **"보류"를
선택한다면 이 경로를 실제로 차단하는 가드가 별도로 필요하다** — 현재는
"보류"가 코드로 강제되어 있지 않고 운영(입력 파일을 안 보내는 것)에만
의존한다.

### 6. 스키마는 유연하다 — 어떤 안을 택해도 마이그레이션 비용은 0

`sql/ddl_raw.sql`, `sql/ddl_mart.sql` 전체에서 `source_key_type` /
`source_channel` / `source_product_id` / `source_truth_quality`는 제약 없는
`text` 컬럼이다(`product_review_stats`만 `source_channel`/`source_key_type`에
`NOT NULL DEFAULT 'unknown'` + 복합 PK `(product_id, source_channel,
source_key_type)`, `ddl_raw.sql` 48-49, 62행). 새 key_type 문자열 값을
추가하는 것 자체는 마이그레이션이 필요 없다 — 비용은 매칭 로직/식별자
의미론에 있지 스키마에 있지 않다. `sql/ddl_quarantine.sql`의
`quarantine_product_match`(7-20행)도 review_id/source_brand/
source_product_name/attempted_match_score/method/status(PENDING/RESOLVED/
REJECTED)/resolved_product_id를 이미 갖추고 있어 B안은 신규 테이블·컬럼이
필요 없다. `src/db/repos/review_repo.py::upsert_review_catalog_link`
(96-117행)도 원문 `source_product_id`와 매칭 결과(`matched_product_id`,
`match_status`, `match_score`, `match_method`)를 이미 분리해서 저장한다 —
"원문 identity 감사 추적"과 "매칭된 product identity"가 이미 서로 다른
컬럼으로 나뉘어 있다.

## 검토한 선택지

### A안 — glb 전용 source_key_type 신설(예: `PRODUCT_NAME_KEY`) + 정규화 상품명 키

`rs_jsonl_loader._source_key_type()`에 amazon/sephora 분기를 추가해 전용
key_type을 반환하고, `source_product_id`를 원문 대신 정규화된 상품명으로
바꾼 뒤 그 키에서 신규 `product_id`를 1:1로 발급해 glb 상품을 own/extn과
별개의 `product_master` row로 온보딩한다.

- **기존 계약과의 정합성**: `source_channel + source_key_type +
  source_product_id` 3-tuple을 명시적으로 채우므로 §3 계약의 문면은
  만족한다. 그러나 조사결과 3번대로 **Phase 1.1 collision validator의
  탐지 사각지대(동일 channel·key_type 내부 충돌)에 정확히 놓인다.** "검증기가
  이미 있으니 안전하다"고 오인하면 안 되고, A안을 채택한다면 별도의 신규
  검증(예: 동일 key_type 내 정규화 키 그룹별 브랜드/카테고리 불일치 탐지)을
  **함께** 구현해야 한다 — 이는 Phase 1.1 산출물의 단순 확장이 아니라 신규
  구현이다.
- **재적재 비용**: 온보딩 "시작 시점"의 비용은 낮다(스키마 변경 없음,
  조사결과 6번). 그러나 정규화 규칙이 처음에 불완전하면(조사결과 4번 —
  기호 정규화 미구현) 나중에 강화할 때 이미 발급된 `product_id`/`product_iri`가
  전부 바뀌어야 하고, 이는 배경에서 설명한 전체 재처리 비용을 그대로
  발생시킨다. 즉 A안은 "재적재 비용을 피하기 위해 사전에 잘 정해야 하는
  대상"을 하나 더(정규화 규칙) 만든다.
- **오염 위험**: "상품명이 바뀌면 다른 상품 취급"이라는 근본 문제는 A안으로
  **해결되지 않는다** — 정규화는 공백/기호/대소문자 노이즈만 흡수하고
  의미적 리네이밍(셀러의 리스팅 타이틀 수정 등)은 여전히 새 product_id를
  만든다. brnd_nm이 대체로 비어 있다는 조사결과 2번 때문에 이름만으로 만든
  키는 동명이상품 충돌 확률을 구조적으로 높인다.

### B안 — 상품마스터 매칭 성공(고신뢰)분만 수용, 미매칭은 quarantine

신규 `product_id`/`product_master` row를 glb로부터 만들지 않는다. 조사결과
1번의 기존 폴백 경로(`_match_product_by_source_id` 실패 → `match_product`
퍼지 체인, 0.93 auto-accept / 0.80~0.93 quarantine / <0.80 quarantine)를
그대로 재사용한다.

- **기존 계약과의 정합성**: 가장 높다. `review_raw`/`review_catalog_link`에는
  원문 `source_channel`(amazon/sephora)·`source_product_id`(상품명 원문)를
  감사 추적용으로 그대로 남기되(조사결과 6번의 기존 컬럼 분리 구조 재사용),
  **product_master 쪽 identity는 매칭된 기존 product_id를 그대로 쓴다** —
  신규 identity를 만들지 않으므로 collision validator의 탐지 범위/사각지대
  논쟁 자체가 발생하지 않는다.
- **재적재 비용**: 가장 낮다. 신규 identity 스킴이 없으므로 나중에
  매칭 품질을 높여도(`fable_doc/03_improvement_plan.md` Phase 3.3 "한글 인지
  퍼지 매칭 개선"이 이미 계획되어 있고 "기존 임계값 체계(0.93/0.80)와
  quarantine 흐름은 유지"라고 명시) 같은 파이프라인(`process_review`)을
  다시 돌리기만 하면 된다 — identity 스킴을 바꾸는 재작업이 아니라 같은
  스킴에서 더 정확히 매칭하는 재작업이라 비용이 작다.
- **오염 위험**: 조사결과 2번의 "brnd_nm 공백 시 무필터 fuzzy 매칭" 위험은
  B안으로도 해결되지 않지만, 오매칭이 발생해도 **기존에 검증된 product_id**
  위에 증거가 잘못 붙는 것이므로 A안(신규 product_id 자체가 여러 실제
  상품을 병합)보다 파급 범위가 작다. 미매칭 상품은 quarantine되어 추천
  증거로 승격되지 않는다 — 이는 회피가 아니라 "증명되지 않은 신규 identity
  스킴을 만들기보다 recall 손실을 감수"하는 의도적 선택이다.
- **한계**: own/extn 카탈로그에 없는 glb 전용 SKU의 리뷰는 전부 quarantine되어
  영구히 유실된다(별도 상품마스터 피드가 생기기 전까지).

### C안 — glb 온보딩 보류

막는 조건: 이 문서가 A/B/D 중 하나로 확정되기 전. 해제 조건: 사용자가 전략을
확정하고 Phase 5(계획 문서 백로그, 착수 조건 "1.2의 glb identity 전략 결정
선행")에 착수할 때.

- **정합성/재적재/오염**: 신규 identity를 만들지 않으므로 세 위험 모두
  0이다. 그러나 조사결과 5번대로 **오늘 코드가 glb 레코드를 실제로 차단하지
  않으므로**, C안을 단독으로 채택하려면 "결정을 안 하는 것"이 아니라
  "차단을 코드로 강제하는 것"까지 포함해야 실효성이 있다. 또한 계획
  문서(`fable_doc/03_improvement_plan.md` Phase 1.2)가 이미 "구현은 glb
  온보딩 시점(Phase 5)"이라고 못박아 두었으므로, C안만 단독 채택하면 이번
  Phase 1.2가 요구하는 "사전 결정" 목적(Phase 5에서 재논의 없이 바로 구현)을
  충족하지 못하고 동일 논의를 반복하게 된다.

### D안(혼합, 권고) — B안을 기본 전략으로 확정 + 현재 상태에 대한 즉시 가드(C적 성격) + A안은 조건부 유보

B안을 Phase 5 구현 시의 기본 전략으로 사전 확정하고, 그 확정 자체가
C안(보류)이 지금 당장 요구하는 "차단"을 대체한다: "언제 시작해도 되는가"의
답이 이미 "성공 매칭분만, 나머지는 quarantine"으로 정해져 있으므로 별도
차단 없이도 안전하게 시작할 수 있다. A안은 채택하지 않되 향후 재검토
가능성을 조건과 함께 남긴다.

### 비교 요약

| 기준 | A안 (신규 key_type) | B안 (매칭 성공분만) | C안 (보류) |
|---|---|---|---|
| 기존 계약 정합성 | 문면상 충족, but collision validator 사각지대 (조사 3) | 최고 (신규 identity 없음) | 해당 없음 |
| 재적재 비용 | 정규화 규칙 부실 시 전체 재처리 위험 | 최저 (동일 스킴 재실행) | 0 (구현 안 함) |
| 오염 위험 | 동명이상품 충돌 해결 안 됨, brnd_nm 공백으로 증폭 | brnd_nm 위험 잔존하나 기존 product_id 위에서만 | 0 |
| 신규 코드 필요량 | 정규화 함수 + key_type 분기 + 신규 collision 검증 | 거의 없음 (기존 경로 재사용, 조사 1) | 가드 추가(차단 목적) |
| glb 전용 SKU recall | 확보 가능 | quarantine으로 유실 | 유실(온보딩 자체 없음) |

## 결정 (권고)

**상태: 권고, 사용자 확정 대기.** glb 온보딩 구현 자체는
`fable_doc/03_improvement_plan.md` Phase 5 백로그이며, 이 문서는 그 착수
조건인 전략만 사전 확정한다.

1. **기본 전략은 B안.** glb 리뷰는 기존 `product_matcher.match_product` 체인
   (0.93 auto-accept / 0.80~0.93 quarantine / <0.80 quarantine, 신규 코드
   불필요 — 조사결과 1번)으로 **기존 product_master 카탈로그에 매칭된 경우에만**
   리뷰 증거로 승격한다. 미매칭은 기존 `quarantine_product_match`로 보낸다.
   glb 리뷰로부터 신규 `product_id`/`product_master` row를 만들지 않는다.
2. `review_raw`/`review_catalog_link`의 `source_channel`(amazon/sephora)과
   `source_product_id`(상품명 원문)는 감사 추적용으로 그대로 보존한다(조사결과
   6번의 기존 컬럼 분리 구조를 그대로 재사용). `source_key_type`에는 "권위
   있는 코드가 아니라 매칭 힌트"임을 나타내는 전용 값(예: `name_hint` — 최종
   명칭은 Phase 5 구현 시 확정)을 부여할 수 있으나, **이 값을
   product_master 신규 identity 발급에는 사용하지 않는다.** 품질 마커와
   identity 발급 경로를 분리해야 A안의 위험이 우회 재유입되지 않는다 — Phase
   5 구현자가 반드시 지켜야 할 경계로 명시한다.
3. **즉시 가드 (해제 전 임시 조치)**: 조사결과 5번대로 현재 로더가
   amazon/sephora를 이미 부분적으로 흘려보내므로, glb rs.jsonl 입력이 실제로
   파이프라인에 들어오는 시점부터는 위 1-2번 규칙이 코드로 구현되어 있어야
   한다. Phase 5 착수 전까지는 glb 데이터가 입력되지 않도록 운영으로
   관리한다(입력 파일 미투입). Phase 5 착수 시 첫 작업으로 이 문서의 규칙을
   코드에 반영한다.
4. **A안은 채택하지 않되 폐기하지도 않는다.** own/extn 카탈로그에 없는
   glb 전용 SKU의 리뷰 회수율이 실제로 문제가 된다고 **실측되면**(계획
   문서가 Phase 4.0에서 이미 쓴 "수요 실증 후 확장" 패턴과 동일한 원칙),
   그때 A안을 별도 DECISIONS로 재검토한다. 단, 그 시점에도 조사결과 3번의
   collision-validator 사각지대(동일 channel·key_type 내부 충돌 비탐지)를
   메우는 신규 검증을 **함께** 구현해야 A안을 안전하게 채택할 수 있다는
   전제를 남긴다.

## 트레이드오프

- B안 채택으로 own/extn 카탈로그에 없는 glb 전용 SKU의 리뷰는 회수되지
  않는다(recall 손실). "새 identity 스킴을 만들어 정합성 리스크를 감수"하는
  대신 "일부 데이터 유실을 감수"하는 명시적 선택이다.
- brnd_nm 공백 시 무필터 퍼지매칭 위험(조사결과 2번)은 이번 결정으로
  해결되지 않는다 — `fable_doc/03_improvement_plan.md` Phase 3.3(한글 인지
  퍼지 매칭 개선)의 범위이며, glb 온보딩 전 안전장치(브랜드 공백 입력에
  대한 임계값 상향 또는 카테고리 등 보조 신호 교차검증)를 그때 함께
  검토해야 한다.
- quarantine으로 빠지는 glb 리뷰 볼륨이 크면 `quarantine_product_match`의
  누적 부담이 커진다 — `docs/architecture/db_consumer_contract.md` §12.3의
  "알려진 무한 누적 위험 3종" 중 하나(`quarantine_*` 5개 테이블, TTL 없음)와
  직접 맞물린다. Phase 1.3(운영 최소 안전장치)/Phase 5(retention 구현)와
  함께 모니터링해야 한다.
- `review_raw`/`review_catalog_link`에 "매칭 힌트" key_type 값을 남기는 것은
  스키마 변경이 없지만(조사결과 6번), 문자열 상수가 여러 로더에 흩어지지
  않도록 Phase 5 구현 시 한 곳(예: `rs_jsonl_loader._source_key_type`류
  헬퍼)에 정의해야 한다.

## Follow-Up

- **사용자 확정 필요**: (a) B안(D안) 채택 여부, (b) `review_raw`에 남길
  key_type 마커의 최종 명칭, (c) A안을 향후 조건부 확장으로 열어둘지 여부.
- Phase 5 착수 시 구현 작업(이 문서 확정 후 별도 계획으로 분리):
  glb 채널 rs.jsonl 처리 분기 추가(현재 미차단 상태 해소), brnd_nm 공백
  대비 매칭 안전장치, `quarantine_product_match` 볼륨 모니터링 연결(Phase 1.3).
  A안 채택 시에는 신규 정규화 함수 + collision validator 사각지대를 메우는
  검증 로직을 구현 항목에 포함해야 한다.
- 이 문서로 `fable_doc/03_improvement_plan.md` Phase 1.2의 완료 기준
  ("DECISIONS 문서 1건")을 충족한다.

## 확정 기록 (2026-07-10)

사용자 승인으로 **D안 확정**. Follow-Up의 사용자 확정 3건에 대한 답:
(a) **D안 채택** — B안을 Phase 5 glb 온보딩의 기본 전략으로 확정
(b) key_type 마커 최종 명칭 **`name_hint` 채택** (Phase 5 구현 시 한 곳에 상수 정의)
(c) **A안 조건부 유보 유지** — glb 전용 SKU 리뷰 유실이 실측으로 문제 될 때
    별도 DECISIONS로 재검토하되, collision-validator 사각지대를 메우는 신규
    검증을 반드시 동반한다는 전제 그대로.

→ Phase 5 백로그 "다국어 사전 구조 개편 + glb 온보딩"의 착수 조건("1.2 결정
선행")이 충족됨. 착수 자체는 백로그 우선순위 결정에 따름.
