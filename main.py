#
#  main.py — 산담(SanDam) 서버
#  공공데이터(기상청/KASI/산림청)는 "연결돼 있다고 가정"하고, 그 자리를 목업 + 연산으로 대체한다.

#  실행:  uvicorn main:app --host 0.0.0.0 --port 8000     (문서: /docs)
# catalog가 들고 있는 코스에 날씨/ 일몰/ 안전지수라는 '오늘의 상태'를 요청 시점에 계산해 얹어 JSON으로 파는 곳

# ① 설정(49~53)      config.json 로드 — 모든 숫자의 원천
# ② 일출몰(59~100)   sun_event — NOAA 순수 연산 (KASI 폴백)
# ③ 날씨(109~187)    interpret_weather — 수치→사람말 번역 + 고도보정
# ④ 안전(193~236)    compute_safety — 신호등 결정
# ⑤ DTO(242~361)     context_for + build_summary/detail — 응답 조립
# ⑥ 엔드포인트(367~) 공개 5개(코스·날씨·일몰·설정) + JWT 3개(기록·즐겨찾기)

#   TODO: 실데이터로 바꿀 때는 catalog.py(코스), interpret_weather(기상청), sun_event(KASI)만 교체하면 된다.
#   TODO: 파일이 더 커지면 계층 분리 (iOS의 MVVM 분리와 같은 원리):
#   routers/courses.py·hikes.py·favorites.py (APIRouter) + services/safety.py·weather.py·sun.py
#   + schemas.py(Pydantic 모델). main.py는 앱 생성+라우터 등록만 남김.
#   kma_weather/kasi_sunset/mtn_weather/catalog/auth/db는 이미 분리 양호 — main만 혼재 상태.

import json
import math
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

# .env 파일의 키들을 환경변수로 로드 (다른 모듈 import 전에 실행되어야 함!)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # python-dotenv 미설치 시 시스템 환경변수만 사용

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel

import catalog
from geo import haversine_km   # 공용 거리 계산 — 사용처: build_summary, /mountains 정렬, /weather 최근접
import kma_weather
import kasi_sunset      # 천문연 출몰시각 실연동 (키 없으면 NOAA 자체 연산 폴백)
import mtn_weather      # 산악기상관측망 실측 (키/관측소 없으면 고도보정 추정 유지)
import auth
import db

app = FastAPI(title="SanDam API", version="1.0")

# REVIEW: ① 설정

# 안전지수 가중치·임계값·날씨 해석 규칙표·하산 버퍼를 코드 밖(config.json)에 둔 이유:
# 코드 수정 없이 숫자만 바꿔 튜닝. /safety/config로 앱에도 통째로 내려줘서
# 서버·앱(SanDamConfig)이 같은 공식을 공유.
#  사용처: interpret_weather, compute_safety.
# TODO: 계산식은 기획에 따라서 수정필요

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG: Dict[str, Any] = json.load(f)

KST = timezone(timedelta(hours=9))   # 한국 산 기준


# REVIEW: ② 일출몰
# TODO: 데이터가 어떻게 들어오는지 확인 필수
# ─────────────────────────────────────────────────────────────
# 일출/일몰 (KASI 대체) —> NOAA 알고리즘 순수 연산 -> 고정값
# ─────────────────────────────────────────────────────────────
def _sind(d): return math.sin(math.radians(d))
def _cosd(d): return math.cos(math.radians(d))
def _tand(d): return math.tan(math.radians(d))
def _asind(x): return math.degrees(math.asin(x))
def _acosd(x): return math.degrees(math.acos(x))
def _atand(x): return math.degrees(math.atan(x))


