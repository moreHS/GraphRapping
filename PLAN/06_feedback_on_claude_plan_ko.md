# GraphRapping 구현 계획서 상세 피드백 (GPT 리뷰)

## 0. 총평

현재 계획서는 방향이 전반적으로 맞다.
특히 아래 6가지는 핵심적으로 옳다.

1. **5-Layer 분리**: Layer 0 truth / Layer 1 evidence / Layer 2 canonical / Layer 3 serving / Layer 4 rec-explain 분리가 적절하다.
2. **Postgres-first hybrid**: 조직 맥락상 가장 현실적이다.
3. **Layer 2에서 relation 65개 보존**: 반드시 유지해야 한다.
4. **Layer 3 projection registry 도입**: 추천/개인화용 serving edge를 결정적으로 만드는 데 필요하다.
5. **BEE_ATTR와 KEYWORD 분리 유지**: 꼭 지켜야 한다.
6. **reviewer proxy와 real user 분리**: 절대 섞으면 안 된다.

다만 지금 계획서는 **구현 가능한 수준의 큰 뼈대는 충분하지만**, Claude Code가 바로 막힘 없이 만들려면 다음 4개가 더 명시돼야 한다.

- 공통 개념층(Common Concept Layer)과 canonical entity registry
- Layer 2 canonical fact/provenance 스키마
- Projection Registry의 컬럼/결정 규칙
- quarantine / manual review / dictionary growth 루프

이 4개가 빠진 상태로 들어가면, 코드가 구현되더라도 나중에
`product_id 매칭`, `BEE/REL projection 충돌`, `user-product join`, `explanation evidence 회수`에서 다시 뜯게 된다.

---

## 1. 이 계획에서 좋은 점 / 그대로 유지해도 되는 점

### 1-1. Sprint 구조
현재 4 Sprint 구조는 타당하다.
- Sprint 1: ingest/link/normalize foundation
- Sprint 2: canonical + signal + user
- Sprint 3: recommendation/explanation
- Sprint 4: optional graph projection

이 순서는 지금 프로젝트 성숙도와 잘 맞는다.
특히 **AGE/Neo4j를 Sprint 4 optional**로 둔 건 좋다.

### 1-2. Projection Registry를 핵심 모듈로 본 점
이건 매우 좋다.
이 프로젝트의 진짜 핵심은 extractor가 아니라,
**canonical fact를 어떤 serving signal로 투영할지 결정하는 deterministic registry**다.

### 1-3. Product DB truth 원칙
반드시 맞다.
리뷰에서 뽑은 브랜드/성분/가격/카테고리는 **truth를 대체하는 값이 아니라 validation/enrichment signal**이어야 한다.

### 1-4. personal-agent 재사용
SignalBuilder / FieldRouter 재사용은 좋다.
다만 직접 import만으로 끝내면 안 되고, **adapter layer**를 두는 게 맞다.

### 1-5. hard filter / shrinkage / confidence-weight scoring
좋다. 다만 식이 필요하다.
현재는 의도는 맞고 수식 스펙이 부족하다.

---

## 2. 가장 먼저 수정/보강해야 할 핵심 12개

## 2-1. Product ingest가 Sprint 2에 있으면 늦다
현재 계획서는 `product_matcher.py`가 Sprint 1인데 `product_ingest.py`가 Sprint 2다.

이건 두 경우를 분리해서 명시해야 한다.

### 경우 A. 상품 DB를 외부 서비스/API/기존 DB에서 직접 조회
그럼 현재 순서도 가능하다.

### 경우 B. GraphRapping DB 안에 product master를 먼저 적재하고 그걸 matcher가 조회
그럼 **product_ingest는 Sprint 1로 당겨야 한다.**

### 권장 수정
- `product_ingest.py`를 Sprint 1로 올리거나,
- 최소한 “matcher는 외부 product DB를 직접 조회하는가 / GraphRapping 내부 product_master를 조회하는가”를 명시하라.

---

## 2-2. 공통 개념층(Common Concept Layer)을 명시적으로 넣어야 한다
현재 계획엔 user graph와 product graph를 연결한다는 설명은 충분하지만,
**무엇을 통해 연결되는지**가 구조적으로 덜 명시돼 있다.

실제로 join은 아래 공통 concept IDs를 통해 일어난다.

- Brand
- Category
- Ingredient
- BEEAttr
- Keyword
- TemporalContext
- Concern
- Goal
- Tool
- optional: ProductFamily, Effect, PriceBand, Country

