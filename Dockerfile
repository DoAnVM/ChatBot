# ============================================================
# Stage 1: Builder - cài đặt dependencies
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Cài các system dependencies cần thiết (psycopg2, onnxruntime, v.v.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy và cài requirements trước (tận dụng Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 2: Runtime
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# Cài runtime system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy packages đã cài từ builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source code
COPY app/ ./app/

# Tạo thư mục lưu ChromaDB (sẽ được tạo khi chạy ingest)
RUN mkdir -p /app/data/chroma_db

# Sử dụng PORT env var (mặc định 8080)
ENV PORT=8080
ENV VECTOR_DB_PATH=/app/data/chroma_db
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1

# Script khởi động: chạy ingest rồi mới start server
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8080

CMD ["./entrypoint.sh"]