def sun_event(lat: float, lon: float, date: datetime, is_sunrise: bool, tz_offset: float = 9.0):
    """주어진 좌표·날짜의 (epoch_utc, 'HH:mm' 로컬) 반환. 백야/극야면 None."""
    # NOTE: 공공데이터가 아닌 계산한 로직 => 특수 상황에 사용함
    # 왜 필요한가: KASI 키가 없거나 호출 실패 시의 폴백 (일몰은 이 앱의 생명줄이라 무조건 값이 있어야 함).
    # 앱 SunsetCalculator와 동일 로직 — 서버·앱 계산 결과 일치 보장.
    # 사용처: _course_sunset()(코스 일몰), /sunset 엔드포인트. 위 _sind 등 6개는 도 단위 삼각함수 단축어.
    N = date.timetuple().tm_yday
    zenith = 90.833
    lng_hour = lon / 15.0
    t = N + ((6 - lng_hour) / 24) if is_sunrise else N + ((18 - lng_hour) / 24)
    M = (0.9856 * t) - 3.289
    L = (M + 1.916 * _sind(M) + 0.020 * _sind(2 * M) + 282.634) % 360
    RA = _atand(0.91764 * _tand(L)) % 360
    L_q = math.floor(L / 90) * 90
    RA_q = math.floor(RA / 90) * 90
    RA = (RA + (L_q - RA_q)) / 15.0
    sin_dec = 0.39782 * _sind(L)
    cos_dec = _cosd(_asind(sin_dec))
    cos_h = (_cosd(zenith) - (sin_dec * _sind(lat))) / (cos_dec * _cosd(lat))
    if cos_h > 1 or cos_h < -1:
        return None
    H = (360 - _acosd(cos_h)) if is_sunrise else _acosd(cos_h)
    H /= 15.0
    T = H + RA - (0.06571 * t) - 6.622
    UT = (T - lng_hour) % 24

    base = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    local = (UT + tz_offset) % 24
    # epoch은 "요청한 로컬 날짜"의 이벤트 시각 기준: 로컬 시각 − 시간대 오프셋.
    # (UT를 그대로 더하면 일출처럼 로컬 자정을 넘는 경우 epoch이 하루 밀린다)
    event_utc = base + timedelta(hours=local) - timedelta(hours=tz_offset)
    epoch = event_utc.timestamp()
    text = f"{int(local):02d}:{int((local - int(local)) * 60):02d}"
    return epoch, text


# REVIEW: ③ 날씨
# ─────────────────────────────────────────────────────────────
# 해석형 날씨 (F-B3) — 원시 수치 → 행동지침 문구 + 정상 고도 보정
# ─────────────────────────────────────────────────────────────
# config의 규칙표에서 값에 맞는 행 선택 (예: 풍속≤3 "바람 약함", ≤8 "강풍"...)하는 함수.
# _cold_penalty: 기온별 감점 조회.
#  _symbol: 날씨 → SF Symbol 아이콘명 (아이콘 선택권이 서버에 있음).
# 셋 다 interpret_weather 전용 헬퍼.

def _pick(rules: List[Dict], value: float, key: str):
    for r in rules:
        if value <= r[key]:
            return r
    return rules[-1]


def _cold_penalty(temp_c: float) -> int:
    for r in CONFIG["coldRules"]:
        if temp_c >= r["minC"]:
            return r["penalty"]
    return CONFIG["coldRules"][-1]["penalty"]

# TODO: 아이콘 수정
def _symbol(precip: int, summit_wind: float, summit_temp: float) -> str:
    if precip >= 50:
        return "cloud.rain.fill"
    if precip >= 20:
        return "cloud.sun.fill"
    if summit_wind >= 8:
        return "wind"
    if summit_temp <= 0:
        return "cloud.snow.fill"
    return "sun.max.fill"


