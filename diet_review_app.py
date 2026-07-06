import streamlit as st
import pandas as pd
from datetime import date
import gspread
from google.oauth2.service_account import Credentials
import json
import base64
from io import BytesIO
from PIL import Image

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
def get_review_eligible_emails() -> set:
    """결제정보 탭 — 상품유형에 '식단검토' 포함된 이메일 (5분 캐시)
    B열=이메일, D열=상품유형"""
    try:
        client = get_gspread_client()
        sh = client.open_by_url(st.secrets["google_sheet"]["url"])
        ws = sh.worksheet("결제내역")
        rows = ws.get_all_values()
        if not rows:
            return set()
        # B열=index 1, D열=index 3 (헤더 자동감지 + fallback)
        header = [h.strip() for h in rows[0]]
        ec = header.index("이메일")    if "이메일"    in header else 1  # B열
        pc = header.index("상품유형")  if "상품유형"  in header else 3  # D열
        return {
            row[ec].strip().lower()
            for row in rows[1:]
            if len(row) > max(ec, pc)
            and row[ec].strip()
            and "식단검토" in row[pc].strip()
        }
    except Exception:
        return set()

def check_auth(email: str) -> tuple[bool, str]:
    """이메일 인증. (통과여부, 인증경로) 반환"""
    e = email.strip().lower()
    if e in get_review_eligible_emails():
        return True, "결제정보"
    return False, ""

def mark_review_used(email: str):
    """현재는 별도 사용처리 없음 (무료 홍보기간)"""
    pass

def append_to_sheet(ws, row_dict: dict):
    """헤더가 없으면 첫 행에 추가, 있으면 데이터만 추가"""
    try:
        existing = ws.get_all_values()
        headers = list(row_dict.keys())
        # 첫 행이 비어있거나 시트 자체가 비어있을 때만 헤더 추가
        if not existing or existing[0] == [] or existing[0][0] == "":
            ws.append_row(headers)
        ws.append_row(list(row_dict.values()))
        return True
    except Exception as e:
        st.warning(f"구글 시트 저장 실패: {e}")
        return False

# ── AAFCO 기준 ─────────────────────────────────────────────────────────────
aafco_standards = {
    "단백질(g)": {"min": 45,   "max": None},
    "지방(g)":   {"min": 13.8, "max": None},
    "칼슘(mg)":  {"min": 1250, "max": 6250},
    "인(mg)":    {"min": 1000, "max": 4000},
    "철(mg)":    {"min": 10,   "max": None},
    "아연(mg)":  {"min": 20,   "max": None},
    "구리(mg)":  {"min": 1.83, "max": None},
    "망간(mg)":  {"min": 1.25, "max": None},
    "비타민A(IU)": {"min": 1250, "max": None},
    "비타민D(IU)": {"min": 125,  "max": None},
    "비타민E(IU)": {"min": 12.5, "max": None},
    "나트륨(mg)": {"min": 200,  "max": None},
    "요오드(mcg)": {"min": 220,  "max": 1400},  # NRC 2006 권장 220, 안전상한 1400 (1000kcal당)
}

