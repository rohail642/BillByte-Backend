from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    ZOMATO_WEBHOOK_SECRET: str = ""
    SWIGGY_WEBHOOK_SECRET: str = ""
    APP_NAME: str = "BillByte"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost/billbyte"
    SECRET_KEY: str  # required — must be set via SECRET_KEY env variable or in .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    ALLOWED_ORIGINS: str = "http://localhost:5500,http://127.0.0.1:5500"

    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    @property
    def origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = ".env"


settings = Settings()