def interpret_weather(raw: Dict[str, Any], summit_alt: float,
                      measured: Optional[Dict[str, Any]] = None):
    # [분석노트] 구역③의 본체. 원시 수치(기온·풍속·강수%)를 사람 말("정상 강풍 예상")로 번역 +
    # 날씨점수(0~100) 산출. 핵심 아이디어 = 고도보정: 평지 예보를 100m당 -0.65℃로 정상 날씨로 변환,
    # 사용처: build_summary, build_detail, /weather. raw 출처: 기상청 실시간 또는 코스 rawWeather 목업.

    # TODO: 실데이터 확인 필수
    # TODO: 데이터 계산 로직 기획 필요
    # TODO: 산악 실측 데이터 조사 필요
    #   └ 조사 결과(2026-06-12): 산악 실측 = 국립산림과학원 산악기상관측망(전국 460여 관측소,
    #     기온·습도·풍속·강수량 등 7요소, 1분 단위 관측). 공공데이터포털 API(1400377/mtweather)로
    #     공개되며 mtn_weather.py가 이미 이 API의 구현체(관측소 10km 이내일 때만 measured로 들어옴).
    #     공식: https://mtweather.nifos.go.kr (100대 명산 서비스) · API 명세: know.nifos.go.kr
    #     참고 구현: github.com/dearsyjang/ClimbingBear (산림청 산정보 + 날씨 결합 등산 앱)

    ac = CONFIG["altitudeCorrection"]
    base_t = float(raw["baseTempC"])
    wind = float(raw["windMs"])
    precip = int(raw["precipPct"])

    summit_t = base_t - ac["tempDropCPer100m"] * (summit_alt / 100.0)
    summit_wind = wind * ac["windGainFactor"]

    source = None
    if measured:
        if measured.get("tempC") is not None:
            summit_t = float(measured["tempC"])
        if measured.get("windMs") is not None:
            summit_wind = float(measured["windMs"])
        source = f"{measured.get('obsname', '산악관측소')} 실측"

    wind_rule = _pick(CONFIG["windRules"], summit_wind, "maxMs")
    precip_rule = _pick(CONFIG["precipRules"], precip, "maxPct")
    cold_pen = _cold_penalty(summit_t)

    summary = wind_rule["summary"] or precip_rule["summary"] or "대체로 맑음"
    advice = precip_rule["advice"] or wind_rule["advice"] or "산행 적합"

    score = max(0, 100 - wind_rule["penalty"] - precip_rule["penalty"] - cold_pen)

    now_kst = datetime.now(KST)
    weather = {
        "summary": summary,
        "advice": advice,
        "symbol": _symbol(precip, summit_wind, summit_t),
        "highC": round(base_t + 4),
        "lowC": round(summit_t - 4),
        "precipitation": precip,
        "windAdvice": wind_rule["advice"],
        "raw": {
            "baseTempC": round(base_t, 1),
            "windMs": round(wind, 1),
            "precipPct": precip,
            "summitTempC": round(summit_t, 1),
            "summitWindMs": round(summit_wind, 1),
        },
        "asOfText": f"오늘 {now_kst:%H:%M} 기준" + (f" · {source}" if source else ""),
    }
    return weather, score


# REVIEW: ④ 안전

# ─────────────────────────────────────────────────────────────
# 안전지수 (F-B2) — weather/sunsetMargin/difficulty 가중합
# ─────────────────────────────────────────────────────────────
def _sunset_margin_score(sunset_epoch: float, total_minutes: int, now: float) -> int:
    # "지금 출발하면 일몰까지 여유가 몇 분?"을 점수화.
    # 여유 = (일몰까지 남은 분) - (왕복 소요) - (안전버퍼). config sunsetMarginScore 규칙표로 점수 변환.
    # 사용처: compute_safety()만. 

    # TODO: 해가 떨어지고 일출을 보기 위해 산행을 하는 사람들이라면 어떻게 해줘야 할지 기획필요
    buffer = CONFIG["safetyBufferMinutes"]
    margin_min = (sunset_epoch - now) / 60.0 - total_minutes - buffer
    for r in CONFIG["sunsetMarginScore"]:
        if margin_min >= r["minMinutes"]:
            return r["score"]
    return CONFIG["sunsetMarginScore"][-1]["score"]


