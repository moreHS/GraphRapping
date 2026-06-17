"""Synthesize `mockdata/review_triples_raw.json` from ver260605 inputs.

Inputs (외부 폴더):
  /Users/amore/Jupyter_workplace/Relation/source_data/ver260605/
    - final_relation_ko_ner2ner.jsonl   (1,400 reviews, NER-NER 관계)
    - fin_ko_ner2bee_true_0528.jsonl    (1,495 reviews, NER-BeE 관계)
    - rs_own.jsonl                      (3,410 reviews, 메타/스키마 reference)

Output:
  mockdata/review_triples_raw.json      (906 reviews, GraphRapping 운영 입력 형식)

Default write behavior:
  - writes only review_triples_raw.json
  - does not overwrite product_catalog_es.json, because the final checked-in
    catalog is source-grounded from the 2026-06-16 product master snapshot

Algorithm v5 (2026-06-10 fix: real product_id 사용):
  1. n2n / n2b id-overlap (998) → broken markup drop (92) → usable 906
  2. cleaning v3: 모든 [X\\d+]...[/X\\d+] 짝 반복 제거 (content 보존)
  3. entity 변환: cleaned[start:end]==word → find() fallback → None
  4. relation 변환: candidate_pairs pair_id ↔ meta_info join → dict
     - n2b word: candidate_pairs.subject/object string 에서 정규식 추출
  5. 메타 결합:
     - **product_id / prd_nm: rs_own sample 의 진짜 값 그대로**
     - brand_name: rs_own 의 명시적 brand/source field 만 사용. 없으면
       null + MISSING_SOURCE_BRAND 로 표시 (prd_nm token 휴리스틱 금지)
     - 인구통계/날짜/채널: rs_own 906 random sample (seed=42)
     - author_key: hashlib.sha256(rs_own.id) % 150 buckets
  6. derived catalog statistics are computed for lineage checks only. The final
     product_catalog_es.json must come from the source-grounded product master
     refresh unless --write-derived-catalog is passed intentionally.

Determinism: 모든 입력 sort 후 처리. seed=42 고정.

최종 기준: DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md
lineage: docs/architecture/v260605_906_fixture_lineage.md

Usage:
  python scripts/synthesize_mock_from_v260605.py            # write reviews only
  python scripts/synthesize_mock_from_v260605.py --dry-run  # 통계만 출력
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
V260605_DIR = Path("/Users/amore/Jupyter_workplace/Relation/source_data/ver260605")
N2N_PATH = V260605_DIR / "final_relation_ko_ner2ner.jsonl"
N2B_PATH = V260605_DIR / "fin_ko_ner2bee_true_0528.jsonl"
RS_OWN_PATH = V260605_DIR / "rs_own.jsonl"
CATALOG_PATH = REPO_ROOT / "mockdata" / "product_catalog_es.json"
OUTPUT_PATH = REPO_ROOT / "mockdata" / "review_triples_raw.json"

# 2026-06-10 fix: rs_own 의 진짜 product_id (string) 를 review 의
# source_product_id 로 그대로 사용. catalog 도 906 sample 의 distinct
# product universe 로 재빌드. 운영 파이프라인의 rs.jsonl 가 진짜
# product_id 를 들고 들어올 것이므로 mock 도 같은 source 의 string 형식
# 그대로 보존 (현재 분포: 5~6자리 숫자 다수 + 20자 P 접두 일부).

# Mock brand truth must come from explicit source fields. Product-name token
# extraction was removed because promo prefixes can look like brands.
_PROMO_PREFIX_CHARS = ("【", "[", "(", "★", "☆", "💥", "🔔", "⏰")
_MISSING_BRAND_VALUES = {"unknown", "none", "null", "n/a", "na", "-", ""}
MISSING_SOURCE_BRAND = "MISSING_SOURCE_BRAND"
MOCK_SOURCE_TRUTH_SOURCE = "mock_synthesis"

# 카테고리 키워드 매칭 (prd_nm 내 substring) — 우선순위 순.
_CATEGORY_RULES = [
    ("에센스", "에센스"),
    ("앰플", "앰플"),
    ("세럼", "세럼"),
    ("토너", "토너"),
    ("크림", "크림"),
    ("로션", "로션"),
    ("선크림", "선케어"),
    ("선스틱", "선케어"),
    ("자외선", "선케어"),
    ("클렌징", "클렌징"),
    ("클렌저", "클렌징"),
    ("폼", "클렌징"),
    ("샴푸", "헤어케어"),
    ("린스", "헤어케어"),
    ("트리트먼트", "헤어케어"),
    ("헤어", "헤어케어"),
    ("마스크", "마스크/팩"),
    ("팩", "마스크/팩"),
    ("립", "립"),
    ("틴트", "립"),
    ("립스틱", "립"),
    ("쿠션", "베이스메이크업"),
    ("파운데이션", "베이스메이크업"),
    ("파데", "베이스메이크업"),
    ("컨실러", "베이스메이크업"),
    ("아이", "아이메이크업"),
    ("마스카라", "아이메이크업"),
    ("섀도우", "아이메이크업"),
    ("쉐도우", "아이메이크업"),
    ("아이라이너", "아이메이크업"),
    ("블러셔", "포인트메이크업"),
    ("치크", "포인트메이크업"),
    ("하이라이터", "포인트메이크업"),
    ("향수", "프래그런스"),
    ("퍼퓸", "프래그런스"),
    ("바디", "바디케어"),
    ("핸드", "바디케어"),
    ("콜라겐", "이너뷰티"),
    ("이너뷰티", "이너뷰티"),
    ("영양", "이너뷰티"),
    ("비타민", "이너뷰티"),
]


def extract_source_brand(meta: dict[str, Any]) -> str | None:
    """Return an explicit source brand, never one inferred from product name."""
    for key in ("brnd_nm", "BRAND_NAME", "brand_name", "rspn_sal_lcns_nm"):
        value = _clean_text(meta.get(key))
        if value and not _is_placeholder_brand(value):
            return value
    return None


def build_catalog_record(pid: str, prd_nm: str, brand_name: str | None, category: str) -> dict[str, Any]:
    """Build a mock catalog row without inventing missing product truth."""
    normalized_brand = _clean_text(brand_name)
    source_truth_quality = "SOURCE_GROUNDED" if normalized_brand else MISSING_SOURCE_BRAND
    return {
        "ONLINE_PROD_SERIAL_NUMBER": pid,
        "REPRESENTATIVE_PROD_CODE": pid,    # mock: same as serial. 운영은 별도 ES code.
        "prd_nm": prd_nm,
        "REPRESENTATIVE_PROD_NAME": prd_nm,
        "BRAND_NAME": normalized_brand,
        "SOURCE_TRUTH_SOURCE": "rs_own.brnd_nm" if normalized_brand else MOCK_SOURCE_TRUTH_SOURCE,
        "SOURCE_TRUTH_QUALITY": source_truth_quality,
        "CTGR_SS_NAME": category,
        "MAIN_EFFECT": "",                  # 진짜 ES catalog 에서 채워질 자리
        "MAIN_INGREDIENT": "",
        "COUNTRY_OF_ORIGIN": "한국",        # mock placeholder
        "SALE_PRICE": 0,                    # 가격 정보 없음 → 0
        "SALE_STATUS": "판매중",
        "REVIEW_COUNT": None,
        "REVIEW_SCORE": None,
    }


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "None":
        return None
    return text


def _is_placeholder_brand(value: str) -> bool:
    text = value.strip()
    if text.lower() in _MISSING_BRAND_VALUES or text in {"기타", "미상"}:
        return True
    if text.startswith(_PROMO_PREFIX_CHARS):
        return True
    text = text.lstrip("!·ㆍ ")
    text = text.lstrip("★☆")
    return text.startswith(("【", "[", "("))


def extract_category(prd_nm: str) -> str:
    """prd_nm 내 키워드 매칭 → category. 미매칭 시 '기타'."""
    s = prd_nm or ""
    for kw, cat in _CATEGORY_RULES:
        if kw in s:
            return cat
    return "기타"

CHANNEL_SITE_MAP = {"031": "화해", "036": "글로우픽", "048": "올리브영"}
AUTHOR_BUCKETS = 150
SEED = 42


# --- Cleaning + broken markup ---

_PAIRED_TAG_RE = re.compile(r"\[([^\[\]/]+?\d+)\](.*?)\[/\1\]")
_SINGLE_TAG_RE = re.compile(r"\[/?[^\[\]]+?\]")


def clean_v3(s: str) -> str:
    """모든 [X\\d+]...[/X\\d+] 짝을 반복 제거. content 보존."""
    prev = None
    while prev != s:
        prev = s
        s = _PAIRED_TAG_RE.sub(r"\2", s)
    return s


def has_broken_markup(s: str) -> bool:
    """짝 제거 후 단독 tag 잔존 → broken."""
    return bool(_SINGLE_TAG_RE.search(clean_v3(s)))


# --- Entity conversion ---

def convert_entity(
    entity: dict[str, Any],
    cleaned_text: str,
    word_override: str | None = None,
) -> dict[str, Any] | None:
    word = word_override or entity.get("word")
    if word is None:
        return None

    start = entity.get("start")
    end = entity.get("end")

    if start is not None and end is not None and 0 <= start < end <= len(cleaned_text) and cleaned_text[start:end] == word:
        out_start, out_end = start, end
    elif word in cleaned_text:
        out_start = cleaned_text.find(word)
        out_end = out_start + len(word)
    else:
        out_start, out_end = None, None

    return {
        "word": word,
        "entity_group": entity.get("entity_group", ""),
        "start": out_start,
        "end": out_end,
        "sentiment": entity.get("sentiment") or "중립",
    }


_QUOTED_WORD_RE = re.compile(r'"([^"]+)"')


def extract_word_from_pair_string(s: str) -> str | None:
    m = _QUOTED_WORD_RE.search(s)
    return m.group(1) if m else None


def build_relation(
    cp: dict[str, Any],
    mi: dict[str, Any],
    cleaned_text: str,
    source_type: str,
) -> dict[str, Any] | None:
    """candidate_pair + meta_info → dict relation. None 반환 시 skip."""
    sub_meta = mi.get("subject", {})
    obj_meta = mi.get("object", {})
    sub_word = sub_meta.get("word") or extract_word_from_pair_string(cp.get("subject", ""))
    obj_word = obj_meta.get("word") or extract_word_from_pair_string(cp.get("object", ""))

    sub_dict = convert_entity(sub_meta, cleaned_text, word_override=sub_word)
    obj_dict = convert_entity(obj_meta, cleaned_text, word_override=obj_word)
    if sub_dict is None or obj_dict is None:
        return None

    return {
        "subject": sub_dict,
        "object": obj_dict,
        "relation": cp.get("relation"),
        "source_type": source_type,
    }


# --- Entity dedup for ner/bee lists ---

def collect_unique_entities(
    meta_info_list: list[dict[str, Any]],
    cleaned_text: str,
    role_filter_groups: tuple[str, ...] | None,
    source_type_filter: str | None,
    word_extractor_cp: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """meta_info 리스트에서 subject/object 의 entity 추출 + dedup.

    - role_filter_groups: 특정 entity_group 만 포함 (None = 전부)
    - source_type_filter: 특정 source_type 만 포함 (None = 전부)
    - word_extractor_cp: n2b 처럼 word 부재 시 candidate_pairs 에서 추출
    """
    seen = set()
    out: list[dict[str, Any]] = []

    for idx, mi in enumerate(meta_info_list):
        for role in ("subject", "object"):
            e = mi.get(role, {})
            if source_type_filter and e.get("source_type") != source_type_filter:
                continue
            if role_filter_groups and e.get("entity_group") not in role_filter_groups:
                continue
            word = e.get("word")
            if word is None and word_extractor_cp is not None and idx < len(word_extractor_cp):
                word = extract_word_from_pair_string(word_extractor_cp[idx].get(role, ""))
            if word is None:
                continue
            key = (word, e.get("entity_group"), e.get("start"), e.get("end"))
            if key in seen:
                continue
            seen.add(key)
            converted = convert_entity(e, cleaned_text, word_override=word)
            if converted is not None:
                out.append(converted)
    return out


# --- Loaders ---

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_catalog_active(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    active = [p for p in data if p.get("SALE_STATUS") == "판매중"]
    active.sort(key=lambda p: p["ONLINE_PROD_SERIAL_NUMBER"])
    return active


# --- Author key ---

def make_author_key(rs_own_id: str, buckets: int = AUTHOR_BUCKETS) -> str:
    digest = hashlib.sha256(str(rs_own_id).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % buckets
    return f"AUTHOR-{bucket:03d}"


# --- Core synthesis ---

def synthesize() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build (reviews, catalog, stats) from v260605 sources.

    Final baseline: copy rs_own sample product_id/prd_nm into each review and
    rebuild the catalog from the sample's distinct source product universe.
    """
    n2n = sorted(load_jsonl(N2N_PATH), key=lambda r: r["id"])
    n2b = sorted(load_jsonl(N2B_PATH), key=lambda r: r["id"])
    rs_own = sorted(load_jsonl(RS_OWN_PATH), key=lambda r: str(r["id"]))

    n2n_by_id = {r["id"]: r for r in n2n}
    n2b_by_id = {r["id"]: r for r in n2b}

    overlap_ids = sorted(set(n2n_by_id) & set(n2b_by_id))
    usable_ids = sorted(
        i for i in overlap_ids
        if not has_broken_markup(n2n_by_id[i]["text"])
        and not has_broken_markup(n2b_by_id[i]["text"])
    )

    rng = random.Random(SEED)
    rs_own_samples = rng.sample(rs_own, k=len(usable_ids))

    # Statistics
    stats: dict[str, Any] = {
        "n2n_total": len(n2n),
        "n2b_total": len(n2b),
        "rs_own_total": len(rs_own),
        "id_overlap": len(overlap_ids),
        "n2n_broken_in_overlap": sum(1 for i in overlap_ids if has_broken_markup(n2n_by_id[i]["text"])),
        "n2b_broken_in_overlap": sum(1 for i in overlap_ids if has_broken_markup(n2b_by_id[i]["text"])),
        "usable": len(usable_ids),
        "synthesized": 0,
        "synthesis_failures": 0,
        "ner_count_total": 0,
        "bee_count_total": 0,
        "relation_count_total": 0,
        "author_distinct_buckets": set(),
        "brand_distribution": Counter(),
        "distinct_products_in_reviews": 0,
        "category_distribution": Counter(),
    }

    synthesized: list[dict[str, Any]] = []

    for i, n2n_id in enumerate(usable_ids):
        n2n_rec = n2n_by_id[n2n_id]
        n2b_rec = n2b_by_id[n2n_id]
        meta = rs_own_samples[i]

        cleaned_text = clean_v3(n2n_rec["text"])

        ner_entries = collect_unique_entities(
            n2n_rec.get("meta_info", []),
            cleaned_text,
            role_filter_groups=None,
            source_type_filter="NER",
        )

        bee_entries = collect_unique_entities(
            n2b_rec.get("meta_info", []),
            cleaned_text,
            role_filter_groups=None,
            source_type_filter="BEE",
            word_extractor_cp=n2b_rec.get("candidate_pairs", []),
        )

        relations: list[dict[str, Any]] = []
        for cp, mi in zip(n2n_rec.get("candidate_pairs", []), n2n_rec.get("meta_info", [])):
            rel = build_relation(cp, mi, cleaned_text, "NER-NER")
            if rel is not None:
                relations.append(rel)
        for cp, mi in zip(n2b_rec.get("candidate_pairs", []), n2b_rec.get("meta_info", [])):
            rel = build_relation(cp, mi, cleaned_text, "NER-BeE")
            if rel is not None:
                relations.append(rel)

        # Synthesis failure check
        if not (ner_entries or bee_entries or relations):
            stats["synthesis_failures"] += 1
            continue

        prd_nm = (meta.get("prd_nm") or "").strip()
        product_id_raw = meta.get("product_id")
        # rs_own.product_id 는 int 또는 str. 운영 contract 는 string 이므로 강제 캐스팅.
        source_product_id = str(product_id_raw) if product_id_raw is not None else ""
        if not source_product_id or not prd_nm:
            # rs_own 에 product_id/prd_nm 누락된 sample 은 stat 만 올리고 skip
            stats["synthesis_failures"] += 1
            continue

        brand_name = extract_source_brand(meta)

        record = {
            "brnd_nm": brand_name,
            "clct_site_nm": CHANNEL_SITE_MAP.get(meta.get("channel", ""), meta.get("channel", "")),
            "prod_nm": prd_nm,
            "text": cleaned_text,
            "drup_dt": meta.get("date"),
            "source_review_key": f"REV-V260605-{n2n_id:06d}",
            "author_key": make_author_key(meta["id"]),
            "source_product_id": source_product_id,
            "channel": meta.get("channel"),
            "reviewer_profile": {
                "age_sctn_cd": meta.get("age_sctn_cd"),
                "sex_cd": meta.get("sex_cd"),
                "sktp_nm": meta.get("sktp_nm"),
                "sktr_nm": meta.get("sktr_nm"),
            },
            "ner": ner_entries,
            "bee": bee_entries,
            "relation": relations,
        }
        synthesized.append(record)
        stats["synthesized"] += 1
        stats["ner_count_total"] += len(ner_entries)
        stats["bee_count_total"] += len(bee_entries)
        stats["relation_count_total"] += len(relations)
        stats["author_distinct_buckets"].add(record["author_key"])
        stats["brand_distribution"][brand_name or "<missing>"] += 1

    # Catalog: review 가 실제 참조하는 distinct product universe 만 빌드.
    # 운영에서는 진짜 ES catalog 가 들어오지만, mock 은 review 와 1:1 일관성을
    # 위해 sample 의 distinct product 들로 minimal catalog 구성. brand 는
    # 명시적 source field 만 사용하고 없으면 missing quality 로 표시한다.
    # category 는 mock 탐색 편의를 위한 prd_nm keyword 분류이다.
    catalog: list[dict[str, Any]] = []
    seen_pids: set[str] = set()
    for record in synthesized:
        pid = record["source_product_id"]
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        prd_nm = record["prod_nm"]
        brand = record["brnd_nm"]
        category = extract_category(prd_nm)
        catalog.append(build_catalog_record(pid, prd_nm, brand, category))
        stats["category_distribution"][category] += 1

    catalog.sort(key=lambda p: p["ONLINE_PROD_SERIAL_NUMBER"])
    stats["distinct_products_in_reviews"] = len(catalog)
    stats["author_distinct_count"] = len(stats["author_distinct_buckets"])
    del stats["author_distinct_buckets"]  # JSON 직렬화 불가
    stats["brand_distribution"] = dict(stats["brand_distribution"])
    stats["category_distribution"] = dict(stats["category_distribution"])

    return synthesized, catalog, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="통계만 출력, 파일 쓰지 않음")
    parser.add_argument(
        "--write-derived-catalog",
        action="store_true",
        help=(
            "also overwrite mockdata/product_catalog_es.json with the derived "
            "non-source-grounded catalog; not used for the final 906 baseline"
        ),
    )
    args = parser.parse_args()

    if not V260605_DIR.exists():
        print(f"ERROR: 입력 폴더 부재 {V260605_DIR}", file=sys.stderr)
        return 1

    missing = [p for p in (N2N_PATH, N2B_PATH, RS_OWN_PATH) if not p.exists()]
    if missing:
        for p in missing:
            print(f"ERROR: 입력 파일 부재 {p}", file=sys.stderr)
        return 1

    synthesized, catalog, stats = synthesize()

    print("=== Synthesis Stats ===")
    for k, v in stats.items():
        if isinstance(v, dict) and len(v) > 10:
            print(f"  {k}: dict[{len(v)}] (top 5: {dict(list(v.items())[:5])})")
        else:
            print(f"  {k}: {v}")

    failure_rate = stats["synthesis_failures"] / max(stats["usable"], 1)
    print(f"\n  synthesis_failure_rate: {failure_rate:.1%}")

    if failure_rate > 0.10:
        print("STOP: synthesis_failure_rate > 10%", file=sys.stderr)
        return 2

    if args.dry_run:
        print("\n[dry-run] no file written")
        return 0

    OUTPUT_PATH.write_text(
        json.dumps(synthesized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {OUTPUT_PATH} ({len(synthesized)} review records)")
    if args.write_derived_catalog:
        CATALOG_PATH.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {CATALOG_PATH} ({len(catalog)} derived catalog records)")
    else:
        print(
            "kept product_catalog_es.json unchanged; final catalog is "
            "source-grounded from the product master snapshot",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
