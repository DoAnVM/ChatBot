#!/bin/bash
set -e

echo "==> [1/2] Đang chạy ingest để build ChromaDB..."
python -m app.ingest

echo "==> [2/2] Khởi động FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