### 꼭 추가해야 할 테이블/개념
- `concept_registry`
- `concept_alias`
- `entity_registry` (혹은 concept/entity 통합 registry)

예시:

```sql
concept_registry(
  concept_id text primary key,
  concept_type text not null,
  canonical_name text not null,
  canonical_name_norm text not null,
  source_system text,
  source_key text,
  is_active boolean not null default true
)
```

이게 있어야 user preference와 product signal이 **동일 concept_id**로 연결된다.

---

## 2-3. Layer 2는 “테이블 이름” 수준이 아니라 “fact model”이 필요하다
현재 계획엔 Layer 2 DDL 작성이 있지만, **canonical fact를 어떤 row 형태로 저장하는지**가 더 명확해야 한다.

권장 스키마:

### canonical_entity
```sql
canonical_entity(
  entity_iri text primary key,
  entity_type text not null,
  canonical_name text not null,
  canonical_name_norm text not null,
  source_system text,
  source_key text,
  match_confidence real,
  created_at timestamptz not null,
  updated_at timestamptz not null
)
```

### canonical_fact
```sql
canonical_fact(
  fact_id text primary key,
  review_id text,
  subject_iri text not null,
  predicate text not null,
  object_iri text,
  object_value_text text,
  object_value_num double precision,
  object_value_json jsonb,
  subject_type text not null,
  object_type text,
  polarity text,
  confidence real,
  source_modality text not null,
  extraction_version text,
  registry_version text,
  valid_from timestamptz,
  valid_to timestamptz,
  created_at timestamptz not null
)
```

### fact_provenance
```sql
fact_provenance(
  fact_id text not null,
  raw_table text not null,
  raw_row_id text not null,
  review_id text,
  snippet text,
  start_offset int,
  end_offset int,
  evidence_rank int,
  primary key (fact_id, raw_table, raw_row_id)
)
```

이 구조가 없으면 explanation과 audit가 약해진다.

---

## 2-4. Projection Registry의 스키마를 지금보다 더 엄격히 정의해야 한다
지금 계획의 핵심은 맞지만, registry CSV 컬럼이 더 정교해야 한다.

최소 컬럼 권장:

```text
registry_version
input_predicate
subject_type
object_type
polarity
qualifier_required
qualifier_type
output_signal_family
output_edge_type
output_dst_type
output_transform
output_weight_rule
if_unresolved_action
notes
```

### 예시
```text
v1,used_on,Product,TemporalContext,,N,,CONTEXT,USED_IN_CONTEXT_SIGNAL,TemporalContext,identity,default_weight,QUARANTINE,
v1,addresses,Product,Concern,POS,N,,CONCERN_POS,ADDRESSES_CONCERN_SIGNAL,Concern,identity,default_weight,QUARANTINE,
v1,causes,Product,Concern,NEG,N,,CONCERN_NEG,MAY_CAUSE_CONCERN_SIGNAL,Concern,identity,default_weight,QUARANTINE,
v1,has_attribute,Product,BEEAttr,POS,Y,Keyword,BEE_ATTR_SIGNAL,HAS_BEE_ATTR_SIGNAL,BEEAttr,identity,bee_weight,DROP_IF_NO_BEE,
```

### 반드시 필요한 규칙
- **1 input 조합 → 1 deterministic action**
- 매핑 불가 조합은 명시적으로 `DROP / QUARANTINE / KEEP_CANONICAL_ONLY`
- projection registry version을 `wrapped_signal` row에 기록

---

## 2-5. `relation_canonicalizer.py`는 idempotent해야 한다
현재 자산을 보면 일부 데이터는 이미 65 canonical에 가까울 가능성이 있다.
그런데 구현이 633→65만 가정하면 중복 canonicalization 문제가 생길 수 있다.

### 권장 규칙
- 입력 predicate가 이미 canonical set에 있으면 그대로 통과
- raw relation이면 mapping 적용
- canonical set 밖이면 quarantine

즉 함수 시그니처 수준에서 **idempotent transformer**로 명시해야 한다.

---

## 2-6. `DATE` splitter는 3분류보다 4분류가 안전하다
현재 계획은 `TemporalContext / Frequency / Duration` 3개로 나누고 있다.
대부분 맞지만, 실제 데이터에서는 아래가 섞일 수 있다.

