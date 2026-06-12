"""
catalog.py — 코스 카탈로그.

데이터 원천: data/courses.json — tools/build_catalog.py가 산림청 등산로 SHP + 산정보 API로 생성한 실데이터.
(과거에 있던 내장 목업 4코스 폴백은 제거됨 — id 규칙을 ETL 형식 "산명-산코드" 하나로 통일.
 courses.json이 없으면 COURSES는 빈 리스트로 시작하고, 검색 시 즉석 변환으로 채워진다.)

추가로 data/control.json(입산통제 — 시즌 공고 수동 반영)을 병합한다.
controlled=True인 코스는 main.py가 안전지수와 무관하게 danger + 사유를 내려준다.

  trail_builder(변환 엔진)이 예쁘게 조립해 준 코스 데이터를 서버 메모리에 안전하게 보관하고, 사용자가 검색할 때 꺼내주거나 실시간으로 통제 정보를 합쳐주는 역할
  trail_builder.py과 catalog.py는 catalog.py이 trail_builder.py에게 데이터르르 요청하는 입장
  REVIEW: trail_builder가 만든 코스를 메모리(COURSES)에 적재하고, 검색, 조회 요청에 꺼내주고, 없으면 즉석 변환으로 채우는 파일이다.
  NOTE: 서버 프로세스가 여러 개(워커)면 COURSES가 프로세스마다 따로 놀고,
        _persist()가 같은 파일을 동시에 쓰면 경합 위험 — 단일 워커 전제의 설계.
"""
import json
import os
from typing import List, Dict, Any

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


# [분석노트] ETL 코스에 rawWeather가 없을 때 끼워줄 기본 목업 날씨(기온 14도, 바람 5m/s, 강수 10%).
# 기상청 키가 있으면 어차피 실시간 값으로 덮이므로 "키 없는 개발 환경"용 기본값.
_DEFAULT_RAW_WEATHER = {"baseTempC": 14.0, "windMs": 5.0, "precipPct": 10}


def _load_real_courses() -> List[Dict[str, Any]]:
    """tools/build_catalog.py 산출물 로드. 없으면 빈 리스트."""
    # ① JSON 배열 [[lat,lon],...] → 파이썬 튜플 [(lat,lon),...] (내부 규약 통일)
    # ② setdefault로 빠진 키 메움: rawWeather/summitAltitudeM(800)/seedHue(0.33)
    # 실패(파일 없음/깨짐)는 조용히 빈 리스트 → 빈 카탈로그로 시작(경고 출력).
    # 사용처: 모듈 import 시 1회 (아래 COURSES = ...).
    # FIXME: summitAltitudeM이 사용된 이유는 즉석 변환된 산의 높이를 알 수 없기 때문에 높이가 없어서이다 -> 실제 데이터 기반으로 수정 필요
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
        c["routePreview"] = [tuple(p) for p in c.get("routePreview", [])]
        if "routeFull" in c:
            c["routeFull"] = [tuple(p) for p in c["routeFull"]]
        c.setdefault("rawWeather", dict(_DEFAULT_RAW_WEATHER))
        c.setdefault("summitAltitudeM", 800)
        c.setdefault("seedHue", 0.33)
        courses.append(c)
    return courses


def _apply_control(courses: List[Dict[str, Any]]) -> None:
    """data/control.json(입산통제 시즌 공고)을 코스에 병합.
    형식: [{ "courseId" 또는 "mountainName", "reason", "until"(선택, "YYYY-MM-DD") }]
    """
    # 입산 통제는 안전지수 계산과는 별개로 danger강등 및 통제 사유에 대해 설명

    # 동작: 매칭되는 코스에 controlled=True, controlReason="사유 (~날짜)" 주입.
    # until이 지난 항목은 무시(자동 해제). 산 단위(mountainName) 또는 코스 단위(courseId) 지정.

    # 사용처: 모듈 import 시 1회 + search_or_convert로 코스 추가될 때마다.
    # NOTE: 수동으로 입력?
    
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


# 모듈 import 시점에 실행되는 초기화 — 이 파일의 심장.
# 이후 모든 API 응답은 이 COURSES 리스트를 읽는다.
# courses.json이 없으면 빈 카탈로그로 시작 → 검색 즉석 변환(search_or_convert)으로 채워짐.
COURSES: List[Dict[str, Any]] = _load_real_courses()
if not COURSES:
    print("[catalog] 경고: data/courses.json 없음 — 빈 카탈로그로 시작 (tools/build_catalog.py 실행 필요)")
_apply_control(COURSES)


def find(course_id: str):
    # 사용처: main.py /courses/{course_id} 상세 엔드포인트.
    # NOTE: 고유한 id를 하나만 있도록 보장하는게 중요할 것 같다.
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
    # trail_builder의 역할이 json형태로 index화 된 애들을 가져오는 역할인데
    # 서버 시작 시가 아니라 '첫 검색 변환 때'(검색한 게 mountain_index.json에 없을떄)
    #    1회만 로드 — 시작 속도 보호. (서버 부팅 속도 보호)
    # 실패하면 빈 dict → 즉석 변환 기능만 조용히 꺼지고 서버는 계속 동작.

    # FIXME: 인덱스를 런타임때 굳이 할 필요없이 ETL때 mountain_index.json까지 확정 생성해버려서 
    #   효율적으로 접근하도록 수정한다.
    global _INDEX
    if _INDEX is None:
        try:
            import trail_builder
            _INDEX = trail_builder.build_index()
        except Exception:
            _INDEX = {}
    return _INDEX


def _persist():
    # 한번 변환된 코스들을 지속적으로 사용하기 위해서 courses.json을 덮어쓴다.
    # ① 튜플 → JSON 배열 역변환
    # ② controlled/controlReason 제거(통제의 원천은 control.json이므로
    #    저장하면 통제 해제 후에도 박제되는 버그가 생김 — 그래서 빼고 저장 후 _apply_control 재실행).
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
    # 흐름: 검색어 ⊂ 산명 매칭(이미 변환된 산 제외, 최대 3개) → trail_builder.build_courses_for_name
    #       → COURSES에 append → _persist(파일 저장) → _apply_control(통제 재병합)

    # 호출처: main.py /mountains — 일반 검색 0건일 때만, asyncio.to_thread로 (CPU 작업이라 첫 검색 1~3초).
    # FIXME: 이 부분을 아예 json을 저장해버려서 조회하는 식으로 하는 게 더 효율적인 것 같다. 산은 정적데이터이기 때문이다.
    
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