# ── 재료 DB ────────────────────────────────────────────────────────────────
db_data = [
    # ── 뼈류 ──
    # ── 뼈고기 (bone) ─────────────────────────────────────────────────────────
    # 칼슘 출처: Segal=Monica Segal K9 Kitchen 실측값, est=bone_pct×4166 추정값
    # 인(P): Segal 실측값 없음 → 뼈 Ca:P≈2:1 기준으로 칼슘×0.5 추정
    # 요오드: 해조류 아니므로 0
    {"재료명":"닭발 (뼈 60%)",        "category":"bone","bone_pct":0.60,"칼슘":1839,"인":920,"칼슘출처":"est_poultry_avg30.6", "칼로리":215,"단백질":19.0,"지방":14.6,"철":2.0, "아연":1.5, "구리":0.1,  "망간":0.05,"비타민A":30,  "비타민D":0,  "비타민E":0,   "나트륨":67, "요오드(mcg)":0},
    {"재료명":"닭목뼈 (뼈 36%)",      "category":"bone","bone_pct":0.36,"칼슘":1150,"인":575, "칼슘출처":"Segal","칼로리":154,"단백질":17.6,"지방":8.78,"철":2.06,"아연":2.68,"구리":0.1,  "망간":0.03,"비타민A":146, "비타민D":0,  "비타민E":0,   "나트륨":81, "요오드(mcg)":0},
    {"재료명":"닭날개 (뼈 45%)",      "category":"bone","bone_pct":0.45,"칼슘":920, "인":460, "칼슘출처":"Segal","칼로리":203,"단백질":18.0,"지방":14.0,"철":1.0, "아연":1.0, "구리":0.1,  "망간":0.02,"비타민A":40,  "비타민D":0,  "비타민E":0.3, "나트륨":70, "요오드(mcg)":0},
    {"재료명":"닭북채 (뼈 30%)",      "category":"bone","bone_pct":0.30,"칼슘":880, "인":440, "칼슘출처":"Segal","칼로리":120,"단백질":18.0,"지방":4.0, "철":0.8, "아연":1.5, "구리":0.1,  "망간":0.02,"비타민A":20,  "비타민D":0,  "비타민E":0.2, "나트륨":80, "요오드(mcg)":0},
    {"재료명":"전체 칠면조 (뼈 21%)", "category":"bone","bone_pct":0.21,"칼슘":644, "인":322, "칼슘출처":"est_poultry_avg30.6", "칼로리":160,"단백질":20.0,"지방":8.0, "철":1.5, "아연":2.0, "구리":0.1,  "망간":0.02,"비타민A":50,  "비타민D":0,  "비타민E":0,   "나트륨":60, "요오드(mcg)":0},
    {"재료명":"칠면조 목뼈 (뼈 42%)", "category":"bone","bone_pct":0.42,"칼슘":1840,"인":920, "칼슘출처":"Segal","칼로리":225,"단백질":30.0,"지방":11.0,"철":2.0, "아연":3.0, "구리":0.2,  "망간":0.04,"비타민A":40,  "비타민D":0,  "비타민E":0,   "나트륨":90, "요오드(mcg)":0},
    {"재료명":"칠면조 날개 (뼈 37%)", "category":"bone","bone_pct":0.37,"칼슘":1134,"인":567,"칼슘출처":"est_poultry_avg30.6","칼로리":200,"단백질":18.0,"지방":13.0,"철":1.5, "아연":1.5, "구리":0.1,  "망간":0.02,"비타민A":30,  "비타민D":0,  "비타민E":0,   "나트륨":80, "요오드(mcg)":0},
    {"재료명":"전체 오리 (뼈 28%)",   "category":"bone","bone_pct":0.28,"칼슘":870, "인":435, "칼슘출처":"Segal","칼로리":250,"단백질":15.0,"지방":20.0,"철":2.5, "아연":1.8, "구리":0.2,  "망간":0.03,"비타민A":60,  "비타민D":0,  "비타민E":0.5, "나트륨":65, "요오드(mcg)":0},
    {"재료명":"오리 목뼈 (뼈 50%)",   "category":"bone","bone_pct":0.50,"칼슘":1532,"인":766,"칼슘출처":"est_poultry_avg30.6", "칼로리":250,"단백질":18.0,"지방":18.0,"철":2.8, "아연":2.0, "구리":0.2,  "망간":0.04,"비타민A":50,  "비타민D":0,  "비타민E":0,   "나트륨":85, "요오드(mcg)":0},
    {"재료명":"오리발 (뼈 60%)",      "category":"bone","bone_pct":0.60,"칼슘":1839,"인":920,"칼슘출처":"est_poultry_avg30.6", "칼로리":253,"단백질":20.0,"지방":18.0,"철":2.0, "아연":1.5, "구리":0.1,  "망간":0.05,"비타민A":40,  "비타민D":0,  "비타민E":0,   "나트륨":90, "요오드(mcg)":0},
    {"재료명":"소갈비뼈 (뼈 52%)",    "category":"bone","bone_pct":0.52,"칼슘":2621,"인":1310,"칼슘출처":"est_mammal_avg50.4", "칼로리":300,"단백질":18.0,"지방":25.0,"철":3.0, "아연":4.5, "구리":0.1,  "망간":0.02,"비타민A":10,  "비타민D":2,  "비타민E":0,   "나트륨":70, "요오드(mcg)":0},
    {"재료명":"소꼬리 (뼈 55%)",      "category":"bone","bone_pct":0.55,"칼슘":2772,"인":1386,"칼슘출처":"est_mammal_avg50.4", "칼로리":262,"단백질":21.0,"지방":18.0,"철":4.9, "아연":3.5, "구리":0.1,  "망간":0.02,"비타민A":0,   "비타민D":0,  "비타민E":0,   "나트륨":60, "요오드(mcg)":0},
    {"재료명":"양 갈비뼈 (뼈 27%)",   "category":"bone","bone_pct":0.27,"칼슘":1360,"인":680, "칼슘출처":"Segal","칼로리":355,"단백질":22.0,"지방":30.0,"철":2.0, "아연":4.0, "구리":0.1,  "망간":0.02,"비타민A":0,   "비타민D":1,  "비타민E":0.1, "나트륨":76, "요오드(mcg)":0},
    {"재료명":"양 목뼈 (뼈 32%)",     "category":"bone","bone_pct":0.32,"칼슘":1613,"인":806, "칼슘출처":"est_mammal_avg50.4", "칼로리":260,"단백질":20.0,"지방":20.0,"철":4.0, "아연":4.2, "구리":0.2,  "망간":0.02,"비타민A":0,   "비타민D":0,  "비타민E":0,   "나트륨":70, "요오드(mcg)":0},
    {"재료명":"전체 메츄리 (뼈 10%)", "category":"bone","bone_pct":0.10,"칼슘":306, "인":153, "칼슘출처":"est_poultry_avg30.6", "칼로리":200,"단백질":20.0,"지방":12.0,"철":4.0, "아연":2.5, "구리":0.5,  "망간":0.02,"비타민A":50,  "비타민D":10, "비타민E":1.0, "나트륨":50, "요오드(mcg)":0},

    # ── 분비성 내장 (organ) ──
    {"재료명":"소간 (Beef Liver)",          "category":"organ","bone_pct":0,"칼로리":135,"단백질":20.4,"지방":3.63,"칼슘":5,  "인":387,"철":4.9,  "아연":4.0, "구리":9.76, "망간":0.31, "비타민A":16900,"비타민D":49,"비타민E":0.38,"나트륨":69, "요오드(mcg)":0},
    {"재료명":"소신장 (Beef Kidney)",        "category":"organ","bone_pct":0,"칼로리":97, "단백질":17.4,"지방":2.82,"칼슘":13, "인":257,"철":4.37, "아연":1.93,"구리":0.436,"망간":0.138,"비타민A":1166, "비타민D":49,"비타민E":0.29,"나트륨":182, "요오드(mcg)":0},
    {"재료명":"소비장/지라 (Beef Spleen)",   "category":"organ","bone_pct":0,"칼로리":105,"단백질":18.3,"지방":3.04,"칼슘":8,  "인":249,"철":30.3, "아연":2.42,"구리":0.147,"망간":0.032,"비타민A":8,    "비타민D":0, "비타민E":0.26,"나트륨":84, "요오드(mcg)":0},
    {"재료명":"소췌장 (Beef Pancreas)",      "category":"organ","bone_pct":0,"칼로리":233,"단백질":14.7,"지방":19.1,"칼슘":10, "인":234,"철":2.26, "아연":2.0, "구리":0.097,"망간":0.046,"비타민A":0,    "비타민D":0, "비타민E":0.23,"나트륨":85, "요오드(mcg)":0},
    {"재료명":"닭간 (Chicken Liver)",        "category":"organ","bone_pct":0,"칼로리":119,"단백질":16.9,"지방":4.83,"칼슘":8,  "인":297,"철":9.0,  "아연":2.67,"구리":0.492,"망간":0.351,"비타민A":3296, "비타민D":55,"비타민E":1.1, "나트륨":71, "요오드(mcg)":0},
    {"재료명":"오리간 (Duck Liver)",         "category":"organ","bone_pct":0,"칼로리":136,"단백질":18.7,"지방":4.64,"칼슘":11, "인":263,"철":30.5, "아연":2.68,"구리":0.999,"망간":0.314,"비타민A":4970, "비타민D":80,"비타민E":1.51,"나트륨":144, "요오드(mcg)":0},
    {"재료명":"돼지간 (Pork Liver)",         "category":"organ","bone_pct":0,"칼로리":134,"단백질":20.9,"지방":3.65,"칼슘":8,  "인":387,"철":17.9, "아연":4.02,"구리":0.796,"망간":0.355,"비타민A":6502, "비타민D":53,"비타민E":0.39,"나트륨":49, "요오드(mcg)":0},
    {"재료명":"돼지신장 (Pork Kidney)",      "category":"organ","bone_pct":0,"칼로리":100,"단백질":16.7,"지방":3.09,"칼슘":10, "인":244,"철":4.52, "아연":2.07,"구리":0.344,"망간":0.078,"비타민A":36,   "비타민D":49,"비타민E":0.26,"나트륨":113, "요오드(mcg)":0},
    {"재료명":"그린트라이프 (Green Tripe)",  "category":"organ","bone_pct":0,"칼로리":85, "단백질":14.9,"지방":1.98,"칼슘":112,"인":159,"철":4.44, "아연":1.72,"구리":0.094,"망간":4.06, "비타민A":20,   "비타민D":8, "비타민E":0.45,"나트륨":81, "요오드(mcg)":0},

    # ── 근육고기 (meat) — 심장·폐·모래주머니·생식기 포함 ──
    {"재료명":"닭가슴살 (Chicken Breast)",   "category":"meat","bone_pct":0,"칼로리":120,"단백질":22.5,"지방":2.62,"칼슘":5,  "인":213,"철":0.37, "아연":0.68,"구리":0.037,"망간":0.011,"비타민A":30,   "비타민D":0, "비타민E":0.56,"나트륨":45, "요오드(mcg)":0},
    {"재료명":"소고기 (Beef)",               "category":"meat","bone_pct":0,"칼로리":152,"단백질":20.8,"지방":7.0, "칼슘":10, "인":192,"철":2.33, "아연":4.97,"구리":0.075,"망간":0.01, "비타민A":14,   "비타민D":3, "비타민E":0.17,"나트륨":66, "요오드(mcg)":0},
    {"재료명":"말고기 (Horse Meat)",         "category":"meat","bone_pct":0,"칼로리":133,"단백질":21.4,"지방":4.6, "칼슘":6,  "인":221,"철":3.82, "아연":2.9, "구리":0.144,"망간":0.019,"비타민A":0,    "비타민D":0, "비타민E":0,   "나트륨":53, "요오드(mcg)":0},
    {"재료명":"사슴고기 (Venison)",          "category":"meat","bone_pct":0,"칼로리":116,"단백질":21.5,"지방":2.66,"칼슘":7,  "인":201,"철":2.92, "아연":4.2, "구리":0.14, "망간":0.014,"비타민A":0,    "비타민D":0, "비타민E":0,   "나트륨":75, "요오드(mcg)":0},
    {"재료명":"정어리 (Sardine)",            "category":"meat","bone_pct":0,"칼로리":208,"단백질":24.6,"지방":11.4,"칼슘":382,"인":490,"철":2.92, "아연":1.4, "구리":0.186,"망간":0,    "비타민A":30,   "비타민D":4.8,"비타민E":1.38,"나트륨":307, "요오드(mcg)":0},
    {"재료명":"계란노른자 (Egg Yolk)",       "category":"meat","bone_pct":0,"칼로리":322,"단백질":15.9,"지방":26.5,"칼슘":129,"인":390,"철":2.73, "아연":2.3, "구리":0.077,"망간":0.31, "비타민A":1440, "비타민D":49,"비타민E":0.38,"나트륨":48, "요오드(mcg)":0},
    # 근육성 기관 (organ → meat 재분류)
    {"재료명":"소심장 (Beef Heart)",         "category":"meat","bone_pct":0,"칼로리":112,"단백질":18.5,"지방":3.4, "칼슘":4,  "인":209,"철":4.38, "아연":1.51,"구리":0.373,"망간":0.034,"비타민A":34,   "비타민D":6, "비타민E":1.22,"나트륨":86, "요오드(mcg)":0},
    {"재료명":"소폐 (Beef Lung)",            "category":"meat","bone_pct":0,"칼로리":92, "단백질":16.2,"지방":2.5, "칼슘":10, "인":224,"철":7.95, "아연":1.61,"구리":0.26, "망간":0.019,"비타민A":46,   "비타민D":0, "비타민E":0,   "나트륨":198, "요오드(mcg)":0},
    {"재료명":"소우신통 (Beef Penis)",       "category":"meat","bone_pct":0,"칼로리":120,"단백질":22.0,"지방":3.0, "칼슘":8,  "인":180,"철":2.0,  "아연":2.0, "구리":0.1,  "망간":0.02, "비타민A":0,    "비타민D":0, "비타민E":0.5, "나트륨":70, "요오드(mcg)":0},
    {"재료명":"닭심장 (Chicken Heart)",      "category":"meat","bone_pct":0,"칼로리":153,"단백질":15.6,"지방":9.33,"칼슘":11, "인":159,"철":5.95, "아연":6.49,"구리":0.301,"망간":0.073,"비타민A":34,   "비타민D":0, "비타민E":1.0, "나트륨":65, "요오드(mcg)":0},
    {"재료명":"닭근위 (Chicken Gizzard)",    "category":"meat","bone_pct":0,"칼로리":94, "단백질":17.7,"지방":2.06,"칼슘":9,  "인":148,"철":2.49, "아연":2.72,"구리":0.122,"망간":0.038,"비타민A":40,   "비타민D":0, "비타민E":0.22,"나트륨":69, "요오드(mcg)":0},
    {"재료명":"돼지심장 (Pork Heart)",       "category":"meat","bone_pct":0,"칼로리":118,"단백질":17.7,"지방":4.67,"칼슘":6,  "인":210,"철":3.37, "아연":2.63,"구리":0.382,"망간":0.031,"비타민A":0,    "비타민D":49,"비타민E":0.83,"나트륨":57, "요오드(mcg)":0},

    # ── 채소·과일·기타 (veggie) ──
    {"재료명":"블루베리 (Blueberry)",        "category":"veggie","bone_pct":0,"칼로리":57, "단백질":0.74,"지방":0.33,"칼슘":6,  "인":12, "철":0.28, "아연":0.06,"구리":1.6,  "망간":0.262,"비타민A":54,   "비타민D":0, "비타민E":0.57,"나트륨":1, "요오드(mcg)":0},
    {"재료명":"브로콜리 퓨레 (Broccoli)",   "category":"veggie","bone_pct":0,"칼로리":34, "단백질":2.82,"지방":0.37,"칼슘":47, "인":66, "철":0.73, "아연":0.41,"구리":0.049,"망간":0.21, "비타민A":623,  "비타민D":0, "비타민E":0.78,"나트륨":33, "요오드(mcg)":0},
    {"재료명":"토마토 퓨레 (Tomato)",       "category":"veggie","bone_pct":0,"칼로리":18, "단백질":0.88,"지방":0.2, "칼슘":10, "인":24, "철":0.27, "아연":0.17,"구리":0.059,"망간":0.114,"비타민A":833,  "비타민D":0, "비타민E":0.54,"나트륨":5, "요오드(mcg)":0},
    {"재료명":"우엉 퓨레 (Burdock Root)",   "category":"veggie","bone_pct":0,"칼로리":72, "단백질":1.53,"지방":0.15,"칼슘":41, "인":51, "철":0.8,  "아연":0.33,"구리":0.08, "망간":0.23, "비타민A":0,    "비타민D":0, "비타민E":0.4, "나트륨":5, "요오드(mcg)":0},
    {"재료명":"청경채 퓨레 (Bok Choy)",    "category":"veggie","bone_pct":0,"칼로리":13, "단백질":1.5, "지방":0.2, "칼슘":105,"인":37, "철":0.8,  "아연":0.19,"구리":0.021,"망간":0.159,"비타민A":4468, "비타민D":0, "비타민E":0.09,"나트륨":65, "요오드(mcg)":0},
    {"재료명":"단호박 퓨레 (Kabocha)",     "category":"veggie","bone_pct":0,"칼로리":34, "단백질":1.0, "지방":0.1, "칼슘":20, "인":30, "철":0.4,  "아연":0.15,"구리":0.07, "망간":0.15, "비타민A":1370, "비타민D":0, "비타민E":0.3, "나트륨":3, "요오드(mcg)":0},
    {"재료명":"본브로스 소뼈 (Bone Broth)","category":"veggie","bone_pct":0,"칼로리":18, "단백질":4.0, "지방":0.0, "칼슘":5,  "인":10, "철":0.2,  "아연":0.1, "구리":0.02, "망간":0,    "비타민A":0,    "비타민D":0, "비타민E":0,   "나트륨":20, "요오드(mcg)":0},
    {"재료명":"파프리카 퓨레 (Paprika)",   "category":"veggie","bone_pct":0,"칼로리":31, "단백질":1.0, "지방":0.3, "칼슘":7,  "인":26, "철":0.43, "아연":0.25,"구리":0.017,"망간":0.11, "비타민A":3131, "비타민D":0, "비타민E":1.58,"나트륨":4, "요오드(mcg)":0},
    {"재료명":"샐러리 퓨레 (Celery)",     "category":"veggie","bone_pct":0,"칼로리":16, "단백질":0.69,"지방":0.17,"칼슘":40, "인":24, "철":0.2,  "아연":0.13,"구리":0.04, "망간":0.1,  "비타민A":449,  "비타민D":0, "비타민E":0.27,"나트륨":80, "요오드(mcg)":0},
    {"재료명":"당근 퓨레 (Carrot)",       "category":"veggie","bone_pct":0,"칼로리":41, "단백질":0.93,"지방":0.24,"칼슘":33, "인":35, "철":0.3,  "아연":0.24,"구리":0.045,"망간":0.143,"비타민A":16706,"비타민D":0, "비타민E":0.66,"나트륨":69, "요오드(mcg)":0},

    # ── v5.3 신규 추가 ──────────────────────────────────────────────────────
    # 칠면조 가슴살 (USDA FDC 171093 Turkey breast raw)
    {"재료명":"칠면조 가슴살 (Turkey Breast)",  "category":"meat","bone_pct":0,"칼로리":111,"단백질":24.6,"지방":0.7, "칼슘":10, "인":206,"철":1.2,  "아연":1.2, "구리":0.06, "망간":0.02, "비타민A":0,    "비타민D":0,  "비타민E":0.1, "나트륨":49, "요오드(mcg)":0},
    # 오리 가슴살 야생 (USDA FDC 174469 Duck wild breast raw)
    {"재료명":"오리 가슴살 (Duck Breast)",      "category":"meat","bone_pct":0,"칼로리":123,"단백질":19.8,"지방":4.3, "칼슘":3,  "인":186,"철":4.5,  "아연":1.5, "구리":0.26, "망간":0.02, "비타민A":60,   "비타민D":0,  "비타민E":0.5, "나트륨":57, "요오드(mcg)":0},
    # 염소고기 (USDA FDC 175306 Goat raw)
    {"재료명":"염소고기 (Goat)",               "category":"meat","bone_pct":0,"칼로리":109,"단백질":20.6,"지방":2.31,"칼슘":13, "인":180,"철":2.83, "아연":4.5, "구리":0.11, "망간":0.019,"비타민A":0,    "비타민D":0,  "비타민E":0.27,"나트륨":82, "요오드(mcg)":0},
    # 양고기 다리살 (USDA FDC 174369 Lamb leg raw)
    {"재료명":"양고기 (Lamb)",                 "category":"meat","bone_pct":0,"칼로리":153,"단백질":20.3,"지방":7.64,"칼슘":16, "인":190,"철":1.88, "아연":3.95,"구리":0.117,"망간":0.023,"비타민A":0,    "비타민D":0,  "비타민E":0.14,"나트륨":72, "요오드(mcg)":0},
    # 열빙어/smelt (USDA FDC 175150 Smelt rainbow raw)
    {"재료명":"열빙어 (Smelt)",                "category":"meat","bone_pct":0,"칼로리":97, "단백질":17.6,"지방":2.42,"칼슘":60, "인":230,"철":0.9,  "아연":1.7, "구리":0.14, "망간":0.7,  "비타민A":15,   "비타민D":32, "비타민E":0.5, "나트륨":60, "요오드(mcg)":0},
    # 산양유 케피어/요거트 (USDA FDC 171264 Goat milk raw 기반, 발효로 단백질/칼슘 소폭 조정)
    {"재료명":"산양유 케피어 (Goat Kefir)",    "category":"meat","bone_pct":0,"칼로리":69, "단백질":3.5, "지방":4.0, "칼슘":134,"인":111,"철":0.05, "아연":0.3, "구리":0.05, "망간":0.02, "비타민A":185,  "비타민D":4,  "비타민E":0.1, "나트륨":50, "요오드(mcg)":0},


    # ── v5.4 신규 추가 ──────────────────────────────────────────────────────
    # 돼지 안심 (USDA FDC 168249 Pork tenderloin raw)
    {"재료명":"돼지 안심 (Pork Tenderloin)",    "category":"meat","bone_pct":0,"칼로리":109,"단백질":20.9,"지방":2.1, "칼슘":5,  "인":247,"철":0.97, "아연":1.88,"구리":0.094,"망간":0.012,"비타민A":0,    "비타민D":8,  "비타민E":0.22,"나트륨":53, "요오드(mcg)":0},
    # 양간 (USDA FDC 172531 Lamb liver raw) — 100g 환산
    {"재료명":"양간 (Lamb Liver)",              "category":"organ","bone_pct":0,"칼로리":139,"단백질":20.7,"지방":5.1, "칼슘":7,  "인":369,"철":7.5,  "아연":4.6, "구리":7.14, "망간":0.18, "비타민A":6990, "비타민D":0,  "비타민E":0,   "나트륨":71, "요오드(mcg)":0},
    # 꿩 살코기 생 (USDA FDC 169902 Pheasant raw meat only)
    {"재료명":"꿩 (Pheasant)",                  "category":"meat","bone_pct":0,"칼로리":133,"단백질":23.6,"지방":3.6, "칼슘":12, "인":214,"철":1.1,  "아연":1.0, "구리":0.07, "망간":0.01, "비타민A":177,  "비타민D":0,  "비타민E":0,   "나트륨":40, "요오드(mcg)":0},
    # 토끼고기 가정사육 생 (USDA FDC 172521 Rabbit domesticated raw)
    {"재료명":"토끼고기 (Rabbit)",              "category":"meat","bone_pct":0,"칼로리":136,"단백질":20.4,"지방":5.6, "칼슘":13, "인":216,"철":1.6,  "아연":1.6, "구리":0.14, "망간":0.04, "비타민A":0,    "비타민D":0,  "비타민E":0,   "나트륨":41, "요오드(mcg)":0},
    # 치아씨드 (USDA FDC 170554 Chia seeds raw)
    {"재료명":"치아씨드 (Chia Seeds)",          "category":"veggie","bone_pct":0,"칼로리":486,"단백질":16.5,"지방":30.7,"칼슘":631,"인":860,"철":7.7,  "아연":4.6, "구리":0.924,"망간":2.72, "비타민A":54,   "비타민D":0,  "비타민E":0.5, "나트륨":16, "요오드(mcg)":0},
    # 메추리알 생 (USDA FDC 172188 Quail egg raw)
    {"재료명":"메추리알 (Quail Egg)",           "category":"meat","bone_pct":0,"칼로리":158,"단백질":13.1,"지방":11.1,"칼슘":64, "인":226,"철":3.65, "아연":1.47,"구리":0.11, "망간":0.10, "비타민A":543,  "비타민D":132,"비타민E":1.08,"나트륨":141, "요오드(mcg)":0},
    # 연어 대서양 양식 생 (USDA FDC 175167 Salmon Atlantic farmed raw)
    {"재료명":"연어 (Salmon)",                  "category":"meat","bone_pct":0,"칼로리":208,"단백질":20.4,"지방":13.4,"칼슘":9,  "인":240,"철":0.34, "아연":0.36,"구리":0.05, "망간":0.01, "비타민A":50,   "비타민D":447,"비타민E":3.55,"나트륨":59,  "요오드(mcg)":0},
    # ── v5.5 신규 ──────────────────────────────────────────────────────────
    # 켈프 생 (USDA FDC 168457) — 요오드는 제품·종·산지마다 다름, 1500mcg/100g은 참고값
    # NRC 권장: 220mcg/1000kcal, 안전상한: 1400mcg/1000kcal
    {"재료명":"켈프 (Kelp/Seaweed)",            "category":"veggie","bone_pct":0,"칼로리":43, "단백질":1.68,"지방":0.56,"칼슘":168,"인":42, "철":2.85, "아연":1.23,"구리":0.13, "망간":0.2,  "비타민A":116, "비타민D":0,  "비타민E":0.87,"나트륨":233, "요오드(mcg)":1500},
    # 양배추 퓨레 생 (USDA FDC 169975 Cabbage raw)
    {"재료명":"양배추 퓨레 (Cabbage)",          "category":"veggie","bone_pct":0,"칼로리":25, "단백질":1.28,"지방":0.1, "칼슘":40, "인":26,  "철":0.47, "아연":0.18,"구리":0.019,"망간":0.16, "비타민A":98,  "비타민D":0,  "비타민E":0.15,"나트륨":18,  "요오드(mcg)":0},
    # 배추 퓨레 생 (USDA FDC 169979 Napa cabbage raw)
    {"재료명":"배추 퓨레 (Napa Cabbage)",       "category":"veggie","bone_pct":0,"칼로리":12, "단백질":0.9, "지방":0.1, "칼슘":105,"인":37,  "철":0.8,  "아연":0.19,"구리":0.021,"망간":0.16, "비타민A":4468,"비타민D":0,  "비타민E":0.09,"나트륨":65,  "요오드(mcg)":0},

    # ── v5.7 신규 ──────────────────────────────────────────────────────────
    # 소목뼈 (뼈 37% 추정) — USDA 실측값 없음, 포유류 평균 밀도 50.4mg/g 적용
    # Ca = 37g × 50.4 = 1865mg, P = Ca × 0.5 = 932mg
    {"재료명":"소목뼈 (Beef Neck Bone, 뼈 37%)", "category":"bone","bone_pct":0.37,"칼슘":1865,"인":932,"칼슘출처":"est_mammal_avg50.4","칼로리":215,"단백질":17.0,"지방":14.0,"철":3.0,"아연":4.0,"구리":0.1,"망간":0.02,"비타민A":0,"비타민D":0,"비타민E":0,"나트륨":65,"요오드(mcg)":0},

    # 캥거루 사태 raw (AFCD Release 3 FSANZ F009791 — 사태/스테이크 부위)
    {"재료명":"캥거루 사태 (Kangaroo Shank)",    "category":"meat","bone_pct":0,"칼로리":102,"단백질":22.5,"지방":1.0, "칼슘":4,  "인":190,"철":3.1,  "아연":2.6, "구리":0.16, "망간":0.02, "비타민A":0,   "비타민D":2,  "비타민E":0.3, "나트륨":40,  "요오드(mcg)":0},

    # 오리울대 (식도, 순수 근육) — 오리 가슴살 유사 추정값
    {"재료명":"오리 울대 (Duck Esophagus)",      "category":"meat","bone_pct":0,"칼로리":123,"단백질":19.8,"지방":4.3, "칼슘":3,  "인":186,"철":4.5,  "아연":1.5, "구리":0.26, "망간":0.02, "비타민A":60,  "비타민D":0,  "비타민E":0.5, "나트륨":57,  "요오드(mcg)":0},

    # 아몬드 가루 (USDA FDC 170567 Almonds raw — 비타민E 공급원, 소량 사용 권장)
    # ⚠️ 지방 함량 높음(50g/100g), 소량(5~10g/일) 이상 급여 시 췌장 부담 위험
    {"재료명":"아몬드 가루 ⚠️ (Almond Flour)",  "category":"veggie","bone_pct":0,"칼로리":579,"단백질":21.2,"지방":49.9,"칼슘":264,"인":484,"철":3.71, "아연":3.12,"구리":1.03, "망간":2.18, "비타민A":0,   "비타민D":0,  "비타민E":25.6,"나트륨":1,   "요오드(mcg)":0},

    # 햄프씨드 탈각 (USDA FDC 170148 Seeds hemp seed hulled)
    {"재료명":"햄프씨드 (Hemp Seeds)",           "category":"veggie","bone_pct":0,"칼로리":553,"단백질":31.6,"지방":48.8,"칼슘":70, "인":1650,"철":8.0,  "아연":9.9, "구리":1.6,  "망간":7.6,  "비타민A":11,  "비타민D":0,  "비타민E":0.8, "나트륨":5,   "요오드(mcg)":0},

    # 호박씨 가루 (USDA FDC 170556 Seeds pumpkin squash kernels dried)
    {"재료명":"호박씨 가루 (Pumpkin Seeds)",     "category":"veggie","bone_pct":0,"칼로리":559,"단백질":30.2,"지방":49.1,"칼슘":46, "인":1174,"철":8.82, "아연":7.81,"구리":1.34, "망간":4.54, "비타민A":0,   "비타민D":0,  "비타민E":2.18,"나트륨":7,   "요오드(mcg)":0},

    # 크랜베리 생 (USDA FDC 171722 Cranberries raw)
    {"재료명":"크랜베리 (Cranberry)",            "category":"veggie","bone_pct":0,"칼로리":46, "단백질":0.46,"지방":0.13,"칼슘":8,  "인":13,  "철":0.25, "아연":0.1, "구리":0.061,"망간":0.36, "비타민A":60,  "비타민D":0,  "비타민E":1.2, "나트륨":2,   "요오드(mcg)":0},

    # 케일 퓨레 생 (USDA FDC 168421 Kale raw)
    {"재료명":"케일 퓨레 (Kale)",               "category":"veggie","bone_pct":0,"칼로리":35, "단백질":2.92,"지방":1.49,"칼슘":150,"인":92,  "철":1.5,  "아연":0.56,"구리":1.5,  "망간":0.66, "비타민A":15376,"비타민D":0, "비타민E":1.54,"나트륨":38,  "요오드(mcg)":0},

    # 아스파라거스 퓨레 생 (USDA FDC 168389 Asparagus raw)
    {"재료명":"아스파라거스 퓨레 (Asparagus)",   "category":"veggie","bone_pct":0,"칼로리":20, "단백질":2.2, "지방":0.12,"칼슘":24, "인":52,  "철":2.14, "아연":0.54,"구리":0.19, "망간":0.16, "비타민A":756, "비타민D":0,  "비타민E":1.13,"나트륨":2,   "요오드(mcg)":0},

    # 고등어 대서양 생 (USDA FDC 175119 Fish mackerel Atlantic raw)
    {"재료명":"고등어 (Mackerel)",               "category":"meat","bone_pct":0,"칼로리":205,"단백질":18.6,"지방":13.9,"칼슘":12, "인":217,"철":1.63, "아연":0.63,"구리":0.072,"망간":0.018,"비타민A":187, "비타민D":720,"비타민E":1.52,"나트륨":90,  "요오드(mcg)":0},

    # 굴 익힘 (USDA FDC 171980 Oyster eastern wild cooked moist heat) — 100g 기준 환산
    {"재료명":"굴 (Oyster, 익힘)",         "category":"meat","bone_pct":0,"칼로리":102,"단백질":11.4,"지방":3.3, "칼슘":116,"인":194,"철":9.3,  "아연":78.6,"구리":5.71, "망간":0.6,  "비타민A":88,  "비타민D":2,  "비타민E":1.69,"나트륨":166, "요오드(mcg)":109},

    # 초록홍합 익힘 — 생 기준 대비 단백질↑ 수분↓ 미네랄 농축, NZ FSANZ 참고 추정
    {"재료명":"초록홍합 (Green-Lipped Mussel, 익힘)",      "category":"meat","bone_pct":0,"칼로리":97, "단백질":14.8,"지방":2.6, "칼슘":40, "인":303,"철":3.95, "아연":3.1, "구리":0.18, "망간":4.1,  "비타민A":50,  "비타민D":5,  "비타민E":1.1, "나트륨":220, "요오드(mcg)":0},
]
food_df = pd.DataFrame(db_data)

