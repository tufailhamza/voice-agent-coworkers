import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, TYPE_CHECKING
import asyncpg
from asyncpg import Pool

from ..config.config import Config
from ..utils.exceptions import DatabaseError

# Import from meeting folder - these methods are only used by meeting agent (which we removed DB from)
# Ryan doesn't call these methods, so this import won't affect ryan's functionality
try:
    from ..agents.meeting.models import Transcript, MeetingSummary
except ImportError:
    # Fallback for type hints only (shouldn't happen in normal operation)
    Transcript = None  # type: ignore
    MeetingSummary = None  # type: ignore

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Database manager with connection pooling and error handling"""

    def __init__(self, config: Config):
        self.config = config
        self.pool: Optional[Pool] = None
        self._is_closed = False
        self._lock = asyncio.Lock()

    async def init_pool(self) -> None:
        """Initialize database connection pool"""
        async with self._lock:
            if self.pool is not None:
                logger.warning("Database pool already initialized")
                return

            try:
                self.pool = await asyncpg.create_pool(
                    self.config.database.url,
                    min_size=self.config.database.min_connections,
                    max_size=self.config.database.max_connections,
                    command_timeout=self.config.database.timeout,
                )

                logger.info("✅ Database connection pool initialized successfully")

            except asyncpg.PostgresError as e:
                logger.error(f"❌ PostgreSQL error: {e}")
                raise DatabaseError(f"Failed to initialize database: {e}")
            except Exception as e:
                logger.error(f"❌ Unexpected database error: {e}")
                raise DatabaseError(f"Unexpected database error: {e}")

    @asynccontextmanager
    async def get_connection(self):
        """Context manager for database connections"""
        if self.pool is None:
            raise DatabaseError("Database pool not initialized")
        if self._is_closed:
            raise DatabaseError("Database pool is closed")

        conn = None
        try:
            conn = await self.pool.acquire()
            yield conn
        except asyncpg.PostgresError as e:
            logger.error(f"Database connection error: {e}")
            raise DatabaseError(f"Database operation failed: {e}")
        finally:
            if conn is not None:
                await self.pool.release(conn)

    async def save_transcript(self, transcript: "Transcript") -> bool:
        """Save transcript to database"""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO transcripts (room_id, session_id, speaker_name, participant_sid, text_content)
                    VALUES ($1, $2, $3, $4, $5)
                """,
                    transcript.room_id,
                    transcript.session_id,
                    transcript.speaker_name,
                    transcript.participant_sid,
                    transcript.text_content,
                )

            logger.info(f"💾 Saved transcript: {transcript.speaker_name}")
            return True

        except DatabaseError:
            raise
        except Exception as e:
            logger.error(f"❌ Error saving transcript: {e}")
            await self._save_transcript_backup(transcript)
            raise DatabaseError(f"Failed to save transcript: {e}")

    async def get_session_transcripts(
        self, room_id: str, session_id: str
    ) -> List["Transcript"]:
        """Get all transcripts for a session"""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, room_id, session_id, speaker_name, participant_sid, text_content, timestamp
                    FROM transcripts 
                    WHERE room_id = $1 AND session_id = $2
                    ORDER BY timestamp ASC
                """,
                    room_id,
                    session_id,
                )

                return [Transcript(**dict(row)) for row in rows]

        except DatabaseError:
            raise
        except Exception as e:
            logger.error(f"❌ Error fetching transcripts: {e}")
            raise DatabaseError(f"Failed to fetch transcripts: {e}")

    async def get_participant_transcripts(
        self, room_id: str, session_id: str, speaker_name: str
    ) -> List[Dict[str, Any]]:
        """Get all transcripts for a specific participant"""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT speaker_name, text_content, timestamp, participant_sid
                    FROM transcripts 
                    WHERE room_id = $1 AND session_id = $2 AND speaker_name = $3
                    ORDER BY timestamp ASC
                """,
                    room_id,
                    session_id,
                    speaker_name,
                )

                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Error fetching participant transcripts: {e}")
            return []

    async def save_summary(self, summary: "MeetingSummary") -> bool:
        """Save meeting summary to database"""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO meeting_summaries (room_id, session_id, summary_content, participants)
                    VALUES ($1, $2, $3, $4)
                """,
                    summary.room_id,
                    summary.session_id,
                    summary.summary_content,
                    summary.participants,
                )

            logger.info("📋 Saved meeting summary")
            return True

        except DatabaseError:
            raise
        except Exception as e:
            logger.error(f"❌ Error saving summary: {e}")
            raise DatabaseError(f"Failed to save summary: {e}")

    async def _save_transcript_backup(self, transcript: "Transcript") -> None:
        """Save transcript to backup file"""
        try:
            backup_file = (
                Path(self.config.transcriber.backup_directory)
                / f"transcript_{transcript.room_id}_{transcript.session_id}.jsonl"
            )

            backup_data = transcript.to_dict()
            backup_data["timestamp"] = datetime.now().isoformat()

            with open(backup_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(backup_data) + "\n")

            logger.info(f"💾 Saved transcript backup: {backup_file}")

        except Exception as e:
            logger.error(f"❌ Failed to save transcript backup: {e}")

    async def health_check(self) -> bool:
        """Check database health"""
        try:
            async with self.get_connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"❌ Database health check failed: {e}")
            return False

    async def close_pool(self) -> None:
        """Close database connection pool"""
        async with self._lock:
            if self.pool and not self._is_closed:
                await self.pool.close()
                self._is_closed = True
                logger.info("🔌 Database connection pool closed")
