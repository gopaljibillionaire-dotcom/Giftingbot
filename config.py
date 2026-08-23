"""
Configuration module for Telegram Gifting & Digital Store Bot.
Uses Pydantic V2 BaseSettings with modern SettingsConfigDict.
"""

from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    # Bot Configuration
    BOT_TOKEN: str = "8788884009:AAEifV0e9MVaLtUzQD40uVoaO1WtxA1VUFs"
    BOT_USERNAME: str = "@Filetostreamrobot"
    OWNER_ID: int = 7952327997
    ADMIN_IDS: List[int] = Field(default_factory=lambda: [7952327997])
    SUPPORT_USERNAME: str = "@no_onefindme"
    TEST_MODE: bool = True

    # Database Configuration (MongoDB Atlas SRV URI)
    MONGO_URI: str = (
        "mongodb+srv://gopaljibillionaire_db_user:gopaljithegreat@cluster0.y1akz97.mongodb.net/?appName=Cluster0"
    )
    MONGO_DB_NAME: str = "telegram_store_bot"

    # Payment Gateway (OxaPay) Configuration
    OXAPAY_MERCHANT_API_KEY: str = "your_oxapay_merchant_key"
    OXAPAY_PAYOUT_API_KEY: str = "your_oxapay_payout_key"
    OXAPAY_CALLBACK_URL: str = "https://your-domain.com/oxapay/webhook"

    # Webhook / HTTP Server Configuration
    WEBHOOK_HOST: str = "0.0.0.0"
    WEBHOOK_PORT: int = 8080

    # UI Banner Configuration
    BANNER_WELCOME: Optional[str] = (
        "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe"
    )
    BANNER_PREMIUM: Optional[str] = (
        "https://images.unsplash.com/photo-1639762681485-074b7f938ba0"
    )
    BANNER_STARS: Optional[str] = (
        "https://images.unsplash.com/photo-1518709268805-4e9042af9f23"
    )
    BANNER_BOOSTS: Optional[str] = (
        "https://images.unsplash.com/photo-1550745165-9bc0b252726f"
    )
    BANNER_GIVEAWAY: Optional[str] = (
        "https://images.unsplash.com/photo-1513151233558-d860c5398176"
    )
    BANNER_WALLET: Optional[str] = (
        "https://images.unsplash.com/photo-1621416894569-0f39ed31d247"
    )

    # Modern Pydantic V2 Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Instantiate Global Config
config = Config()