def compute_safety(course: Dict[str, Any], weather_score: float,
                   weather_summary: str, sunset_epoch: float, now: float):
    # [분석노트] 앱의 '신호등'이 결정되는 곳. 날씨×일몰여유×난이도 가중합(config 가중치) → 0~100점
    # → 4단계: safe≥67 / caution 34~66 / warning 16~33 / danger≤15.
    # TODO: 안전한지 점수를 내는 계산식에 대해 기획필요

    # reason: factors 중 최저 점수 항목을 골라 "왜 이 등급인지" 사유 문구 생성 (앱 상세화면에 노출).

    # 사용처: build_summary, build_detail. 앱 SafetyEvaluator가 같은 공식의 클라이언트 버전(오프라인 폴백).
    w = CONFIG["safetyWeights"]
    diff_score = CONFIG["difficultyScore"].get(course["difficulty"], 60)
    total = course["ascentMinutes"] + course["descentMinutes"]
    sun_score = _sunset_margin_score(sunset_epoch, total, now)

    score = round(w["weather"] * weather_score
                  + w["sunsetMargin"] * sun_score
                  + w["difficulty"] * diff_score)
    score = max(0, min(100, score))

    # 4단계 (확정 기획 ④): safe ≥67 / caution 34~66 / warning 16~33 / danger ≤15
    th = CONFIG["safetyThresholds"]
    if score >= th["safeMin"]:
        level = "safe"
    elif score >= th["cautionMin"]:
        level = "caution"
    elif score >= th.get("warningMin", 16):
        level = "warning"
    else:
        level = "danger"

    factors = {"weather": round(weather_score), "sunsetMargin": round(sun_score), "difficulty": diff_score}
    lowest = min(factors, key=factors.get)
    difficulty_reason = {"쉬움": "무난한 코스", "보통": "적당한 난이도의 코스"}.get(course["difficulty"], "가파른 코스")
    reason_map = {"weather": weather_summary, "sunsetMargin": "일몰 임박 · 시간 여유 부족", "difficulty": difficulty_reason}
    return {"score": score, "level": level, "reason": reason_map[lowest], "factors": factors}


# REVIEW: ⑤ DTO

def _today_kst() -> datetime:
    return datetime.now(KST)


def _course_sunset(course: Dict[str, Any], override=None):
    """일몰 (epoch, 'HH:mm'). 우선순위: KASI 실데이터(override) → NOAA 자체 연산 → 고정 폴백."""
    # [분석노트] 일몰 3단 폴백의 교통정리. 마지막 19:21 고정값은 '그래도 죽지 않기' 최후 보루.
    # 사용처: build_summary(안전지수 계산용), build_detail(+표시용 텍스트).
    if override is not None:
        return override
    res = sun_event(course["latitude"], course["longitude"], _today_kst(), is_sunrise=False)
    if res is None:
        d = _today_kst().replace(hour=19, minute=21, second=0, microsecond=0)
        return d.timestamp(), "19:21"
    return res


async def context_for(course: Dict[str, Any]):
    """코스별 실데이터 컨텍스트 일괄 수집(전부 키 없으면 None → 목업/자체연산 폴백).
    반환: (raw날씨[기상청], measured[산악기상 실측], sunset_override[KASI])"""
    # [분석노트] 외부 API 3종(기상청·산악관측·KASI) 일괄 수집기. 각 모듈이 자체 캐시(30분/24h) 보유.
    # 사용처: /mountains(코스마다 호출!), /courses/{id}.
    # TODO: async라서 성능개선 필수

    lat, lon = course["latitude"], course["longitude"]
    raw = await kma_weather.fetch_raw_weather(lat, lon)
    measured = await mtn_weather.fetch_measured(lat, lon)
    kasi = await kasi_sunset.fetch(lat, lon)
    sunset_override = (kasi[2], kasi[3]) if kasi else None   # (sunsetEpoch, "HH:mm")
    return raw, measured, sunset_override


