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

HERE = os.path.dirname(os.path.abspath(__file__))           # hiking-server/
RAW_DIR = os.path.join(HERE, "data", "raw")
INDEX_PATH = os.path.join(HERE, "data", "mountain_index.json")

# [분석노트] 좌표 번역기. 산림청 raw는 미터 단위 평면좌표(EPSG:5186, "기준점에서 동/북으로 몇 m"),
# 지도·GPS·MapKit은 위경도(EPSG:4326). 이 변환 없이는 지도에 산이 엉뚱한 곳에 그려짐.
# always_xy=True: pyproj가 버전 따라 (위도,경도)/(경도,위도) 순서를 바꾸는 함정 방지용 안전핀.
# 생성 비용이 커서 모듈 로드 시 1회만 만들어 재사용. 사용처: load_segments()만.
TRANSFORMER = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
# [분석노트] 산림청 난이도 표기("중간")를 앱 표기("보통")로 통일. 사용처: build_course().
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
# 개선 TODO: mountains.json(산정보 API, heightM 보유)을 전 산으로 채워서 대체할 것.
FALLBACK_HEIGHTS = {"북한산": 836, "도봉산": 740, "관악산": 632, "수락산": 638,
                    "설악산": 1708, "지리산": 1915, "한라산": 1947, "태백산": 1567}


def _decode_name(n):
    # [분석노트] zip 포맷은 파일명 인코딩 정보가 없어, 한글(euc-kr) 파일명이 cp437로
    # 잘못 읽혀 "║╧╛╟╗Ω"처럼 깨짐. cp437 바이트로 되돌린 뒤 euc-kr로 재해석해 "북악산" 복원.
    # 사용처: build_index(), load_segments()의 zip 내부 파일명 필터링.
    try:
        return n.encode("cp437").decode("euc-kr")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return n


# ─────────────────────────────────────────────────────────────
# 인덱스 (코드 → 산명/zip 경로)
# ─────────────────────────────────────────────────────────────
def build_index(force=False):
    # [분석노트] 왜 필요한가: 사용자가 "설악산"을 검색해도 zip 파일명은 산코드(421901101)뿐.
    # 이름→zip을 찾으려면 zip을 열어봐야 하는데, 검색마다 8,802개를 열 순 없음.
    # → 미리 1회 전부 스캔해 "전화번호부"를 만듦: {산코드: {name: 산명, zip: 경로}}
    # 산출물: data/mountain_index.json (2,932산). 캐시가 있으면 스캔을 평생 건너뜀.
    # 사용처: catalog._index() → search_or_convert()의 검색어 매칭, build_courses_for_name().
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
    # [분석노트] 역할: zip 1개 → "구간 목록"으로 변환하는 입구. raw의 지저분함(ESRI JSON 구조,
    # 평면좌표, 한글 깨짐, 결측치)을 전부 여기서 흡수해 이후 코드는 깨끗한 dict만 다룸.
    # 입력: "mountain/111100101_geojson.zip" (북악산)
    # 출력: [{pts: [(위도,경도),...], km, up(상행분), down(하행분), dffl(난이도), risk, name}, ...]
    # ※ 출력은 아직 '코스'가 아니라 토막난 '구간' 수십 개 — 코스 합성은 build_course 몫.
    # 사용처: cluster_codes(). build_index와 동일 필터로 PMNTN_ 폴리라인 파일만 읽음.
    # ⚠️ 발견한 문제: PMNTN_CLS_(폐쇄여부)="Y" 구간을 거르지 않음 → 폐쇄 등산로가 코스에 혼입 가능.
    #    SPOT(시종점·분기점)/SAFE_SPOT(안전지점) 파일도 통째로 버리는 중 — 들머리 판별에 쓸 수 있음.
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
    # ⚠️ main.py·mtn_weather.py에 같은 함수가 복붙돼 있음 — 공용 모듈로 통합 후보(5단계 Rename 때).
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
    # 사용처: longest_path()의 그래프 노드 키.
    return (round(pt[0] * 5000) / 5000, round(pt[1] * 5000) / 5000)


