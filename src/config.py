"""
config.py — centralised settings loaded from environment / .env file.

Java equivalent: a @ConfigurationProperties class.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # AWS
    aws_region: str = "ap-south-2"

    # DynamoDB tables
    session_table: str = "ChatSessions"
    user_table: str = "Users"
    department_table: str = "Departments"
    doctor_table: str = "Doctors"
    timeslot_table: str = "TimeSlots"
    appointment_table: str = "Appointments"
    feedback_table: str = "Feedback"
    admins_table: str = "Admins"

    # Telegram Bot
    telegram_bot_token: str = ""          # from @BotFather

    # Calendar booking page URL (your public ECS URL after deploy)
    booking_calendar_url: str = "http://localhost:3000/book"

    # App
    port: int = 3000
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


# Singleton — import everywhere:  from src.config import settings
settings = Settings()
