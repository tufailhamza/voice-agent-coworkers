import os
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


@dataclass
class DatabaseConfig:
    url: str
    max_connections: int = 10
    min_connections: int = 2
    timeout: int = 30


@dataclass
class AIConfig:
    anthropic_api_key: str
    openai_api_key: Optional[str] = None
    deepgram_api_key: Optional[str] = None
    summary_model: str = "claude-sonnet-4-20250514"
    summary_temperature: float = 0.8


@dataclass
class TranscriberConfig:
    summary_trigger_phrases: List[str]
    backup_directory: str = "backups"
    deepgram_model: str = "nova-3"
    language: str = "multi"


@dataclass
class Config:
    database: DatabaseConfig
    ai: AIConfig
    transcriber: TranscriberConfig
    debug: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        database_config = DatabaseConfig(
            url=os.getenv("DATABASE_URL", ""),
            max_connections=int(os.getenv("MAX_DB_CONNECTIONS", "10")),
            min_connections=int(os.getenv("MIN_DB_CONNECTIONS", "2")),
            timeout=int(os.getenv("DB_TIMEOUT", "30")),
        )

        ai_config = AIConfig(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY"),
            summary_model=os.getenv("SUMMARY_MODEL", "claude-sonnet-4-20250514"),
            summary_temperature=float(os.getenv("SUMMARY_TEMPERATURE", "0.8")),
        )

        transcriber_config = TranscriberConfig(
            summary_trigger_phrases=[
                "end meeting",
                "stop recording",
                "summarize this",
                "generate summary",
                "wrap up",
                "conclude meeting",
            ],
            backup_directory=os.getenv("BACKUP_DIRECTORY", "backups"),
            deepgram_model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
            language=os.getenv("TRANSCRIBER_LANGUAGE", "multi"),
        )

        return cls(
            database=database_config,
            ai=ai_config,
            transcriber=transcriber_config,
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