def _apply_control_override(course: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
    """입산통제 코스는 안전지수와 무관하게 danger + 통제 사유 (확정 기획 ④)."""
    # [분석노트] "통제 = 점수 불문 가지 마세요" 정책의 코드화. catalog._apply_control이 단 플래그를 소비.
    # 사용처: build_summary, build_detail.
    if not course.get("controlled"):
        return safety
    return {**safety,
            "level": "danger",
            "score": min(safety["score"], 10),
            "reason": course.get("controlReason", "입산통제 구간")}


def build_summary(course: Dict[str, Any], user_lat: Optional[float], user_lon: Optional[float],
                  raw_override: Optional[Dict[str, Any]] = None,
                  measured: Optional[Dict[str, Any]] = None,
                  sunset_override=None) -> Dict[str, Any]:
    # [분석노트] /mountains 목록 아이템 JSON 조립. 안전지수는 실어주되 날씨 '본문'은 안 실음(목록 경량화).
    # → 앱에서 목록 출신 HikingCourse의 weather가 placeholder인 이유가 바로 여기.
    # estimatedMinutes = ascent+descent 합산값(저장 필드 아님). 앱 ServerCourseSummary와 1:1.
    now = time.time()
    weather, wscore = interpret_weather(raw_override or course["rawWeather"],
                                        course["summitAltitudeM"], measured)
    sunset_epoch, _ = _course_sunset(course, sunset_override)
    safety = _apply_control_override(
        course, compute_safety(course, wscore, weather["summary"], sunset_epoch, now))

    dist_from_user = None
    if user_lat is not None and user_lon is not None:
        # 표시용이라 0.1km 반올림 (구 _haversine_km은 함수 안에서 반올림했음 — geo 통합 후 호출부 책임)
        dist_from_user = round(haversine_km(user_lat, user_lon, course["latitude"], course["longitude"]), 1)

    return {
        "id": course["id"],
        "mountainName": course["mountainName"],
        "courseName": course["courseName"],
        "distanceKm": course["distanceKm"],
        "estimatedMinutes": course["ascentMinutes"] + course["descentMinutes"],
        "ascentMinutes": course["ascentMinutes"],
        "descentMinutes": course["descentMinutes"],
        "cumulativeGainM": course["cumulativeGainM"],
        "difficulty": course["difficulty"],
        "latitude": course["latitude"],
        "longitude": course["longitude"],
        "seedHue": course["seedHue"],
        "distanceFromUserKm": dist_from_user,
        "safetyLevel": safety["level"],
        "safetyScore": safety["score"],
        "controlled": bool(course.get("controlled", False)),
        "controlReason": course.get("controlReason"),
    }


def build_detail(course: Dict[str, Any],
                 raw_override: Optional[Dict[str, Any]] = None,
                 measured: Optional[Dict[str, Any]] = None,
                 sunset_override=None) -> Dict[str, Any]:
    # 경로·일몰·권장하산시각·날씨 본문·안전지수를 얹음. 앱 ServerCourseDetail과 1:1.
    # 권장 하산 시작 = 일몰 - descentMinutes - 안전버퍼 (앱에도 같은 공식 존재: recommendedDescentStart).
    # TODO: 기획 수정 필수

    now = time.time()
    weather, wscore = interpret_weather(raw_override or course["rawWeather"],
                                        course["summitAltitudeM"], measured)
    sunset_epoch, sunset_text = _course_sunset(course, sunset_override)
    safety = _apply_control_override(
        course, compute_safety(course, wscore, weather["summary"], sunset_epoch, now))

    buffer = CONFIG["safetyBufferMinutes"]
    rec_start_epoch = sunset_epoch - (course["descentMinutes"] + buffer) * 60
    rec_start_text = datetime.fromtimestamp(rec_start_epoch, KST).strftime("%H:%M")

    summary = build_summary(course, None, None, raw_override, measured, sunset_override)
    summary.update({
        "routePreview": [{"latitude": la, "longitude": lo} for (la, lo) in course["routePreview"]],
        "sunsetEpoch": sunset_epoch,
        "sunsetText": sunset_text,
        "recommendedDescentStartEpoch": rec_start_epoch,
        "recommendedDescentStartText": rec_start_text,
        "weather": weather,
        "safety": safety,
    })
    # 경로이탈 판정용 원본 해상도 경로(실데이터 ETL 산출물에만 존재)
    if course.get("routeFull"):
        summary["routeFull"] = [{"latitude": la, "longitude": lo} for (la, lo) in course["routeFull"]]
    return summary


# REVIEW: ⑥ 엔드포인트

@app.get("/")
def home():
    return {"message": "서버가 살아있어요 🏔️"}

@app.get("/mountains")
async def list_mountains(query: Optional[str] = None,
                         lat: Optional[float] = None,
                         lon: Optional[float] = None):
    """코스 검색/근처. query 부분일치, lat·lon 있으면 거리 오름차순.
    검색어가 아직 변환 안 된 산이면 등산로 원본(전국 2,200여 산)에서 즉석 변환한다."""

    # ① 검색어 매칭 → 0건이면 catalog.search_or_convert 즉석 변환(to_thread: CPU 작업이 이벤트루프 안 막게)
    # ② 거리순 정렬 후 상위 30개 컷(외부 API 폭주 방지) ③ 코스마다 context_for+build_summary.
    # FIXME: 즉석으로 찾는건 너무 비효율 -> 수정필수

    import asyncio
    courses = catalog.COURSES
    if query:
        q = query.strip()
        hits = [c for c in courses if q in c["mountainName"] or q in c["courseName"]]
        if not hits and len(q) >= 2:
            # 변환 안 된 산 → 즉석 변환(CPU 작업이라 스레드로, 첫 검색만 1~3초)
            await asyncio.to_thread(catalog.search_or_convert, q)
            courses = catalog.COURSES
            hits = [c for c in courses if q in c["mountainName"] or q in c["courseName"]]
        courses = hits

    # 외부 API(날씨·일몰) 폭주 방지: 가까운 순 상위 30개까지만 요약 생성
    if lat is not None and lon is not None:
        courses = sorted(courses,
                         key=lambda c: haversine_km(lat, lon, c["latitude"], c["longitude"]))
    courses = courses[:30]

    summaries = []
    for c in courses:
        raw, measured, sunset_ov = await context_for(c)
        summaries.append(build_summary(c, lat, lon, raw, measured, sunset_ov))
    if lat is not None and lon is not None:
        summaries.sort(key=lambda s: s["distanceFromUserKm"] if s["distanceFromUserKm"] is not None else 1e9)
    return summaries


@app.get("/courses/{course_id}")
async def course_detail(course_id: str):
    c = catalog.find(course_id)
    if c is None:
        raise HTTPException(status_code=404, detail="코스를 찾을 수 없습니다")
    raw, measured, sunset_ov = await context_for(c)
    return build_detail(c, raw, measured, sunset_ov)


@app.get("/weather")
async def weather(lat: float, lon: float, summitAltitude: float = 800.0):
    """좌표 기반 해석형 날씨. 기상청 예보(키 있으면) + 산악기상 실측 보강 → 없으면 목업."""
    measured = await mtn_weather.fetch_measured(lat, lon)
    live = await kma_weather.fetch_raw_weather(lat, lon)
    if live is not None:
        w, _ = interpret_weather(live, summitAltitude, measured)
        return w
    if not catalog.COURSES:   # 빈 카탈로그(courses.json 없음) → 기본 목업 날씨
        w, _ = interpret_weather(dict(catalog._DEFAULT_RAW_WEATHER), summitAltitude, measured)
        return w
    nearest = min(catalog.COURSES,
                  key=lambda c: haversine_km(lat, lon, c["latitude"], c["longitude"]))
    w, _ = interpret_weather(nearest["rawWeather"], summitAltitude, measured)
    return w


@app.get("/sunset")
async def sunset(lat: float, lon: float, date: Optional[str] = None):
    """일출/일몰. ① KASI 실데이터(키 있으면) → ② NOAA 자체 연산 폴백."""
    if date:
        d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST)
    else:
        d = _today_kst()

    kasi = await kasi_sunset.fetch(lat, lon, d)
    if kasi is not None:
        rise_epoch, rise_text, set_epoch, set_text = kasi
        return {"sunriseEpoch": rise_epoch, "sunriseText": rise_text,
                "sunsetEpoch": set_epoch, "sunsetText": set_text}

    rise = sun_event(lat, lon, d, is_sunrise=True)
    set_ = sun_event(lat, lon, d, is_sunrise=False)
    return {
        "sunriseEpoch": rise[0] if rise else None,
        "sunriseText": rise[1] if rise else None,
        "sunsetEpoch": set_[0] if set_ else None,
        "sunsetText": set_[1] if set_ else None,
    }


