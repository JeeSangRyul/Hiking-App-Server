"""
trail_builder.py — 산림청 등산로(ESRI JSON) → 산담 코스 변환 코어.

tools/build_catalog.py(일괄 변환)와 catalog.py(검색 시 즉석 변환)가 공유한다.
  · 좌표계: EPSG:5186 → WGS84
  · 산 등산로망(구간 그래프)에서 최장 종주 경로(직경 근사)를 대표 코스로 추출
  · 동명이산: 코드별 중심좌표 15km 클러스터링으로 분리
"""
import glob
import json
import math
import os
import zipfile
from collections import defaultdict

from pyproj import Transformer

HERE = os.path.dirname(os.path.abspath(__file__))           # hiking-server/
RAW_DIR = os.path.join(HERE, "data", "raw")
INDEX_PATH = os.path.join(HERE, "data", "mountain_index.json")

TRANSFORMER = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
DIFFICULTY_MAP = {"어려움": "어려움", "중간": "보통", "보통": "보통", "쉬움": "쉬움"}
CLUSTER_KM = 15.0
SIDO = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주",
        "30": "대전", "31": "울산", "36": "세종", "41": "경기", "42": "강원",
        "43": "충북", "44": "충남", "45": "전북", "46": "전남", "47": "경북",
        "48": "경남", "50": "제주"}
FALLBACK_HEIGHTS = {"북한산": 836, "도봉산": 740, "관악산": 632, "수락산": 638,
                    "설악산": 1708, "지리산": 1915, "한라산": 1947, "태백산": 1567}


def _decode_name(n):
    try:
        return n.encode("cp437").decode("euc-kr")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return n


# ─────────────────────────────────────────────────────────────
# 인덱스 (코드 → 산명/zip 경로)
# ─────────────────────────────────────────────────────────────
def build_index(force=False):
    if os.path.exists(INDEX_PATH) and not force:
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    zips = glob.glob(os.path.join(RAW_DIR, "**", "*_geojson.zip"), recursive=True)
    index = {}
    for zp in zips:
        code = os.path.basename(zp).replace("_geojson.zip", "")
        try:
            with zipfile.ZipFile(zp) as z:
                for n in z.namelist():
                    base = os.path.basename(_decode_name(n))
                    if base.startswith("PMNTN_") and "SPOT" not in base.upper() and base.endswith(".json"):
                        parts = base[:-5].split("_")
                        if len(parts) >= 2:
                            index[code] = {"name": parts[1], "zip": os.path.relpath(zp, RAW_DIR)}
                        break
        except zipfile.BadZipFile:
            pass
    if index:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
    return index


# ─────────────────────────────────────────────────────────────
# ESRI JSON → 구간
# ─────────────────────────────────────────────────────────────
def load_segments(zp_rel):
    out = []
    path = os.path.join(RAW_DIR, zp_rel)
    if not os.path.exists(path):
        return out
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if _decode_name(n).split("/")[-1].startswith("PMNTN_")
                 and "SPOT" not in n.upper() and n.lower().endswith(".json")]
        if not names:
            return out
        data = json.loads(z.read(names[0]).decode("utf-8", errors="replace"))
    for f in data.get("features", []):
        a = f.get("attributes", {})
        for p in f.get("geometry", {}).get("paths", []):
            if len(p) < 2:
                continue
            pts = []
            for x, y in p:
                lon, lat = TRANSFORMER.transform(x, y)
                pts.append((round(lat, 5), round(lon, 5)))
            km = a.get("PMNTN_LT") or 0
            out.append({
                "pts": pts,
                "km": float(km) if km else _geo_km(pts),
                "up": int(a.get("PMNTN_UPPL") or 0),
                "down": int(a.get("PMNTN_GODN") or 0),
                "dffl": (a.get("PMNTN_DFFL") or "").strip(),
                "risk": (a.get("PMNTN_RISK") or "").strip(),
                "name": (a.get("PMNTN_NM") or "").strip(),
            })
    return out


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _geo_km(pts):
    return sum(haversine_km(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))


# ─────────────────────────────────────────────────────────────
# 최장 종주 경로 (그래프 직경 근사 — 더블 스윕 다익스트라)
# ─────────────────────────────────────────────────────────────
def _node(pt):
    return (round(pt[0] * 5000) / 5000, round(pt[1] * 5000) / 5000)


