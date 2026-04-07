# Step 1 상세 실행 작업지시서 — Texture 정본화

## 1. 목적
Texture를 **상위 BEE_ATTR 축 + 하위 KEYWORD 표현**의 2단 구조로 완전히 고정한다.

예:
- 상위 축(BEE_ATTR): `Texture`
- 하위 KEYWORD: `GelLike`, `LightLotionLike`, `WateryLike`, `RichCreamLike`

이 구조는 user와 review 모두에서 동일하게 쓰여야 한다.
현재 방향은 맞지만, taxonomy가 둘 이상의 파일에 중복돼 drift 위험이 있다.

---

## 2. 목표
1. texture taxonomy의 authoritative source를 하나로 고정한다.
2. user adapter와 review normalizer가 동일한 texture taxonomy version을 사용한다.
3. `keyword_surface_map.yaml`의 texture 섹션이 수동 관리 대상이 아니라 생성/검증 대상이 되게 한다.
4. texture 관련 user preference와 review signal이 동일한 KEYWORD/BEE_ATTR로 수렴하도록 보장한다.

---

## 3. 현재 상태 요약
- `configs/texture_keyword_map.yaml`
  - `texture_axis: "Texture"`
  - surface → keyword 정규화 규칙 보유
- `configs/keyword_surface_map.yaml`
  - texture 항목도 일부 중복 포함
- `src/user/adapters/personal_agent_adapter.py`
  - `preferred_texture`를 상위 `PREFERS_BEE_ATTR(Texture)`와 하위 `PREFERS_KEYWORD(...)`로 함께 생성
- review-side keyword normalization도 texture를 다루지만, taxonomy 정본이 완전히 한 곳으로 고정됐다고 보긴 어렵다.

---

## 4. 방향성 규칙 (불변)
- Texture는 BEE_ATTR 축이다.
- `젤`, `가벼운 로션`, `워터리`, `리치 크림` 등은 Texture 축 아래 KEYWORD다.
- scoring에서는 **keyword 우선 / residual BEE_ATTR backoff**를 유지한다.
- explanation에서는 **Texture 축 + 하위 keyword**를 함께 노출한다.
- user와 review가 다른 texture keyword namespace를 쓰면 안 된다.

---

## 5. 수정 대상 파일

### 핵심 파일
- `configs/texture_keyword_map.yaml`
- `configs/keyword_surface_map.yaml`
- `src/user/adapters/personal_agent_adapter.py`
- `src/normalize/bee_normalizer.py`
- `src/normalize/keyword_normalizer.py` 또는 review-side keyword 처리 경로
- `src/common/config_loader.py` (필요 시)
- `tests/test_texture_taxonomy_alignment.py`
- `tests/test_texture_preference_flow.py`

### 선택적 신규 파일
- `scripts/generate_texture_keyword_surface_map.py`
- `tests/test_texture_taxonomy_generation.py`

---

## 6. 상세 구현 지시

### 6-1. `texture_keyword_map.yaml`을 정본으로 선언
#### 해야 할 일
`configs/texture_keyword_map.yaml` 맨 위에 아래 메타를 추가한다.

```yaml
version: "v1"
authoritative: true
texture_axis: "Texture"
keywords:
  GelLike:
    surfaces: ["젤", "젤타입", "젤 타입"]
  LightLotionLike:
    surfaces: ["가벼운 로션", "가벼운로션", "라이트 로션"]
  WateryLike:
    surfaces: ["워터리", "묽은 제형"]
  RichCreamLike:
    surfaces: ["리치 크림", "쫀쫀한 크림"]
```

#### 목적
이 파일 하나를 texture taxonomy SoT로 고정한다.

---

### 6-2. `keyword_surface_map.yaml`의 texture 섹션을 생성/검증 대상으로 전환
#### 해야 할 일
두 방식 중 하나를 선택한다.

##### 권장안 A
`keyword_surface_map.yaml`에서 texture 섹션을 제거하고, runtime에 `texture_keyword_map.yaml`을 merge해서 사용한다.

##### 대안 B
`keyword_surface_map.yaml`에 texture 섹션을 남기되, `scripts/generate_texture_keyword_surface_map.py`로 자동 생성하고 수동 편집 금지 주석을 단다.

예:
```yaml
# AUTO-GENERATED FROM texture_keyword_map.yaml — DO NOT EDIT MANUALLY
젤: GelLike
젤타입: GelLike
가벼운 로션: LightLotionLike
```

#### 목적
두 파일의 drift를 막는다.

---

### 6-3. user adapter와 review normalizer의 공통 로더 사용
#### 수정 파일
- `src/user/adapters/personal_agent_adapter.py`
- review-side keyword normalization 경로 (`src/normalize/bee_normalizer.py`, `keyword_normalizer.py` 등)

#### 해야 할 일
공통 helper 추가:

```python
def load_texture_taxonomy() -> TextureTaxonomy:
    ...
```

또는 `config_loader.py`에 texture taxonomy loader 추가.

user/review 양쪽 모두 같은 loader를 사용해야 한다.

#### 목적
Texture taxonomy version drift를 방지한다.

---

### 6-4. review-side normalization에서 texture 축 처리 명시화
#### 해야 할 일
review-side BEE/keyword 처리 시:
- texture 관련 phrase는 먼저 texture taxonomy를 조회
- 매칭되면
  - BEE_ATTR = Texture
  - KEYWORD = canonical texture keyword
- 매칭 실패 시
  - 일반 keyword normalizer로 fallback
  - 또는 quarantine 후보로 보냄

#### 금지
- review 쪽에서 texture phrase를 별도 독자 keyword namespace로 생성하는 것

---

### 6-5. explanation에서 attr + keyword 같이 출력
#### 수정 파일
- `src/rec/explainer.py`

#### 해야 할 일
texture 관련 explanation은 아래처럼 나오게 한다.

예:
- "이 제품은 **제형(Texture) 축**에서 신호가 강하고, 특히 **GelLike(젤 계열)** 표현이 반복됩니다."

#### 목적
상위 축과 하위 표현을 동시에 보여줘 의미 손실을 막는다.

---

## 7. Acceptance Criteria
1. `texture_keyword_map.yaml`이 texture 정본으로 선언된다.
2. user adapter와 review normalizer가 동일 taxonomy loader를 사용한다.
3. texture surface form은 user/review 양쪽에서 같은 KEYWORD로 정규화된다.
4. `keyword_surface_map.yaml`의 texture 항목이 수동 drift 없이 유지된다.
5. explanation에 Texture 축과 하위 keyword가 함께 나타난다.

---

## 8. 테스트 항목

### 8-1. `tests/test_texture_taxonomy_alignment.py`
- user input `젤` → `Texture + GelLike`
- review phrase `젤 타입이에요` → `Texture + GelLike`
- user/review 결과가 동일 keyword id로 수렴하는지 검증

### 8-2. `tests/test_texture_preference_flow.py`
- `preferred_texture=["젤","가벼운 로션"]`
- user canonical facts에
  - `PREFERS_BEE_ATTR(Texture)`
  - `PREFERS_KEYWORD(GelLike)`
  - `PREFERS_KEYWORD(LightLotionLike)`
  생성 확인

### 8-3. `tests/test_texture_taxonomy_generation.py` (선택)
- `texture_keyword_map.yaml` → generated `keyword_surface_map` texture section이 예상대로 생성되는지 검증

---

## 9. 완료 후 검토 포인트
- texture taxonomy version을 recommendation response metadata에 남길지 검토
- review-side에서 texture phrase 미매칭 비율을 QA metric으로 볼지 검토
