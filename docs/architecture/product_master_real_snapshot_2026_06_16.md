# 2026-06-16 Real Product Master Snapshot

## 목적

906개 v260605 테스트 리뷰가 참조하는 상품 키를 오늘자 실상품마스터로
로컬 고정했다. 기존 `mockdata/product_catalog_es.json`는 mock synthesis
기반이라 브랜드, 가격, 성분, 효능, 원천 리뷰통계가 대부분 손실되어 있었고,
이번 스냅샷은 개인화 에이전트/요약 파이프라인의 실제 조회 경로를 기준으로
다시 구성했다.

## 조회 경로

- ES: `/Users/amore/workplace/agent-aibc/review-agent/src/common/retriever.py`
  - index: `amore-prod-mstr`
  - key: `ONLINE_PROD_SERIAL_NUMBER`
  - 결과: 517 distinct product id 중 380개 매칭
- Snowflake:
  `/Users/amore/workplace/inference-gerter/sm_batch_pipeline/src/scripts/preprocessing.py`
  및
  `/Users/amore/workplace/rs_origin/service-rs/sm_batch_pipeline/src/utils/sku_enrichment/snowflake_query.py`
  - auth: dev config + AWS Secrets Manager key-pair auth
  - tables:
    - `cdp.sf_cdpdw.f_prd_rv_hist`
    - `cdp.sf_cdpdw.d_chn_prd_mstr`
  - 결과: 518 source identity 모두 매칭

비밀값은 문서와 스냅샷에 저장하지 않았다.

## 키 규칙

실측 결과, 리뷰의 `source_product_id`는 채널별로 의미가 다르다.

| channel | `source_product_id` 원천 컬럼 | matched ids |
| --- | --- | ---: |
| `031` | `f_prd_rv_hist.ECP_ONLN_PRD_SRNO` | 358/358 |
| `036` | `f_prd_rv_hist.CHN_PRD_CD` | 156/156 |
| `039` | `f_prd_rv_hist.CHN_PRD_CD` | 2/2 |
| `048` | `f_prd_rv_hist.CHN_PRD_CD` | 2/2 |

따라서 lossless 식별자는 단순 `prd_id`가 아니라
`source_channel + source_key_type + source_product_id`이다.

## 충돌

`source_product_id = 35119`는 실제 충돌이다.

| source identity | product name | brand |
| --- | --- | --- |
| `031:ecp_onln_prd_srno:35119` | 세라마이드 아토 버블워시 앤 샴푸 | 일리윤 |
| `036:chn_prd_cd:35119` | 스페셜 케어 마스크 [풋] | 이니스프리 |

현행 GraphRapping schema는 `product_master.product_id` 단일키라 두 상품을
동시에 lossless 적재할 수 없다. 그래서 compatibility catalog에서는
`35119`를 실제 상품 하나로 위장하지 않고 `SOURCE_KEY_COLLISION` 품질로
표시했다. 이 상태는 안전한 임시 호환책이며, 최종적으로는 product identity를
composite source identity로 승격하는 schema 변경이 필요하다.

## 로컬 파일

| path | grain | count | 설명 |
| --- | --- | ---: | --- |
| `data/source_snapshots/product_master_es_2026-06-16.json` | ES product id | 380 | 개인화 에이전트 ES 조회 원본 |
| `data/source_snapshots/product_master_snowflake_2026-06-16.json` | source identity | 518 | Snowflake 실측 원천 마스터/리뷰통계 |
| `data/source_snapshots/product_master_source_identity_merged_2026-06-16.json` | source identity | 518 | ES 풍부 필드 + Snowflake 채널/통계 병합 |
| `data/source_snapshots/product_master_source_identity_latest.json` | source identity | 518 | latest copy |
| `data/source_snapshots/product_master_compat_product_id_2026-06-16.json` | legacy product id | 517 | 현행 GraphRapping 로더 호환본 |
| `data/source_snapshots/product_master_compat_product_id_latest.json` | legacy product id | 517 | latest copy |
| `mockdata/product_catalog_es.json` | legacy product id | 517 | full load 입력 파일 |

compat catalog 품질:

- `SOURCE_GROUNDED`: 516
- `SOURCE_KEY_COLLISION`: 1
- brand present: 516
- price present: 353
- ingredients present: 203
- main benefits present: 90
- source review stats present: 516

source identity snapshot 품질:

- `SOURCE_GROUNDED`: 518
- source review stats present: 518
- missing source identity: 0

## DB 적재 결과

2026-06-16 기준 로컬 `graphrapping` Postgres는 데이터 테이블을 truncate 후
오늘자 catalog로 full load 재적재했다.

| table/check | count |
| --- | ---: |
| `pipeline_run` latest status | `COMPLETED` |
| `product_master` active | 517 |
| `product_review_stats` | 516 |
| `user_master` active | 50 |
| `review_raw` active | 906 |
| `review_catalog_link` | 906 |
| `serving_product_profile` | 517 |
| `serving_user_profile` | 50 |
| `agg_product_signal` | 6849 |
| `review_catalog_link` joined to `product_master` | 906/906 |
| `source_product_id = matched_product_id` | 906/906 |

`35119` 관련 리뷰 2건은 둘 다 `SOURCE_KEY_COLLISION` master에 연결된다.
이는 잘못된 단일 상품으로 연결하는 것보다 안전한 표현이다.

## 검증

실행 결과:

- `python -m pytest tests/test_product_truth_merge.py tests/test_product_loader_mock_schema.py tests/test_source_product_id_contract.py tests/test_product_review_stats_repo.py -q`
  - `33 passed, 1 skipped`
- `validate_all(...)`
  - status: `OK`
  - active products: 517
  - active users: 50
  - concepts: 407
  - promoted signals: 70
  - product id mismatches: 0

## 남은 보완

1. `product_master.product_id` 단일키 구조는 cross-channel key collision을
   lossless 표현하지 못한다.
2. 다음 schema 설계는 `source_channel + source_key_type + source_product_id`
   를 별도 product source identity로 두고, internal product id와 source
   identity를 분리해야 한다.
3. AmoreSimulation 소비 쿼리는 현행 `product_id`뿐 아니라
   `source_product_id`, `source_channel`, `source_key_type`,
   `source_truth_quality`를 함께 읽어야 collision/누락 상태를 안전하게
   처리할 수 있다.
