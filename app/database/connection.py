from langchain_community.utilities import SQLDatabase
import os
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    hotel_db_url = os.getenv("HOTEL_DB_URL")
    room_db_url = os.getenv("ROOM_DB_URL")
    review_db_url = os.getenv("REVIEW_DB_URL")
    # Chúng ta chỉ định các bảng liên quan từ schema bạn gửi để tránh LLM bị loạn
    hotel_db = SQLDatabase.from_uri(
        hotel_db_url,
        include_tables=['homes', 'home_image', 'amenities', 'home_amenities']
    )
    room_db = SQLDatabase.from_uri(
        room_db_url,
        include_tables=['rooms', 'room_amenities']
    )
    review_db = SQLDatabase.from_uri(
        review_db_url,
        include_tables=['comment']
    )
    return hotel_db, room_db, review_db