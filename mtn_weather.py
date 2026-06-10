"""
mtn_weather.py — 국립산림과학원 산악기상관측망 실측 연동 (기술문서 v1.5).

  · 엔드포인트: http://apis.data.go.kr/1400377/mtweather/mountListSearch
  · 관측소 454곳 목록(data/mtweather_stations.json — 기술문서에서 추출)에서
    코스 좌표와 가장 가까운 지점을 찾아 실측 기온/풍속을 가져온다.
  · 용도: 평지 예보 + 고도보정 '추정'을 능선 실측으로 대체/보강 (F-B3 정확도 ↑)
  · 30분 TTL 캐시. 키 없음/관측소 멀음/호출 실패 → None (호출부는 기존 보정 추정 유지)

환경변수: MTWEATHER_SERVICE_KEY (없으면 DATA_GO_KR_SERVICE_KEY → KMA_SERVICE_KEY 재사용)
"""
import os
import json
import math
import time
from typing import Optional, Dict, Any, List

import httpx

BASE = "http://apis.data.go.kr/1400377/mtweather/mountListSearch"
# 관측소가 이보다 멀면 실측을 쓰지 않는다(다른 산의 날씨일 수 있으므로)
MAX_STATION_DISTANCE_KM = 10.0

def _key() -> str:
    return (os.environ.get("MTWEATHER_SERVICE_KEY")
            or os.environ.get("DATA_GO_KR_SERVICE_KEY")
            or os.environ.get("KMA_SERVICE_KEY") or "").strip()

def enabled() -> bool:
    return bool(_key()) and bool(_stations())

# 관측소 목록 (기술문서 표에서 추출한 JSON)
_STATIONS: Optional[List[Dict[str, Any]]] = None

def _stations() -> List[Dict[str, Any]]:
    global _STATIONS
    if _STATIONS is None:
        path = os.path.join(os.path.dirname(__file__), "data", "mtweather_stations.json")
        try:
            with open(path, encoding="utf-8") as f:
                _STATIONS = json.load(f)
        except Exception:
            _STATIONS = []
    return _STATIONS


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_station(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    best, best_d = None, 1e9
    for s in _stations():
        d = _haversine_km(lat, lon, s["lat"], s["lon"])
        if d < best_d:
            best, best_d = s, d
    if best is None or best_d > MAX_STATION_DISTANCE_KM:
        return None
    return {**best, "distanceKm": round(best_d, 1)}


def _float(v) -> Optional[float]:
    try:
        f = float(v)
        # 관측 장비 결측 코드(-99 등) 거름
        return f if -90 < f < 90 or (0 <= f <= 100) else None
    except (TypeError, ValueError):
        return None


# obsid -> (ts, result)
_CACHE: Dict[str, Any] = {}
_TTL = 30 * 60


async def fetch_measured(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """가까운 관측소의 실측값.
    반환: {"tempC", "windMs", "humidityPct", "obsname", "distanceKm", "tm"} 또는 None.
    풍속은 ws10m(10m 풍속·결측 시 ws2m), 기온은 tm2m(결측 시 tm10m)을 쓴다.
    """
    if not _key():
        return None
    st = nearest_station(lat, lon)
    if st is None:
        return None

    now = time.time()
    if st["obsid"] in _CACHE and now - _CACHE[st["obsid"]][0] < _TTL:
        return _CACHE[st["obsid"]][1]

    # ⚠️ tm(관측시간)을 생략하면 items가 비어서 온다(실호출로 확인, 2026-06-10).
    #    현재 시각을 10분 단위로 내림해 조회하고, 관측 지연 대비 과거로 재시도.
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)

    item = None
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            for back_min in (0, 10, 20, 60):
                t = now_kst - timedelta(minutes=back_min)
                tm = t.strftime("%Y%m%d%H") + f"{(t.minute // 10) * 10:02d}"
                r = await client.get(BASE, params={
                    "serviceKey": _key(), "pageNo": "1", "numOfRows": "5",
                    "_type": "json", "obsid": st["obsid"], "tm": tm,
                })
                r.raise_for_status()
                data = r.json()
                items = (data.get("response", {}).get("body", {}).get("items", {}) or "")
                if not items:
                    continue
                got = items.get("item")
                item = got[-1] if isinstance(got, list) and got else got
                if item:
                    break
        if not item:
            return None

        temp = _float(item.get("tm2m")) or _float(item.get("tm10m"))
        wind = _float(item.get("ws10m"))
        if wind is None:
            wind = _float(item.get("ws2m"))
        if temp is None and wind is None:
            return None

        result = {
            "tempC": temp,
            "windMs": wind,
            "humidityPct": _float(item.get("hm2m")),
            "obsname": str(item.get("obsname", st["mountain"])),
            "distanceKm": st["distanceKm"],
            "tm": str(item.get("tm", "")),
        }
        _CACHE[st["obsid"]] = (now, result)
        return result
    except Exception:
        return None
