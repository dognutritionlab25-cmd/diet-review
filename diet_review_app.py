from nutrition_core import basic_judgments
from nutrition_core import energy_requirements
import streamlit as st
from nutrition_core import calculate, make_request, standards, catalog_data
from nutrition_core import create_snapshot, dumps_snapshot, parse_material_string as parse_legacy_materials
from nutrition_core import review_analysis, SNAPSHOT_COLUMN, SnapshotError
import pandas as pd
from nutrition_ui import (PRECOOKED_ITEMS, WEIGHT_BASIS_NOTE, weight_label as nutrition_weight_label,
    render_data_warnings, render_coverage, render_scope, render_cooking_policy)
from datetime import date
import gspread
from google.oauth2.service_account import Credentials
import json
import base64
import gzip
from io import BytesIO
from PIL import Image
from copy import deepcopy

st.set_page_config(
    page_title="반려견 영양연구소 | 반려견 식단 분석",
    page_icon="🐾",
    layout="wide"
)

# ── 브랜딩 헤더 ──────────────────────────────────────────────────────────
import os, base64 as _b64

def _logo_b64(path="logo.png"):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return _b64.b64encode(f.read()).decode()
    return None

def _qr_b64(path="qr.png"):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return _b64.b64encode(f.read()).decode()
    return None

_logo = _logo_b64()
_qr   = _qr_b64()

if _logo:
    st.markdown(f"""
<div style="
    display:flex; align-items:center; gap:1.2rem;
    padding: 1rem 1rem 0.8rem 1rem;
    border-bottom: 2px solid #e8e8e8;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
">
    <img src="data:image/png;base64,{_logo}"
         style="height:clamp(60px, 15vw, 120px); width:auto; object-fit:contain; flex-shrink:0;" />
    <div style="min-width:0;">
        <p style="margin:0 0 0.1rem 0; font-size:clamp(0.85rem, 2.5vw, 1rem); font-weight:700; color:#3a2a1a;">
            반려견영양연구소
        </p>
        <h1 style="margin:0 0 0.2rem 0;
                   font-size:clamp(1.2rem, 4vw, 2rem);
                   font-weight:900; color:#3a2a1a;
                   letter-spacing:-0.5px; line-height:1.2;">
            식단 분석 보고서
        </h1>
        <p style="margin:0; font-size:clamp(0.8rem, 2vw, 0.95rem); color:#666; font-weight:400;">
            전문가가 현재 식단을 분석하고 개선 방향을 함께 제안합니다.
        </p>
    </div>
</div>
""", unsafe_allow_html=True)
else:
    st.markdown("""
<div style="padding:1rem 0; border-bottom:2px solid #e8e8e8; margin-bottom:0.5rem;">
    <p style="margin:0; font-weight:700; color:#3a2a1a;">반려견영양연구소</p>
    <h1 style="margin:0.1rem 0 0.2rem 0; color:#3a2a1a; font-size:clamp(1.2rem, 4vw, 2rem); font-weight:900;">식단 분석 보고서</h1>
    <p style="color:#666; margin:0; font-size:0.95rem;">전문가가 현재 식단을 분석하고 개선 방향을 함께 제안합니다.</p>
</div>
""", unsafe_allow_html=True)

