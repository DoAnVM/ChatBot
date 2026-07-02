# ChatBot

FastAPI chatbot sử dụng Google Gemini + LangChain + ChromaDB, hỗ trợ hội thoại theo session và streaming SSE.

---

## 📋 Yêu cầu hệ thống

- **Python** 3.11+
- **pip** (hoặc dùng virtual environment)
- Kết nối tới PostgreSQL (hotel_service & room_service)
- Google API Key (Gemini)

---

## ⚙️ Cài đặt & Chạy local

### 1. Tạo và kích hoạt virtual environment

```bash
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Windows (CMD)
python -m venv .venv
.\.venv\Scripts\activate.bat

# Linux / macOS
python -m venv .venv
source .venv/bin/activate
```

### 2. Cài dependencies

```bash
pip install -r requirements.txt
```

### 3. Cấu hình biến môi trường

Tạo file `.env` từ file mẫu:

```bash
copy .env.example .env   # Windows
cp .env.example .env     # Linux / macOS
```

Mở `.env` và cấu hình các giá trị. Tùy thuộc vào nơi bạn đặt Database, hãy chọn 1 trong 2 cấu hình dưới đây:

**Cách 1: Dùng GCP Cloud SQL (Khuyến nghị)**
```env
GOOGLE_API_KEY=your_google_api_key_here
HOTEL_DB_URL=postgresql://postgres:18052004@34.21.247.183:5432/hotel_service
ROOM_DB_URL=postgresql://postgres:18052004@34.21.247.183:5432/room_service
REVIEW_DB_URL=postgresql://postgres:18052004@34.21.247.183:5432/review_service
VECTOR_DB_PATH=D:/20252/datnlocal/ChatBot/app/data/chroma_db
```

**Cách 2: Dùng Local PostgreSQL (Database chạy trên máy của bạn)**
```env
GOOGLE_API_KEY=your_google_api_key_here
HOTEL_DB_URL=postgresql://postgres:your_password@localhost:5432/hotel_service
ROOM_DB_URL=postgresql://postgres:your_password@localhost:5432/room_service
REVIEW_DB_URL=postgresql://postgres:your_password@localhost:5432/review_service
VECTOR_DB_PATH=D:/20252/datnlocal/ChatBot/app/data/chroma_db
```

> ⚠️ **Lưu ý:** `VECTOR_DB_PATH` phải là đường dẫn tuyệt đối tới thư mục lưu ChromaDB.

### 4. Ingest dữ liệu vào ChromaDB (chỉ cần chạy 1 lần hoặc khi data thay đổi)

```bash
python -m app.ingest
```

### 5. Khởi động server

```bash
# Chạy trực tiếp (cổng mặc định 8000)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Hoặc chạy qua Python
python -m app.main
```

Server sẽ chạy tại: **http://localhost:8000**

---

## 🐳 Chạy bằng Docker

### 1. Build image

Mở terminal tại thư mục `ChatBot` và chạy lệnh:

```bash
docker build -t chatbot .
```

### 2. Cấu hình kết nối Database (Quan trọng)

Tùy vào việc bạn dùng Database ở đâu, hãy chỉnh sửa file `.env` cho phù hợp:

**Trường hợp 1: Dùng GCP Cloud SQL**
Nếu dùng database trên Google Cloud, chỉ việc dùng IP thật:
```env
GOOGLE_API_KEY=your_google_api_key_here
HOTEL_DB_URL=postgresql://postgres:18052004@34.21.247.183:5432/hotel_service
ROOM_DB_URL=postgresql://postgres:18052004@34.21.247.183:5432/room_service
REVIEW_DB_URL=postgresql://postgres:18052004@34.21.247.183:5432/review_service
VECTOR_DB_PATH=/app/data/chroma_db
```

**Trường hợp 2: Dùng Local PostgreSQL (Phòng hỏng GCP)**
Nếu các dịch vụ PostgreSQL của bạn đang chạy ở local (đã restore db_backup trên máy vật lý), bạn **không được dùng `localhost`** vì `localhost` trong Docker là của container. Bạn phải dùng `host.docker.internal`:

```env
GOOGLE_API_KEY=your_google_api_key_here
HOTEL_DB_URL=postgresql://postgres:your_password@host.docker.internal:5432/hotel_service
ROOM_DB_URL=postgresql://postgres:your_password@host.docker.internal:5432/room_service
REVIEW_DB_URL=postgresql://postgres:your_password@host.docker.internal:5432/review_service
VECTOR_DB_PATH=/app/data/chroma_db
```

### 3. Chạy container

Nên mount một volume ra ngoài máy host để dữ liệu ChromaDB không bị mất khi khởi động lại container:

```bash
docker run -d \
  --name chatbot \
  -p 8080:8080 \
  --env-file .env \
  -v ./chroma_data:/app/data/chroma_db \
  huststay-chatbot
```

> **Lưu ý:** 
> - Container sẽ tự động chạy script `ingest.py` để lấy dữ liệu từ các DB PostgreSQL và tạo vector embedding lưu vào thư mục `chroma_data` trước khi khởi động server.
> - Tham số `-v ./chroma_data:/app/data/chroma_db` giúp lưu trữ dữ liệu ChromaDB ra thư mục `chroma_data` ngay trên máy thật của bạn.

### 4. Kiểm tra server

Server sẽ chạy tại: **http://localhost:8080**

Để xem log quá trình ingest data và khởi động:
```bash
docker logs -f chatbot
```

---

## 🔌 API Endpoints

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/api/v1/chat` | Chat thông thường (non-streaming) |
| `POST` | `/api/v1/chat/stream` | Chat streaming (SSE) |
| `DELETE` | `/api/v1/chat/{session_id}` | Xóa lịch sử hội thoại theo session |
| `GET` | `/health` | Kiểm tra trạng thái server |

### Ví dụ request

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Xin chào!", "session_id": "user_123"}'
```

### Docs tự động (Swagger UI)

Truy cập: **http://localhost:8000/docs**

---

## 📁 Cấu trúc thư mục

```
ChatBot/
├── app/
│   ├── main.py          # FastAPI app, routes
│   ├── ingest.py        # Script nạp dữ liệu vào ChromaDB
│   ├── api/             # Định nghĩa router
│   ├── core/            # Cấu hình, settings
│   ├── database/        # Kết nối PostgreSQL
│   ├── service/         # LLM service (Gemini + LangChain)
│   └── data/            # Dữ liệu thô & ChromaDB storage
├── .env                 # Biến môi trường (không commit)
├── .env.example         # Mẫu biến môi trường
├── requirements.txt     # Python dependencies
├── Dockerfile           # Docker build
├── entrypoint.sh        # Script khởi động trong Docker
└── README.md
```