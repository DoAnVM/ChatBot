import json
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from app.service.llm_service import ChatbotService
import uvicorn

app = FastAPI(title="HustStay AI Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Khởi tạo một instance chatbot duy nhất (thread-safe vì chỉ đọc model)
chatbot = ChatbotService()

# Lưu lịch sử hội thoại theo session_id
session_histories: dict = {}
# Lock theo session để tránh race condition khi 2 request cùng session ghi đồng thời
session_locks: dict = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in session_locks:
        session_locks[session_id] = asyncio.Lock()
    return session_locks[session_id]


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default_user"

class ChatResponse(BaseModel):
    reply: str
    session_id: str


# ----------------------------------------------------------------
# POST /api/v1/chat  ← Non-streaming, tương thích với frontend cũ
# ----------------------------------------------------------------
@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Xử lý đồng thời nhiều request: chatbot.chat() là blocking sync,
    được đẩy vào thread pool để không block event loop.
    Mỗi request chạy trong thread riêng biệt → nhiều user cùng lúc OK.
    """
    lock = _get_session_lock(request.session_id)

    try:
        async with lock:
            history = session_histories.setdefault(request.session_id, [])
            # ⚡ run_in_threadpool: chạy hàm sync trong thread pool
            # → Event loop FREE để nhận request khác trong lúc chờ Gemini
            history_snapshot = list(history)  # copy để thread an toàn

        # Chạy blocking call NGOÀI lock (không block session khác)
        reply = await run_in_threadpool(
            chatbot.chat,
            request.message,
            history_snapshot
        )

        async with lock:
            history = session_histories.setdefault(request.session_id, [])
            history.append({"human": request.message, "ai": reply})
            if len(history) > 5:
                history.pop(0)

        return ChatResponse(reply=reply, session_id=request.session_id)

    except Exception as e:
        error_msg = str(e)
        print(f"--- LOG ERROR ---: {error_msg}")
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            raise HTTPException(status_code=429, detail="Hệ thống đang bận, vui lòng thử lại sau vài giây.")
        raise HTTPException(status_code=500, detail=f"Bot error: {error_msg}")


# ----------------------------------------------------------------
# POST /api/v1/chat/stream  ← Streaming SSE
# ----------------------------------------------------------------
@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Streaming SSE — trả về từng chunk ngay khi LLM sinh ra.
    Generator sync được chạy trong thread pool bởi StreamingResponse.
    Mỗi request stream chạy trong thread riêng → không block nhau.
    """
    lock = _get_session_lock(request.session_id)

    async with lock:
        history = session_histories.setdefault(request.session_id, [])
        history_snapshot = list(history)

    full_reply_parts = []

    async def async_generate():
        try:
            # Chạy generator sync trong thread pool, yield từng chunk
            # asyncio.to_thread (Python 3.9+): thread an toàn hơn run_in_executor
            loop = asyncio.get_event_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def producer():
                """Chạy trong thread: đẩy chunk vào queue"""
                try:
                    for chunk in chatbot.chat_stream(request.message, history_snapshot):
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
                except Exception as e:
                    loop.call_soon_threadsafe(queue.put_nowait, Exception(str(e)))
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

            # Chạy producer trong thread pool
            loop.run_in_executor(None, producer)

            # Consumer: đọc queue trong event loop
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    err = json.dumps({"error": str(item), "done": True}, ensure_ascii=False)
                    yield f"data: {err}\n\n"
                    return

                full_reply_parts.append(item)
                data = json.dumps({"chunk": item, "done": False}, ensure_ascii=False)
                yield f"data: {data}\n\n"

            # Lưu history sau khi stream xong
            full_reply = "".join(full_reply_parts)
            async with lock:
                h = session_histories.setdefault(request.session_id, [])
                h.append({"human": request.message, "ai": full_reply})
                if len(h) > 5:
                    h.pop(0)

            yield f"data: {json.dumps({'chunk': '', 'done': True})}\n\n"

        except Exception as e:
            err = json.dumps({"error": str(e), "done": True}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        async_generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.delete("/api/v1/chat/{session_id}")
async def clear_history(session_id: str):
    session_histories.pop(session_id, None)
    session_locks.pop(session_id, None)
    return {"message": f"Đã xóa lịch sử của session '{session_id}'"}


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(session_histories)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)