# 헤더 아래 브랜딩 섹션
_qr_tag = f'<img src="data:image/png;base64,{_qr}" style="width:80px; height:80px; object-fit:contain;" />' if _qr else ''
st.markdown(f"""
<div style="
    display:flex; align-items:center; gap:1.5rem;
    padding: 0.8rem 1rem;
    background:#fafafa;
    border-bottom: 1px solid #e8e8e8;
    margin-bottom: 1.2rem;
    flex-wrap: wrap;
">
    {_qr_tag}
    <div style="font-size:0.85rem; color:#555; line-height:1.8;">
        <div style="font-weight:700; color:#3a2a1a; margin-bottom:0.2rem;">© 반려견 영양연구소 &nbsp;·&nbsp; 무단 전재 및 재배포를 금합니다.</div>
        <div>📱 <b>반려견영양연구소</b> &nbsp;·&nbsp; 반려견의 건강은 오늘의 식단에서 시작됩니다.</div>
        <div style="color:#888;">YouTube · 네이버 프리미엄 · 오디오 레터 · 반려견 식단 분석 · 카카오채널</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Google Sheets 연결 ────────────────────────────────────────────────────
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

MAX_PHOTO_MB = 5

@st.cache_resource(ttl=300)
def get_gspread_client():
    """서비스 계정으로 gspread 클라이언트 반환 (5분 캐시)"""
    creds_dict = dict(st.secrets["gcp_service_account"])
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

@st.cache_resource(ttl=300)
def get_drive_service():
    """Google Drive API 서비스 객체 반환"""
    creds_dict = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return build("drive", "v3", credentials=creds)

def upload_to_drive(uploaded_file, filename: str) -> str:
    """Drive 지정 폴더에 사진 업로드 → 공개 URL 반환. 실패 시 빈 문자열.
    
    서비스 계정은 자체 스토리지 quota가 없으므로,
    업로드 후 소유권을 owner_email로 이전하고 서비스 계정 권한은 삭제.
    """
    if uploaded_file is None:
        return ""
    try:
        folder_id = st.secrets["google_drive"]["folder_id"]
        owner_email = st.secrets["google_drive"]["owner_email"]
        service = get_drive_service()

        # PIL로 리사이즈 후 JPEG 변환
        from PIL import Image as _PIL
        from io import BytesIO as _BytesIO
        uploaded_file.seek(0)
        img = _PIL.open(uploaded_file)
        img.thumbnail((1600, 1600))
        buf = _BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)

        media = MediaIoBaseUpload(buf, mimetype="image/jpeg", resumable=False)
        meta = {"name": filename, "parents": [folder_id]}
        f = service.files().create(
            body=meta, media_body=media, fields="id"
        ).execute()
        fid = f.get("id", "")

        # 1) 실제 소유자(내 구글 계정)에게 소유권 이전
        service.permissions().create(
            fileId=fid,
            body={"role": "owner", "type": "user", "emailAddress": owner_email},
            transferOwnership=True,
            sendNotificationEmail=False,
        ).execute()

        # 2) 누구나 읽을 수 있게 공개 권한 추가
        service.permissions().create(
            fileId=fid,
            body={"role": "reader", "type": "anyone"},
        ).execute()

        return f"https://drive.google.com/uc?id={fid}"
    except Exception as e:
        st.warning(f"사진 업로드 실패: {e}")
        return ""

def get_gsheet():
    """식단검토신청 탭 반환"""
    try:
        client = get_gspread_client()
        sheet_url = st.secrets["google_sheet"]["url"]
        sh = client.open_by_url(sheet_url)
        return sh.worksheet("식단검토신청")
    except Exception as e:
        st.error(f"🔴 구글 시트 연결 오류: {e}")
        return None

def get_sheet(tab_name: str):
    """탭 이름으로 시트 반환"""
    try:
        client = get_gspread_client()
        sheet_url = st.secrets["google_sheet"]["url"]
        sh = client.open_by_url(sheet_url)
        return sh.worksheet(tab_name)
    except Exception:
        return None

@st.cache_data(ttl=300)
def _norm_phone(p: str) -> str:
    """전화번호 비교용: 숫자만 추출"""
    return "".join(ch for ch in str(p) if ch.isdigit())

def get_review_eligible_row(phone: str):
    """결제내역 탭에서 전화번호가 일치하고 상품유형에 '식단분석'이 포함되며
    '사용여부'가 비어있는(=미사용) 첫 행을 찾아 시트상의 실제 행 번호를 반환.
    해당 행이 없으면 None. ('사용여부' 열이 없으면 전부 미사용으로 간주)
    F열=전화번호(index 5), D열=상품유형(index 3). 헤더명 자동감지 + fallback"""
    try:
        client = get_gspread_client()
        sh = client.open_by_url(st.secrets["google_sheet"]["url"])
        ws = sh.worksheet("결제내역")
        rows = ws.get_all_values()
        if not rows:
            return None
        header = [h.strip() for h in rows[0]]
        pc_phone = header.index("전화번호") if "전화번호" in header else 5   # F열
        pc_type  = header.index("상품유형") if "상품유형" in header else 3   # D열
        pc_used  = header.index("사용여부") if "사용여부" in header else None

        p = _norm_phone(phone)
        for i, row in enumerate(rows[1:], start=2):  # 1행=헤더 → 데이터는 2행부터
            if len(row) <= max(pc_phone, pc_type):
                continue
            if not row[pc_phone].strip() or _norm_phone(row[pc_phone]) != p:
                continue
            if "식단분석" not in row[pc_type].strip():
                continue
            used_val = row[pc_used].strip() if (pc_used is not None and len(row) > pc_used) else ""
            if used_val:
                continue  # 이미 사용된 결제 건은 건너뛰고 다음 행(재구매 등) 확인
            return i
        return None
    except Exception:
        return None

def check_auth(phone: str) -> tuple[bool, str, int | None]:
    """전화번호 인증. (통과여부, 인증경로, 결제내역 시트 행번호) 반환"""
    p = _norm_phone(phone)
    if not p:
        return False, "", None
    row_no = get_review_eligible_row(p)
    if row_no:
        return True, "결제정보", row_no
    return False, "", None

def mark_review_used(row_no):
    """결제내역 시트의 해당 행 '사용여부' 칸에 사용완료를 기록.
    '사용여부' 열이 없으면 조용히 건너뜀."""
    if not row_no:
        return
    try:
        client = get_gspread_client()
        sh = client.open_by_url(st.secrets["google_sheet"]["url"])
        ws = sh.worksheet("결제내역")
        header = [h.strip() for h in ws.row_values(1)]
        if "사용여부" not in header:
            return
        col_idx = header.index("사용여부") + 1
        ws.update_cell(row_no, col_idx, "사용완료")
    except Exception:
        pass

def append_to_sheet(ws, row_dict: dict):
    """시트의 실제 헤더 이름에 맞춰 값을 배치해서 추가.
    헤더 순서가 바뀌거나 중간에 열이 추가되어도 값이 밀리지 않는다."""
    try:
        existing = ws.get_all_values()

        # 시트가 완전히 비어있을 때만 헤더를 새로 만든다
        if not existing or not existing[0] or existing[0][0] == "":
            ws.append_row(list(row_dict.keys()))
            ws.append_row(list(row_dict.values()))
            return True

        headers = [h.strip() for h in existing[0]]

        # 시트 헤더 순서대로 값을 배치. 앱이 모르는 열은 빈칸으로 남긴다.
        new_row = [row_dict.get(h, "") for h in headers]
        ws.append_row(new_row)

        # 시트에 대응 열이 없어서 저장되지 못한 항목 알림
        missing = [k for k in row_dict if k not in headers]
        if missing:
            st.warning("시트에 해당 열이 없어 저장되지 않은 항목: " + ", ".join(missing))

        return True
    except Exception as e:
        st.warning(f"구글 시트 저장 실패: {e}")
        return False

# Shared immutable catalog; review's existing iodine policy is preserved.
aafco_standards = standards("review")
db_data = catalog_data()["db_data"]
food_df = pd.DataFrame(db_data)

# 반드시 익혀서 급여해야 하는 재료 — DB 수치 자체가 '익힌 상태' 기준이므로
# 화식 조리 보존율(중복 손실 계산)을 적용하지 않고, 입력값도 익힌 무게 그대로 사용
# PRECOOKED_ITEMS comes from the shared catalog via nutrition_ui.

SHEET_SNAPSHOT_PREFIX = "gz1:"
SHEET_CELL_SAFE_LIMIT = 45000


def encode_snapshot_for_sheet(snapshot_json):
    """Compress a validated snapshot for one Google Sheets cell."""
    compressed = gzip.compress(snapshot_json.encode("utf-8"), compresslevel=9, mtime=0)
    encoded = SHEET_SNAPSHOT_PREFIX + base64.urlsafe_b64encode(compressed).decode("ascii")
    if len(encoded.encode("utf-16-le")) // 2 > SHEET_CELL_SAFE_LIMIT:
        raise SnapshotError("입력 스냅샷이 Google Sheets 셀 저장 한도를 초과합니다.")
    return encoded


def decode_snapshot_from_sheet(payload):
    """Read new compressed cells while retaining compatibility with plain JSON cells."""
    text = str(payload or "")
    if not text.startswith(SHEET_SNAPSHOT_PREFIX):
        return text
    try:
        compressed = base64.urlsafe_b64decode(text[len(SHEET_SNAPSHOT_PREFIX):].encode("ascii"))
        return gzip.decompress(compressed).decode("utf-8")
    except Exception as exc:
        raise SnapshotError("압축된 입력계산스냅샷을 읽을 수 없습니다.") from exc


def review_analysis_from_sheet(row_data, food_names):
    normalized = dict(row_data)
    if normalized.get(SNAPSHOT_COLUMN):
        normalized[SNAPSHOT_COLUMN] = decode_snapshot_from_sheet(normalized[SNAPSHOT_COLUMN])
    return review_analysis(normalized, food_names)


def build_admin_detail_request(row_data, stored_snapshot):
    """Build a calculator request from the lossless snapshot or a legacy Sheet row."""
    if stored_snapshot:
        return deepcopy(stored_snapshot["request"]), []

    parsed = parse_legacy_materials(row_data.get("선택재료", ""))
    db_names = set(food_df["재료명"].tolist())
    amounts, excluded = {}, []
    for name, amount_text in parsed.items():
        text = str(amount_text).strip()
        try:
            amount_g = float(text[:-1]) if text.endswith("g") else None
        except ValueError:
            amount_g = None
        if name in db_names and amount_g is not None:
            amounts[name] = amount_g
        else:
            excluded.append({"name": name, "amount_text": amount_text, "reason": "legacy_unmapped"})

    cooked = row_data.get("식단종류") == "화식"
    request = make_request(
        amounts,
        cooked=cooked,
        method=row_data.get("조리방법") or "삶기",
        weight=row_data.get("체중(kg)") or 3,
        activity=row_data.get("활동계수") or 1.6,
        original_fields=row_data,
        excluded=excluded,
    )
    gaps = ["LEGACY_NO_SNAPSHOT", "KELP_AMOUNT_NOT_RECORDED"]
    if cooked:
        gaps.append("CALCIUM_SUPPLEMENT_NOT_RECORDED")
    return request, gaps


def render_admin_calculator_details(base_request, selected_idx):
    """Render the existing calculator analyses without writing derived results to Sheets."""
    request = deepcopy(base_request)
    supplements = request["supplement_totals"]
    saved_unit = supplements.get("omega_unit", "mg")
    if saved_unit not in ("mg", "g"):
        saved_unit = "mg"
    unit = st.radio(
        "EPA/DHA 입력 단위",
        ["mg", "g"],
        index=0 if saved_unit == "mg" else 1,
        horizontal=True,
        key=f"admin_omega_unit_{selected_idx}",
    )
    saved_epa = float(supplements.get("epa", 0) or 0)
    saved_dha = float(supplements.get("dha", 0) or 0)
    if saved_unit != unit:
        factor = 1000 if unit == "mg" else 0.001
        saved_epa *= factor
        saved_dha *= factor
    epa_col, dha_col = st.columns(2)
    with epa_col:
        epa = st.number_input(
            "관리자 EPA 추가량",
            min_value=0.0,
            value=saved_epa,
            step=1.0 if unit == "mg" else 0.01,
            key=f"admin_epa_{selected_idx}",
        )
    with dha_col:
        dha = st.number_input(
            "관리자 DHA 추가량",
            min_value=0.0,
            value=saved_dha,
            step=1.0 if unit == "mg" else 0.01,
            key=f"admin_dha_{selected_idx}",
        )
    st.caption("이 값은 현재 관리자 화면의 계산에만 반영되며 Google Sheets에는 저장되지 않습니다.")

    request["supplement_totals"].update(epa=epa, dha=dha, omega_unit=unit)
    request["supplement_inputs"]["admin_omega3"] = {"epa": epa, "dha": dha, "unit": unit}
    result = calculate(request, "calculator")
    calculator_standards = standards("calculator")
    catalog = catalog_data()
    foods = {row["재료명"]: row for row in catalog["db_data"]}
    amino_db = catalog["amino_db"]
    amino_map = catalog["amino_name_map"]
    omega_db = catalog["omega_db"]
    cooked = request["mode"] == "cooked"
    st.caption("현재 원본 입력을 현재 DB·calculator 프로필로 재계산한 상세 결과입니다. 신청 당시 저장 결과와 기준·시점이 다를 수 있습니다.")
    st.caption(f"현재 DB: {result['food_db_version']} | 현재 계산 정책: {result['calculation_policy_version']}")
    render_scope(st, "calculator")
    render_data_warnings(st, result)
    if cooked:
        render_cooking_policy(st, request["method"])

    tab_aafco, tab_amino, tab_omega, tab_mineral = st.tabs([
        "📊 AAFCO 영양분석", "🧬 아미노산 분석", "🐟 오메가 6:3 분석", "🔬 아연:구리 비율"
    ])

    with tab_aafco:
        if result["kcal"] <= 0:
            st.info("DB에 연결된 재료가 없어 상세 영양 분석을 계산할 수 없습니다.")
        else:
            kcal_col, der_col, diff_col = st.columns(3)
            with kcal_col:
                st.metric("섭취 칼로리", f"{result['kcal']:.0f} kcal")
            with der_col:
                st.metric("목표 칼로리", f"{result['der']:.0f} kcal")
            with diff_col:
                st.metric("차이", f"{result['kcal'] - result['der']:+.0f} kcal")

            ca_p = result["ratios"]["ca_p"]
            ca_p_ok = ca_p is not None and 1.1 <= ca_p <= 2.0
            judgments = basic_judgments(result, "calculator")
            rows = []
            for nutrient, reference in calculator_standards.items():
                value = result["per_1000kcal"][nutrient]
                status = "✅ 적합"
                if judgments[nutrient] == "low":
                    status = f"❌ 부족 (최소 {reference['min']})"
                elif judgments[nutrient] == "high":
                    status = f"⚠️ 과잉 (최대 {reference['max']})"
                if nutrient == "칼슘(mg)" and status == "✅ 적합" and not ca_p_ok:
                    ratio_text = "계산 불가" if ca_p is None else f"{ca_p:.2f}:1"
                    status = f"⚠️ Ca:P 불균형 ({ratio_text}, 권장 1.1~2:1)"
                rows.append({
                    "영양소": nutrient,
                    "현재(1000kcal당)": f"{value:.2f}",
                    "AAFCO 기준": f"{reference['min']}~{reference['max'] if reference['max'] else ''}",
                    "판정": status,
                })

            def detail_color(value):
                return f'color:{"green" if "적합" in value else "red" if "부족" in value else "orange"};font-weight:bold'

            st.dataframe(
                pd.DataFrame(rows).style.map(detail_color, subset=["판정"]),
                use_container_width=True,
                hide_index=True,
            )
            if ca_p is None:
                st.info("인 합계가 0이어서 Ca:P 비율을 계산할 수 없습니다.")
            elif ca_p_ok:
                st.info(f"🦴 **Ca:P 비율 = {ca_p:.2f} : 1** ✅ (권장 1.1~2 : 1)")
            else:
                st.warning(f"🦴 **Ca:P 비율 = {ca_p:.2f} : 1** ⚠️ 권장 범위(1.1~2:1) 벗어남")

            selected_names = [item["name"] for item in request["items"] if item["grams"] > 0]
            if not cooked and any("아몬드" in name for name in selected_names):
                st.warning(
                    "⚠️ **아몬드 가루 주의**: 비타민E 공급 목적으로 소량(5~10g/일) 사용 권장. "
                    "지방 함량이 높아(50g/100g) 과량 급여 시 소화 장애 및 췌장 부담 위험이 있습니다."
                )
            estimated = [name for name in selected_names
                         if name in foods and foods[name].get("category") == "bone"
                         and str(foods[name].get("칼슘출처", "")).startswith("est")]
            if estimated:
                st.caption(f"⚠️ 칼슘 추정값 사용 재료: {', '.join(estimated)} — AAFCO 칼슘 판정은 참고값으로 활용하세요.")

    with tab_amino:
        st.subheader("🧬 필수 아미노산 분석")
        render_coverage(st, result, "amino")
        st.caption("기존 표시 10개 항목입니다. 티로신은 엔진 결과에 있으나 이 표에는 표시하지 않습니다. BCAA·Phe+Trp도 등록분 합계입니다.")
        if cooked:
            st.caption(f"조리법: {request['method']} | 기존 단백질 보존율 적용 (veggie·익힌 굴/홍합 제외)")
        else:
            st.caption("출처: 기존 생식 계산기 아미노산 DB | 생식(raw) 기준")
        display_aa = ["류신", "이소류신", "발린", "메티오닌", "리신", "트레오닌", "트립토판", "히스티딘", "페닐알라닌", "아르기닌"]
        has_amino = any(result["amino"].get(name, 0) > 0 for name in display_aa)
        if has_amino and result["kcal"] > 0:
            nrc_adult = {"류신":1700,"이소류신":950,"발린":1230,"메티오닌":830,"리신":1580,"트레오닌":1200,"트립토판":400,"히스티딘":480,"페닐알라닌":1130,"아르기닌":1280}
            aa_rows = []
            for name in display_aa:
                total = result["amino"][name]
                per_1000 = result["amino_per_1000kcal"][name]
                row = {"아미노산": name, "총량(mg)": f"{total:.0f}", "1000kcal당(mg)": f"{per_1000:.0f}"}
                if not cooked:
                    row.update({"NRC 성견기준": str(nrc_adult[name]), "판정": "✅" if per_1000 >= nrc_adult[name] else "⚠️"})
                aa_rows.append(row)
            st.dataframe(pd.DataFrame(aa_rows), use_container_width=True, hide_index=True)
            metric1, metric2 = st.columns(2)
            with metric1:
                st.metric("BCAA 합계", f"{result['bcaa']:.0f} mg")
            with metric2:
                st.metric("페닐알라닌+트립토판", f"{result['phenylalanine_plus_tryptophan']:.0f} mg")

            if not cooked:
                with st.expander("📋 재료별 아미노산 상세"):
                    detail = []
                    for item in request["items"]:
                        if item["grams"] <= 0:
                            continue
                        key = amino_map.get(item["name"])
                        row = {"재료명": item["name"], "급여량(g)": item["grams"]}
                        if key and key in amino_db:
                            row.update({name: f"{amino_db[key][name]}mg/100g" for name in display_aa})
                        else:
                            row["류신"] = "데이터없음"
                        detail.append(row)
                    st.dataframe(pd.DataFrame(detail), use_container_width=True, hide_index=True)
        else:
            st.info("아미노산 데이터가 있는 재료를 선택하면 분석됩니다.")
        # Missing-food coverage is displayed above, including fully missing diets.

    with tab_omega:
        st.subheader("🐟 오메가 6:3 비율 분석")
        has_omega_data = render_coverage(st, result, "omega")
        omega3 = result["omega3"]
        ratio = result["ratios"]["omega6_3"]
        col6, col3, colr = st.columns(3)
        with col6:
            st.metric("오메가-6 등록분", f"{result['omega6']:.2f} g" if has_omega_data else "미등록")
        with col3:
            added = (result["epa_supplement_g"] + result["dha_supplement_g"]) * 1000
            st.metric("오메가-3 등록분 + 보충", f"{omega3:.2f} g" if has_omega_data or added else "미등록", delta=f"+{added:.0f}mg 관리자 입력" if added else None)
        with colr:
            st.metric("오메가 6:3 비율", f"{ratio:.1f} : 1" if ratio is not None and has_omega_data else "계산 불가")
        if not has_omega_data:
            st.info("식품 오메가 데이터 미등록으로 식단 비율을 표시할 수 없습니다. 보충 입력이 있다면 그 양만 반영합니다.")
        elif ratio is None:
            st.info("오메가3 합계가 0이어서 비율을 계산할 수 없습니다.")
        elif ratio <= 5:
            st.success(f"✅ {ratio:.1f}:1 — 기존 계산기의 목표 범위")
        elif ratio <= 10:
            st.warning(f"⚠️ {ratio:.1f}:1 — 기존 계산기의 허용 범위")
        else:
            st.error(f"❌ {ratio:.1f}:1 — 기존 계산기 기준 오메가-6 비율이 높습니다.")

        if not cooked:
            source_rows = []
            for item in request["items"]:
                name, amount = item["name"], item["grams"]
                if amount > 0 and name in omega_db:
                    o6, o3, ratio_text, note = omega_db[name]
                    source_rows.append({"재료명": name, "급여량(g)": amount,
                                        "해당량 O6(g)": f"{o6 * amount / 100:.3f}",
                                        "해당량 O3(g)": f"{o3 * amount / 100:.3f}",
                                        "비율": ratio_text, "비고": note})
            if source_rows:
                st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
        # Missing-food coverage is displayed above; missing values are not measured zeros.

    with tab_mineral:
        st.subheader("🔬 아연:구리 비율 분석")
        st.caption("비율 평가는 아연·구리 절대량의 부족/과잉 판정과 다릅니다. 기본 영양표를 함께 확인하세요.")
        zinc = result["nutrients"].get("아연(mg)", 0)
        copper = result["nutrients"].get("구리(mg)", 0)
        zinc_1000 = result["per_1000kcal"].get("아연(mg)") or 0
        copper_1000 = result["per_1000kcal"].get("구리(mg)") or 0
        ratio = result["ratios"]["zn_cu"]
        zc1, zc2, zc3 = st.columns(3)
        with zc1:
            st.metric("아연(1000kcal당)", f"{zinc_1000:.2f} mg")
        with zc2:
            st.metric("구리(1000kcal당)", f"{copper_1000:.2f} mg")
        with zc3:
            st.metric("아연:구리 비율", f"{ratio:.1f} : 1" if ratio is not None else "계산 불가")
        st.caption(f"총 아연 {zinc:.2f}mg | 총 구리 {copper:.2f}mg")
        if ratio is None:
            st.info("구리 합계가 0이어서 비율을 계산할 수 없습니다.")
        elif cooked:
            if ratio < 8:
                st.error(f"❌ 기존 화식 계산기 기준 범위보다 낮음 ({ratio:.1f}:1)")
            elif ratio <= 15:
                st.success(f"✅ 기존 화식 계산기 적정 범위 ({ratio:.1f}:1)")
            elif ratio <= 20:
                st.warning(f"⚠️ 기존 화식 계산기 기준 범위보다 높음 ({ratio:.1f}:1)")
            else:
                st.error(f"❌ 기존 화식 계산기 기준 크게 높음 ({ratio:.1f}:1)")
        else:
            if 5 <= ratio <= 12:
                st.success(f"✅ 기존 생식 계산기 이상적 범위 ({ratio:.1f}:1)")
            elif 12 < ratio <= 16:
                st.info(f"ℹ️ 기존 생식 계산기 허용 범위 ({ratio:.1f}:1)")
            elif ratio > 16:
                st.warning(f"⚠️ 기존 생식 계산기 기준 범위보다 높음 ({ratio:.1f}:1)")
            elif 3 <= ratio < 5:
                st.warning(f"⚠️ 기존 생식 계산기 기준 범위보다 낮음 ({ratio:.1f}:1)")
            else:
                st.error(f"❌ 기존 생식 계산기 기준 크게 낮음 ({ratio:.1f}:1)")

    return result, request

# ═══════════════════════════════════════════════════════════════════════════
# 이메일 인증 게이트
# ═══════════════════════════════════════════════════════════════════════════
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "auth_phone" not in st.session_state:
    st.session_state.auth_phone = ""
if "auth_track" not in st.session_state:
    st.session_state.auth_track = ""   # "subscriber" | "paid"
if "auth_row" not in st.session_state:
    st.session_state.auth_row = None   # 결제내역 시트에서 매칭된 행 번호

# 탭: 신청자 / 관리자
tab_user, tab_admin = st.tabs(["🐾 반려견 식단 분석", "🔒 관리자"])

with tab_admin:
    st.markdown("### 관리자 로그인")
    admin_pw_input = st.text_input("비밀번호", type="password", key="admin_pw_main")
    if st.button("로그인", key="admin_login_main", type="primary"):
        correct_pw = st.secrets.get("admin", {}).get("password", "")
        if correct_pw and admin_pw_input == correct_pw:
            st.session_state["admin_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")

    if st.session_state.get("admin_authenticated"):
        st.success("✅ 관리자로 로그인됐습니다.")
        st.markdown("---")
        # ── 신청 목록 로드 ──────────────────────────────────────────────
        def load_applications():
            try:
                ws = get_gsheet()
                if not ws:
                    return [], []
                rows = ws.get_all_values()
                if len(rows) < 2:
                    return [], []
                return rows[0], rows[1:]
            except Exception as e:
                st.error(f"데이터 로드 실패: {e}")
                return [], []

        def update_sheet_cell(row_idx: int, col_name: str, value: str):
            try:
                ws = get_gsheet()
                rows = ws.get_all_values()
                headers = rows[0]
                col_idx = headers.index(col_name) + 1
                ws.update_cell(row_idx + 2, col_idx, value)
                return True
            except Exception as e:
                st.error(f"저장 실패: {e}")
                return False

        def parse_material_string(s):
            try:
                return parse_legacy_materials(s)
            except SnapshotError as exc:
                st.error(f"기존 재료 정보 확인 필요: {exc}")
                st.stop()

        def _as_grams(v):
            """'50g' 형식 문자열에서 숫자만 추출, 아니면 None (직접입력 재료는 단위가 다를 수 있음)"""
            v = str(v).strip()
            if v.endswith("g"):
                try:
                    return float(v[:-1])
                except ValueError:
                    return None
            return None

        def recalculate(row_data):
            result, _, _ = review_analysis_from_sheet(row_data, set(food_df["재료명"]))
            return result["kcal"], result["nutrients"], result["mass"], result["der"]

        import pandas as _pd

        st.markdown("### 📋 신청 목록")
        headers, data_rows = load_applications()

        if not data_rows:
            st.info("신청 내역이 없습니다.")
        else:
            list_cols = ["신청일시", "보호자이름", "이름", "견종", "체중(kg)", "현재식단", "결제확인여부", "검토상태"]
            available = [c for c in list_cols if c in headers]
            summary_rows = []
            for i, row in enumerate(data_rows):
                rd = dict(zip(headers, row))
                summary_rows.append({c: rd.get(c, "") for c in available} | {"idx": i})
            df_summary = _pd.DataFrame(summary_rows)

            def color_status(val):
                if val == "완료": return "color:green;font-weight:bold"
                if val == "검토중": return "color:orange;font-weight:bold"
                return "color:gray"

            st.dataframe(
                df_summary.drop(columns=["idx"]).style.map(
                    color_status, subset=["검토상태"] if "검토상태" in available else []
                ),
                use_container_width=True, hide_index=True
            )

            name_options = [
                f"{dict(zip(headers,r)).get('신청일시','')} | {dict(zip(headers,r)).get('이름','')} ({dict(zip(headers,r)).get('검토상태','미검토')})"
                for r in data_rows
            ]
            selected_idx = st.selectbox("상세 보기", range(len(name_options)),
                                        format_func=lambda i: name_options[i],
                                        key="admin_select")
            st.divider()
            rd = dict(zip(headers, data_rows[selected_idx]))

            try:
                admin_result, input_gaps, stored_snapshot = review_analysis_from_sheet(rd, set(food_df["재료명"]))
            except (SnapshotError, ValueError) as exc:
                st.error(f"계산 자료 확인 필요: {exc}")
                st.stop()

            if stored_snapshot:
                original_fields = stored_snapshot["original_input"]
                snapshot_labels = {
                    "보호자이름":"owner_name", "신청이메일":"owner_email", "전화번호":"auth_phone",
                    "주문번호":"order_number", "이름":"dog_name", "견종":"dog_breed", "나이":"dog_age",
                    "성별":"dog_gender", "중성화":"neutered", "체중(kg)":"dog_weight", "목표체중(kg)":"goal_weight",
                    "현재식단":"current_diet", "식단기간":"diet_duration", "급여횟수":"feeding_freq",
                    "질환":"diseases", "알레르기":"allergies", "영양제":"supplements", "약":"medications",
                    "체형(BCS)":"body_condition", "체중변화":"weight_change_final", "궁금한점":"question",
                    "배변상태":"stool_status", "산책시간":"walk_time", "추가운동":"exercise",
                    "수면시간":"sleep_hours", "물섭취":"water_intake", "구토":"vomit_status",
                    "활동량메모":"activity_memo", "간식":"snack_input"}
                rd.update({label: original_fields[key] for label,key in snapshot_labels.items() if key in original_fields})
                rd["DER(kcal)"] = f"{admin_result['der']:.0f}"

            st.markdown(f"## 🐾 {rd.get('이름','?')} ({rd.get('견종','?')}) 상세 검토")

            with st.expander("📋 기본 정보", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"**보호자:** {rd.get('보호자이름','')}")
                    st.markdown(f"**전화번호:** {rd.get('전화번호','')}")
                    st.markdown(f"**주문번호:** {rd.get('주문번호','-')}")
                    st.markdown(f"**결제확인:** {rd.get('결제확인여부','미확인')}")
                    st.markdown(f"**이름:** {rd.get('이름','')}")
                    st.markdown(f"**견종:** {rd.get('견종','')}")
                    st.markdown(f"**나이:** {rd.get('나이','')}세")
                    st.markdown(f"**성별:** {rd.get('성별','')} / {rd.get('중성화','')}")
                with c2:
                    st.markdown(f"**체중:** {rd.get('체중(kg)','')}kg → 목표 {rd.get('목표체중(kg)','')}kg")
                    st.markdown(f"**DER:** {rd.get('DER(kcal)','')}kcal")
                    st.markdown(f"**현재식단:** {rd.get('현재식단','')} ({rd.get('식단기간','')})")
                    st.markdown(f"**급여횟수:** {rd.get('급여횟수','')}")
                with c3:
                    st.markdown(f"**질환:** {rd.get('질환','없음')}")
                    st.markdown(f"**알레르기:** {rd.get('알레르기','없음')}")
                    st.markdown(f"**영양제:** {rd.get('영양제','없음')}")
                    st.markdown(f"**복용약:** {rd.get('약','없음')}")
                st.markdown(f"**체형(BCS):** {rd.get('체형(BCS)','없음')}")
                st.markdown(f"**체중 변화:** {rd.get('체중변화','없음')}")
                st.markdown(f"**궁금한 점:** {rd.get('궁금한점','없음')}")

            with st.expander("🚶 생활 패턴", expanded=False):
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**배변상태:** {rd.get('배변상태','')}")
                    st.markdown(f"**산책시간:** {rd.get('산책시간','')}")
                    st.markdown(f"**추가운동:** {rd.get('추가운동','')}")
                with c2:
                    st.markdown(f"**수면시간:** {rd.get('수면시간','')}")
                    st.markdown(f"**물섭취:** {rd.get('물섭취','')}")
                    st.markdown(f"**구토(최근 1개월):** {rd.get('구토','')}")
                    st.markdown(f"**활동량메모:** {rd.get('활동량메모','')}")

            with st.expander("📷 사진 확인", expanded=True):
                st.info("사진은 카카오채널 채팅창으로 별도 수신합니다.")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**제출 방식:** {rd.get('사진제출방식', '카카오채널')}")
                with c2:
                    st.markdown(f"**확인 여부:** {rd.get('사진확인여부', '미확인')}")

            with st.expander("🥩 식단 입력 내용", expanded=True):
                if stored_snapshot:
                    # The lossless input drives details; legacy text is only for old rows.
                    db_items = [{"재료명": item["name"], "급여량(g)": item["grams"]}
                                for item in stored_snapshot["request"]["items"]]
                    extra_items_admin = [{"재료명(직접입력)": f"{item['재료명']}: {item['용량']}{item['단위']}"}
                                         for item in stored_snapshot["original_input"].get("extra_items", [])]
                else:
                    materials = parse_material_string(rd.get("선택재료", ""))
                    db_names = set(food_df["재료명"].tolist())
                    db_items = [{"재료명": n, "급여량(g)": _as_grams(v)}
                                for n, v in materials.items() if n in db_names and _as_grams(v) is not None]
                    extra_items_admin = [{"재료명(직접입력)": f"{n}: {v}"}
                                         for n, v in materials.items() if n not in db_names]

                if db_items:
                    st.markdown("**📋 고정 DB 재료**")
                    st.dataframe(_pd.DataFrame(db_items), use_container_width=True, hide_index=True)
                else:
                    st.caption("고정 DB 재료 없음")

                if extra_items_admin:
                    st.markdown("**➕ 추가 입력 재료** (영양 계산 미반영)")
                    st.dataframe(_pd.DataFrame(extra_items_admin), use_container_width=True, hide_index=True)

                snack_val = rd.get("간식", "").strip()
                if snack_val:
                    st.markdown("**🍖 오늘 급여한 간식** (영양 계산 미반영, 참고용)")
                    st.info(snack_val)

            if input_gaps:
                st.warning("이전 형식의 신청입니다. 당시 계산 결과와 켈프·칼슘 보충량이 저장되지 않아, 아래는 기록된 재료의 부분 재계산입니다.")
            else:
                st.caption("아래 기본 표는 신청 당시 저장 결과입니다. 저장 결과를 재계산하거나 덮어쓰지 않습니다. 이후 별도의 상세 표는 현재 기준으로 재계산합니다.")
                st.caption(f"DB: {admin_result['food_db_version']} | 계산 정책: {admin_result['calculation_policy_version']}")
                with st.expander("📦 신청 당시 원본 입력·보충제"):
                    st.json(stored_snapshot["original_input"])
            total_kcal_r, total_stats_r, mass_bd_r, der_r = (
                admin_result["kcal"], admin_result["nutrients"], admin_result["mass"], admin_result["der"])
            admin_standards = (stored_snapshot["policy_snapshot"]["standards"][admin_result["profile"]]
                               if stored_snapshot else aafco_standards)
            total_grams_r = sum(mass_bd_r.values())

            with st.expander("📊 신청 당시 저장 결과" if stored_snapshot else "📊 과거 기록의 부분 재계산", expanded=True):
                render_scope(st, admin_result["profile"], reference=admin_standards)
                render_data_warnings(st, admin_result)
                if total_kcal_r <= 0:
                    st.caption("계산 불가 (DB 등록 재료 없음)")
                else:
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        st.markdown("**⚖️ 식단 비율**")
                        if total_grams_r > 0:
                            pct_b = mass_bd_r["actual_bone"]  / total_grams_r * 100
                            pct_m = mass_bd_r["muscle_meat"]  / total_grams_r * 100
                            pct_o = mass_bd_r["organ"]        / total_grams_r * 100
                            pct_v = mass_bd_r["veggie"]       / total_grams_r * 100
                            st.write(f"🦴 뼈 **{pct_b:.1f}%** (목표 12%)")
                            st.progress(min(pct_b/20, 1.0))
                            st.write(f"🥩 살코기 **{pct_m:.1f}%** (목표 60~70%)")
                            st.progress(min(pct_m/100, 1.0))
                            st.write(f"🫀 내장 **{pct_o:.1f}%** (목표 10~25%)")
                            st.progress(min(pct_o/40, 1.0))
                            st.write(f"🥦 야채 **{pct_v:.1f}%** (목표 5~10%)")
                            st.progress(min(pct_v/20, 1.0))
                    with rc2:
                        st.markdown("**🔥 칼로리**")
                        kcal_pct_r = (total_kcal_r / der_r * 100) if der_r > 0 else 0
                        st.metric("섭취 칼로리", f"{total_kcal_r:.0f} kcal")
                        st.metric("목표 칼로리 (DER)", f"{der_r:.0f} kcal")
                        st.metric("차이", f"{total_kcal_r - der_r:+.0f} kcal")
                        st.progress(min(kcal_pct_r/100, 1.0), text=f"칼로리 충족률: {kcal_pct_r:.1f}%")

                    st.markdown("**📊 AAFCO 영양 판정**")
                    aafco_rows = []
                    for nutri, std in admin_standards.items():
                        val = admin_result["per_1000kcal"][nutri]
                        min_v, max_v = std["min"], std["max"]
                        if basic_judgments(admin_result, reference=admin_standards)[nutri] == "low": status = "❌ 부족"
                        elif basic_judgments(admin_result, reference=admin_standards)[nutri] == "high": status = "⚠️ 과잉"
                        else: status = "✅ 적합"
                        aafco_rows.append({"영양소": nutri, "현재(1000kcal당)": f"{val:.2f}", "AAFCO 최소": str(min_v), "판정": status})

                    def _color(val):
                        if "적합" in str(val): return "color:green;font-weight:bold"
                        if "부족" in str(val): return "color:red;font-weight:bold"
                        return "color:orange;font-weight:bold"

                    st.dataframe(
                        _pd.DataFrame(aafco_rows).style.map(_color, subset=["판정"]),
                        use_container_width=True, hide_index=True
                    )

            st.markdown("### 🔎 현재 기준 상세 재계산")
            try:
                admin_detail_request, admin_detail_input_gaps = build_admin_detail_request(rd, stored_snapshot)
                admin_detail_result, admin_detail_request = render_admin_calculator_details(
                    admin_detail_request, selected_idx
                )
                if admin_detail_input_gaps:
                    missing_text = ["켈프 보충량"]
                    if "CALCIUM_SUPPLEMENT_NOT_RECORDED" in admin_detail_input_gaps:
                        missing_text.append("칼슘 보충량")
                    st.warning(
                        "이전 형식의 신청이라 " + ", ".join(missing_text) +
                        "이 숫자로 저장되지 않았습니다. 상세 분석은 시트에 남은 DB 재료와 급여량만 반영합니다."
                    )
            except (SnapshotError, ValueError) as exc:
                st.error(f"상세 분석 입력 확인 필요: {exc}")

            with st.expander("✍️ 검토 메모 & 상태", expanded=True):
                new_status = st.selectbox(
                    "검토 상태",
                    ["미검토", "검토중", "완료"],
                    index=["미검토", "검토중", "완료"].index(rd.get("검토상태", "미검토") if rd.get("검토상태", "미검토") in ["미검토", "검토중", "완료"] else "미검토"),
                    key=f"status_{selected_idx}"
                )
                new_memo = st.text_area(
                    "관리자 메모",
                    value=rd.get("관리자메모", ""),
                    height=150,
                    placeholder="검토 내용, 피드백, 개선 사항 등을 입력하세요.",
                    key=f"memo_{selected_idx}"
                )
                if st.button("💾 저장", key=f"save_{selected_idx}", type="primary"):
                    ok1 = update_sheet_cell(selected_idx, "검토상태", new_status)
                    ok2 = update_sheet_cell(selected_idx, "관리자메모", new_memo)
                    if ok1 and ok2:
                        st.success("저장됐습니다!")
                        st.cache_data.clear()

with tab_user:
    if not st.session_state.authenticated:
        st.divider()
        st.markdown("### 🔐 전화번호 인증")
        st.caption(
            "반려견 식단 분석 이용권을 구매하신 분만 이용하실 수 있습니다. "
            "결제 시 입력하신 전화번호를 입력해주세요."
        )
        auth_phone_input = st.text_input(
            "전화번호",
            placeholder="010-1234-5678",
            key="auth_phone_input"
        )
        auth_btn = st.button("✅ 인증하기", type="primary", use_container_width=True)
        if auth_btn:
            if not _norm_phone(auth_phone_input):
                st.error("올바른 전화번호를 입력해주세요.")
            else:
                ok, track, row_no = check_auth(auth_phone_input)
                if ok:
                    st.session_state.authenticated = True
                    st.session_state.auth_phone = _norm_phone(auth_phone_input)
                    st.session_state.auth_track = track
                    st.session_state.auth_row = row_no
                    st.rerun()
                else:
                    st.error("❌ 이용권 구매 내역이 없거나 이미 사용하신 전화번호입니다. 결제 시 입력한 전화번호를 다시 확인해주세요.")
        st.stop()

    # ═══════════════════════════════════════════════════════════════════════════
    # 주문번호 URL 파라미터 (숨겨진 필드)
    order_number = st.query_params.get("order", "")

    # STEP 1: 기본 정보
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📋 STEP 1. 보호자 & 반려견 기본 정보")

    col1, col2, col3 = st.columns(3)
    with col1:
        owner_name = st.text_input("보호자 이름 *", placeholder="예: 홍길동")
        owner_email = st.text_input("이메일 주소 *", placeholder="예: example@email.com")
        dog_name  = st.text_input("반려견 이름", placeholder="예: 토실이")
        dog_breed = st.text_input("견종", placeholder="예: 말티즈")
        dog_age   = st.number_input("나이 (세)", 0, 25, 3)
    with col2:
        dog_gender = st.radio("성별", ["수컷", "암컷"])
        neutered   = st.radio("중성화 여부", ["예", "아니오"])
        dog_weight = st.number_input("현재 체중 (kg)", 0.1, 60.0, 3.0, step=0.1)
    with col3:
        goal_weight = st.number_input("목표 체중 (kg)", 0.1, 60.0, 3.0, step=0.1)
        der_options = {
            "3.0: 성장기 강아지 (퍼피)": 3.0,
            "2.0: 체중 증가 필요": 2.0,
            "2.0: 매우 활동적인 성견": 2.0,
            "1.8: 비중성화 성견 · 보통 활동량": 1.8,
            "1.6: 중성화 성견 · 보통 활동량 ⭐": 1.6,
            "1.4: 중성화 성견 · 낮은 활동량": 1.4,
            "1.4: 노견 · 활동적": 1.4,
            "1.2: 노견 · 보통": 1.2,
            "1.0: 노견 · 거의 안 움직임": 1.0,
            "1.0: 체중 감량 필요 (다이어트)": 1.0,
        }
        selected_der = st.selectbox("현재 상태", list(der_options.keys()), index=4)
        activity = der_options[selected_der]
        rer, der = energy_requirements(dog_weight, activity)
        st.metric("하루 목표 칼로리 (DER)", f"{der:.0f} kcal")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: 사진 업로드 (신규)
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📸 STEP 2. 사진 업로드")

    st.markdown("""
    <div style="background:#fff8e1; border-left:4px solid #f9a825;
                padding:1rem 1.2rem; border-radius:8px; margin-bottom:0.5rem;">
        <b>📷 사진 제출 안내</b><br>
        아래 사진들을 신청 완료 후 <b>카카오채널 채팅창</b>으로 보내주세요.<br><br>
        <span style="font-size:0.9rem; color:#444;">
        🐶 <b>반려견 전신 사진</b> — 체형 확인용 (전신이 보이게)<br>
        🥣 <b>식단 사진</b> — 그릇 전체가 보이게<br>
        📦 <b>사료 또는 동결건조 제품을 급여하는 경우</b><br>
        &nbsp;&nbsp;&nbsp;&nbsp;→ 제품 뒷면 영양 성분 표기 라벨을 <u>수치가 선명하게 보이도록</u> 찍어 보내주세요.
        </span>
    </div>
    """, unsafe_allow_html=True)

    dog_photo = None
    diet_photo = None

    # ── 체형(BCS) 체크리스트 ─────────────────────────────────────────────
    st.markdown("**📏 체형(BCS) — 해당하는 항목을 선택해주세요**")
    bcs_options = [
        "갈비뼈가 살짝 만져지고 허리 라인이 보입니다. (적정)",
        "갈비뼈는 만져지지만 지방이 약간 느껴집니다.",
        "갈비뼈가 잘 만져지지 않고 허리 라인이 없습니다.",
        "갈비뼈가 쉽게 만져지고 허리가 많이 들어가 있습니다.",
        "잘 모르겠습니다.",
    ]
    bcs_selected = []
    for opt in bcs_options:
        if st.checkbox(opt, key=f"bcs_{opt}"):
            bcs_selected.append(opt)
    body_condition = ", ".join(bcs_selected) if bcs_selected else ""

    # ── 최근 6개월 체중 변화 ──────────────────────────────────────────────
    st.markdown("**⚖️ 최근 6개월 체중 변화**")
    weight_change = st.radio(
        "체중 변화",
        ["변화 없음", "증가", "감소"],
        horizontal=True,
        label_visibility="collapsed"
    )
    weight_change_detail = ""
    if weight_change in ["증가", "감소"]:
        weight_change_detail = st.text_input(
            "구체적으로 입력해주세요",
            placeholder=f"예: 3kg {weight_change} (6개월 전 5kg → 현재 8kg)",
            key="weight_change_detail"
        )
    weight_change_final = weight_change if weight_change == "변화 없음" else f"{weight_change} / {weight_change_detail}"

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: 식이 이력
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🍽️ STEP 3. 식이 이력")

    col1, col2 = st.columns(2)
    with col1:
        current_diet  = st.radio("현재 식단", ["건사료", "동결건조", "화식", "생식", "혼합급여"])
        diet_duration = st.selectbox("현재 식단을 언제부터 먹었나요?",
            ["1주 미만", "1개월", "3개월", "6개월", "1년", "3년 이상"])
        feeding_freq  = st.radio("하루 급여 횟수",
            ["하루 1회", "하루 2회", "하루 3회", "자유급식"])
    with col2:
        prev_diet = st.text_area("이전 식단 이력 (자유 입력)",
            placeholder="예: 건사료(로얄캐닌) 8년 → 화식 6개월 → 생식 1년\n사료/동결건조 급여 시 브랜드명을 함께 적어주세요.", height=120)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: 식단 입력
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🥩 STEP 4. 오늘의 식단 입력")
    render_scope(st, "review")
    st.markdown("""
    <div style="background:#fff3e0; border-left:4px solid #ef6c00;
                padding:1rem 1.2rem; border-radius:8px; margin-bottom:1rem;">
        <b>⚠️ 필수 입력 안내</b><br>
        원활한 식단 분석을 위해 <b>하루 식단(재료명과 용량)</b>을
        먼저 입력해주셔야 상담이 시작됩니다.<br>
        <span style="font-size:0.9rem; color:#666;">
        정보가 부족한 경우 추가 자료를 요청드릴 수 있으며,
        자료가 준비된 순서대로 검토를 진행합니다.
        </span>
    </div>
    """, unsafe_allow_html=True)

    # 생식/화식 선택
    diet_type = st.radio("식단 종류", ["🥩 생식", "🍲 화식"], horizontal=True, key="diet_type_select")
    is_cooked = diet_type == "🍲 화식"

    all_foods = food_df['재료명'].tolist()
    cooked_foods = food_df[food_df['category'] != 'bone']['재료명'].tolist()

    # 켈프 (말린 보충제) — 요오드 총량 직접 입력 방식
    # 생켈프 DB값과 실제 급여되는 말린 보충제(캡슐/파우더)는 요오드 농도가 크게 다르고
    # 제품마다 편차도 매우 커서, "오늘 급여한 요오드 총량"을 바로 입력하는 방식으로 처리함.
    use_kelp = st.checkbox("켈프 (말린 보충제)", key="c_use_kelp")
    kelp_iodine_total = 0.0
    if use_kelp:
        kelp_iodine_total = st.number_input(
            "오늘 급여한 켈프 요오드 총량 (mcg)", 0.0, 5000.0, 0.0, step=10.0, key="c_kelp_iodine_total"
        )
        st.caption(
            "🌿 제품 라벨의 '1회 제공량당 요오드(mcg)'를 보고, 오늘 실제로 급여한 만큼 계산해서 넣어주세요.\n\n"
            "예: 나우푸드 켈프 1정 = 요오드 150mcg → 1정을 통째로 줬다면 150 입력, 1/4정만 줬다면 37.5 입력.\n\n"
            "파우더 제품은 (급여한 g수) × (라벨의 g당 요오드 mcg)로 계산해서 넣어주세요. "
            "요오드 함량이 라벨에 없는 제품은 사용을 권장하지 않습니다."
        )

    # ── 생식 입력 ──────────────────────────────────────────────────────────────
    if not is_cooked:
        st.markdown("""
        <div style="background:#e8f5e9; border-left:4px solid #388e3c;
                    padding:0.8rem 1.2rem; border-radius:8px; margin-bottom:0.5rem; font-size:0.92rem;">
            <b>📋 재료 선택 안내</b><br>
            목록에 없는 재료는 <b>가장 유사한 재료로 대체</b>하여 선택해주세요.<br>
            목록에 <u>완전히 해당하는 재료가 없는 경우</u>에만 아래 직접 입력란을 사용해주세요.
        </div>
        """, unsafe_allow_html=True)

        selected = st.multiselect("재료 선택", all_foods, key="raw_selected")
        amounts = {}
        if selected:
            cols = st.columns(3)
            for i, f in enumerate(selected):
                with cols[i % 3]:
                    amounts[f] = st.number_input(f"{f} (g)", 0, 1000, 50, step=5, key=f"amt_{f}")

        # 화식 관련 변수 초기화
        cooking_method_input = "생식"
        cooked_selected = []
        cooked_amounts = {}
        ca_supplement_total = 0.0

    # ── 화식 입력 ──────────────────────────────────────────────────────────────
    else:
        st.caption("⚠️ 화식 계산은 조리 과정의 수분 변화와 영양소 손실을 반영한 **추정치**입니다.")

        cooking_method_input = st.radio(
            "조리 방법",
            ["저온찜", "삶기", "볶기/구이", "압력조리"],
            horizontal=True, key="cook_method_input"
        )

        st.markdown("""
        <div style="background:#e8f5e9; border-left:4px solid #388e3c;
                    padding:0.8rem 1.2rem; border-radius:8px; margin-bottom:0.5rem; font-size:0.92rem;">
            <b>📋 재료 선택 안내 (화식)</b><br>
            화식에서는 <b>뼈고기를 익히면 안 됩니다</b> — 뼈고기 항목은 자동 제외됩니다.<br>
            칼슘은 아래 칼슘 보충제 항목에서 입력해주세요.
        </div>
        """, unsafe_allow_html=True)

        render_cooking_policy(st, cooking_method_input)
        cooked_selected = st.multiselect("재료 선택 (화식 — 뼈고기 제외)", cooked_foods, key="cooked_selected")
        st.caption(WEIGHT_BASIS_NOTE)
        cooked_amounts = {}
        if cooked_selected:
            cols = st.columns(3)
            for i, f in enumerate(cooked_selected):
                with cols[i % 3]:
                    weight_label = nutrition_weight_label(f)
                    cooked_amounts[f] = st.number_input(weight_label, 0, 1000, 50, step=5, key=f"camt_{f}")

        # 생식 관련 변수 초기화
        selected = []
        amounts = {}

        # 칼슘 보충제
        st.markdown("**🦴 칼슘 보충**")
        csup1, csup2 = st.columns(2)
        with csup1:
            use_egg = st.checkbox("난각가루", key="c_use_egg")
            egg_g, egg_ca = 0.0, 380
            if use_egg:
                egg_g = st.number_input("난각가루 (g)", 0.0, 10.0, 0.5, step=0.1, key="c_egg_g")
                egg_ca = st.number_input("Ca 함량 (mg/g)", 100, 600, 380, step=10, key="c_egg_ca")
        with csup2:
            use_sup = st.checkbox("칼슘 보충제", key="c_use_sup")
            sup_g, sup_ca = 0.0, 400
            if use_sup:
                sup_g = st.number_input("보충제 (g)", 0.0, 10.0, 0.5, step=0.1, key="c_sup_g")
                sup_ca = st.number_input("Ca 함량 (mg/g) — 라벨 확인", 50, 600, 400, step=10, key="c_sup_ca")
        ca_supplement_total = (egg_g * egg_ca if use_egg else 0) + (sup_g * sup_ca if use_sup else 0)
        if ca_supplement_total > 0:
            st.caption(f"칼슘 보충제 합계: {ca_supplement_total:.0f}mg")

    # 추가 직접 입력 (생식/화식 공통)
    st.markdown("#### ➕ DB에 없는 재료 직접 입력 (선택, 최대 3개)")
    st.caption("위 목록에 완전히 해당하는 재료가 없을 때만 입력해주세요. 영양 계산에는 반영되지 않으며 전문가 검토 시 참고합니다.")

    MAX_EXTRA = 3
    if "extra_rows" not in st.session_state:
        st.session_state.extra_rows = 1

    extra_items = []
    for i in range(st.session_state.extra_rows):
        ec1, ec2, ec3 = st.columns([4, 2, 2])
        with ec1:
            name = st.text_input("재료명", key=f"ex_name_{i}", placeholder="예: 표고버섯 분말",
                                 label_visibility="collapsed" if i > 0 else "visible")
        with ec2:
            qty = st.number_input("용량", min_value=0.0, step=1.0, key=f"ex_qty_{i}",
                                  label_visibility="collapsed" if i > 0 else "visible")
        with ec3:
            unit = st.selectbox("단위", ["g", "ml", "개", "꼬집", "작은술", "큰술"],
                                key=f"ex_unit_{i}",
                                label_visibility="collapsed" if i > 0 else "visible")
        if name:
            extra_items.append({"재료명": name, "용량": qty, "단위": unit})

    if st.session_state.extra_rows < MAX_EXTRA:
        if st.button("＋ 행 추가", key="add_extra_row"):
            st.session_state.extra_rows += 1
            st.rerun()
    else:
        st.caption("✋ 최대 3개까지 입력 가능합니다.")

    # 간식 (선택, 전문가가 영양 검토 시 직접 참고 — 자동 계산에는 미반영)
    st.markdown("#### 🍖 오늘 급여한 간식 (선택)")
    st.caption("메인 식단 외에 오늘 추가로 준 간식이 있다면, 검토에 참고할 수 있도록 종류와 양(g)을 최대한 자세히 적어주세요.")
    snack_input = st.text_area(
        "간식", placeholder="예: 닭가슴살 말랭이 20g, 오리목뼈 1개(약 30g), 치즈 한 조각(약 10g)",
        key="snack_input", label_visibility="collapsed"
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: 건강 상태
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🏥 STEP 5. 건강 상태")

    col1, col2 = st.columns(2)
    with col1:
        diseases    = st.text_area("현재 질환 (있으면 입력)", placeholder="예: 슬개골 탈구 2등급, 피부 알레르기", height=80)
        medications = st.text_area("복용 중인 약", placeholder="예: 없음 / 소염제", height=80)
    with col2:
        supplements = st.text_area("영양제", placeholder="예: 오메가3, 유산균", height=80)
        allergies   = st.text_area("알레르기 (알려진 것)", placeholder="예: 닭고기, 없음", height=80)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 6: 배변 & 생활 패턴 (선택)
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🚶 STEP 6. 배변 & 생활 패턴 (선택)")

    col1, col2 = st.columns(2)
    with col1:
        stool_status = st.radio("배변 상태",
            ["좋음", "약간 무름", "설사", "변비", "들쭉날쭉"], horizontal=True)
        walk_time = st.radio("하루 산책 시간",
            ["거의 없음", "20분 이하", "30~60분", "1시간 이상"], horizontal=True)
        exercise = st.text_input("추가 운동 (종류·시간)", placeholder="예: 공놀이 10분, 노즈워크, 수영 주 1회")
        vomit_status = st.radio("최근 한 달간 구토",
            ["없음", "한두 번", "자주"], horizontal=True)

    with col2:
        sleep_hours = st.radio("하루 수면 시간",
            ["10시간 미만 (매우 적음)", "10~12시간", "12~16시간 (권장/가장 흔함)", "16시간 이상"],
            index=2, horizontal=True)
        water_intake = st.radio("물 섭취량",
            ["적은 편", "평소와 비슷", "많은 편"], horizontal=True)
        activity_memo = st.text_input("활동량 메모 (자유 입력)", placeholder="예: 실내에서만 생활, 계단 못 내려감")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 7: 궁금한 점
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("💬 STEP 7. 가장 궁금한 점 1가지")
    st.caption("식단 검토는 제출하신 식단 전반에 대한 평가와 함께, 가장 궁금한 질문 1가지에 대해 자세히 답변드립니다.")
    question = st.text_area(
        "가장 궁금한 점 1가지를 적어주세요.",
        placeholder="예: 최근 변이 묽어졌는데 식단 때문인지 궁금합니다.",
        height=100
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # 제출 버튼
    # ═══════════════════════════════════════════════════════════════════════════
    if "submitted" not in st.session_state:
        st.session_state.submitted = False

    st.divider()

    # 이미 제출 완료된 경우 완료 화면만 표시
    if st.session_state.submitted:
        name = st.session_state.get('submitted_name', '')
        st.success(f"✅ {name} 보호자님, 신청이 완료되었습니다.")
        st.markdown(f"""
    <div style="background:#f8fffe; border:1.5px solid #74c69d; border-radius:14px; padding:1.8rem 2rem; margin:1rem 0; line-height:2;">
        <p style="font-size:1.05rem; color:#222; margin:0;">
            식단 검토 결과는 <b>영업일 기준 5일 이내</b> 등록해주신 이메일로 보내드립니다.<br>
            모든 식단은 직접 검토하여 개별적으로 작성하기 때문에,<br>
            신청이 많을 경우 안내드린 기간보다 조금 더 소요될 수 있습니다.<br>
            일정이 지연되는 경우에는 별도로 안내드리겠습니다.
        </p>
        <hr style="border:none; border-top:1px solid #d0ece4; margin:1.2rem 0;">
        <p style="font-size:1rem; color:#2d6a4f; margin:0;">
            📷 <b>사진을 아직 보내지 않으셨다면, 지금 카카오채널 채팅창으로 보내주세요.</b><br>
            <span style="font-size:0.9rem; color:#555;">
            · 반려견 전신 사진 (체형 확인용)<br>
            · 오늘의 식단 사진<br>
            · 사료·동결건조 급여 시 제품 뒷면 영양 성분 라벨 (수치가 선명하게 보이도록)
            </span>
        </p>
        <hr style="border:none; border-top:1px solid #d0ece4; margin:1.2rem 0;">
        <p style="font-size:1rem; color:#444; margin:0;">감사합니다. 🐾</p>
    </div>
    """, unsafe_allow_html=True)
        if st.button("🔄 새 신청서 작성", use_container_width=True):
            # 재인증을 강제해 사용여부 체크를 다시 거치도록 함 (재제출 방지)
            st.session_state.submitted = False
            st.session_state.authenticated = False
            st.session_state.auth_phone = ""
            st.session_state.auth_track = ""
            st.session_state.auth_row = None
            st.rerun()
        st.stop()

    submit = st.button("📨 식단 검토 신청하기", type="primary", use_container_width=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # 계산 및 결과 출력
    # ═══════════════════════════════════════════════════════════════════════════
    if submit:
        dry_diet = current_diet in ["건사료", "동결건조"]
        has_extra = any(item.get("재료명", "").strip() for item in extra_items)
        active_selected = cooked_selected if is_cooked else selected
        active_amounts  = cooked_amounts  if is_cooked else amounts

        if not owner_name:
            st.error("보호자 이름을 입력해주세요.")
        elif not owner_email or "@" not in owner_email:
            st.error("올바른 이메일 주소를 입력해주세요.")
        elif not dog_name:
            st.error("반려견 이름을 입력해주세요.")
        elif not active_selected and not has_extra and not dry_diet:
            st.error("⚠️ 식단 재료를 입력해주세요. 생식·화식·혼합급여는 재료 선택 또는 직접 입력이 필수입니다. (건사료·동결건조만 선택하지 않아도 됩니다.)")
        else:
            # One canonical request, including the untouched questionnaire and supplement inputs.
            original_input = {k: globals().get(k) for k in (
                "owner_name", "owner_email", "dog_name", "dog_breed", "dog_age", "dog_gender", "neutered",
                "dog_weight", "goal_weight", "selected_der", "activity", "current_diet", "diet_duration",
                "feeding_freq", "prev_diet", "body_condition", "weight_change_final", "diseases", "medications",
                "supplements", "allergies", "stool_status", "walk_time", "exercise", "vomit_status", "sleep_hours",
                "water_intake", "activity_memo", "question", "snack_input", "order_number")}
            original_input.update(auth_phone=st.session_state.get("auth_phone", ""),
                diet_type=diet_type, cooking_method=cooking_method_input, amounts=dict(active_amounts),
                selected=list(active_selected), extra_items=list(extra_items),
                kelp_enabled=use_kelp, kelp_iodine_mcg=kelp_iodine_total,
                calcium_inputs={"eggshell_enabled": use_egg, "eggshell_g": egg_g, "eggshell_ca_mg_per_g": egg_ca,
                                "calcium_enabled": use_sup, "calcium_g": sup_g, "calcium_mg_per_g": sup_ca} if is_cooked else {},
                photo_submission="카카오채널")
            nutrition_request = make_request(active_amounts, cooked=is_cooked, method=cooking_method_input,
                weight=dog_weight, activity=activity, kelp=kelp_iodine_total, calcium=ca_supplement_total,
                supplements={"kelp_enabled": use_kelp, "iodine_mcg": kelp_iodine_total,
                             "calcium_inputs": original_input["calcium_inputs"]},
                original_fields=original_input, excluded=extra_items + ([{"snacks_text": snack_input}] if snack_input else []))
            nutrition_result = calculate(nutrition_request, "review")
            render_data_warnings(st, nutrition_result)
            total_grams = nutrition_result["input_grams"]
            mass_breakdown = nutrition_result["mass"]
            total_stats = nutrition_result["nutrients"]
            total_kcal = nutrition_result["kcal"]
            application_snapshot = create_snapshot(nutrition_request, nutrition_result, original_input)
            snapshot_json = dumps_snapshot(application_snapshot)
            if kelp_iodine_total > 0:
                st.caption(f"🌿 켈프 보충제로 요오드 {kelp_iodine_total:.0f}mcg 추가 공급 중")

            # ── AAFCO 판정 ────────────────────────────────────────────────────
            res_data = []
            aafco_summary = {}
            if total_kcal > 0:
                for nutri, std in aafco_standards.items():
                    val_1000 = nutrition_result["per_1000kcal"][nutri]
                    min_v, max_v = std['min'], std['max']
                    if basic_judgments(nutrition_result, "review")[nutri] == "low":
                        status = "❌ 부족"
                    elif basic_judgments(nutrition_result, "review")[nutri] == "high":
                        status = "⚠️ 과잉"
                    else:
                        status = "✅ 적합"
                    res_data.append({
                        "영양소": nutri,
                        "현재(1000kcal당)": f"{val_1000:.2f}",
                        "AAFCO 최소": str(min_v),
                        "판정": status
                    })
                    aafco_summary[nutri] = f"{val_1000:.2f} ({status})"

            # ── 구글 시트 저장 ────────────────────────────────────────────────
            auth_ph = st.session_state.get("auth_phone", "unknown")
            today_str = str(date.today())
            ws = get_gsheet()
            sheet_saved = False
            st.session_state["snapshot_saved_to_sheet"] = False
            if ws:
                row_dict = {
                    "신청일시": today_str,
                    "신청이메일": owner_email,
                    "인증경로": st.session_state.get("auth_track", ""),
                    "보호자이름": owner_name,
                    "전화번호": auth_ph,
                    "주문번호": order_number,
                    "결제확인여부": "미확인",
                    "이름": dog_name,
                    "견종": dog_breed,
                    "나이": dog_age,
                    "성별": dog_gender,
                    "중성화": neutered,
                    "체중(kg)": dog_weight,
                    "목표체중(kg)": goal_weight,
                    "활동계수": activity,
                    "DER(kcal)": f"{der:.0f}",
                    "현재식단": current_diet,
                    "식단종류": "화식" if is_cooked else "생식",
                    "조리방법": cooking_method_input if is_cooked else "-",
                    "식단기간": diet_duration,
                    "급여횟수": feeding_freq,
                    "이전식단이력": prev_diet,
                    "체형(BCS)": body_condition,
                    "체중변화": weight_change_final,
                    "질환": diseases,
                    "약": medications,
                    "영양제": supplements,
                    "알레르기": allergies,
                    "배변상태": stool_status,
                    "산책시간": walk_time,
                    "추가운동": exercise,
                    "수면시간": sleep_hours,
                    "물섭취": water_intake,
                    "구토": vomit_status,
                    "활동량메모": activity_memo,
                    "궁금한점": question,
                    "총칼로리(kcal)": f"{total_kcal:.0f}",
                    "총그람(g)": total_grams,
                    "사진제출방식": "카카오채널",
                    "사진확인여부": "미확인",
                }
                # 선택 재료를 한 칸에 텍스트로 합쳐서 저장 (컬럼 개수 변동 방지)
                material_parts = [f"{f}:{active_amounts.get(f, 0)}g" for f in active_selected]
                for item in extra_items:
                    if item.get("재료명", "").strip():
                        material_parts.append(f"{item['재료명']}:{item['용량']}{item['단위']}")
                row_dict["선택재료"] = ", ".join(material_parts)
                row_dict["간식"] = snack_input.strip()
                # 검토 상태 / 관리자 메모 / 확인메일 (초기값, 항상 고정된 마지막 컬럼)
                row_dict["검토상태"] = "미검토"
                row_dict["관리자메모"] = ""
                row_dict["확인메일"] = "미발송"

                # The administrator adds this last header once. Never mutate operational columns here.
                snapshot_headers = [h.strip() for h in ws.row_values(1)] if ws.get_all_values() else []
                snapshot_supported = SNAPSHOT_COLUMN in snapshot_headers
                if not snapshot_supported:
                    st.error(
                        f"신청을 저장할 수 없습니다. Google Sheets 마지막 열에 '{SNAPSHOT_COLUMN}' 헤더가 필요합니다. "
                        "이용권은 사용완료로 처리되지 않았습니다."
                    )
                else:
                    try:
                        row_dict[SNAPSHOT_COLUMN] = encode_snapshot_for_sheet(snapshot_json)
                    except SnapshotError as exc:
                        st.error(f"신청을 저장할 수 없습니다: {exc} 이용권은 사용완료로 처리되지 않았습니다.")
                    else:
                        sheet_saved = append_to_sheet(ws, row_dict)
                        st.session_state["snapshot_saved_to_sheet"] = bool(sheet_saved)

                # 결제 건 사용완료 처리 (재제출 방지)
                if sheet_saved and st.session_state["snapshot_saved_to_sheet"]:
                    mark_review_used(st.session_state.get("auth_row"))

            # 제출 완료 → session_state 업데이트 후 완료 화면으로 전환
            if sheet_saved and st.session_state["snapshot_saved_to_sheet"]:
                st.session_state.submitted = True
                st.session_state.submitted_name = dog_name
                st.rerun()

            # 시트 저장 실패 시에도 결과는 표시
            st.divider()
            st.subheader("📊 자동 영양 분석 결과")

            # 신청 정보 요약
            with st.expander("📋 신청 정보 요약", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"**이름:** {dog_name}")
                    st.markdown(f"**견종:** {dog_breed}")
                    st.markdown(f"**나이:** {dog_age}세")
                with c2:
                    st.markdown(f"**성별:** {dog_gender} / {'중성화' if neutered == '예' else '미중성화'}")
                    st.markdown(f"**체중:** {dog_weight}kg → 목표 {goal_weight}kg")
                    st.markdown(f"**현재 식단:** {current_diet} ({diet_duration})")
                with c3:
                    st.markdown(f"**질환:** {diseases or '없음'}")
                    st.markdown(f"**알레르기:** {allergies or '없음'}")
                    st.markdown(f"**궁금한 점:** {question or '없음'}")

            # 업로드된 사진 미리보기
            if dog_photo or diet_photo:
                with st.expander("📸 업로드된 사진", expanded=True):
                    pc1, pc2 = st.columns(2)
                    with pc1:
                        if dog_photo:
                            dog_photo.seek(0)
                            st.image(dog_photo, caption="반려견 사진", use_container_width=True)
                        else:
                            st.caption("반려견 사진 없음")
                    with pc2:
                        if diet_photo:
                            diet_photo.seek(0)
                            st.image(diet_photo, caption="식단 사진", use_container_width=True)
                        else:
                            st.caption("식단 사진 없음")

            # 칼로리 & 비율
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### ⚖️ 식단 비율")
                if total_grams > 0:
                    pct_bone   = mass_breakdown['actual_bone']  / total_grams * 100
                    pct_meat   = mass_breakdown['muscle_meat']  / total_grams * 100
                    pct_organ  = mass_breakdown['organ']        / total_grams * 100
                    pct_veggie = mass_breakdown['veggie']       / total_grams * 100
                    st.write(f"🦴 뼈 **{pct_bone:.1f}%** (목표 12%)")
                    st.progress(min(pct_bone/20, 1.0))
                    st.write(f"🥩 살코기 **{pct_meat:.1f}%** (목표 60~70%)")
                    st.progress(min(pct_meat/100, 1.0))
                    st.write(f"🫀 내장 **{pct_organ:.1f}%** (목표 10~25%)")
                    st.progress(min(pct_organ/40, 1.0))
                    st.write(f"🥦 야채 **{pct_veggie:.1f}%** (목표 5~10%)")
                    st.progress(min(pct_veggie/20, 1.0))

            with col2:
                st.markdown("#### 🔥 칼로리")
                kcal_pct = (total_kcal / der) * 100 if der > 0 else 0
                st.metric("섭취 칼로리", f"{total_kcal:.0f} kcal")
                st.metric("목표 칼로리 (DER)", f"{der:.0f} kcal")
                delta = total_kcal - der
                st.metric("차이", f"{delta:+.0f} kcal")
                st.progress(min(kcal_pct/100, 1.0), text=f"칼로리 충족률: {kcal_pct:.1f}%")

            # AAFCO 분석
            st.markdown("#### 📊 AAFCO 영양 분석")
            if res_data:
                def color_status(val):
                    if "적합" in str(val): return "color:green;font-weight:bold"
                    if "부족" in str(val): return "color:red;font-weight:bold"
                    return "color:orange;font-weight:bold"

                st.dataframe(
                    pd.DataFrame(res_data).style.map(color_status, subset=['판정']),
                    use_container_width=True, hide_index=True
                )

            # CSV 다운로드
            st.divider()
            save_data = {
                "보호자이름": owner_name, "전화번호": st.session_state.get("auth_phone", ""),
                "신청이메일": owner_email, "주문번호": order_number,
                "이름": dog_name, "견종": dog_breed, "나이": dog_age,
                "체중": dog_weight, "목표체중": goal_weight,
                "현재식단": current_diet, "식단기간": diet_duration,
                "급여횟수": feeding_freq, "체형소견": body_condition,
                "질환": diseases, "알레르기": allergies,
                "영양제": supplements, "궁금한점": question,
                "배변상태": stool_status, "산책시간": walk_time,
                "추가운동": exercise, "수면시간": sleep_hours,
                "물섭취": water_intake, "구토": vomit_status, "활동량메모": activity_memo,
                "총칼로리": f"{total_kcal:.0f}", "목표칼로리": f"{der:.0f}",
                "사진제출방식": "카카오채널",
                "사진확인여부": "미확인",
            }
            material_parts_csv = [f"{f}:{active_amounts.get(f, 0)}g" for f in active_selected]
            for item in extra_items:
                if item.get("재료명", "").strip():
                    material_parts_csv.append(f"{item['재료명']}:{item['용량']}{item['단위']}")
            save_data["선택재료"] = ", ".join(material_parts_csv)

            csv = pd.DataFrame([save_data]).to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                "📥 신청서 저장 (CSV)",
                csv,
                f"식단검토_{dog_name}_{date.today()}.csv",
                "text/csv",
                use_container_width=True
            )

# ── 푸터 ──────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("🐾 반려견 영양연구소 | 반려견 식단 분석")