@app.get("/safety/config")
def safety_config():
    return CONFIG


# TODO: 로그인 기능은 새롭게 만들 필요있음 -> 현재는 그냥 임시방편

class TrackPoint(BaseModel):
    tOffset: int
    latitude: float
    longitude: float
    altitude: Optional[float] = None


class HikeUpload(BaseModel):
    courseId: Optional[str] = None
    courseName: str
    startedAt: float            # epoch
    endedAt: float              # epoch
    distanceKm: float = 0
    durationSec: int = 0
    cumulativeGainM: int = 0
    avgHeartRate: Optional[int] = None
    weatherSummary: Optional[str] = None
    track: list[TrackPoint] = []


class FavoriteUpload(BaseModel):
    courseId: str
    courseName: Optional[str] = None


def _require_db():
    if not db.configured():
        raise HTTPException(status_code=503, detail="DB 미설정: DATABASE_URL 필요")


@app.post("/hikes")
async def create_hike(hike: HikeUpload, user_id: str = Depends(auth.get_current_user)):
    _require_db()
    pool = await db.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """insert into public.hikes
                   (user_id, course_id, course_name, started_at, ended_at,
                    distance_km, duration_sec, cumulative_gain_m, avg_heart_rate, weather_summary)
                   values ($1,$2,$3, to_timestamp($4), to_timestamp($5), $6,$7,$8,$9,$10)
                   returning id""",
                user_id, hike.courseId, hike.courseName, hike.startedAt, hike.endedAt,
                hike.distanceKm, hike.durationSec, hike.cumulativeGainM,
                hike.avgHeartRate, hike.weatherSummary,
            )
            hike_id = row["id"]
            if hike.track:
                await conn.executemany(
                    """insert into public.trackpoints (hike_id, t_offset, latitude, longitude, altitude)
                       values ($1,$2,$3,$4,$5)""",
                    [(hike_id, p.tOffset, p.latitude, p.longitude, p.altitude) for p in hike.track],
                )
    return {"id": str(hike_id)}


