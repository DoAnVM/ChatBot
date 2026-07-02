import os
import re
import hashlib
from dotenv import load_dotenv
from functools import lru_cache
from typing import Generator

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

load_dotenv()

# System prompt cho chatbot khách sạn
SYSTEM_PROMPT = """Bạn là trợ lý tư vấn khách sạn thân thiện và chuyên nghiệp của hệ thống TravelStay.
Nhiệm vụ của bạn là giúp khách hàng tìm kiếm và tư vấn về các cơ sở lưu trú, phòng nghỉ phù hợp.

QUY TẮC QUAN TRỌNG:
1. BẮT BUỘC phải dựa vào THÔNG TIN NGỮ CẢNH bên dưới để trả lời.
2. Nếu khách hỏi "có những khách sạn nào", hãy liệt kê TẤT CẢ các khách sạn có trong ngữ cảnh, dù chỉ có 1 hay 2 cái. Tuyệt đối không nói là "không có danh sách cụ thể" nếu trong ngữ cảnh đã có tên khách sạn.
3. Nếu ngữ cảnh thực sự trống hoặc không liên quan, mới được xin lỗi và khuyên liên hệ lễ tân.
4. KHÔNG SỬ DỤNG Markdown (không dùng **, *, _, #). Trả về văn bản thuần túy (plain text) thân thiện, dễ đọc.

QUY TẮC VỀ ĐÁNH GIÁ & BÌNH LUẬN:
5. Khi khách hỏi về đánh giá, nhận xét, số sao, chất lượng của một khách sạn, hãy tổng hợp từ dữ liệu bình luận trong ngữ cảnh.
6. Nêu rõ điểm trung bình (ví dụ: "4.2/5 sao"), số lượng đánh giá, và trích dẫn một vài nhận xét tiêu biểu của khách hàng.
7. Nếu có cả đánh giá tốt lẫn chưa tốt, hãy trình bày cân bằng và khách quan để khách hàng tự quyết định.
8. Khi gợi ý khách sạn, ưu tiên giới thiệu những nơi có điểm đánh giá cao và nhiều phản hồi tích cực.
9. Không bịa ra đánh giá nếu ngữ cảnh không có thông tin bình luận của khách sạn đó.

THÔNG TIN NGỮ CẢNH (kết quả tìm kiếm từ cơ sở dữ liệu):
---
{context}
---

Hãy trả lời bằng tiếng Việt, thân thiện, tự nhiên và hữu ích."""

# Cache context tìm kiếm: tránh gọi ChromaDB + Gemini Embedding nhiều lần
# cho cùng một câu hỏi (maxsize=128 query gần nhất)
_context_cache: dict = {}
_MAX_CACHE = 128

def _clean_markdown(text: str) -> str:
    text = re.sub(r'[*_]{1,2}', '', text)
    text = re.sub(r'#+\s+', '', text)
    return text.strip()


class ChatbotService:
    def __init__(self):
        api_key = os.getenv("GOOGLE_API_KEY")

        # 1. LLM - streaming enabled
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-lite",   # Nhanh hơn preview, stable hơn
            temperature=0.3,
            google_api_key=api_key,
            streaming=True                   # Bật streaming
        )

        # 2. Embeddings
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=api_key
        )

        # 3. ChromaDB
        self.vector_db = Chroma(
            persist_directory=os.getenv("VECTOR_DB_PATH"),
            embedding_function=self.embeddings
        )

        # 4. Prompt template (khởi tạo 1 lần, tái sử dụng)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}")
        ])
        self.chain = self.prompt | self.llm

    def _retrieve_context(self, query: str, k: int = 5) -> str:
        """
        Tìm kiếm ChromaDB với cache:
        - Lần đầu: gọi Gemini Embedding + ChromaDB (~300-500ms)
        - Lần sau cùng câu hỏi: trả về ngay từ cache (~0ms)
        """
        # Dùng hash để làm cache key (tránh lưu string dài)
        cache_key = hashlib.md5(query.lower().strip().encode()).hexdigest()

        if cache_key in _context_cache:
            print(f"[CACHE HIT] query='{query[:40]}...'")
            return _context_cache[cache_key]

        docs = self.vector_db.similarity_search(query, k=k)
        context = "\n---\n".join([doc.page_content for doc in docs]) if docs else "Không tìm thấy thông tin liên quan."

        # Lưu vào cache (giới hạn kích thước)
        if len(_context_cache) >= _MAX_CACHE:
            # Xóa 1 entry cũ nhất (FIFO đơn giản)
            oldest_key = next(iter(_context_cache))
            del _context_cache[oldest_key]
        _context_cache[cache_key] = context

        print(f"[CACHE MISS] query='{query[:40]}' → {len(docs)} docs")
        return context

    def _build_history(self, chat_history: list) -> list:
        """Chuyển đổi chat_history sang LangChain Messages, chỉ giữ 5 lượt gần nhất."""
        recent = chat_history[-5:]   # Giảm từ 10 → 5 lượt: prompt ngắn hơn = nhanh hơn
        messages = []
        for turn in recent:
            messages.append(HumanMessage(content=turn["human"]))
            messages.append(AIMessage(content=turn["ai"]))
        return messages

    # ------------------------------------------------------------------
    # Non-streaming: dùng cho các client không hỗ trợ SSE
    # ------------------------------------------------------------------
    def chat(self, user_message: str, chat_history: list) -> str:
        context = self._retrieve_context(user_message)
        print(f"\n[DEBUG] User: {user_message}")
        print(f"[DEBUG] Context ({len(context)} chars):\n{context[:300]}...\n")

        response = self.chain.invoke({
            "context": context,
            "history": self._build_history(chat_history),
            "question": user_message
        })

        content = response.content
        if isinstance(content, list):
            content = "".join(
                part["text"] for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return _clean_markdown(content)

    # ------------------------------------------------------------------
    # Streaming: trả về từng chunk ngay khi Gemini sinh ra
    # ------------------------------------------------------------------
    def chat_stream(self, user_message: str, chat_history: list) -> Generator[str, None, None]:
        """
        Generator yield từng đoạn text ngay khi LLM trả về.
        User thấy chữ xuất hiện gần như ngay lập tức.
        """
        context = self._retrieve_context(user_message)
        print(f"\n[STREAM] User: {user_message}")

        for chunk in self.chain.stream({
            "context": context,
            "history": self._build_history(chat_history),
            "question": user_message
        }):
            content = chunk.content
            if isinstance(content, list):
                content = "".join(
                    part["text"] for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if content:
                # Loại bỏ markdown từng chunk
                cleaned = re.sub(r'[*_]{1,2}', '', content)
                cleaned = re.sub(r'#+\s+', '', cleaned)
                if cleaned:
                    yield cleaned