def longest_path(segments):
    graph = defaultdict(list)
    for i, s in enumerate(segments):
        a, b = _node(s["pts"][0]), _node(s["pts"][-1])
        if a == b:
            continue
        graph[a].append((b, i))
        graph[b].append((a, i))
    if not graph:
        return list(range(len(segments)))

    import heapq
    def dijkstra(src):
        dist = {src: 0.0}
        prev = {}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18):
                continue
            for v, si in graph[u]:
                nd = d + max(segments[si]["km"], 0.01)
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = (u, si)
                    heapq.heappush(pq, (nd, v))
        far = max(dist, key=dist.get)
        return far, dist[far], prev

    start = _node(segments[max(range(len(segments)), key=lambda i: segments[i]["km"])]["pts"][0])
    a, _, _ = dijkstra(start)
    b, _, prev = dijkstra(a)
    path, cur = [], b
    while cur in prev:
        cur, si = prev[cur]
        path.append(si)
    path.reverse()
    return path


# ─────────────────────────────────────────────────────────────
# 클러스터링(동명이산) + 코스 생성
# ─────────────────────────────────────────────────────────────
def cluster_codes(codes, index):
    loaded = []
    for code in codes:
        segs = load_segments(index[code]["zip"])
        if not segs:
            continue
        pts = [p for s in segs for p in (s["pts"][0], s["pts"][-1])]
        centroid = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        loaded.append({"code": code, "segs": segs, "centroid": centroid})
    clusters = []
    for item in loaded:
        for cl in clusters:
            if haversine_km(*item["centroid"], *cl["centroid"]) <= CLUSTER_KM:
                cl["codes"].append(item["code"])
                cl["segs"].extend(item["segs"])
                break
        else:
            clusters.append({"codes": [item["code"]], "segs": list(item["segs"]),
                             "centroid": item["centroid"]})
    return clusters


def _downsample(points, target):
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)] + [points[-1]]


def build_course(name, segs, codes):
    if not segs:
        return None
    path_idx = longest_path(segs)
    chosen = [segs[i] for i in path_idx]

    route = []
    for s in chosen:
        pts = s["pts"]
        if route and haversine_km(*route[-1], *pts[0]) > haversine_km(*route[-1], *pts[-1]):
            pts = pts[::-1]
        route.extend(pts if not route else pts[1:])

    total_km = sum(s["km"] for s in chosen)
    up = sum(s["up"] for s in chosen)
    down = sum(s["down"] for s in chosen)
    rank = {"쉬움": 0, "보통": 1, "중간": 1, "어려움": 2}
    worst = max((s["dffl"] for s in chosen if s["dffl"]), key=lambda d: rank.get(d, 1), default="보통")
    risks = sorted({s["risk"] for s in chosen if s["risk"]})
    if up == 0:
        up = int(total_km * 18 * 0.65)
    if down == 0:
        down = int(total_km * 12)
    height = FALLBACK_HEIGHTS.get(name, 800)

    # 원본 난이도(PMNTN_DFFL)가 듬성한 경우 보정: 거리·고도 기준 하한선
    # (안전 앱이므로 보수적으로 — 31km 종주가 '쉬움'으로 나가면 안 됨)
    difficulty = DIFFICULTY_MAP.get(worst, "보통")
    if total_km >= 14 or height >= 1300:
        difficulty = "어려움"
    elif total_km >= 8 and difficulty == "쉬움":
        difficulty = "보통"

    return {
        "id": f"{name}-{codes[0]}",
        "mountainCode": codes[0],
        "mountainName": name,
        "courseName": "종주 코스",
        "distanceKm": round(total_km, 1),
        "ascentMinutes": up, "descentMinutes": down,
        "cumulativeGainM": max(100, height - 150),
        "difficulty": difficulty,
        "latitude": route[0][0], "longitude": route[0][1],
        "summitAltitudeM": height,
        "seedHue": round((abs(hash(name)) % 100) / 100, 2),
        "routePreview": _downsample(route, 40),
        "routeFull": _downsample(route, 400),
        "riskNotes": risks,
        "segmentCount": len(chosen),
    }


def build_courses_for_name(name, index, sido_filter=None):
    """산명 1개 → 코스 목록(동명이산이면 여러 개, 시도 라벨 포함)."""
    codes = sorted(c for c, v in index.items() if v["name"] == name)
    if not codes:
        return []
    out = []
    clusters = cluster_codes(codes, index)
    used_labels = set()
    for cl in clusters:
        sidos = sorted({SIDO.get(c[:2], c[:2]) for c in cl["codes"]})
        if sido_filter and sido_filter not in sidos:
            continue
        c = build_course(name, cl["segs"], cl["codes"])
        if c:
            if len(clusters) > 1 and not sido_filter:
                label = f"{name}({'/'.join(sidos)})"
                # 같은 시도에 동명이산 여러 개(예: 경기 청계산 3곳) → 번호로 구분
                n = 2
                base = label
                while label in used_labels:
                    label = f"{base[:-1]}·{n})"
                    n += 1
                used_labels.add(label)
                c["mountainName"] = label
            out.append(c)
    return out