- 아침, 세안후, 여름 → TemporalContext
- 하루에 1번, 매일 → Frequency
- 2주째, 한달동안 → Duration
- 2024년 여름 세일 때, 3월 1일 주문 → **AbsoluteDate / EventDate**

### 권장
`DATE -> TemporalContext | Frequency | Duration | AbsoluteDate` 4분기.

`AbsoluteDate`는 serving graph엔 거의 안 쓰더라도 canonical layer에 버리지 말고 보존하는 게 안전하다.

---

## 2-7. BEE normalizer는 polarity/negation/intensity를 독립 필드로 유지해야 한다
현재 계획은 `BEE_ATTR + KEYWORD` 분리는 잘 잡았지만,
실무에선 아래 3개가 매우 중요하다.

- polarity
- negation
- intensity/degree

예:
- “안 건조해요” → keyword=`건조감적음`, polarity=POS, negated=true
- “조금 두꺼워요” → keyword=`두껍게발림`, polarity=NEG 또는 NEU, intensity=low

### 권장 출력
```json
{
  "bee_attr_id": "bee_moisture",
  "keyword_ids": ["kw_low_dryness"],
  "polarity": "POS",
  "negated": true,
  "intensity": 0.4,
  "confidence": 0.91
}
```

이게 없으면 later scoring과 explanation fidelity가 떨어진다.

---

## 2-8. user derivation에는 “canonical user facts” 레이어가 하나 더 필요하다
현재 계획은 `user_ingest.py` + `aggregate_user_preferences.py`가 있는데,
product side처럼 user side도 **Layer 2 canonical facts**가 있어야 한다.

예:
- `HAS_SKIN_TYPE(User, OilySkin)`
- `HAS_CONCERN(User, Dryness)`
- `WANTS_GOAL(User, LongLasting)`
- `AVOIDS_INGREDIENT(User, Fragrance)`

즉 권장 흐름:

`user raw / summary`  
→ `canonical_user_fact`  
→ `agg_user_preference`  
→ `serving user profile`

지금 계획은 여기 중간층이 약간 생략돼 있다.

---

## 2-9. quarantine 테이블/큐가 더 많아야 한다
현재 계획은 product matching 저신뢰 quarantine만 강조한다.
하지만 실제로는 최소 5개가 필요하다.

### 권장 quarantine 종류
1. `quarantine_product_match`
2. `quarantine_placeholder_resolution`
3. `quarantine_unknown_keyword`
4. `quarantine_projection_registry_miss`
5. `quarantine_untyped_entity`

예:
- relation은 canonical인데 object type이 registry에 없는 경우
- keyword surface form이 dictionary에 없는 경우
- `used_with(X)`에서 X가 Tool인지 Product인지 분류 실패한 경우

---

## 2-10. user-product 연결은 “동일 노드 공유”가 아니라 “공통 개념층 join”이라고 문서에 더 강하게 써야 한다
지금 방향은 대체로 맞지만, 구현자가 헷갈릴 수 있다.

잘못 구현하면:
- reviewer_proxy를 user로 merge하거나
- review mention entity를 user preference entity로 그대로 재사용하거나
- product keyword와 user keyword를 다른 사전으로 관리해서 join이 안 될 수 있다.

### 문서에 박아야 할 문장
- reviewer_proxy와 real user는 절대 동일 identity로 merge하지 않는다.
- user/product 연결은 `shared concept_id`를 통해서만 한다.
- concept dictionary는 product/user 모두 공통으로 사용한다.

---

## 2-11. window/retention/freshness 정책이 빠져 있다
사용자가 이미 “일정 기간만 사용하지만 몇 개월 단위”라고 했기 때문에,
Layer 3 집계는 반드시 **windowed aggregate**여야 한다.

### 최소 필요 mart
- `agg_product_signal_30d`
- `agg_product_signal_90d`
- `agg_product_signal_all`

또는 `window_type` 컬럼 방식.

### 권장 컬럼
```sql
window_type text not null,   -- 30d / 90d / all
support_count int not null,
recent_support_count int,
score real,
recent_score real,
last_seen_at timestamptz,
```

이게 있어야 “최근 트렌드”와 “장기 평판”을 분리할 수 있다.

---

## 2-12. scoring 식이 더 구체화돼야 한다
현재 계획은 개념적으로 맞다. 하지만 코드 구현을 위해선 식이 더 필요하다.

