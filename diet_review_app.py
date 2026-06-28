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
    page_title="반려견 영양연구소 | 식단 검토 신청",
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

_logo = _logo_b64()

if _logo:
    st.markdown(f"""
<div style="
    display:flex; align-items:center; gap:2.5rem;
    padding: 1.8rem 2rem 1.6rem 2rem;
    border-bottom: 2px solid #e8e8e8;
    margin-bottom: 1.5rem;
">
    <img src="data:image/png;base64,{_logo}"
         style="height:220px; width:auto; object-fit:contain;" />
    <div>
        <h1 style="margin:0 0 0.4rem 0; font-size:2.2rem; font-weight:900;
                   color:#3a2a1a; letter-spacing:-1px; line-height:1.2;">
            반려견 영양연구소<br>식단 검토 신청서
        </h1>
        <p style="margin:0; font-size:1.05rem; color:#666; font-weight:500;">
            전문가가 직접 분석해 드립니다
        </p>
    </div>
</div>
""", unsafe_allow_html=True)
else:
    st.markdown("""
<div style="padding:1rem 0; border-bottom:2px solid #e8e8e8; margin-bottom:1.2rem;">
    <h1 style="margin:0; color:#3a2a1a; font-size:2.2rem; font-weight:900;">🐾 반려견 영양연구소 식단 검토 신청서</h1>
    <p style="color:#666; margin:0.3rem 0 0 0; font-size:1.05rem;">전문가가 직접 분석해 드립니다</p>
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
    """Drive 지정 폴더에 사진 업로드 → 공개 URL 반환. 실패 시 빈 문자열."""
    if uploaded_file is None:
        return ""
    try:
        folder_id = st.secrets["google_drive"]["folder_id"]
        service = get_drive_service()
        uploaded_file.seek(0)
        media = MediaIoBaseUpload(uploaded_file, mimetype="image/jpeg", resumable=False)
        meta = {"name": filename, "parents": [folder_id]}
        f = service.files().create(body=meta, media_body=media, fields="id").execute()
        fid = f.get("id", "")
        # 누구나 볼 수 있게 권한 설정
        service.permissions().create(
            fileId=fid,
            body={"role": "reader", "type": "anyone"}
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
    """헤더가 없으면 첫 행에 추가, 이후 데이터 추가"""
    try:
        existing = ws.get_all_values()
        if not existing:
            ws.append_row(list(row_dict.keys()))
        ws.append_row(list(row_dict.values()))
        return True
    except Exception as e:
        st.warning(f"구글 시트 저장 실패: {e}")
        return False

# ── AAFCO 기준 ─────────────────────────────────────────────────────────────
aafco_standards = {
    "단백질(g)": {"min": 45, "max": None},
    "지방(g)": {"min": 13.8, "max": None},
    "칼슘(mg)": {"min": 1250, "max": 6250},
    "인(mg)": {"min": 1000, "max": 4000},
    "철(mg)": {"min": 10, "max": None},
    "아연(mg)": {"min": 20, "max": None},
    "구리(mg)": {"min": 1.83, "max": None},
    "망간(mg)": {"min": 1.25, "max": None},
    "비타민A(IU)": {"min": 1250, "max": None},
    "비타민D(IU)": {"min": 125, "max": None},
    "비타민E(IU)": {"min": 12.5, "max": None},
    "나트륨(mg)": {"min": 200, "max": None},
}

# ── 재료 DB ────────────────────────────────────────────────────────────────
db_data = [
    {"재료명":"닭발 (뼈 60%)","category":"bone","bone_pct":0.60,"칼로리":215,"단백질":19.0,"지방":14.6,"칼슘":2500,"인":1500,"철":2.0,"아연":1.5,"구리":0.1,"망간":0.05,"비타민A":30,"비타민D":0,"비타민E":0,"나트륨":67},
    {"재료명":"닭목뼈 (뼈 36%)","category":"bone","bone_pct":0.36,"칼로리":154,"단백질":17.6,"지방":8.78,"칼슘":1500,"인":900,"철":2.06,"아연":2.68,"구리":0.1,"망간":0.03,"비타민A":146,"비타민D":0,"비타민E":0,"나트륨":81},
    {"재료명":"닭날개 (뼈 45%)","category":"bone","bone_pct":0.45,"칼로리":203,"단백질":18.0,"지방":14.0,"칼슘":1875,"인":1125,"철":1.0,"아연":1.0,"구리":0.1,"망간":0.02,"비타민A":40,"비타민D":0,"비타민E":0.3,"나트륨":70},
    {"재료명":"닭북채 (뼈 30%)","category":"bone","bone_pct":0.30,"칼로리":120,"단백질":18.0,"지방":4.0,"칼슘":1250,"인":750,"철":0.8,"아연":1.5,"구리":0.1,"망간":0.02,"비타민A":20,"비타민D":0,"비타민E":0.2,"나트륨":80},
    {"재료명":"전체 칠면조 (뼈 21%)","category":"bone","bone_pct":0.21,"칼로리":160,"단백질":20.0,"지방":8.0,"칼슘":875,"인":525,"철":1.5,"아연":2.0,"구리":0.1,"망간":0.02,"비타민A":50,"비타민D":0,"비타민E":0,"나트륨":60},
    {"재료명":"칠면조 목뼈 (뼈 42%)","category":"bone","bone_pct":0.42,"칼로리":225,"단백질":30.0,"지방":11.0,"칼슘":1750,"인":1050,"철":2.0,"아연":3.0,"구리":0.2,"망간":0.04,"비타민A":40,"비타민D":0,"비타민E":0,"나트륨":90},
    {"재료명":"칠면조 날개 (뼈 37%)","category":"bone","bone_pct":0.37,"칼로리":200,"단백질":18.0,"지방":13.0,"칼슘":1540,"인":925,"철":1.5,"아연":1.5,"구리":0.1,"망간":0.02,"비타민A":30,"비타민D":0,"비타민E":0,"나트륨":80},
    {"재료명":"전체 오리 (뼈 28%)","category":"bone","bone_pct":0.28,"칼로리":250,"단백질":15.0,"지방":20.0,"칼슘":1166,"인":700,"철":2.5,"아연":1.8,"구리":0.2,"망간":0.03,"비타민A":60,"비타민D":0,"비타민E":0.5,"나트륨":65},
    {"재료명":"오리 목뼈 (뼈 50%)","category":"bone","bone_pct":0.50,"칼로리":250,"단백질":18.0,"지방":18.0,"칼슘":2083,"인":1250,"철":2.8,"아연":2.0,"구리":0.2,"망간":0.04,"비타민A":50,"비타민D":0,"비타민E":0,"나트륨":85},
    {"재료명":"오리발 (뼈 60%)","category":"bone","bone_pct":0.60,"칼로리":253,"단백질":20.0,"지방":18.0,"칼슘":2500,"인":1500,"철":2.0,"아연":1.5,"구리":0.1,"망간":0.05,"비타민A":40,"비타민D":0,"비타민E":0,"나트륨":90},
    {"재료명":"소갈비뼈 (뼈 52%)","category":"bone","bone_pct":0.52,"칼로리":300,"단백질":18.0,"지방":25.0,"칼슘":2166,"인":1300,"철":3.0,"아연":4.5,"구리":0.1,"망간":0.02,"비타민A":10,"비타민D":2,"비타민E":0,"나트륨":70},
    {"재료명":"소꼬리 (뼈 55%)","category":"bone","bone_pct":0.55,"칼로리":262,"단백질":21.0,"지방":18.0,"칼슘":2290,"인":1375,"철":4.9,"아연":3.5,"구리":0.1,"망간":0.02,"비타민A":0,"비타민D":0,"비타민E":0,"나트륨":60},
    {"재료명":"양 갈비뼈 (뼈 27%)","category":"bone","bone_pct":0.27,"칼로리":355,"단백질":22.0,"지방":30.0,"칼슘":1125,"인":675,"철":2.0,"아연":4.0,"구리":0.1,"망간":0.02,"비타민A":0,"비타민D":1,"비타민E":0.1,"나트륨":76},
    {"재료명":"양 목뼈 (뼈 32%)","category":"bone","bone_pct":0.32,"칼로리":260,"단백질":20.0,"지방":20.0,"칼슘":1333,"인":800,"철":4.0,"아연":4.2,"구리":0.2,"망간":0.02,"비타민A":0,"비타민D":0,"비타민E":0,"나트륨":70},
    {"재료명":"전체 메츄리 (뼈 10%)","category":"bone","bone_pct":0.10,"칼로리":200,"단백질":20.0,"지방":12.0,"칼슘":416,"인":250,"철":4.0,"아연":2.5,"구리":0.5,"망간":0.02,"비타민A":50,"비타민D":10,"비타민E":1.0,"나트륨":50},
    {"재료명":"소간 (Beef Liver)","category":"organ","bone_pct":0,"칼로리":135,"단백질":20.4,"지방":3.63,"칼슘":5,"인":387,"철":4.9,"아연":4.0,"구리":9.76,"망간":0.31,"비타민A":16900,"비타민D":49,"비타민E":0.38,"나트륨":69},
    {"재료명":"소신장 (Beef Kidney)","category":"organ","bone_pct":0,"칼로리":97,"단백질":17.4,"지방":2.82,"칼슘":13,"인":257,"철":4.37,"아연":1.93,"구리":0.436,"망간":0.138,"비타민A":1166,"비타민D":49,"비타민E":0.29,"나트륨":182},
    {"재료명":"소비장/지라 (Beef Spleen)","category":"organ","bone_pct":0,"칼로리":105,"단백질":18.3,"지방":3.04,"칼슘":8,"인":249,"철":30.3,"아연":2.42,"구리":0.147,"망간":0.032,"비타민A":8,"비타민D":0,"비타민E":0.26,"나트륨":84},
    {"재료명":"소췌장 (Beef Pancreas)","category":"organ","bone_pct":0,"칼로리":233,"단백질":14.7,"지방":19.1,"칼슘":10,"인":234,"철":2.26,"아연":2.0,"구리":0.097,"망간":0.046,"비타민A":0,"비타민D":0,"비타민E":0.23,"나트륨":85},
    {"재료명":"닭간 (Chicken Liver)","category":"organ","bone_pct":0,"칼로리":119,"단백질":16.9,"지방":4.83,"칼슘":8,"인":297,"철":9.0,"아연":2.67,"구리":0.492,"망간":0.351,"비타민A":3296,"비타민D":55,"비타민E":1.1,"나트륨":71},
    {"재료명":"오리간 (Duck Liver)","category":"organ","bone_pct":0,"칼로리":136,"단백질":18.7,"지방":4.64,"칼슘":11,"인":263,"철":30.5,"아연":2.68,"구리":0.999,"망간":0.314,"비타민A":4970,"비타민D":80,"비타민E":1.51,"나트륨":144},
    {"재료명":"돼지간 (Pork Liver)","category":"organ","bone_pct":0,"칼로리":134,"단백질":20.9,"지방":3.65,"칼슘":8,"인":387,"철":17.9,"아연":4.02,"구리":0.796,"망간":0.355,"비타민A":6502,"비타민D":53,"비타민E":0.39,"나트륨":49},
    {"재료명":"돼지신장 (Pork Kidney)","category":"organ","bone_pct":0,"칼로리":100,"단백질":16.7,"지방":3.09,"칼슘":10,"인":244,"철":4.52,"아연":2.07,"구리":0.344,"망간":0.078,"비타민A":36,"비타민D":49,"비타민E":0.26,"나트륨":113},
    {"재료명":"그린트라이프 (Green Tripe)","category":"organ","bone_pct":0,"칼로리":85,"단백질":14.9,"지방":1.98,"칼슘":112,"인":159,"철":4.44,"아연":1.72,"구리":0.094,"망간":4.06,"비타민A":20,"비타민D":8,"비타민E":0.45,"나트륨":81},
    {"재료명":"닭가슴살 (Chicken Breast)","category":"meat","bone_pct":0,"칼로리":120,"단백질":22.5,"지방":2.62,"칼슘":5,"인":213,"철":0.37,"아연":0.68,"구리":0.037,"망간":0.011,"비타민A":30,"비타민D":0,"비타민E":0.56,"나트륨":45},
    {"재료명":"소고기 (Beef)","category":"meat","bone_pct":0,"칼로리":152,"단백질":20.8,"지방":7.0,"칼슘":10,"인":192,"철":2.33,"아연":4.97,"구리":0.075,"망간":0.01,"비타민A":14,"비타민D":3,"비타민E":0.17,"나트륨":66},
    {"재료명":"말고기 (Horse Meat)","category":"meat","bone_pct":0,"칼로리":133,"단백질":21.4,"지방":4.6,"칼슘":6,"인":221,"철":3.82,"아연":2.9,"구리":0.144,"망간":0.019,"비타민A":0,"비타민D":0,"비타민E":0,"나트륨":53},
    {"재료명":"사슴고기 (Venison)","category":"meat","bone_pct":0,"칼로리":116,"단백질":21.5,"지방":2.66,"칼슘":7,"인":201,"철":2.92,"아연":4.2,"구리":0.14,"망간":0.014,"비타민A":0,"비타민D":0,"비타민E":0,"나트륨":75},
    {"재료명":"정어리 (Sardine)","category":"meat","bone_pct":0,"칼로리":208,"단백질":24.6,"지방":11.4,"칼슘":382,"인":490,"철":2.92,"아연":1.4,"구리":0.186,"망간":0,"비타민A":30,"비타민D":4.8,"비타민E":1.38,"나트륨":307},
    {"재료명":"계란노른자 (Egg Yolk)","category":"meat","bone_pct":0,"칼로리":322,"단백질":15.9,"지방":26.5,"칼슘":129,"인":390,"철":2.73,"아연":2.3,"구리":0.077,"망간":0.31,"비타민A":1440,"비타민D":49,"비타민E":0.38,"나트륨":48},
    {"재료명":"소심장 (Beef Heart)","category":"meat","bone_pct":0,"칼로리":112,"단백질":18.5,"지방":3.4,"칼슘":4,"인":209,"철":4.38,"아연":1.51,"구리":0.373,"망간":0.034,"비타민A":34,"비타민D":6,"비타민E":1.22,"나트륨":86},
    {"재료명":"소폐 (Beef Lung)","category":"meat","bone_pct":0,"칼로리":92,"단백질":16.2,"지방":2.5,"칼슘":10,"인":224,"철":7.95,"아연":1.61,"구리":0.26,"망간":0.019,"비타민A":46,"비타민D":0,"비타민E":0,"나트륨":198},
    {"재료명":"소우신통 (Beef Penis)","category":"meat","bone_pct":0,"칼로리":120,"단백질":22.0,"지방":3.0,"칼슘":8,"인":180,"철":2.0,"아연":2.0,"구리":0.1,"망간":0.02,"비타민A":0,"비타민D":0,"비타민E":0.5,"나트륨":70},
    {"재료명":"닭심장 (Chicken Heart)","category":"meat","bone_pct":0,"칼로리":153,"단백질":15.6,"지방":9.33,"칼슘":11,"인":159,"철":5.95,"아연":6.49,"구리":0.301,"망간":0.073,"비타민A":34,"비타민D":0,"비타민E":1.0,"나트륨":65},
    {"재료명":"닭근위 (Chicken Gizzard)","category":"meat","bone_pct":0,"칼로리":94,"단백질":17.7,"지방":2.06,"칼슘":9,"인":148,"철":2.49,"아연":2.72,"구리":0.122,"망간":0.038,"비타민A":40,"비타민D":0,"비타민E":0.22,"나트륨":69},
    {"재료명":"돼지심장 (Pork Heart)","category":"meat","bone_pct":0,"칼로리":118,"단백질":17.7,"지방":4.67,"칼슘":6,"인":210,"철":3.37,"아연":2.63,"구리":0.382,"망간":0.031,"비타민A":0,"비타민D":49,"비타민E":0.83,"나트륨":57},
    {"재료명":"굴 (Oyster)","category":"veggie","bone_pct":0,"칼로리":68,"단백질":7.67,"지방":2.68,"칼슘":49,"인":151,"철":7.28,"아연":98.9,"구리":4.85,"망간":0.45,"비타민A":326,"비타민D":1,"비타민E":0.92,"나트륨":122},
    {"재료명":"블루베리 (Blueberry)","category":"veggie","bone_pct":0,"칼로리":57,"단백질":0.74,"지방":0.33,"칼슘":6,"인":12,"철":0.28,"아연":0.06,"구리":1.6,"망간":0.262,"비타민A":54,"비타민D":0,"비타민E":0.57,"나트륨":1},
    {"재료명":"브로콜리 퓨레 (Broccoli)","category":"veggie","bone_pct":0,"칼로리":34,"단백질":2.82,"지방":0.37,"칼슘":47,"인":66,"철":0.73,"아연":0.41,"구리":0.049,"망간":0.21,"비타민A":623,"비타민D":0,"비타민E":0.78,"나트륨":33},
    {"재료명":"토마토 퓨레 (Tomato)","category":"veggie","bone_pct":0,"칼로리":18,"단백질":0.88,"지방":0.2,"칼슘":10,"인":24,"철":0.27,"아연":0.17,"구리":0.059,"망간":0.114,"비타민A":833,"비타민D":0,"비타민E":0.54,"나트륨":5},
    {"재료명":"우엉 퓨레 (Burdock Root)","category":"veggie","bone_pct":0,"칼로리":72,"단백질":1.53,"지방":0.15,"칼슘":41,"인":51,"철":0.8,"아연":0.33,"구리":0.08,"망간":0.23,"비타민A":0,"비타민D":0,"비타민E":0.4,"나트륨":5},
    {"재료명":"청경채 퓨레 (Bok Choy)","category":"veggie","bone_pct":0,"칼로리":13,"단백질":1.5,"지방":0.2,"칼슘":105,"인":37,"철":0.8,"아연":0.19,"구리":0.021,"망간":0.159,"비타민A":4468,"비타민D":0,"비타민E":0.09,"나트륨":65},
    {"재료명":"단호박 퓨레 (Kabocha)","category":"veggie","bone_pct":0,"칼로리":34,"단백질":1.0,"지방":0.1,"칼슘":20,"인":30,"철":0.4,"아연":0.15,"구리":0.07,"망간":0.15,"비타민A":1370,"비타민D":0,"비타민E":0.3,"나트륨":3},
    {"재료명":"본브로스 소뼈 (Bone Broth)","category":"veggie","bone_pct":0,"칼로리":18,"단백질":4.0,"지방":0.0,"칼슘":5,"인":10,"철":0.2,"아연":0.1,"구리":0.02,"망간":0,"비타민A":0,"비타민D":0,"비타민E":0,"나트륨":20},
    {"재료명":"파프리카 퓨레 (Paprika)","category":"veggie","bone_pct":0,"칼로리":31,"단백질":1.0,"지방":0.3,"칼슘":7,"인":26,"철":0.43,"아연":0.25,"구리":0.017,"망간":0.11,"비타민A":3131,"비타민D":0,"비타민E":1.58,"나트륨":4},
    {"재료명":"샐러리 퓨레 (Celery)","category":"veggie","bone_pct":0,"칼로리":16,"단백질":0.69,"지방":0.17,"칼슘":40,"인":24,"철":0.2,"아연":0.13,"구리":0.04,"망간":0.1,"비타민A":449,"비타민D":0,"비타민E":0.27,"나트륨":80},
    {"재료명":"당근 퓨레 (Carrot)","category":"veggie","bone_pct":0,"칼로리":41,"단백질":0.93,"지방":0.24,"칼슘":33,"인":35,"철":0.3,"아연":0.24,"구리":0.045,"망간":0.143,"비타민A":16706,"비타민D":0,"비타민E":0.66,"나트륨":69},
    {"재료명":"칠면조 가슴살 (Turkey Breast)","category":"meat","bone_pct":0,"칼로리":111,"단백질":24.6,"지방":0.7,"칼슘":10,"인":206,"철":1.2,"아연":1.2,"구리":0.06,"망간":0.02,"비타민A":0,"비타민D":0,"비타민E":0.1,"나트륨":49},
    {"재료명":"오리 가슴살 (Duck Breast)","category":"meat","bone_pct":0,"칼로리":123,"단백질":19.8,"지방":4.3,"칼슘":3,"인":186,"철":4.5,"아연":1.5,"구리":0.26,"망간":0.02,"비타민A":60,"비타민D":0,"비타민E":0.5,"나트륨":57},
    {"재료명":"염소고기 (Goat)","category":"meat","bone_pct":0,"칼로리":109,"단백질":20.6,"지방":2.31,"칼슘":13,"인":180,"철":2.83,"아연":4.5,"구리":0.11,"망간":0.019,"비타민A":0,"비타민D":0,"비타민E":0.27,"나트륨":82},
    {"재료명":"양고기 (Lamb)","category":"meat","bone_pct":0,"칼로리":153,"단백질":20.3,"지방":7.64,"칼슘":16,"인":190,"철":1.88,"아연":3.95,"구리":0.117,"망간":0.023,"비타민A":0,"비타민D":0,"비타민E":0.14,"나트륨":72},
    {"재료명":"열빙어 (Smelt)","category":"meat","bone_pct":0,"칼로리":97,"단백질":17.6,"지방":2.42,"칼슘":60,"인":230,"철":0.9,"아연":1.7,"구리":0.14,"망간":0.7,"비타민A":15,"비타민D":32,"비타민E":0.5,"나트륨":60},
    {"재료명":"산양유 케피어 (Goat Kefir)","category":"meat","bone_pct":0,"칼로리":69,"단백질":3.5,"지방":4.0,"칼슘":134,"인":111,"철":0.05,"아연":0.3,"구리":0.05,"망간":0.02,"비타민A":185,"비타민D":4,"비타민E":0.1,"나트륨":50},
    {"재료명":"초록홍합 (Green-Lipped Mussel)","category":"meat","bone_pct":0,"칼로리":80,"단백질":11.9,"지방":2.24,"칼슘":33,"인":285,"철":3.36,"아연":2.67,"구리":0.15,"망간":3.4,"비타민A":40,"비타민D":4,"비타민E":0.9,"나트륨":185},
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
tab_user, tab_admin = st.tabs(["🐾 식단 검토 신청", "🔒 관리자"])

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
                st.markdown(f"**체형 소견:** {rd.get('체형소견','없음')}")
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
                    st.markdown(f"**활동량메모:** {rd.get('활동량메모','')}")

            with st.expander("📸 업로드 사진", expanded=True):
                pc1, pc2 = st.columns(2)
                with pc1:
                    dog_url = rd.get("반려견사진URL", "")
                    if dog_url:
                        st.markdown("**반려견 전신 사진**")
                        st.image(dog_url, use_container_width=True)
                    else:
                        st.caption("반려견 사진 없음")
                with pc2:
                    diet_url = rd.get("식단사진URL", "")
                    if diet_url:
                        st.markdown("**식단 사진**")
                        st.image(diet_url, use_container_width=True)
                    else:
                        st.caption("식단 사진 없음")

            with st.expander("🥩 식단 입력 내용", expanded=True):
                diet_items = []
                for fname in food_df["재료명"].tolist():
                    if fname in rd and rd[fname] not in ("", "0", 0):
                        diet_items.append({"재료명": fname, "급여량(g)": rd[fname]})
                for k, v in rd.items():
                    if k.startswith("추가재료") and v:
                        diet_items.append({"재료명": k, "급여량(g)": v})
                if diet_items:
                    st.dataframe(_pd.DataFrame(diet_items), use_container_width=True, hide_index=True)
                else:
                    st.caption("식단 데이터 없음")

            total_kcal_r, total_stats_r, mass_bd_r, der_r = recalculate(rd)
            total_grams_r = sum(mass_bd_r.values())

            with st.expander("📊 영양 분석 결과", expanded=True):
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
                if total_kcal_r > 0:
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
                    index=["미검토", "검토중", "완료"].index(rd.get("검토상태", "미검토")),
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
        st.markdown("### 이메일 인증")
        st.caption(
            "오디오레터 구독자 또는 식단 검토 이용권을 구매하신 분만 신청하실 수 있습니다. "
            "가입 시 사용한 이메일을 입력해주세요."
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
                    st.error("❌ 식단 검토 신청 내역이 없는 이메일입니다. 신청 시 입력한 이메일을 확인해주세요.")
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

photo_col1, photo_col2 = st.columns(2)

with photo_col1:
    st.markdown("**🐶 반려견 전신 사진** (체형 확인용)")
    st.caption("체형 평가를 위해 전신이 보이는 사진을 올려주세요. (최대 5MB)")
    dog_photo = st.file_uploader(
        "반려견의 전신 사진을 업로드해주세요",
        type=["jpg", "jpeg", "png", "webp"],
        key="dog_photo"
    )
    if dog_photo:
        if dog_photo.size > MAX_PHOTO_MB * 1024 * 1024:
            st.error(f"사진 용량이 {MAX_PHOTO_MB}MB를 초과합니다. 더 작은 사진을 올려주세요.")
            dog_photo = None
        else:
            st.image(dog_photo, caption=f"{dog_name or '반려견'} 전신 사진", use_container_width=True)
    body_condition = st.text_area(
        "체형에 대한 보호자 소견 (선택)",
        placeholder="예: 갈비뼈가 잘 안 만져져요 / 허리 라인이 없어요 / 배가 처져 보여요",
        height=100
    )

with photo_col2:
    st.markdown("**🥩 식단 사진** (검토용)")
    st.caption("오늘 급여한 식단 전체가 보이도록 찍어주세요. (최대 5MB)")
    diet_photo = st.file_uploader(
        "오늘의 식단 사진을 업로드해주세요",
        type=["jpg", "jpeg", "png", "webp"],
        key="diet_photo"
    )
    if diet_photo:
        if diet_photo.size > MAX_PHOTO_MB * 1024 * 1024:
            st.error(f"사진 용량이 {MAX_PHOTO_MB}MB를 초과합니다. 더 작은 사진을 올려주세요.")
            diet_photo = None
        else:
            st.image(diet_photo, caption="오늘의 식단", use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: 식이 이력
# ═══════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("🍽️ STEP 3. 식이 이력")

col1, col2 = st.columns(2)
with col1:
    current_diet  = st.radio("현재 식단", ["건사료", "화식", "생식", "혼합급여"])
    diet_duration = st.selectbox("현재 식단을 언제부터 먹었나요?",
        ["1주 미만", "1개월", "3개월", "6개월", "1년", "3년 이상"])
    feeding_freq  = st.radio("하루 급여 횟수",
        ["하루 1회", "하루 2회", "하루 3회", "자유급식"])
with col2:
    prev_diet = st.text_area("이전 식단 이력 (자유 입력)",
        placeholder="예: 건사료 8년 → 화식 6개월 → 생식 1년", height=120)

# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: 식단 입력
# ═══════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("🥩 STEP 4. 오늘의 식단 입력")
st.caption("재료를 선택하고 급여량(g)을 입력하세요.")

all_foods = food_df['재료명'].tolist()
selected = st.multiselect("재료 선택", all_foods)

amounts = {}
if selected:
    cols = st.columns(3)
    for i, f in enumerate(selected):
        with cols[i % 3]:
            amounts[f] = st.number_input(f"{f} (g)", 0, 1000, 50, step=5, key=f"amt_{f}")

# 추가 식단 입력 (DB에 없는 재료)
st.markdown("#### ➕ DB에 없는 재료 직접 입력 (선택)")
st.caption("목록에 없는 재료는 아래에 직접 입력해주세요. 전문가 검토 시 참고합니다.")

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

if st.button("＋ 행 추가", key="add_extra_row"):
    st.session_state.extra_rows += 1
    st.rerun()

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
        ["좋음", "무름", "설사", "변비"], horizontal=True)
    walk_time = st.radio("하루 산책 시간",
        ["거의 없음", "20분 이하", "30~60분", "1시간 이상"], horizontal=True)
    exercise = st.text_input("추가 운동 (종류·시간)", placeholder="예: 공놀이 10분, 수영 주 1회")

with col2:
    sleep_hours = st.radio("하루 수면 시간",
        ["10시간 미만", "10~14시간", "14~18시간", "18시간 이상"], horizontal=True)
    water_intake = st.radio("물 섭취량",
        ["거의 안 마심", "보통", "많이 마심"], horizontal=True)
    activity_memo = st.text_input("활동량 메모 (자유 입력)", placeholder="예: 실내에서만 생활, 계단 못 내려감")

# ═══════════════════════════════════════════════════════════════════════════
# STEP 7: 궁금한 점
# ═══════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("💬 STEP 7. 궁금한 점 / 특이사항")
question = st.text_area("자유 입력",
    placeholder="예: 최근 변이 묽어졌는데 식단 때문인지 궁금합니다.", height=100)

# ═══════════════════════════════════════════════════════════════════════════
# 제출 버튼
# ═══════════════════════════════════════════════════════════════════════════
st.divider()
submit = st.button("📨 식단 검토 신청하기", type="primary", use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# 계산 및 결과 출력
# ═══════════════════════════════════════════════════════════════════════════
if submit:
    if not dog_name:
        st.error("반려견 이름을 입력해주세요.")
    elif not selected:
        st.error("식단 재료를 하나 이상 선택해주세요.")
    else:
        # ── 영양 계산 ─────────────────────────────────────────────────────
        total_grams = sum(amounts.values())
        mass_breakdown = {"actual_bone": 0, "muscle_meat": 0, "organ": 0, "veggie": 0}
        total_stats = {k: 0.0 for k in aafco_standards}
        total_kcal = 0.0

        for f in selected:
            grams = amounts.get(f, 0)
            if grams <= 0:
                continue
            row = food_df[food_df['재료명'] == f].iloc[0]
            ratio = grams / 100
            total_kcal += row['칼로리'] * ratio
            for nutri in aafco_standards:
                col_name = nutri.split("(")[0]
                if col_name in row:
                    total_stats[nutri] += row[col_name] * ratio
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

        # ── 사진 → 구글 드라이브 업로드 ──────────────────────────────────
        dog_photo_url = ""
        diet_photo_url = ""
        auth_em = st.session_state.get("auth_email", "unknown")
        today_str = str(date.today())
        if dog_photo:
            dog_photo.seek(0)
            dog_photo_url = upload_to_drive(
                dog_photo,
                f"{today_str}_{dog_name}_반려견사진.jpg"
            )
        if diet_photo:
            diet_photo.seek(0)
            diet_photo_url = upload_to_drive(
                diet_photo,
                f"{today_str}_{dog_name}_식단사진.jpg"
            )

        # ── 구글 시트 저장 ────────────────────────────────────────────────
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
                "식단기간": diet_duration,
                "급여횟수": feeding_freq,
                "이전식단이력": prev_diet,
                "체형소견": body_condition,
                "질환": diseases,
                "약": medications,
                "영양제": supplements,
                "알레르기": allergies,
                "배변상태": stool_status,
                "산책시간": walk_time,
                "추가운동": exercise,
                "수면시간": sleep_hours,
                "물섭취": water_intake,
                "활동량메모": activity_memo,
                "궁금한점": question,
                "총칼로리(kcal)": f"{total_kcal:.0f}",
                "총그람(g)": total_grams,
                "반려견사진URL": dog_photo_url,
                "식단사진URL": diet_photo_url,
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

        # ── 결과 표시 ─────────────────────────────────────────────────────
        st.success(f"✅ **{dog_name}** 보호자님, 신청이 완료됐습니다!")
        if sheet_saved:
            st.info("📊 구글 시트에 자동 저장됐습니다.")
        elif ws is None:
            st.warning("⚠️ 구글 시트 연결 설정이 없습니다. (Secrets 미설정) CSV로만 저장됩니다.")

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
            "물섭취": water_intake, "활동량메모": activity_memo,
            "총칼로리": f"{total_kcal:.0f}", "목표칼로리": f"{der:.0f}",
            "반려견사진": "O" if dog_photo else "X",
            "식단사진": "O" if diet_photo else "X",
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
st.caption("🐾 반려견 영양연구소 | 식단 검토 신청 시스템")

