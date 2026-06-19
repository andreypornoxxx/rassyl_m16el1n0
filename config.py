import os

BOT_TOKEN    = os.getenv("BOT_TOKEN")
API_ID       = int(os.getenv("API_ID", "0"))
API_HASH     = os.getenv("API_HASH", "")
SESSION_DIR  = os.getenv("SESSION_DIR", "sessions")  # папка для хранения сессий

# ID шоп-бота — берём отсюда список подписчиков
SHOP_DB      = os.getenv("SHOP_DB", "../shop_bot_v2/shop.db")  # локально
DATABASE_URL = os.getenv("DATABASE_URL")  # Railway PostgreSQL
