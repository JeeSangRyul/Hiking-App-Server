"""
kasi_sunset.py — 한국천문연구원(KASI) 출몰시각 실연동.

  · 오퍼레이션: RiseSetInfoService/getLCRiseSetInfo (위치별 해달 출몰시각)
  · 좌표 형식: 도분(DM) — 예: 126.9779° → "12658" (126도 58분)   ※ 가이드 v1.2 기준
  · 응답: XML, sunrise/sunset = "HHMMSS" (KST)
  · 24시간 TTL 캐시 (같은 좌표·날짜는 하루 1회만 호출)
  · 키 없거나 호출 실패 → None 반환 (호출부가 자체 NOAA 연산으로 폴백)

환경변수: KASI_SERVICE_KEY (없으면 DATA_GO_KR_SERVICE_KEY → KMA_SERVICE_KEY 순으로 재사용)
          공공데이터포털 키는 계정당 1개라 보통 전부 같은 키입니다. Decoding 키를 넣으세요.
"""
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any

import httpx

KST = timezone(timedelta(hours=9))
BASE = "http://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getLCRiseSetInfo"

def _key() -> str:
    return (os.environ.get("KASI_SERVICE_KEY")
            or os.environ.get("DATA_GO_KR_SERVICE_KEY")
            or os.environ.get("KMA_SERVICE_KEY") or "").strip()

def enabled() -> bool:
    return bool(_key())

# (lat2,lon2,date) -> (ts, result)
_CACHE: Dict[str, Any] = {}
_TTL = 24 * 3600


# ⚠️ 좌표 형식 주의: 가이드 예시(12800)는 도분처럼 보이지만, 실측 결과
#    getLCRiseSetInfo는 '십진수 도'(126.98)를 받는다. 도분("12659")으로 보내면
#    엉뚱한 지역(독도)으로 스냅됨 — 실제 호출로 검증 완료 (2026-06-10).


def _hhmmss_to_epoch(date_kst: datetime, hhmmss: str) -> Optional[Tuple[float, str]]:
    s = (hhmmss or "").strip()
    if len(s) < 4 or not s[:4].isdigit():
        return None
    h, m = int(s[0:2]), int(s[2:4])
    sec = int(s[4:6]) if len(s) >= 6 and s[4:6].isdigit() else 0
    dt = date_kst.replace(hour=h, minute=m, second=sec, microsecond=0)
    return dt.timestamp(), f"{h:02d}:{m:02d}"


async def fetch(lat: float, lon: float, date: Optional[datetime] = None):
    """(sunrise_epoch, sunrise_text, sunset_epoch, sunset_text) 또는 None."""
    if not enabled():
        return None
    d = (date or datetime.now(KST)).astimezone(KST)
    cache_key = f"{round(lat, 2)},{round(lon, 2)},{d:%Y%m%d}"
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key][0] < _TTL:
        return _CACHE[cache_key][1]

    params = {
        "serviceKey": _key(),
        "locdate": f"{d:%Y%m%d}",
        "longitude": f"{lon:.4f}",   # 십진수 도 (위 주석 참고)
        "latitude": f"{lat:.4f}",
        "dnYn": "Y",
    }
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(BASE, params=params)
            r.raise_for_status()
            root = ET.fromstring(r.text)
        item = root.find(".//item")
        if item is None:
            return None
        rise = _hhmmss_to_epoch(d, item.findtext("sunrise", ""))
        set_ = _hhmmss_to_epoch(d, item.findtext("sunset", ""))
        if rise is None or set_ is None:
            return None
        result = (rise[0], rise[1], set_[0], set_[1])
        _CACHE[cache_key] = (now, result)
        return result
    except Exception:
        return None   # 폴백(NOAA 자체 연산)은 호출부 책임