def longest_path(segments):
    # [분석노트] 왜 필요한가: 산림청 raw는 '코스' 개념이 없는 구간 토막 덩어리.
    # 앱은 사용자에게 "코스 하나"(거리·시간·경로 한 줄)를 보여줘야 하므로,
    # 구간들을 그래프(구간=간선, 끝점=노드)로 잇고 "가장 길게 이어지는 한 줄기"를 대표로 뽑음.
    # 알고리즘: 더블 스윕 — 아무 점에서 가장 먼 점 a를 찾고, a에서 가장 먼 점 b를 찾으면
    # a~b가 그래프 직경(최장 경로) 근사라는 성질 이용. 다익스트라 2회.
    # 반환: 그 줄기를 이루는 구간 인덱스 목록 (좌표뿐 아니라 km/시간/난이도까지 같이 집계해야 해서 인덱스로).
    # ⚠️ 트레이드오프: 최장 줄기 1개만 살아남음 → 북한산의 수십 갈래 등산로가 "종주 코스" 하나로 뭉개짐.
    #    (고도화 때 분기점 기준 N개 코스 추출로 개선 여지)
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
    # [분석노트] 왜 필요한가: 검색은 이름 기반인데 "청계산"이 전국에 3곳(과천/가평/양평).
    # 이름만 같은 다른 산들이 한 그래프에 섞이면 서로 연결 안 되는 덩어리에서 최장 경로가 깨짐.
    # → 산코드별 중심좌표를 구해 15km(CLUSTER_KM) 이내끼리만 묶음.
    #   같은 산이 행정경계로 코드가 쪼개진 경우(북한산 서울/고양측)는 하나로 합쳐지는 효과도.
    # 반환: [{codes: [산코드들], segs: [합쳐진 구간들], centroid}] — 클러스터 1개 = 코스 1개가 됨.
    # 사용처: build_courses_for_name(). ※ 같은 산의 '여러 등산로' 처리가 아님(그건 longest_path가 1개로 압축).
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
    # [분석노트] 원본 경로는 점 수천~수만 개. 그대로 쓰면 courses.json/API 응답이 수 MB,
    # 지도 렌더링 버벅임, 경로이탈 판정(매초 내 위치 vs 모든 점 거리 계산) 과부하.
    # → 용도별 2단계 축소: routePreview 40점(미리보기, 모양만), routeFull 400점(이탈 판정, 수십 m 정밀도).
    # 균등 간격 샘플링이라 급커브 보존은 안 됨(Douglas-Peucker 아님). 마지막 점은 반드시 보존.
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)] + [points[-1]]


def build_course(name, segs, codes):
    # [분석노트] 역할: 구간들 → 최종 '코스 dict' 조립. 여기서 만든 dict가 courses.json에 저장되고
    # catalog.COURSES → main.py 응답 → 앱 ServerCourseDetail → HikingCourse까지 그대로 흘러감.
    # 즉 이 함수의 키 이름들이 사실상 전 시스템의 스키마.
    # 순서: ① 최장 경로 선택 → ② 구간 방향 맞춰 한 줄로 연결 → ③ 거리/시간/난이도/위험 집계 → ④ dict 조립.
    # ⚠️ 추정값 3형제: cumulativeGainM = max(100, 정상고도-150) (실측 아님!),
    #    up/down 결측 시 km×11.7/km×12 추정, 정상고도는 FALLBACK_HEIGHTS 8개 외 800m 고정.
    # ⚠️ latitude/longitude(들머리) = 그냥 경로 첫 점 — 실제 입구 보장 없음 (SPOT 시종점으로 개선 가능).
    # ⚠️ seedHue가 hash(name) 기반 — 파이썬 해시 랜덤화 때문에 ETL 돌릴 때마다 색이 바뀜.
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
    # 호출자: catalog.search_or_convert(검색 시 즉석 변환), tools/build_catalog.py(일괄 변환).
    # 개선 여지: 시도 라벨이 산코드 앞 2자리 추정 — MNT_CODE.xlsx(시군구 주소)로 "청계산(과천)" 가능.
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
