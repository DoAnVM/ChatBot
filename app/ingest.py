import os
import sys

# Force utf-8 encoding for standard output to avoid UnicodeEncodeError on Windows
sys.stdout.reconfigure(encoding='utf-8')

import shutil
import time
import schedule
import pytz
from datetime import datetime
from sqlalchemy import create_engine, text
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv

load_dotenv()

def ingest_data():
    print(f"[{datetime.now()}] Bắt đầu quá trình cập nhật dữ liệu...")
    
    hotel_engine = create_engine(os.getenv("HOTEL_DB_URL"))
    room_engine = create_engine(os.getenv("ROOM_DB_URL"))
    review_engine = create_engine(os.getenv("REVIEW_DB_URL"))
    documents = []

    # Xóa DB cũ để tránh duplicate
    db_path = os.getenv("VECTOR_DB_PATH")
    if os.path.exists(db_path):
        # Xóa nội dung bên trong thay vì xóa cả thư mục
        # (rmtree trên Docker volume mount point sẽ bị lỗi Device or resource busy)
        for item in os.listdir(db_path):
            item_path = os.path.join(db_path, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        print(f"Đã xóa dữ liệu cũ trong {db_path}")

    # --- Lấy dữ liệu từ Hotel Service ---
    print("Đang quét dữ liệu từ Hotel Service...")
    hotel_name_map = {}
    with hotel_engine.connect() as conn:
        query = text("SELECT id, home_name, description, street, district, city, country FROM homes")
        hotels = conn.execute(query)

        amenity_query = text("""
            SELECT ha.home_id, a.name, a.category
            FROM home_amenities ha
            JOIN amenities a ON ha.amenity_id = a.id
            ORDER BY ha.home_id, a.category, a.name
        """)
        amenity_rows = conn.execute(amenity_query).fetchall()

        amenities_map = {}
        for row in amenity_rows:
            home_id = str(row.home_id)
            amenities_map.setdefault(home_id, []).append((row.name, row.category))

        for h in hotels:
            hotel_name_map[str(h.id)] = h.home_name
            full_address = f"{h.street}, {h.district}, {h.city}, {h.country}"

            amenities_list = amenities_map.get(str(h.id), [])
            if amenities_list:
                by_category = {}
                for name, category in amenities_list:
                    by_category.setdefault(category, []).append(name)
                amenity_parts = [f"{cat}: {', '.join(names)}" for cat, names in by_category.items()]
                amenity_content = f" Tiện ích: {'; '.join(amenity_parts)}."
            else:
                amenity_content = ""

            content = (
                f"Cơ sở lưu trú: {h.home_name}. "
                f"Địa chỉ: {full_address}. "
                f"Mô tả chi tiết: {h.description}."
                f"{amenity_content}"
            )
            documents.append(Document(page_content=content, metadata={"id": str(h.id), "source": "hotel_db"}))

    # --- Lấy dữ liệu từ Room Service ---
    print("Đang quét dữ liệu từ Room Service...")
    with room_engine.connect() as conn:
        room_query = text("""
            SELECT id, hotel_id, room_number, room_type, price_per_night, capacity, description
            FROM rooms
            WHERE is_active = true
        """)
        rooms = conn.execute(room_query).fetchall()

        room_amenity_query = text("""
            SELECT ra.room_id, ra.amenity_name, ra.description
            FROM room_amenities ra
            WHERE ra.is_active = true
            ORDER BY ra.room_id, ra.amenity_name
        """)
        room_amenity_rows = conn.execute(room_amenity_query).fetchall()

        room_amenities_map = {}
        for row in room_amenity_rows:
            room_id = str(row.room_id)
            room_amenities_map.setdefault(room_id, []).append((row.amenity_name, row.description))

        for r in rooms:
            hotel_name = hotel_name_map.get(str(r.hotel_id), f"Khách sạn ID {r.hotel_id}")
            room_amenities_list = room_amenities_map.get(str(r.id), [])
            room_amenity_str = f" Tiện ích phòng: {', '.join([n for n, d in room_amenities_list])}." if room_amenities_list else ""

            content = (
                f"Khách sạn: {hotel_name}. "
                f"Số phòng: {r.room_number}. "
                f"Loại phòng: {r.room_type}. "
                f"Giá mỗi đêm: {r.price_per_night} VND. "
                f"Sức chứa: {r.capacity} người. "
                f"Mô tả phòng: {r.description}."
                f"{room_amenity_str}"
            )
            documents.append(Document(
                page_content=content,
                metadata={"room_id": str(r.id), "hotel_id": str(r.hotel_id), "source": "room_db"}
            ))

    # --- Lấy dữ liệu từ Review Service ---
    print("Đang quét dữ liệu bình luận/đánh giá từ Review Service...")
    with review_engine.connect() as conn:
        review_query = text("""
            SELECT id, customer_id, hotel_id, comment, created_at, star
            FROM comment
            ORDER BY hotel_id, created_at DESC
        """)
        reviews = conn.execute(review_query).fetchall()

        # Nhóm review theo hotel_id, chỉ lấy tối đa 10 review mới nhất mỗi khách sạn
        reviews_by_hotel: dict = {}
        for rv in reviews:
            hotel_id_str = str(rv.hotel_id)
            reviews_by_hotel.setdefault(hotel_id_str, []).append(rv)

        for hotel_id_str, hotel_reviews in reviews_by_hotel.items():
            top_reviews = hotel_reviews[:10]  # Mời nhất (ORDER BY DESC)
            hotel_name = hotel_name_map.get(hotel_id_str, f"Khách sạn ID {hotel_id_str}")

            star_values = [rv.star for rv in top_reviews if rv.star is not None]
            avg_star = sum(star_values) / len(star_values) if star_values else None
            avg_star_str = f"{avg_star:.1f}/5" if avg_star is not None else "chưa có"

            review_lines = []
            for rv in top_reviews:
                date_str = rv.created_at.strftime("%d/%m/%Y") if rv.created_at else "?"
                star_str = f"{rv.star} sao" if rv.star is not None else "chưa chấm"
                review_lines.append(
                    f"- [{date_str}] {star_str}: {rv.comment}"
                )

            content = (
                f"Bình luận khách hàng về khách sạn: {hotel_name}. "
                f"Điểm đánh giá trung bình: {avg_star_str} (dựa trên {len(star_values)} đánh giá). "
                f"Các bình luận gần nhất:\n" + "\n".join(review_lines)
            )
            documents.append(Document(
                page_content=content,
                metadata={"hotel_id": hotel_id_str, "source": "review_db"}
            ))

    # --- Lưu vào ChromaDB ---
    if documents:
        print(f"Đang tạo vector bằng Gemini cho {len(documents)} bản ghi...")
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=os.getenv("GOOGLE_API_KEY")
        )
        vector_db = Chroma.from_documents(
            documents=documents,
            embedding=embeddings,
            persist_directory=os.getenv("VECTOR_DB_PATH")
        )
        print(f"Hoàn tất! Dữ liệu đã cập nhật vào lúc {datetime.now()}")
    else:
        print("Không tìm thấy dữ liệu để ingest.")

# --- Phần cấu hình Schedule ---
def run_scheduler():
    # Định nghĩa múi giờ Việt Nam
    tz = pytz.timezone('Asia/Ho_Chi_Minh')

    schedule.every().day.at("00:00", tz).do(ingest_data)
    
    print(f"Service khởi chạy thành công. Đã lên lịch 12h đêm hàng ngày (Múi giờ: Asia/Ho_Chi_Minh)")

    while True:
        # Kiểm tra xem có task nào đến hạn chạy không
        schedule.run_pending()
        time.sleep(60) # Nghỉ 1 phút mỗi lần kiểm tra

if __name__ == "__main__":
    ingest_data()