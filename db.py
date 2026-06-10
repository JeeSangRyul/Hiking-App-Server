"""
db.py — Supabase(Postgres) 연결 풀.

환경변수:
  DATABASE_URL = Supabase Settings > Database > Connection string (URI, asyncpg 호환)
                 예: postgresql://postgres:[PW]@db.<ref>.supabase.co:5432/postgres

DATABASE_URL이 없으면 서버는 정상 부팅하되, 기록/즐겨찾기 API만 503을 반환합니다.
(공개 엔드포인트 — 검색·날씨·일몰 — 는 DB 없이도 동작)
"""
import os
from typing import Optional

import asyncpg
from fastapi import HTTPException

_POOL: Optional[asyncpg.Pool] = None


def configured() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


async def pool() -> asyncpg.Pool:
    """연결 풀. 실패 시 500 침묵 대신 명확한 503 + 원인 메시지를 던진다.
    (DATABASE_URL 호스트 오타/리전 불일치를 바로 알 수 있게 — 2026-06-10 디버깅 교훈)"""
    global _POOL
    if _POOL is None:
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            raise HTTPException(status_code=503, detail="DB 미설정: DATABASE_URL 필요")
        try:
            # statement_cache_size=0: pgbouncer(transaction pooler) 전환 시에도 안전
            _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=5,
                                              statement_cache_size=0, timeout=10)
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"DB 연결 실패: {type(e).__name__} — DATABASE_URL의 호스트/리전을 확인하세요 "
                       f"(Supabase 대시보드 Connect → Session pooler URI). 원인: {str(e)[:120]}")
    return _POOL