### 권장 baseline
1. hard filter 먼저
2. 남은 후보에 linear scoring
3. evidence shrinkage 적용
4. calibration/rerank

예시:

```text
raw_score
= 0.22 * bee_attr_match
+ 0.18 * keyword_match
+ 0.15 * context_match
+ 0.15 * concern_fit
+ 0.10 * ingredient_match
+ 0.08 * brand_match_conf_weighted
+ 0.07 * category_affinity
+ 0.05 * freshness_boost

shrinked_score
= raw_score * (support_count / (support_count + k))

final_score
= shrinked_score - hard_conflict_penalty
```

### hard filter 예시
- `AVOIDS_INGREDIENT`와 product ingredient 충돌 → zero-out
- price ceiling 초과 → zero-out
- category mismatch 강함 → zero-out 또는 큰 penalty

### brand match confidence-weight 예시
브랜드 선호가 purchase 기반이면 weight 1.0,
chat 기반 weak preference면 0.4처럼 차등.

---

## 3. 추가로 꼭 넣으면 좋은 설계 포인트

## 3-1. deterministic ID 전략을 더 명시하라
`ids.py`는 단순 UUID 생성기가 아니라 **결정적(deterministic) ID 생성기**여야 한다.

예:
- `review_id = hash(source + source_review_key)`
- `fact_id = hash(review_id + subj_iri + predicate + obj_iri + provenance_key)`
- `signal_id = hash(review_id + product_id + edge_type + dst_id + registry_version)`

그래야 재실행 시 idempotent upsert가 가능하다.

---

## 3-2. adapter layer를 두는 게 좋다
personal-agent 코드 재사용은 좋은데, 경로를 직접 import하면 결합도가 커진다.

### 권장
`src/user/adapters/personal_agent_adapter.py`

역할:
- SignalBuilder 입력 스키마 변환
- FieldRouter 결과를 GraphRapping canonical fact로 변환
- upstream 변경 시 adapter만 수정

---

## 3-3. Tool / Concern / Segment derivation은 dictionary + model hybrid가 필요하다
단순 dict만으론 부족하다.

예:
- “퍼프로” → Tool
- “건조한 날에도” → Concern + Context
- “엄마한테 추천” → PER raw evidence only
- “건성 피부인 엄마한테 추천” → Segment=건성피부

즉 도출 규칙은:
1. exact dictionary
2. normalized dictionary
3. pattern rule
4. fallback classifier

---

## 3-4. `used_with`와 `recommended_to`는 분기 로직이 더 필요하다
### `used_with(target, X)`
- X=퍼프 → Tool
- X=프라이머 → Product
- X=브러시 → Tool

### `recommended_to(target, X)`
- X=엄마 → raw only
- X=건성 피부인 엄마 → Segment=건성피부
- X=민감성 피부 → Segment=민감성피부

즉 registry 하나로 끝나는 게 아니라 **type derivation 전 단계**가 있어야 한다.

---

## 3-5. explanation evidence sample은 Top-K ref를 저장하라
GPT Architect 피드백에 `evidence_sample -> top-k evidence refs`가 들어간 건 매우 좋다.
이를 실제 테이블 컬럼으로 박는 게 좋다.

예:
```sql
evidence_sample jsonb  -- [{review_id, bee_id, rel_id, snippet, score}, ...]
```

Product signal aggregate row마다 상위 3~5개 evidence를 저장해두면 explainer가 빨라진다.

---

## 4. Sprint별 구체 피드백

## Sprint 1 피드백

### 유지해도 되는 것
- review ingest
- product matcher
- placeholder resolver
- date splitter
- projection registry 초안

### 추가/수정 필요
1. `product_ingest.py` 위치 재검토
2. `canonical_entity`, `canonical_fact`, `fact_provenance` DDL 구체화
3. `match_status`, `match_score`, `match_method` 컬럼 명시
4. quarantine 테이블 DDL 추가
5. alias resolver에 다국어/romanization 규칙 추가

### Sprint 1 완료 기준 보강
- exact / norm / alias / fuzzy / quarantine 각각 최소 1건 테스트
- same_entity union-find로 1 review 내부 placeholder 해소 검증
- DATE splitter 4분류 테스트

---

## Sprint 2 피드백

### 유지해도 되는 것
- BEE normalizer
- relation canonicalizer
- signal emitter
- user ingest/agg
- serving view

