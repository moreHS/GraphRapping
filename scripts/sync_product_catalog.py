#!/usr/bin/env python3
"""Sync product catalog from personal-agent brand/category enums.

Generates mockdata/product_catalog_es.json with at least one product per brand.
Uses BRAND_LIST and representative categories from personal-agent constants.

Usage:
    python scripts/sync_product_catalog.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

GRAPHRAPPING_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = GRAPHRAPPING_ROOT / "mockdata" / "product_catalog_es.json"
PERSONAL_AGENT_SRC = Path("/Users/amore/workplace/agent-aibc/persnal-agent/src")
PERSONALIZATION_DIR = PERSONAL_AGENT_SRC / "personalization"


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Brand → representative product templates
# Each brand gets 2-4 products across different categories
_BRAND_PRODUCTS = {
    "라네즈": [
        ("에센스", "워터뱅크 블루 히알루로닉 세럼", "히알루론산,세라마이드", "보습", 52000),
        ("크림", "워터 슬리핑 마스크", "히알루론산", "수분충전", 32000),
        ("립밤/립케어", "립 슬리핑 마스크", "", "보습", 18000),
    ],
    "설화수": [
        ("크림", "자음생크림", "인삼추출물,세라마이드", "주름개선", 120000),
        ("에센스", "윤조에센스", "인삼추출물", "피부결개선", 90000),
    ],
    "이니스프리": [
        ("에센스", "그린티 씨드 세럼", "녹차씨오일", "수분공급", 27000),
        ("페이셜 워시", "비자 시카밤 클렌징 폼", "비자오일,시카", "진정", 12000),
        ("파우더", "노세범 미네랄 파우더", "", "유분조절", 9000),
    ],
    "한율": [
        ("로션", "어린쑥 수분진정 로션", "쑥추출물", "진정", 28000),
        ("크림", "어린쑥 수분진정 크림", "쑥추출물,세라마이드", "보습", 32000),
    ],
    "헤라": [
        ("쿠션", "블랙쿠션", "", "커버력", 55000),
        ("립스틱(고체)", "센슈얼 파우더 매트 립스틱", "", "발색력", 38000),
    ],
    "아이오페": [
        ("에센스", "레티놀 엑스퍼트 0.1%", "레티놀", "주름개선", 65000),
        ("크림", "슈퍼 바이탈 크림", "바이오펩타이드", "탄력개선", 80000),
    ],
    "에뛰드": [
        ("아이섀도우", "플레이 컬러 아이즈", "", "발색력", 20000),
        ("립스틱(고체)", "베러 립스 톡", "", "보습", 12000),
    ],
    "에스쁘아": [
        ("파운데이션", "비 글로우 쿠션", "", "광채", 35000),
    ],
    "프리메라": [
        ("크림", "알파인 베리 워터리 크림", "알파인베리", "수분", 42000),
        ("에센스", "오가니언스 세럼", "", "진정", 45000),
    ],
    "일리윤": [
        ("크림", "세라마이드 아토 집중 크림", "세라마이드", "보습", 18000),
        ("로션", "세라마이드 아토 로션", "세라마이드", "보습", 15000),
    ],
    "코스알엑스": [
        ("에센스", "어드밴스드 스네일 96 뮤신 파워 에센스", "달팽이점액", "재생", 15000),
        ("크림", "어드밴스드 스네일 92 올인원 크림", "달팽이점액", "보습", 17000),
    ],
    "마몽드": [
        ("크림", "레드 에너지 리커버리 크림", "석류추출물", "탄력", 35000),
    ],
    "에스트라": [
        ("크림", "아토베리어 365 크림", "판테놀", "진정", 25000),
    ],
    "미쟝센": [
        ("세정", "퍼펙트 세럼 샴푸", "", "윤기", 12000),
    ],
    "려": [
        ("세정", "함빛 손상케어 샴푸", "인삼", "손상케어", 15000),
    ],
    "해피바스": [
        ("바디세정", "내추럴 바디워시", "", "보습", 9000),
    ],
    "오설록": [
        # Non-cosmetic brand — skip
    ],
}


def main() -> None:
    # Load brand list from personal-agent
    pkg = types.ModuleType("personalization")
    pkg.__path__ = [str(PERSONALIZATION_DIR)]
    sys.modules["personalization"] = pkg

    constants = _load_module("personalization.constants", PERSONALIZATION_DIR / "constants.py")
    BRAND_LIST = constants.BRAND_LIST

    # Load existing catalog to preserve manual entries
    existing = {}
    if OUTPUT_PATH.exists():
        for p in json.loads(OUTPUT_PATH.read_text(encoding="utf-8")):
            existing[p["ONLINE_PROD_SERIAL_NUMBER"]] = p

    catalog = []
    pid_counter = 1
    used_pids = set()

    # Generate products for each brand
    for brand in sorted(BRAND_LIST):
        templates = _BRAND_PRODUCTS.get(brand, [])
        if not templates:
            # Default: one generic skincare product per brand without template
            templates = [("에센스", "대표 에센스", "", "보습", 30000)]

        for category, name, ingredients, effect, price in templates:
            pid = f"P{pid_counter:03d}"
            pid_counter += 1
            used_pids.add(pid)

            # Family code: brand-based
            family_code = str(10000000 + pid_counter * 1000 + 1)

            catalog.append({
                "ONLINE_PROD_SERIAL_NUMBER": pid,
                "prd_nm": f"{brand} {name}",
                "BRAND_NAME": brand,
                "CTGR_SS_NAME": category,
                "SALE_STATUS": "판매중",
                "SALE_PRICE": price,
                "MAIN_INGREDIENT": ingredients,
                "MAIN_EFFECT": effect,
                "COUNTRY_OF_ORIGIN": "한국",
                "REPRESENTATIVE_PROD_CODE": family_code,
                "REPRESENTATIVE_PROD_NAME": name,
                "REVIEW_COUNT": 50,
                "REVIEW_SCORE": 4.2,
            })

    # Write
    OUTPUT_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Stats
    brands = {p["BRAND_NAME"] for p in catalog}
    categories = {p["CTGR_SS_NAME"] for p in catalog}
    active = [p for p in catalog if p["SALE_STATUS"] == "판매중"]
    print(f"Generated {len(catalog)} products ({len(active)} active)")
    print(f"Brands: {len(brands)} ({', '.join(sorted(brands)[:10])}...)")
    print(f"Categories: {len(categories)} ({', '.join(sorted(categories)[:10])}...)")
    print(f"Output: {OUTPUT_PATH}")

    # Verify all BRAND_LIST brands are covered
    missing = set(BRAND_LIST) - brands
    if missing:
        print(f"\nWARN: {len(missing)} brands from BRAND_LIST not in catalog: {sorted(missing)}")
    else:
        print(f"\nAll {len(BRAND_LIST)} brands covered!")


if __name__ == "__main__":
    main()
