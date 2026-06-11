"""
catalog.py — 코스 카탈로그.

우선순위:
  ① data/courses.json     — tools/build_catalog.py가 산림청 등산로 SHP + 산정보 API로 생성한 실데이터
  ② 내장 목업 4개 코스     — 실데이터 없을 때의 폴백 (앱 MountainCatalog.swift와 동일)

추가로 data/control.json(입산통제 — 시즌 공고 수동 반영)을 병합한다.
controlled=True인 코스는 main.py가 안전지수와 무관하게 danger + 사유를 내려준다.

[분석노트] 이 파일의 위치: ETL 산출물(courses.json)을 서버 메모리에 올리고 관리하는 '창고지기'.
  trail_builder(변환) ──courses.json──▶ 이 파일(적재·검색·즉석변환) ──COURSES──▶ main.py(API 응답)
  핵심 상태는 모듈 전역 리스트 COURSES 하나. import 시점에 1회 적재되고,
  검색 시 즉석 변환(search_or_convert)으로 런타임에 늘어날 수 있음.
  주의: 서버 프로세스가 여러 개(워커)면 COURSES가 프로세스마다 따로 놀고,
        _persist()가 같은 파일을 동시에 쓰면 경합 위험 — 단일 워커 전제의 설계.
"""
import json
import os
from typing import List, Dict, Any

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _slug(name: str, course: str) -> str:
    # [분석노트] 내장 목업 4코스의 id 생성기. "북한산"+"백운대 코스" → "bukhansan-baegundae".
    # 이 id(slug)가 전 시스템의 조인 키: 앱 HikingCourse.serverId = DB hikes.course_id = favorites.course_id.
    # ETL 코스는 다른 규칙("산명-산코드", build_course에서 생성) — 두 형식이 공존함에 주의.
    # 사용처: 아래 COURSES 내장 4코스 정의에서만.
    table = {
        "북한산": "bukhansan", "도봉산": "dobongsan",
        "관악산": "gwanaksan", "수락산": "suraksan",
        "백운대 코스": "baegundae", "자운봉 코스": "jaunbong",
        "연주대 코스": "yeonjudae", "주봉 코스": "jubong",
    }
    return f"{table.get(name, name)}-{table.get(course, course)}"


# 핵심 카탈로그. summitAltitudeM은 정상 고도 보정(F-B3)에 사용.
# [분석노트] 내장 목업 4코스(북한산·도봉산·관악산·수락산). 앱 MountainCatalog.swift의 4코스와 짝.
# courses.json(실데이터)이 있으면 아래 134행에서 통째로 교체되므로, 실제 운영에선 쓰이지 않는 폴백.
# rawWeather: 기상청 키가 없을 때 interpret_weather()에 들어갈 가짜 원시 날씨.
# ⚠️ 여기 dict의 키 구성이 ETL 코스와 미묘하게 다름: routeFull/riskNotes/mountainCode 없음.
COURSES: List[Dict[str, Any]] = [
    {
        "id": _slug("북한산", "백운대 코스"),
        "mountainName": "북한산", "courseName": "백운대 코스",
        "distanceKm": 4.2, "ascentMinutes": 95, "descentMinutes": 65,
        "cumulativeGainM": 540, "difficulty": "보통", "seedHue": 0.33,
        "latitude": 37.6597, "longitude": 126.9779, "summitAltitudeM": 836,
        "rawWeather": {"baseTempC": 14.0, "windMs": 9.0, "precipPct": 10},
        "routePreview": [
            (37.6597, 126.9779), (37.6608, 126.9818),
            (37.6601, 126.9855), (37.6586, 126.9886),
        ],
    },
    {
        "id": _slug("도봉산", "자운봉 코스"),
        "mountainName": "도봉산", "courseName": "자운봉 코스",
        "distanceKm": 6.4, "ascentMinutes": 130, "descentMinutes": 80,
        "cumulativeGainM": 720, "difficulty": "어려움", "seedHue": 0.28,
        "latitude": 37.6987, "longitude": 127.0144, "summitAltitudeM": 740,
        "rawWeather": {"baseTempC": 12.0, "windMs": 6.5, "precipPct": 20},
        "routePreview": [
            (37.6890, 127.0150), (37.6940, 127.0148), (37.6987, 127.0144),
        ],
    },
    {
        "id": _slug("관악산", "연주대 코스"),
        "mountainName": "관악산", "courseName": "연주대 코스",
        "distanceKm": 5.1, "ascentMinutes": 100, "descentMinutes": 65,
        "cumulativeGainM": 610, "difficulty": "보통", "seedHue": 0.42,
        "latitude": 37.4445, "longitude": 126.9636, "summitAltitudeM": 632,
        "rawWeather": {"baseTempC": 18.0, "windMs": 2.5, "precipPct": 0},
        "routePreview": [
            (37.4660, 126.9573), (37.4550, 126.9600), (37.4445, 126.9636),
        ],
    },
    {
        "id": _slug("수락산", "주봉 코스"),
        "mountainName": "수락산", "courseName": "주봉 코스",
        "distanceKm": 7.2, "ascentMinutes": 140, "descentMinutes": 90,
        "cumulativeGainM": 690, "difficulty": "어려움", "seedHue": 0.30,
        "latitude": 37.6779, "longitude": 127.0586, "summitAltitudeM": 638,
        "rawWeather": {"baseTempC": 8.0, "windMs": 11.5, "precipPct": 10},
        "routePreview": [
            (37.6700, 127.0550), (37.6740, 127.0570), (37.6779, 127.0586),
        ],
    },
]


