"""
auth.py — Supabase 인증 토큰 검증.

앱이 Supabase 로그인으로 받은 access token(JWT)을 Authorization: Bearer로 보내면 검증한다.
  · 구형 프로젝트: HS256 (SUPABASE_JWT_SECRET으로 검증)
  · 신형 프로젝트: ES256/RS256 (JWT Signing Keys — 프로젝트 JWKS 공개키로 검증, SUPABASE_URL 필요)
토큰의 alg 헤더를 보고 자동 분기한다. 만료/서명오류를 구분해 401 사유를 명확히 내려준다.

환경변수:
  SUPABASE_JWT_SECRET = Settings > API > JWT Secret (HS256용)
  SUPABASE_URL        = https://<ref>.supabase.co   (ES256/RS256 JWKS용)
"""
import os
import logging
from fastapi import Header, HTTPException

import jwt  # PyJWT

log = logging.getLogger("uvicorn.error")   # uvicorn 터미널에 바로 보이는 로거


def _deny(status: int, detail: str):
    """401/503 사유를 터미널 로그에도 출력하고 예외를 던진다(디버깅 가시성)."""
    log.warning(f"🔒 인증 거부({status}): {detail}")
    raise HTTPException(status_code=status, detail=detail)

_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")

_jwks_client = None


def _jwks():
    global _jwks_client
    if _jwks_client is None and _URL:
        from jwt import PyJWKClient
        _jwks_client = PyJWKClient(f"{_URL}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def configured() -> bool:
    return bool(_SECRET or _URL)


def get_current_user(authorization: str = Header(default="")) -> str:
    """검증 성공 시 Supabase user id(sub) 반환. 실패 시 401/503."""
    if not configured():
        _deny(503, "인증 미설정: SUPABASE_JWT_SECRET 또는 SUPABASE_URL 필요")
    if not authorization.startswith("Bearer "):
        _deny(401, "인증 토큰이 없습니다 (Authorization 헤더 누락)")
    token = authorization.split(" ", 1)[1]

    alg = "?"
    try:
        alg = jwt.get_unverified_header(token).get("alg", "HS256")
        if alg == "HS256":
            if not _SECRET:
                _deny(503, "HS256 토큰인데 SUPABASE_JWT_SECRET이 없습니다")
            payload = jwt.decode(token, _SECRET, algorithms=["HS256"], audience="authenticated")
        else:
            client = _jwks()
            if client is None:
                _deny(503, f"{alg} 토큰인데 SUPABASE_URL이 없습니다(JWKS 필요)")
            key = client.get_signing_key_from_jwt(token).key
            payload = jwt.decode(token, key, algorithms=[alg], audience="authenticated")
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        # 가장 흔한 401: access token은 1시간 만료 → 앱이 refresh token으로 갱신해야 함
        _deny(401, "토큰 만료(1시간) — 앱이 자동 갱신하거나 재로그인 필요")
    except Exception as e:
        _deny(401, f"토큰 검증 실패: {type(e).__name__} — {str(e)[:100]} (alg={alg})")

    sub = payload.get("sub")
    if not sub:
        _deny(401, "토큰에 사용자 식별자가 없습니다")
    return sub
