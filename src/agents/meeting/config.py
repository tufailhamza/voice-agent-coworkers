import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

from src.utils.openai_env import get_openai_api_key_primary

load_dotenv()


@dataclass
class AIConfig:
    openai_api_key: str
    deepgram_api_key: Optional[str] = None
    summary_model: str = "gpt-4.1"
    summary_temperature: float = 0.8


@dataclass
class TranscriberConfig:
    backup_directory: str = "backups"
    deepgram_model: str = "nova-3"
    language: str = "multi"


@dataclass
class Config:
    ai: AIConfig
    transcriber: TranscriberConfig
    debug: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        ai_config = AIConfig(
            openai_api_key=get_openai_api_key_primary(default=""),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY"),
            summary_model=os.getenv("SUMMARY_MODEL", "gpt-4.1"),
            summary_temperature=float(os.getenv("SUMMARY_TEMPERATURE", "0.8")),
        )

        transcriber_config = TranscriberConfig(
            backup_directory=os.getenv("BACKUP_DIRECTORY", "backups"),
            deepgram_model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
            language=os.getenv("TRANSCRIBER_LANGUAGE", "multi"),
        )

        return cls(
            ai=ai_config,
            transcriber=transcriber_config,
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
