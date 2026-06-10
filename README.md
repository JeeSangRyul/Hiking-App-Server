# 산담(SanDam) 서버

안전 중심 등산 앱 산담의 백엔드. **FastAPI**로 공공데이터(기상청)를 중계하고,
안전지수·일몰·하산 데드라인을 연산하며, Supabase에 사용자 산행 기록을 저장합니다.

## 구성

```
main.py          엔드포인트 + 안전지수/일몰 연산
catalog.py       코스 카탈로그(추후 산림청 등산로 DB로 교체)
config.json      안전지수·날씨변환표·버퍼 튜닝값
kma_weather.py   기상청 단기예보 실연동 (위경도→격자, 캐싱)
auth.py          Supabase JWT 검증
db.py            Supabase(Postgres) 연결 풀
schema.sql       Supabase 테이블 스키마
```

## 환경변수 (`.env.example` 참고)

| 변수 | 용도 | 없을 때 |
|---|---|---|
| `DATA_GO_KR_SERVICE_KEY` | **공공데이터포털 공통키** — 기상청 단기예보 + 천문연 일출일몰 + 산악기상 실측 전부 이 키 하나로 동작 (Decoding 키 사용) | 목업 날씨 + NOAA 자체 일몰 연산 |
| `SUPABASE_JWT_SECRET` | 로그인 토큰 검증 | 기록/즐겨찾기 API만 503 |
| `DATABASE_URL` | Supabase Postgres 접속 | 기록/즐겨찾기 API만 503 |

> 키가 하나도 없어도 검색·날씨(목업)·일몰·안전지수 등 **공개 엔드포인트는 정상 동작**합니다.

## 실데이터 파이프라인

```
data/raw/산명_산코드.zip   ← 산림청 등산로 SHP를 여기에
  ↓  python tools/build_catalog.py        (pip install pyshp pyproj)
data/courses.json + data/mountains.json   ← catalog.py가 자동 로드 (없으면 목업 4코스)
data/control.json                          ← 입산통제 시즌 공고 수동 반영 → 해당 코스 danger 강제
data/mtweather_stations.json               ← 산악기상관측소 454곳 (기술문서에서 추출, 커밋됨)
```

- 변환 첫 실행 전 `--inspect`로 SHP 필드 구조 확인 권장. 좌표계는 EPSG:5186 가정.
- **검수 필수:** 변환 후 앱 지도에서 경로가 실제 등산로 위에 그려지는지 확인.
- 통제정보 출처: 국립공원공단 knps.or.kr / 산림청 hiking.kworks.co.kr (시즌마다 갱신).

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env        # 값 채우기(선택)
uvicorn main:app --reload --port 8000
# 문서: http://localhost:8000/docs
```

## 엔드포인트

| 메서드·경로 | 설명 | 로그인 |
|---|---|---|
| `GET /` | 헬스체크 | — |
| `GET /mountains?query=&lat=&lon=` | 코스 검색/근처 | — |
| `GET /courses/{id}` | 코스 상세(날씨·안전지수·일몰) | — |
| `GET /weather?lat=&lon=&summitAltitude=` | 해석형 날씨 | — |
| `GET /sunset?lat=&lon=&date=` | 일출/일몰 | — |
| `GET /safety/config` | 안전지수 튜닝값 | — |
| `POST /hikes` | 산행 기록 업로드 | ✅ |
| `GET /hikes` | 내 기록 목록 | ✅ |
| `GET/POST /favorites`, `DELETE /favorites/{id}` | 즐겨찾기 동기화 | ✅ |

## 배포 (Railway + Supabase)

### 1) Supabase
1. supabase.com에서 프로젝트 생성.
2. SQL Editor에 `schema.sql` 붙여넣고 실행(테이블 생성).
3. Authentication > Providers에서 **Apple** 활성화(서비스 ID·키 입력 — Apple Developer 필요).
4. `Settings > API`의 **JWT Secret** → `SUPABASE_JWT_SECRET`.
5. `Settings > Database`의 **Connection string(URI)** → `DATABASE_URL`.

### 2) Railway
1. railway.app에서 New Project → Deploy from GitHub repo(이 `hiking-server` 폴더).
2. `railway.json`이 있어 빌드/시작은 자동(`uvicorn main:app`).
3. Variables 탭에 `KMA_SERVICE_KEY`, `SUPABASE_JWT_SECRET`, `DATABASE_URL` 입력.
4. 배포되면 발급된 **https 도메인**을 앱 `SanDamAPI.baseURL`(prod)에 넣기.

### 3) 기상청 키
공공데이터포털(data.go.kr) → '기상청_단기예보 조회서비스' 활용신청 → 서비스키를 `KMA_SERVICE_KEY`에.

## 실데이터로 교체할 지점

- `kma_weather.py` — 이미 기상청 실연동. 산악기상관측망(mtweather) 추가 시 여기 보강.
- `catalog.py` — 산림청/국립공원 등산로 DB·GPX로 교체.
- `_course_sunset()` — KASI API로 바꾸거나 현재 NOAA 연산 유지.
