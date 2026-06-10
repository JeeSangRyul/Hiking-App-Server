"""
catalog.py — 코스 카탈로그.

우선순위:
  ① data/courses.json     — tools/build_catalog.py가 산림청 등산로 SHP + 산정보 API로 생성한 실데이터
  ② 내장 목업 4개 코스     — 실데이터 없을 때의 폴백 (앱 MountainCatalog.swift와 동일)

추가로 data/control.json(입산통제 — 시즌 공고 수동 반영)을 병합한다.
controlled=True인 코스는 main.py가 안전지수와 무관하게 danger + 사유를 내려준다.
"""
import json
import os
from typing import List, Dict, Any

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _slug(name: str, course: str) -> str:
    table = {
        "북한산": "bukhansan", "도봉산": "dobongsan",
        "관악산": "gwanaksan", "수락산": "suraksan",
        "백운대 코스": "baegundae", "자운봉 코스": "jaunbong",
        "연주대 코스": "yeonjudae", "주봉 코스": "jubong",
    }
    return f"{table.get(name, name)}-{table.get(course, course)}"


# 핵심 카탈로그. summitAltitudeM은 정상 고도 보정(F-B3)에 사용.
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

_DEFAULT_RAW_WEATHER = {"baseTempC": 14.0, "windMs": 5.0, "precipPct": 10}


def _load_real_courses() -> List[Dict[str, Any]]:
    """tools/build_catalog.py 산출물 로드. 없으면 빈 리스트."""
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


_real = _load_real_courses()
COURSES = _real if _real else _BUILTIN
_apply_control(COURSES)


def find(course_id: str):
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
