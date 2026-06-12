"""
geo.py — 지리 계산 공용 모듈.

haversine_km: 두 위경도 사이 구면 거리(km). 표준 하버사인 공식.
과거 main.py(_haversine_km)·mtn_weather.py(_haversine_km)·trail_builder.py(haversine_km)에
3벌 복붙돼 있던 것을 통합 (백로그 #12). 반올림 없이 원값 반환 — 표시용 반올림은 호출부에서.
"""
import math


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))
