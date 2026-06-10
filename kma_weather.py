"""
kma_weather.py — 기상청 단기예보 실연동 (공공데이터포털).

  · 위경도 → 기상청 격자(nx, ny) 변환 (Lambert Conformal Conic)
  · 초단기실황(getUltraSrtNcst): 현재 기온(T1H)·풍속(WSD)
  · 단기예보(getVilageFcst): 강수확률(POP)
  · 30분 TTL 캐시 (개발계정 트래픽 1만/일 보호)
  · KMA_SERVICE_KEY 없거나 호출 실패 → None 반환(호출부가 catalog 목업으로 폴백)

환경변수:
  KMA_SERVICE_KEY = 공공데이터포털 '기상청_단기예보 조회서비스' 인코딩/디코딩 키
"""
import os
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import httpx

KST = timezone(timedelta(hours=9))
BASE = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
# 공공데이터포털 키는 계정당 1개 — 공통 키(DATA_GO_KR_SERVICE_KEY)로도 동작
_KEY = (os.environ.get("KMA_SERVICE_KEY")
        or os.environ.get("DATA_GO_KR_SERVICE_KEY") or "").strip()

# (nx, ny) -> (timestamp, raw_dict)
_CACHE: Dict[str, Any] = {}
_TTL = 30 * 60  # 30분

def enabled() -> bool:
    return bool(_KEY)


# ─────────────────────────────────────────────────────────────
# 위경도 → 기상청 격자 (공식 변환식)
# ─────────────────────────────────────────────────────────────
def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2 = 30.0, 60.0
    OLON, OLAT = 126.0, 38.0
    XO, YO = 43, 136
    DEGRAD = math.pi / 180.0

    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = int(ra * math.sin(theta) + XO + 0.5)
    ny = int(ro - ra * math.cos(theta) + YO + 0.5)
    return nx, ny


# ─────────────────────────────────────────────────────────────
# base_date / base_time 계산
# ─────────────────────────────────────────────────────────────
def _ncst_base(now: datetime) -> tuple[str, str]:
    """초단기실황: 매시 정시 생성, 약 40분 후 제공. 안전하게 1시간 전 정시 사용."""
    t = now - timedelta(minutes=40)
    return t.strftime("%Y%m%d"), t.strftime("%H00")


def _vilage_base(now: datetime) -> tuple[str, str]:
    """단기예보: 0200,0500,0800,1100,1400,1700,2000,2300 발표. 직전 발표 사용."""
    slots = [2, 5, 8, 11, 14, 17, 20, 23]
    t = now - timedelta(minutes=10)
    hour = t.hour
    chosen = None
    for s in reversed(slots):
        if hour >= s:
            chosen = s
            break
    if chosen is None:                       # 새벽 02시 이전 → 전날 2300
        t = t - timedelta(days=1)
        chosen = 23
    return t.strftime("%Y%m%d"), f"{chosen:02d}00"


# ─────────────────────────────────────────────────────────────
# 호출
# ─────────────────────────────────────────────────────────────
async def _call(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    common = {
        "serviceKey": _KEY,
        "dataType": "JSON",
        "numOfRows": "300",
        "pageNo": "1",
    }
    common.update(params)
    r = await client.get(f"{BASE}/{path}", params=common, timeout=8)
    r.raise_for_status()
    body = r.json()["response"]["body"]
    return body["items"]["item"]


async def fetch_raw_weather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """현재 산악 기상의 raw 값 {baseTempC, windMs, precipPct} 반환. 실패 시 None."""
    if not enabled():
        return None

    nx, ny = latlon_to_grid(lat, lon)
    key = f"{nx},{ny}"
    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    now = datetime.now(KST)
    try:
        async with httpx.AsyncClient() as client:
            # 초단기실황: 현재 기온·풍속
            nd, nt = _ncst_base(now)
            ncst = await _call(client, "getUltraSrtNcst",
                               {"base_date": nd, "base_time": nt, "nx": nx, "ny": ny})
            obs = {it["category"]: it["obsrValue"] for it in ncst}
            base_temp = float(obs.get("T1H", 12.0))
            wind = float(obs.get("WSD", 3.0))

            # 단기예보: 가장 가까운 시각의 강수확률(POP)
            vd, vt = _vilage_base(now)
            fcst = await _call(client, "getVilageFcst",
                               {"base_date": vd, "base_time": vt, "nx": nx, "ny": ny})
            pops = [int(it["fcstValue"]) for it in fcst if it["category"] == "POP"]
            precip = pops[0] if pops else 0

        raw = {"baseTempC": base_temp, "windMs": wind, "precipPct": precip}
        _CACHE[key] = (time.time(), raw)
        return raw
    except Exception:
        # 네트워크/파싱/키 오류 → 폴백(호출부가 catalog 목업 사용)
        return None
