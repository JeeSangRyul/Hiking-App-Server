# 
#  main.py - 하이킹 앱 서버 1단계
#  "산 목록을 JSON으로 돌려주는" 가장 작은 FastAPI 서버
#  지금은 목업 데이터로 쓰고, 나중에 이 부분을 공공데이터 호출로 바꿀 예정

from typing import Optional, List
from fastapi import FastAPI
from pydantic import BaseModel

#  FastAPI 앱(서버) 객체 생성. title은 자동 문서(/docs)에 표시된다.
app = FastAPI(title="hiking API", version = "0.1")

# -- 데이터 모델 --
# Swift의 'struct ...: Codable' 와 똑같은 역할
# 필드 이름/타입을 적으면 FastAPI가 자동으로 JSON으로 바꿔준다.

class Course(BaseModel):
    name: str           # 산 이름
    courseName: str     # 코스 이름
    distanceKm: float   # 거리(km)
    difficulty: str     # 난이도
    safety: str         # 안전 단계
    latitude: float     # 위도
    longitude: float    # 경도

# ── 임시 목업 데이터 (나중에 공공데이터로 교체) ──────────────────
MOUNTAINS: List[Course] = [
    Course(name="북한산", courseName="백운대 코스", distanceKm=4.2,
           difficulty="보통", safety="safe",
           latitude=37.6597, longitude=126.9779),
    Course(name="도봉산", courseName="자운봉 코스", distanceKm=6.4,
           difficulty="어려움", safety="caution",
           latitude=37.6987, longitude=127.0144),
    Course(name="관악산", courseName="연주대 코스", distanceKm=5.1,
           difficulty="보통", safety="safe",
           latitude=37.4445, longitude=126.9636),
    Course(name="수락산", courseName="주봉 코스", distanceKm=7.2,
           difficulty="어려움", safety="warning",
           latitude=37.6779, longitude=127.0586),
]

# -- API 엔드포인트(주소) --
# @app.get("/주소") 아래 함수가, 그 주소로 요청이 오면 실행된다

@app.get("/")
def home():
    """
    서버가 살아있는지 확인용.
    """
    return {"message": "서버가 살아있어요 🏔️"}

@app.get("/mountains", response_model=List[Course])
def list_mountains(query: Optional[str] = None):
    """
    산/코스 목록을 돌려줍니다.
    /mountains            → 전체 목록
    /mountains?query=관악  → 이름에 '관악'이 포함된 것만
    """

    if query:
        return [m for m in MOUNTAINS if query in m.name or query in m.courseName]
    return MOUNTAINS