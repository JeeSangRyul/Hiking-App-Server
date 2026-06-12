"""
trail_builder.py — 산림청 등산로(ESRI JSON) → 산담 코스 변환 코어.

tools/build_catalog.py(일괄 변환)와 catalog.py(검색 시 즉석 변환)가 공유한다.
  · 좌표계: EPSG:5186 → WGS84
  · 산 등산로망(구간 그래프)에서 최장 종주 경로(직경 근사)를 대표 코스로 추출
  · 동명이산: 코드별 중심좌표 15km 클러스터링으로 분리

[분석노트] 이 파일의 위치: ETL의 'T(변환)' 담당.
  data/raw/*.zip (구간 토막들) ──이 파일──▶ 코스 dict ──▶ data/courses.json
  파이프라인 전체: zip → load_segments → longest_path/cluster_codes → build_course
                 → build_courses_for_name → courses.json → catalog.COURSES → API 응답
  호출자는 단 둘: tools/build_catalog.py(전체 일괄), catalog.search_or_convert(검색 시 즉석).
  주의: 변환 코드를 고쳐도 courses.json을 재생성하기 전까진 옛 산출물이 서빙됨.
"""
import glob
import json
import math
import os
import zipfile
from collections import defaultdict

from pyproj import Transformer

from geo import haversine_km   # 공용 거리 계산 (geo.py) — 사용처: _geo_km, cluster_codes, build_course

HERE = os.path.dirname(os.path.abspath(__file__))           # hiking-server/
RAW_DIR = os.path.join(HERE, "data", "raw")
INDEX_PATH = os.path.join(HERE, "data", "mountain_index.json")

# 평면 미터 좌표계 => GPS 위경도 좌표계로 바꿔주는 역할
# always_xy=True: pyproj가 버전 따라 (위도,경도)/(경도,위도) 순서를 바꾸는 함정 방지용 안전핀.
# 생성 비용이 커서 모듈 로드 시 1회만 만들어 재사용. 사용처: load_segments()만.
# TODO: 실제로 데이터가 대한민국 전용 평면 좌표로 나오는지 테스트 

TRANSFORMER = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)

# 사용처: build_course().
# TODO: 실제로 데이터가 그렇게 나오는지 테스트 

DIFFICULTY_MAP = {"어려움": "어려움", "중간": "보통", "보통": "보통", "쉬움": "쉬움"}

# [분석노트] 동명이산 판별 반경(km). 중심좌표가 이보다 멀면 "이름만 같은 다른 산"으로 분리.
# 사용처: cluster_codes(). 같은 산이 행정경계로 쪼개진 경우(북한산 서울/고양)는 합쳐줌.
CLUSTER_KM = 15.0
SIDO = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주",
        "30": "대전", "31": "울산", "36": "세종", "41": "경기", "42": "강원",
        "43": "충북", "44": "충남", "45": "전북", "46": "전남", "47": "경북",
        "48": "경남", "50": "제주"}

# [분석노트] 산림청 등산로 데이터에는 산 높이가 없음. 그런데 높이는 ① 정상 날씨 고도보정
# (main.py interpret_weather: 100m당 -0.65℃), ② cumulativeGainM 추정에 필수.
# → 유명산 8개만 하드코딩, 나머지는 build_course()에서 800m로 퉁침.
# 개선 FIXME: mountains.json(산정보 API, heightM 보유)을 전 산으로 채워서 대체할 것.
FALLBACK_HEIGHTS = {"북한산": 836, "도봉산": 740, "관악산": 632, "수락산": 638,
                    "설악산": 1708, "지리산": 1915, "한라산": 1947, "태백산": 1567}


def _decode_name(n):
    # zip 포맷은 파일명 인코딩 정보가 없어, 한글(euc-kr)로 재해석해 산 이름 복원.
    # 사용처: build_index(), load_segments()의 zip 내부 파일명 필터링.
    try:
        return n.encode("cp437").decode("euc-kr")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return n