### 추가/수정 필요
1. `canonical_user_fact` 추가
2. `projection_registry` completeness test 추가
3. `relation_canonicalizer` idempotent 보장
4. `signal_emitter` dedup 기준 명시
5. `BEE_ATTR + KEYWORD + polarity/negation/intensity` 출력 구조 확정

### Sprint 2 완료 기준 보강
- review 1건에서 `canonical_fact`가 생성되고 provenance가 연결됨
- projection registry 없는 combo는 quarantine됨
- user raw → canonical user fact 변환 확인
- master truth + signal join된 serving view 생성 확인

---

## Sprint 3 피드백

### 유지해도 되는 것
- hard filter -> candidate -> scoring -> explanation -> hook/question

### 추가/수정 필요
1. scoring 식과 default coefficients를 config화
2. hard filter와 soft score를 코드 구조상 분리
3. explanation은 score-faithful subset만 사용
4. next-best-question은 uncertainty axis 기반으로만 생성

### Sprint 3 완료 기준 보강
- 동일 입력에 deterministic top-k 추천
- explanation path가 실제 score feature와 충돌하지 않음
- hard exclusion 케이스 zero-out 재현

---

## Sprint 4 피드백

### 유지해도 되는 것
- AGE/Neo4j optional materializer
- analyst queries
- incremental pipeline

### 추가/수정 필요
1. graph projection은 interface 먼저 정의
2. AGE/Neo4j 둘 중 하나만 먼저 구현해도 됨
3. graph projection은 mart 기반 read-model 생성기라고 명시

### 그래프 projection 추천 순서
- 1차: Postgres SQL serving
- 2차: AGE projection if query pain exists
- 3차: Neo4j projection if analyst/explainer needs path traversal heavily

---

## 5. 테스트 전략 보강

현재 테스트 구조는 좋지만 아래 5개를 더 넣는 게 좋다.

### 5-1. registry completeness test
실제 데이터에 등장하는 `(predicate, subj_type, obj_type, polarity)` 조합을 샘플링해서,
projection registry에 반드시 매핑이 있거나 explicit drop/quarantine rule이 있어야 한다.

### 5-2. idempotency test
같은 리뷰를 두 번 넣어도 `canonical_fact`, `wrapped_signal`, `agg`가 중복되지 않아야 한다.

### 5-3. provenance fidelity test
추천 설명에 쓰인 evidence ref가 실제 raw row/snippet으로 역추적 가능해야 한다.

### 5-4. truth override protection test
리뷰에서 잘못 뽑힌 brand/category/ingredient가 product master를 덮어쓰지 못해야 한다.

### 5-5. reviewer-real-user isolation test
reviewer proxy와 real user가 같은 concept graph에 잘못 merge되지 않아야 한다.

---

## 6. 권장 추가 파일

지금 구조에 아래 파일을 추가하면 좋다.

```text
src/user/
  adapters/
    personal_agent_adapter.py
  canonicalize_user_facts.py

src/qa/
  quarantine_handler.py
  dictionary_growth.py
  evidence_sampler.py

sql/
  ddl_quarantine.sql
  views_serving.sql
```

---

## 7. Claude Code에 넘길 때 같이 적어둘 문장

아래 문장을 구현 지시서 맨 앞에 넣는 걸 권장한다.

1. **Layer 2는 relation 65개를 절대 잃어버리지 않는다.**
2. **Layer 3는 Projection Registry를 통해서만 생성한다. 임의 projection 금지.**
3. **Product/User 연결은 shared concept_id를 통해서만 한다.**
4. **reviewer proxy와 real user는 절대 merge하지 않는다.**
5. **Product master truth는 review-derived signal로 override하지 않는다.**
6. **모든 signal은 provenance 역추적이 가능해야 한다.**
7. **매핑 실패/미분류는 침묵 drop이 아니라 explicit quarantine가 기본이다.**

---

## 8. 최종 판단

이 계획서는 **방향은 맞고, 바로 구현에 들어갈 수 있는 수준에 꽤 근접했다.**
다만 Claude Code가 실제로 막힘 없이 코드를 생성하게 하려면, 아래 5개는 먼저 문서에 박아두는 게 좋다.

1. Layer 2 canonical fact / provenance 테이블 스펙
2. Common Concept Layer / concept registry
3. Projection Registry 컬럼 정의 + completeness test
4. quarantine 체계
5. scoring baseline 식

이 5개만 보강하면, 현재 계획서는 충분히 실행 가능한 수준이다.