# 위 4개는 내장 목업. 아래에서 실데이터(JSON)가 있으면 교체된다.
_BUILTIN = COURSES

# [분석노트] ETL 코스에 rawWeather가 없을 때 끼워줄 기본 목업 날씨(기온 14도, 바람 5m/s, 강수 10%).
# 기상청 키가 있으면 어차피 실시간 값으로 덮이므로 "키 없는 개발 환경"용 기본값.
_DEFAULT_RAW_WEATHER = {"baseTempC": 14.0, "windMs": 5.0, "precipPct": 10}


def _load_real_courses() -> List[Dict[str, Any]]:
    """tools/build_catalog.py 산출물 로드. 없으면 빈 리스트."""
    # [분석노트] 역할: courses.json(ETL 산출물)을 읽어 서버가 쓸 형태로 손질.
    # ① JSON 배열 [[lat,lon],...] → 파이썬 튜플 [(lat,lon),...] (내부 규약 통일)
    # ② setdefault로 빠진 키 메움: rawWeather/summitAltitudeM(800)/seedHue(0.33)
    # 실패(파일 없음/깨짐)는 조용히 빈 리스트 → 호출부(134행)가 내장 목업으로 폴백.
    # 사용처: 모듈 import 시 1회 (아래 _real = ...).
    path = os.path.join(_DATA_DIR, "courses.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return []
    courses = []
    for c in loaded:
        # routePreview/routeFull: [[lat, lon], ...] → [(lat, lon), ...]
        c["routePreview"] = [tuple(p) for p in c.get("routePreview", [])]
        if "routeFull" in c:
            c["routeFull"] = [tuple(p) for p in c["routeFull"]]
        # 기상청 키가 없을 때 쓸 목업 날씨 기본값
        c.setdefault("rawWeather", dict(_DEFAULT_RAW_WEATHER))
        c.setdefault("summitAltitudeM", 800)
        c.setdefault("seedHue", 0.33)
        courses.append(c)
    return courses


def _apply_control(courses: List[Dict[str, Any]]) -> None:
    """data/control.json(입산통제 시즌 공고)을 코스에 병합.
    형식: [{ "courseId" 또는 "mountainName", "reason", "until"(선택, "YYYY-MM-DD") }]
    """
    # [분석노트] 왜 필요한가: 산불조심기간 등 입산통제는 '사람이 공고를 보고' control.json에
    # 수동으로 적는 운영 데이터. 코스 자체(ETL)와 분리해 코스 재생성 없이 통제만 갱신 가능.
    # 동작: 매칭되는 코스에 controlled=True, controlReason="사유 (~날짜)" 주입.
    # until이 지난 항목은 무시(자동 해제). 산 단위(mountainName) 또는 코스 단위(courseId) 지정.
    # 효과: main.py _apply_control_override()가 안전지수와 무관하게 danger로 강등.
    # 사용처: 모듈 import 시 1회 + search_or_convert로 코스 추가될 때마다.
    # ⚠️ 원천 데이터의 PMNTN_CLS_(폐쇄여부)는 안 쓰고 100% 수동 운영 — 자동화 개선 후보.
    path = os.path.join(_DATA_DIR, "control.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except Exception:
        return
    from datetime import date
    today = date.today().isoformat()
    for e in entries:
        until = e.get("until")
        if until and until < today:
            continue   # 통제 기간 종료 → 무시
        for c in courses:
            if e.get("courseId") == c["id"] or e.get("mountainName") == c["mountainName"]:
                c["controlled"] = True
                reason = e.get("reason", "입산통제 구간")
                c["controlReason"] = f"{reason}" + (f" (~{until})" if until else "")


# [분석노트] 모듈 import 시점에 실행되는 초기화 3줄 — 이 파일의 심장.
# 실데이터가 1개라도 있으면 내장 4코스는 완전히 버려짐(합쳐지는 게 아님!).
# 이후 모든 API 응답은 이 COURSES 리스트를 읽는다.
_real = _load_real_courses()
COURSES = _real if _real else _BUILTIN
_apply_control(COURSES)


def find(course_id: str):
    # [분석노트] slug로 코스 1개 조회. 사용처: main.py /courses/{course_id} 상세 엔드포인트.
    # 선형 탐색 O(n) — 코스가 수천 개로 늘면 dict 인덱스로 바꿀 후보.
    for c in COURSES:
        if c["id"] == course_id:
            return c
    return None


# ─────────────────────────────────────────────────────────────
# 검색 시 즉석 변환 (전국 2,200여 산 커버)
# 검색어가 변환된 코스에 없으면, 등산로 원본(zip 인덱스)에서 찾아
# 그 자리에서 대표 코스를 생성하고 courses.json에 캐시한다.
# ─────────────────────────────────────────────────────────────
_INDEX = None


def _index():
    # [분석노트] trail_builder.build_index()(산코드→산명/zip 전화번호부)를 지연 로딩.
    # 서버 시작 시가 아니라 '첫 검색 변환 때' 1회만 로드 — 시작 속도 보호.
    # 실패하면 빈 dict → 즉석 변환 기능만 조용히 꺼지고 서버는 계속 동작.
    global _INDEX
    if _INDEX is None:
        try:
            import trail_builder
            _INDEX = trail_builder.build_index()
        except Exception:
            _INDEX = {}
    return _INDEX


def _persist():
    """변환 누적분을 courses.json에 저장(서버 재시작 후에도 유지)."""
    # [분석노트] 즉석 변환된 코스를 다음 부팅에도 쓰려고 COURSES 전체를 courses.json에 되쓰기.
    # ① 튜플 → JSON 배열 역변환 ② controlled/controlReason 제거(통제의 원천은 control.json이므로
    #    저장하면 통제 해제 후에도 박제되는 버그가 생김 — 그래서 빼고 저장 후 _apply_control 재실행).
    # ⚠️ 모든 예외를 삼킴(pass) — 저장 실패해도 모름. 디스크 권한 문제 시 조용히 휘발.
    # 사용처: search_or_convert()에서 코스 추가 성공 시.
    try:
        path = os.path.join(_DATA_DIR, "courses.json")
        serializable = []
        for c in COURSES:
            c2 = dict(c)
            c2["routePreview"] = [list(p) for p in c2.get("routePreview", [])]
            if "routeFull" in c2:
                c2["routeFull"] = [list(p) for p in c2["routeFull"]]
            c2.pop("controlled", None)       # 통제는 control.json이 원천
            c2.pop("controlReason", None)
            serializable.append(c2)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception:
        pass


def search_or_convert(query: str, limit_names: int = 3):
    """검색어와 일치하는 산이 아직 변환 전이면 즉석 변환해 COURSES에 추가.
    반환: 새로 추가된 코스 수. (호출부는 그 후 평소처럼 COURSES를 검색하면 됨)"""
    # [분석노트] '전국 2,932산 검색'이 되는 비결. 미리 다 변환하지 않고(시간·용량) 검색될 때만 변환.
    # 흐름: 검색어 ⊂ 산명 매칭(이미 변환된 산 제외, 최대 3개) → trail_builder.build_courses_for_name
    #       → COURSES에 append → _persist(파일 저장) → _apply_control(통제 재병합)
    # 호출처: main.py /mountains — 일반 검색 0건일 때만, asyncio.to_thread로 (CPU 작업이라 첫 검색 1~3초).
    # ⚠️ have(이미 보유한 산명) 비교가 "이름(" 앞부분 기준이라 동명이산 라벨과 미묘하게 어긋날 수 있음.
    # ⚠️ 변환 실패는 except: continue로 무시 — 어떤 산이 왜 안 나오는지 로그가 없음.
    q = query.strip()
    if len(q) < 2:
        return 0
    have = {c["mountainName"].split("(")[0] for c in COURSES}
    matches = sorted({v["name"] for v in _index().values()
                      if q in v["name"] and v["name"] not in have})[:limit_names]
    if not matches:
        return 0

    import trail_builder
    added = 0
    for name in matches:
        try:
            for course in trail_builder.build_courses_for_name(name, _index()):
                course["routePreview"] = [tuple(p) for p in course["routePreview"]]
                course["routeFull"] = [tuple(p) for p in course["routeFull"]]
                course.setdefault("rawWeather", dict(_DEFAULT_RAW_WEATHER))
                COURSES.append(course)
                added += 1
        except Exception:
            continue
    if added:
        _persist()
        _apply_control(COURSES)
    return added
