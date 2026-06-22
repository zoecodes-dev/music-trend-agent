"""아티스트명 정규화 — 시스템 전체의 단일 진실(single source of truth).

영문/한글/대소문자 변형 + YouTube 'Topic' suffix를 단일 대표명(영문)으로 통일.
큐, 워치리스트, 모든 collector가 이 모듈을 import해서 사용한다.
별칭 추가는 반드시 여기 한 곳에서만.

대표명은 리포트 ##5/##7 출력에 그대로 노출됨.
"""

_ALIAS_TO_CANONICAL = {
    # 한글 → 영문
    "코르티스": "CORTIS",
    "방탄소년단": "BTS",
    "있지": "ITZY",
    "투모로우바이투게더": "TXT",
    "아일릿": "ILLIT",
    "르세라핌": "LE SSERAFIM",
    "키스오브라이프": "KISS OF LIFE",
    "한로로": "Hanroro",
    "볼빨간사춘기": "Bolbbalgan4",
    "엔믹스": "NMIXX",
    "세븐틴": "SEVENTEEN",
    "태양": "Taeyang",
    # 대소문자/표기 변형
    "wave to earth": "Wave to Earth",
    "s2it": "S2IT",
}


def canonicalize(name: str) -> str:
    """별칭을 대표명으로 변환.

    1) 앞뒤 공백 제거
    2) YouTube ' - Topic' suffix 제거
    3) 별칭 사전 매핑 (없으면 정리된 원본 반환)
    """
    if not name:
        return name
    n = name.strip()
    if n.endswith(" - Topic"):
        n = n[: -len(" - Topic")].strip()
    return _ALIAS_TO_CANONICAL.get(n, n)