@app.get("/hikes")
async def list_hikes(user_id: str = Depends(auth.get_current_user)):
    _require_db()
    pool = await db.pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id, course_id, course_name,
                      extract(epoch from started_at) as started_at,
                      extract(epoch from ended_at) as ended_at,
                      distance_km, duration_sec, cumulative_gain_m, avg_heart_rate, weather_summary
               from public.hikes where user_id = $1 order by started_at desc limit 200""",
            user_id,
        )
    # 명세 §1: 모든 키는 camelCase
    return [{
        "id": str(r["id"]),
        "courseId": r["course_id"],
        "courseName": r["course_name"],
        "startedAt": float(r["started_at"]),
        "endedAt": float(r["ended_at"]),
        "distanceKm": float(r["distance_km"]),
        "durationSec": r["duration_sec"],
        "cumulativeGainM": r["cumulative_gain_m"],
        "avgHeartRate": r["avg_heart_rate"],
        "weatherSummary": r["weather_summary"],
    } for r in rows]


@app.get("/favorites")
async def list_favorites(user_id: str = Depends(auth.get_current_user)):
    _require_db()
    pool = await db.pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select course_id, course_name from public.favorites where user_id=$1", user_id)
    # 명세 §1: 모든 키는 camelCase
    return [{"courseId": r["course_id"], "courseName": r["course_name"]} for r in rows]


@app.post("/favorites")
async def add_favorite(fav: FavoriteUpload, user_id: str = Depends(auth.get_current_user)):
    _require_db()
    pool = await db.pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """insert into public.favorites (user_id, course_id, course_name)
               values ($1,$2,$3)
               on conflict (user_id, course_id) do update set course_name = excluded.course_name""",
            user_id, fav.courseId, fav.courseName)
    return {"ok": True}


@app.delete("/favorites/{course_id}")
async def remove_favorite(course_id: str, user_id: str = Depends(auth.get_current_user)):
    _require_db()
    pool = await db.pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "delete from public.favorites where user_id=$1 and course_id=$2", user_id, course_id)
    return {"ok": True}
