# 산담 서버 — Railway는 Nixpacks로 자동 빌드되므로 이 파일은 선택입니다.
# (다른 호스팅/로컬 컨테이너 실행용)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Railway/PaaS가 주입하는 $PORT 사용, 없으면 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
