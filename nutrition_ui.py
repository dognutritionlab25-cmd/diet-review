"""Display-only helpers. Never mutate requests, results, DB or policies."""
from nutrition_core import catalog_data, retention, standards

PRECOOKED_ITEMS = frozenset(catalog_data()["PRECOOKED_ITEMS"])
WEIGHT_BASIS_NOTE = (
    "입력 기준: 익힌 굴·홍합은 익힌 상태의 중량을 사용합니다. 그 외 재료는 현재 계산의 "
    "조리 전 기준 중량을 입력합니다. 과일·채소·퓨레의 실제 가열 여부를 자동 판별하지 않습니다."
)


def weight_label(name):
    suffix = "익힌 무게 (g)" if name in PRECOOKED_ITEMS else "입력 중량 (g)"
    return f"{name} {suffix}"


def render_data_warnings(st, result):
    for warning in result.get("data_warnings", []):
        if warning.get("code") == "FATTY_ACIDS_EXCEED_TOTAL_FAT":
            st.warning(
                f"식품 DB 내부 불일치: {warning['food']} — 등록된 오메가6+3 합계가 "
                "총지방보다 큽니다. 해당 식품의 지방·오메가 결과는 원자료 확인이 필요합니다. "
                "계산값은 변경하지 않았습니다."
            )


def render_coverage(st, result, kind):
    """Return whether any positive-weight food has registered data (display only)."""
    missing = result.get("coverage", {}).get(kind + "_missing", [])
    total = len(result.get("contributions", []))
    registered = total - len(missing)
    label = "아미노산" if kind == "amino" else "오메가6/3"
    st.caption(f"{label} 데이터 coverage: 급여량이 있는 식품 {total}개 중 {registered}개 등록")
    if missing:
        st.warning(
            f"{label} 부분 집계 — 미등록 식품: {', '.join(missing)}. "
            "미등록은 함량 0을 뜻하지 않습니다. 총량·비율은 등록분 기준이며, "
            "아미노산의 1000kcal 환산 분모에는 미등록 식품의 kcal도 포함됩니다."
        )
    return registered > 0


def render_scope(st, profile, reference=None):
    ref = reference if reference is not None else standards(profile)
    iodine = ref["요오드(mcg)"]
    st.caption(
        f"적용 프로필: {profile} | 기본 영양 판정은 고정된 {len(ref)}항목 기준입니다. "
        "성장기·강아지 상태 선택은 목표 kcal(DER)에 반영되며 주 영양표 기준을 자동 전환하지 않습니다. "
        f"요오드 기준: {iodine['min']}~{iodine['max']}mcg/1000kcal. "
        "비타민B군은 현재 계산하지 않습니다."
    )


def render_cooking_policy(st, method):
    st.caption(
        f"{method} 실제 적용 보존율: "
        f"단백질·지방 {retention('단백질(g)', method):.1%}, "
        f"미네랄 {retention('칼슘(mg)', method):.1%}, "
        f"비타민A {retention('비타민A(IU)', method):.1%}, "
        f"D {retention('비타민D(IU)', method):.1%}, "
        f"E {retention('비타민E(IU)', method):.1%}, "
        f"오메가3 {retention('오메가3', method):.1%}. "
        "오메가3는 현재 미네랄 계수를 사용하며 보존 정책은 미결정 상태입니다. "
        "오메가6·kcal에는 조리 보정을 적용하지 않습니다. 비타민B군은 계산하지 않습니다."
    )
    st.caption(
        "현재 veggie 그룹은 영양소 보존율을 적용하지 않고 예상 중량에만 수율을 적용합니다. "
        "익힌 굴·홍합은 수율과 보존율을 모두 추가 적용하지 않습니다. "
        "실제 조리 후 중량 입력은 중량 표시에만 반영됩니다."
    )