# ─────────────────────────────────────────────────────────────
# 인덱스 (코드 → 산명/zip 경로)
# ─────────────────────────────────────────────────────────────
def build_index(force=False):
    # zip파일을 스캔해서 json 형태로 찾을 수 있는 Index화 시킨다.
    # 사용처: catalog._index() → search_or_convert()의 검색어 매칭, build_courses_for_name().
    # TODO: 앱 킬때마다? 서버 킬때마다? 언제 이걸 돌리는 지 파악하고 혹시 산이 새로 추가될 일은 없으니 디비에 넣어놓고 조회하는 식으로 가능한지?
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
    # 쪼개져있는 평면 좌표들을 위도, 경도 구간으로 바꿔줌, 그 외 필요한 데이터 정제
    # 사용처: cluster_codes(). build_index와 동일 필터로 PMNTN_ 폴리라인 파일만 읽음.
    # TODO: 더 사용할 수 있는 데이터가 있다면 이쪽을 건들면 된다.
    
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
    # [분석노트] 두 위경도 사이 구면 거리(km). 표준 하버사인 공식.
    # 사용처: _geo_km(거리 결측 보정), cluster_codes(동명이산 판별), build_course(구간 방향 맞추기).
    # FIXME: 중복 함수 제거 
    # FIXME: 로직 개선?

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _geo_km(pts):
    # [분석노트] 폴리라인 길이 = 인접 점 거리의 합. PMNTN_LT(공식 구간거리)가 결측일 때만 쓰는 백업.
    return sum(haversine_km(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))


# ─────────────────────────────────────────────────────────────
# 최장 종주 경로 (그래프 직경 근사 — 더블 스윕 다익스트라)
# ─────────────────────────────────────────────────────────────
def _node(pt):
    # [분석노트] 좌표를 1/5000도(≈22m) 격자에 스냅. 구간 A의 끝과 구간 B의 시작이
    # GPS 오차로 좌표가 미세하게 달라도 "같은 교차점"으로 인식시키는 장치.
    # 끊어진 좌표라고 판단하고 같은 점의 교차점으로 인식하라는 뜻
    # 사용처: longest_path()의 그래프 노드 키.
    return (round(pt[0] * 5000) / 5000, round(pt[1] * 5000) / 5000)


def longest_path(segments):
    # FIXME: 산 정보를 눌렀을 때 코스라는 개념이 없기때문에 제일 긴 코스를 보여준다? -> 기획적으로 수정할 필요 있어보임
    # FIXME: 들어오는 데이터를 무조건 보고 판단하자.
    
    # 알고리즘: 더블 스윕 — 아무 점에서 가장 먼 점 a를 찾고, a에서 가장 먼 점 b를 찾으면
    # a~b가 그래프 직경(최장 경로) 근사라는 성질 이용. 다익스트라 2회.
    # 반환: 그 줄기를 이루는 구간 인덱스 목록 (좌표뿐 아니라 km/시간/난이도까지 같이 집계해야 해서 인덱스로).
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
    # 산코드별 중심좌표를 구해 15km(CLUSTER_KM) 이내끼리 이름이 같은 산이라면 하나로 클러스터링
    # 사용처: build_courses_for_name(). ※ 같은 산의 '여러 등산로' 처리가 아님(그건 longest_path가 1개로 압축).
    # FIXME: 기획적으로 맞는지 확인해야한다.
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
    # 백엔드의 기본 소양 같은 코드
    # 원본 데이터는 너무 많으니 미리보기의 경우 40점 -> 상세보기 더 세밀하게 보여지게 하기
    # 모든 점 별로 비교를 하게 되면 과부화가 오게되므로 필요한 코드이다.
    
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)] + [points[-1]]


def build_course(name, segs, codes):
    # dict가 courses.json에 저장되고
    # catalog.COURSES → main.py 응답 → 앱 ServerCourseDetail → HikingCourse까지 그대로 흘러감.(앱으로 감)
    # 즉 이 함수의 키 이름들이 사실상 전 시스템의 스키마.
    
    # 순서: ① 최장 경로 선택 → ② 구간 방향 맞춰 한 줄로 연결 → ③ 거리/시간/난이도/위험 집계 → ④ dict 조립.
    # FIXME: 기획상 실제 내가 가야할 코스의 정보이기 때문에 가장 긴 코스를 쓰는건 수정한다.
    # FIXME: 그 외 계산되는 로직들은 기획적으로 세세하게 고민해본다.
    
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
    # [분석노트] 이 파일의 최종 진입점. 흐름: 산명 → 산코드들 → cluster_codes(동명이산 분리)
    # → 클러스터마다 build_course 1개 → 동명이산이면 "청계산(경기)" 식 시도 라벨, 중복 시 "(경기·2)".
    # 호출순서: 산 검색 -> idx로 파일경로 찾음 -> cluster(수정예정) -> 등산로 위경도로 변환 -> 종주 경로(수정예정) -> 점 개수 최적화 -> JSON으로 반환
    # 호출순서: 산 검색 -> idx로 파일경로 찾음 -> cluster(수정예정) -> 등산로 위경도로 변환 -> 종주 경로(수정예정) -> 점 개수 최적화 -> JSON으로 반환
    # 호출자: catalog.search_or_convert(검색 시 즉석 변환), tools/build_catalog.py(일괄 변환).
    # FIXME: 산 코드, 계산 로직, 등 기획적으로 수정 뒤 수정예정
    
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
