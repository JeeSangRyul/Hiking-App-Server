"""
build_catalog.py — 등산로 실데이터 일괄 변환 CLI (변환 코어는 trail_builder.py 공유).

서버는 검색 시 즉석 변환(lazy)도 하므로 이 스크립트는 선택사항이다 —
출시 전 주요 산을 미리 구워두거나, 산정보 API로 높이를 일괄 갱신할 때 쓴다.

사용법:
  python tools/build_catalog.py                          # 기본: 서울 4산
  python tools/build_catalog.py --mountains 북한산@서울,설악산
  python tools/build_catalog.py --list 설악              # 산명 검색
  python tools/build_catalog.py --reindex                # zip 추가/변경 후 인덱스 재생성

[분석노트] ETL의 'E+L 실행 버튼'. 변환 로직은 전부 trail_builder에 있고 여기는 CLI 껍데기.
  catalog.search_or_convert(즉석 변환)와 같은 코어를 쓰므로 결과물 형식은 동일.
  차이점 둘: ① 산정보 API(fetch_mountain_info)로 진짜 높이를 받아 summitAltitudeM/cumulativeGainM을
  갱신 — 즉석 변환에는 없는 보정! (즉석 변환된 산은 높이 800m 고정인 이유)
  ② mountains.json(산 높이·소개·교통편)도 같이 생성.
  ⚠️ courses.json을 통째로 덮어씀 — 즉석 변환으로 쌓인 산들이 날아감(병합 아님).
  [다음 업무 TODO] 백로그 #4 'courses.json 재생성'은 이 스크립트 실행으로 해결:
    python tools/build_catalog.py --mountains 북한산@서울,도봉산@서울,... (기존 보유 산 전부 나열)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

import trail_builder as tb

OUT_COURSES = os.path.join(tb.HERE, "data", "courses.json")
OUT_MOUNTAINS = os.path.join(tb.HERE, "data", "mountains.json")
DEFAULT_TARGETS = "북한산@서울,도봉산@서울,관악산@서울,수락산@서울"


def fetch_mountain_info(names):
    """산림청 산정보 API — 높이·소재지. 키 없으면 빈 dict(폴백 높이 유지).
    TODO: 미리 산의 메타데이터를 가지고 있어야한다. 검색할 때 마다 조회는 너무 비효율적
    """

    key = (os.environ.get("DATA_GO_KR_SERVICE_KEY")
           or os.environ.get("KMA_SERVICE_KEY") or "").strip()
    if not key:
        print("  (산정보 API 키 없음 — 내장 폴백 높이 사용)")
        return {}
    import httpx
    import xml.etree.ElementTree as ET
    base = "http://api.forest.go.kr/openapi/service/trailInfoService/getforeststoryservice"
    out = {}
    for name in names:
        clean = name.split("(")[0]
        try:
            r = httpx.get(base, params={"ServiceKey": key, "mntnNm": clean,
                                        "pageNo": "1", "numOfRows": "5"}, timeout=8)
            root = ET.fromstring(r.text)
            for item in root.iter("item"):
                nm = (item.findtext("mntnnm") or "").strip()
                if clean not in nm:
                    continue
                try:
                    height = int(float((item.findtext("mntninfohght") or "").strip()))
                except ValueError:
                    height = None
                out[clean] = {
                    "code": (item.findtext("mntnid") or "").strip(),
                    "name": nm, "heightM": height,
                    "location": (item.findtext("mntninfopoflc") or "").strip(),
                    "overview": (item.findtext("mntninfodscrt") or "").strip()[:2000],
                    "transport": (item.findtext("pbtrninfodscrt") or "").strip()[:2000],
                }
                print(f"  산정보 OK: {nm} {height}m")
                break
        except Exception as e:
            print(f"  ⚠️ 산정보 조회 실패({clean}): {e}")
    return out


def main():
    """
    인자 받기 -> 코스 변환 -> 높이 보정 -> 두 json파일로 저장
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--mountains", default=DEFAULT_TARGETS)
    ap.add_argument("--list", help="산명 검색(인덱스)")
    ap.add_argument("--reindex", action="store_true")
    args = ap.parse_args()

    index = tb.build_index(force=args.reindex)
    print(f"인덱스: 산 {len(set(v['name'] for v in index.values()))}개 / 코드 {len(index)}개")

    if args.list:
        hits = sorted({(v["name"], c) for c, v in index.items() if args.list in v["name"]})
        for name, c in hits:
            print(f"  {name}  (코드 {c}, {tb.SIDO.get(c[:2], '?')})")
        print(f"{len(hits)}건")
        return

    courses = []
    for target in [t.strip() for t in args.mountains.split(",") if t.strip()]:
        name, _, sido = target.partition("@")
        built = tb.build_courses_for_name(name, index, sido_filter=sido or None)
        if not built:
            print(f"⚠️ '{target}' 결과 없음 — --list {name} 으로 확인")
        for c in built:
            courses.append(c)
            print(f"• {c['mountainName']}: 종주 {c['distanceKm']}km · 상행 {c['ascentMinutes']}분"
                  f"/하행 {c['descentMinutes']}분 · {c['difficulty']} · 위험정보 {len(c['riskNotes'])}건")

    info = fetch_mountain_info([c["mountainName"] for c in courses])
    for c in courses:
        i = info.get(c["mountainName"].split("(")[0])
        if i and i.get("heightM"):
            c["summitAltitudeM"] = i["heightM"]
            c["cumulativeGainM"] = max(100, i["heightM"] - 150)

    with open(OUT_COURSES, "w", encoding="utf-8") as f:
        json.dump(courses, f, ensure_ascii=False, indent=1)
    with open(OUT_MOUNTAINS, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(f"\n✅ {OUT_COURSES} ({len(courses)}개 코스)")
    print("⚠️ 검수: 앱 지도에서 경로가 실제 등산로 위에 있는지 확인하세요.")


if __name__ == "__main__":
    main()
