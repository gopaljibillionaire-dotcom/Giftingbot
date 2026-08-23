import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8788884009:AAEifV0e9MVaLtUzQD40uVoaO1WtxA1VUFs")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "@Filetostreamrobot")
    DB_PATH: str = os.getenv("DB_PATH", "bot_database.db")
    
    OXAPAY_MERCHANT_API_KEY: str = os.getenv("OXAPAY_MERCHANT_API_KEY", "")
    OXAPAY_PAYOUT_API_KEY: str = os.getenv("OXAPAY_PAYOUT_API_KEY", "")
    OXAPAY_CALLBACK_URL: str = os.getenv("OXAPAY_CALLBACK_URL", "https://yourdomain.com/oxapay/webhook")
    
    OWNER_ID: int = int(os.getenv("OWNER_ID", "7952327997"))
    ADMIN_IDS: list[int] = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    
    SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "Support")
    WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))
    TEST_MODE: bool = os.getenv("TEST_MODE", "false").lower() == "true"

    class Config:
        env_file = ".env"

settings = Settings()