# ═══════════════════════════════════════════════════════════════════════════
# 이메일 인증 게이트
# ═══════════════════════════════════════════════════════════════════════════
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "auth_email" not in st.session_state:
    st.session_state.auth_email = ""
if "auth_track" not in st.session_state:
    st.session_state.auth_track = ""   # "subscriber" | "paid"

# 탭: 신청자 / 관리자
tab_user, tab_admin = st.tabs(["🐾 반려견 식단 분석", "🔒 관리자"])

with tab_admin:
    st.markdown("### 관리자 로그인")
    admin_pw_input = st.text_input("비밀번호", type="password", key="admin_pw_main")
    if st.button("로그인", key="admin_login_main", type="primary"):
        correct_pw = st.secrets.get("admin", {}).get("password", "")
        if admin_pw_input == correct_pw:
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

        def recalculate(row_data: dict) -> tuple:
            weight = float(row_data.get("체중(kg)", 3) or 3)
            activity_val = float(row_data.get("활동계수", 1.6) or 1.6)
            rer = 70 * (weight ** 0.75)
            der_calc = rer * activity_val
            total_kcal = 0.0
            total_stats = {k: 0.0 for k in aafco_standards}
            mass_bd = {"actual_bone": 0, "muscle_meat": 0, "organ": 0, "veggie": 0}
            for _, frow in food_df.iterrows():
                fname = frow["재료명"]
                if fname not in row_data:
                    continue
                try:
                    grams = float(row_data[fname])
                except (ValueError, TypeError):
                    continue
                if grams <= 0:
                    continue
                ratio = grams / 100
                total_kcal += frow["칼로리"] * ratio
                for nutri in aafco_standards:
                    cname = nutri.split("(")[0]
                    if cname in frow:
                        total_stats[nutri] += frow[cname] * ratio
                cat, b_pct = frow["category"], frow["bone_pct"]
                if cat == "bone":
                    mass_bd["actual_bone"] += grams * b_pct
                    mass_bd["muscle_meat"] += grams * (1 - b_pct)
                elif cat == "meat":
                    mass_bd["muscle_meat"] += grams
                elif cat == "organ":
                    mass_bd["organ"] += grams
                else:
                    mass_bd["veggie"] += grams
            return total_kcal, total_stats, mass_bd, der_calc

        import pandas as _pd

        st.markdown("### 📋 신청 목록")
        headers, data_rows = load_applications()

        if not data_rows:
            st.info("신청 내역이 없습니다.")
        else:
            list_cols = ["신청일시", "이름", "견종", "체중(kg)", "현재식단", "검토상태"]
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

            st.markdown(f"## 🐾 {rd.get('이름','?')} ({rd.get('견종','?')}) 상세 검토")

            with st.expander("📋 기본 정보", expanded=True):
                c1, c2, c3 = st.columns(3)
                with c1:
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
                # 고정 DB 재료
                db_items = []
                for fname in food_df["재료명"].tolist():
                    if fname in rd and rd[fname] not in ("", "0", 0):
                        db_items.append({"재료명": fname, "급여량(g)": rd[fname]})

                # 추가 입력 재료
                extra_items_admin = []
                for k, v in rd.items():
                    if k.startswith("추가재료") and v:
                        extra_items_admin.append({"재료명(직접입력)": v})

                if db_items:
                    st.markdown("**📋 고정 DB 재료**")
                    st.dataframe(_pd.DataFrame(db_items), use_container_width=True, hide_index=True)
                else:
                    st.caption("고정 DB 재료 없음")

                if extra_items_admin:
                    st.markdown("**➕ 추가 입력 재료** (영양 계산 미반영)")
                    st.dataframe(_pd.DataFrame(extra_items_admin), use_container_width=True, hide_index=True)

            total_kcal_r, total_stats_r, mass_bd_r, der_r = recalculate(rd)
            total_grams_r = sum(mass_bd_r.values())

            with st.expander("📊 영양 분석 결과", expanded=True):
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
                    for nutri, std in aafco_standards.items():
                        val = (total_stats_r[nutri] / total_kcal_r) * 1000
                        min_v, max_v = std["min"], std["max"]
                        if val < min_v: status = "❌ 부족"
                        elif max_v and val > max_v: status = "⚠️ 과잉"
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
        st.markdown("### 🔐 이메일 인증")
        st.caption(
            "반려견 식단 분석 이용권을 구매하신 분만 이용하실 수 있습니다. "
            "결제 후 신청 시 입력하신 이메일 주소를 입력해주세요."
        )
        auth_email_input = st.text_input(
            "이메일 주소",
            placeholder="example@email.com",
            key="auth_email_input"
        )
        auth_btn = st.button("✅ 인증하기", type="primary", use_container_width=True)
        if auth_btn:
            if not auth_email_input or "@" not in auth_email_input:
                st.error("올바른 이메일 주소를 입력해주세요.")
            else:
                ok, track = check_auth(auth_email_input)
                if ok:
                    st.session_state.authenticated = True
                    st.session_state.auth_email = auth_email_input.strip().lower()
                    st.session_state.auth_track = track
                    st.rerun()
                else:
                    st.error("❌ 이용권 구매 내역이 없는 이메일입니다. 결제 시 입력한 이메일 주소를 다시 확인해주세요.")
        st.stop()

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: 기본 정보
    # ═══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("📋 STEP 1. 반려견 기본 정보")

    col1, col2, col3 = st.columns(3)
    with col1:
        dog_name  = st.text_input("이름", placeholder="예: 토실이")
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
        rer = 70 * (dog_weight ** 0.75)
        der = rer * activity
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

        # 화식 보존율 테이블 (간소화)
        COOK_RETENTION = {
            "저온찜":    {"단백질": 0.975, "미네랄": 0.975, "비타민지용성": 0.875, "비타민B": 0.80, "오메가3": 0.875},
            "삶기":      {"단백질": 0.95,  "미네랄": 0.95,  "비타민지용성": 0.825, "비타민B": 0.65, "오메가3": 0.825},
            "볶기/구이": {"단백질": 0.95,  "미네랄": 0.95,  "비타민지용성": 0.80,  "비타민B": 0.725,"오메가3": 0.725},
            "압력조리":  {"단백질": 0.95,  "미네랄": 0.95,  "비타민지용성": 0.875, "비타민B": 0.775,"오메가3": 0.825},
        }
        COOK_YIELD = {"저온찜": 0.85, "삶기": 0.80, "볶기/구이": 0.75, "압력조리": 0.82}

        def get_rf_simple(nutri, method):
            r = COOK_RETENTION.get(method, COOK_RETENTION["삶기"])
            if nutri in ["단백질(g)", "지방(g)"]: return r["단백질"]
            if nutri in ["칼슘(mg)", "인(mg)", "철(mg)", "아연(mg)", "구리(mg)", "망간(mg)", "나트륨(mg)", "요오드(mcg)"]: return r["미네랄"]
            if nutri in ["비타민A(IU)", "비타민D(IU)", "비타민E(IU)"]: return r["비타민지용성"]
            return r["미네랄"]

        st.markdown("""
        <div style="background:#e8f5e9; border-left:4px solid #388e3c;
                    padding:0.8rem 1.2rem; border-radius:8px; margin-bottom:0.5rem; font-size:0.92rem;">
            <b>📋 재료 선택 안내 (화식)</b><br>
            화식에서는 <b>뼈고기를 익히면 안 됩니다</b> — 뼈고기 항목은 자동 제외됩니다.<br>
            칼슘은 아래 칼슘 보충제 항목에서 입력해주세요.
        </div>
        """, unsafe_allow_html=True)

        cooked_selected = st.multiselect("재료 선택 (화식 — 뼈고기 제외)", cooked_foods, key="cooked_selected")
        cooked_amounts = {}
        if cooked_selected:
            cols = st.columns(3)
            for i, f in enumerate(cooked_selected):
                with cols[i % 3]:
                    cooked_amounts[f] = st.number_input(f"{f} 생고기 기준 (g)", 0, 1000, 50, step=5, key=f"camt_{f}")

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
            st.session_state.submitted = False
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

        if not dog_name:
            st.error("반려견 이름을 입력해주세요.")
        elif not active_selected and not has_extra and not dry_diet:
            st.error("⚠️ 식단 재료를 입력해주세요. 생식·화식·혼합급여는 재료 선택 또는 직접 입력이 필수입니다. (건사료·동결건조만 선택하지 않아도 됩니다.)")
        else:
            # ── 영양 계산 ─────────────────────────────────────────────────────
            total_grams = sum(active_amounts.values())
            mass_breakdown = {"actual_bone": 0, "muscle_meat": 0, "organ": 0, "veggie": 0}
            total_stats = {k: 0.0 for k in aafco_standards}
            total_kcal = 0.0

            for f in active_selected:
                grams = active_amounts.get(f, 0)
                if grams <= 0:
                    continue
                row = food_df[food_df['재료명'] == f].iloc[0]
                ratio = grams / 100
                total_kcal += row['칼로리'] * ratio
                for nutri in aafco_standards:
                    col_name = nutri if nutri in row.index else nutri.split("(")[0]
                    if col_name in row:
                        raw_val = row[col_name] * ratio
                        if is_cooked and row['category'] != 'veggie':
                            raw_val *= get_rf_simple(nutri, cooking_method_input)
                        total_stats[nutri] += raw_val
                cat, b_pct = row['category'], row['bone_pct']
                if cat == 'bone':
                    mass_breakdown['actual_bone'] += grams * b_pct
                    mass_breakdown['muscle_meat'] += grams * (1 - b_pct)
                elif cat == 'meat':
                    mass_breakdown['muscle_meat'] += grams
                elif cat == 'organ':
                    mass_breakdown['organ'] += grams
                else:
                    mass_breakdown['veggie'] += grams

            # 화식 칼슘 보충제 추가
            if is_cooked:
                total_stats["칼슘(mg)"] += ca_supplement_total

            # ── AAFCO 판정 ────────────────────────────────────────────────────
            res_data = []
            aafco_summary = {}
            if total_kcal > 0:
                for nutri, std in aafco_standards.items():
                    val_1000 = (total_stats[nutri] / total_kcal) * 1000
                    min_v, max_v = std['min'], std['max']
                    if val_1000 < min_v:
                        status = "❌ 부족"
                    elif max_v and val_1000 > max_v:
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
            auth_em = st.session_state.get("auth_email", "unknown")
            today_str = str(date.today())
            ws = get_gsheet()
            sheet_saved = False
            if ws:
                row_dict = {
                    "신청일시": today_str,
                    "신청이메일": auth_em,
                    "인증경로": st.session_state.get("auth_track", ""),
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
                # 재료별 급여량 추가
                for f in selected:
                    row_dict[f] = amounts.get(f, 0)
                # 추가 입력 재료 저장
                for idx, item in enumerate(extra_items):
                    row_dict[f"추가재료{idx+1}"] = f"{item['재료명']} {item['용량']}{item['단위']}"
                # 검토 상태 / 관리자 메모 (초기값)
                row_dict["검토상태"] = "미검토"
                row_dict["관리자메모"] = ""

                sheet_saved = append_to_sheet(ws, row_dict)

                # 유료 이용권인 경우 사용완료 처리
                if sheet_saved and st.session_state.get("auth_track") == "paid":
                    mark_review_used(st.session_state.get("auth_email", ""))

            # 제출 완료 → session_state 업데이트 후 완료 화면으로 전환
            if sheet_saved:
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
            for f in selected:
                save_data[f] = amounts.get(f, 0)

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

