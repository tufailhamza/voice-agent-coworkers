from collections import defaultdict
from datetime import datetime
from src.utils.logger import Logger
import asyncio
import json
import uuid
from src.database.manager import DatabaseManager
from src.config.config import Config

logging = Logger(__name__)


class RyanRecallManager:
    """Manages in-memory storage of user queries and AI responses for search functionality for a specific user."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.search_history = []  # List of query:response pairs for this user only
        self.query_frequency = defaultdict(
            int
        )  # Frequency of specific queries for this user only

        # Database and content storage
        self.db_manager = None
        self.user_conversation_history = []
        self.content_loaded = False
        self._db_fetch_task = None

    def start_background_db_fetch(self):
        """Start background database fetch without blocking"""
        if self._db_fetch_task is None:
            self._db_fetch_task = asyncio.create_task(
                self._fetch_user_conversation_history(), name="background_db_fetch"
            )
            logging.info(
                f"🚀 Started background ryan database fetch for user {self.user_id}"
            )

    async def _fetch_user_conversation_history(self):
        """Fetch user conversation history from database in background"""
        try:
            config = Config.from_env()
            self.db_manager = DatabaseManager(config)
            await self.db_manager.init_pool()

            async with self.db_manager.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, timestamp, user_query, ai_response, created_at
                    FROM ryan_search_history 
                    WHERE user_id = $1 
                    ORDER BY created_at DESC
                """,
                    self.user_id,
                )

                for row in rows:
                    record = dict(row)
                    if not record.get("id"):
                        record["id"] = str(uuid.uuid4())
                    if not record.get("timestamp"):
                        record["timestamp"] = datetime.now().isoformat()
                    self.search_history.append(record)

                    user_query = record.get("user_query", "")
                    if user_query:
                        self.query_frequency[user_query.lower().strip()] += 1

                self.content_loaded = True

                logging.info(
                    f"✅ Loaded {len(self.search_history)} conversation records for user {self.user_id} {self.search_history}"
                )

        except Exception as e:
            logging.error(f"❌ Failed to fetch conversation history: {e}")
            self.search_history = []
            self.content_loaded = True

        finally:
            if self.db_manager:
                await self.db_manager.close_pool()

    def start_background_db_save(self):
        """Start background database save without blocking exit"""
        if hasattr(self, "_db_save_task") and self._db_save_task is not None:
            return  # Already saving

        self._db_save_task = asyncio.create_task(
            self._save_user_conversation_history(), name="background_db_save"
        )
        logging.info(f"💾 Started background database save for user {self.user_id}")

    async def _save_user_conversation_history(self):
        """Save user conversation history to database in background"""
        try:
            config = Config.from_env()
            self.db_manager = DatabaseManager(config)
            await self.db_manager.init_pool()

            all_records = self.search_history

            if not all_records:
                logging.info(f"📝 No records to save for user {self.user_id}")
                return

            async with self.db_manager.get_connection() as conn:
                for record in all_records:
                    record_id = record.get("id") or str(uuid.uuid4())
                    record_timestamp = record.get("timestamp")
                    if record_timestamp:
                        if isinstance(record_timestamp, str):
                            try:
                                record_timestamp = datetime.fromisoformat(
                                    record_timestamp
                                )
                            except ValueError:
                                record_timestamp = datetime.now()
                    else:
                        record_timestamp = datetime.now()

                    await conn.execute(
                        """
                        INSERT INTO ryan_search_history (id, user_id, timestamp, user_query, ai_response)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (id) DO NOTHING
                    """,
                        record_id,
                        self.user_id,
                        record_timestamp,
                        record.get("user_query", ""),
                        record.get("ai_response", ""),
                    )

                logging.info(
                    f"💾 Attempted to insert {len(all_records)} records with conflict handling for user {self.user_id}"
                )

        except Exception as e:
            logging.error(f"❌ Failed to save conversation history: {e}")

        finally:
            if self.db_manager:
                await self.db_manager.close_pool()

    async def wait_for_save_complete(self, timeout: float = 10.0):
        """Wait for database save to complete (useful during shutdown)"""
        if not hasattr(self, "_db_save_task") or self._db_save_task is None:
            return

        try:
            await asyncio.wait_for(self._wait_for_save_complete(), timeout=timeout)
        except asyncio.TimeoutError:
            logging.warning(f"⚠️ Database save timed out after {timeout}s")

    async def _wait_for_save_complete(self):
        """Internal method to wait for save completion"""
        while not self._db_save_task.done():
            await asyncio.sleep(0.1)

    def add_search_record(self, user_query: str, ai_response: str):
        """Add a new search record to the history for this specific user."""
        record = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "user_query": user_query,
            "ai_response": ai_response,
        }
        self.search_history.append(record)

        self.query_frequency[user_query.lower().strip()] += 1
        logging.info(
            f"Added search record for user {self.user_id}: {user_query[:50]}..."
        )

    def get_conversation_history(self, limit: int = 10):
        """Get search history for this user"""

        # Sort records by timestamp, handling both datetime and string types
        def get_sortable_timestamp(record):
            timestamp = record.get("timestamp")
            if isinstance(timestamp, datetime):
                return timestamp
            elif isinstance(timestamp, str):
                try:
                    return datetime.fromisoformat(timestamp)
                except ValueError:
                    return datetime.min
            else:
                return datetime.min

        sorted_records = sorted(
            self.search_history, key=get_sortable_timestamp, reverse=True
        )[:limit]

        clean_records = []
        for record in sorted_records:
            clean_record = {
                "query": record.get("user_query", ""),
                "ai_response": record.get("ai_response", ""),
            }
            clean_records.append(clean_record)

        return clean_records

    def search_by_query(self, search_term: str):
        """Search through stored queries and responses for this specific user only."""
        results = []
        search_term_lower = search_term.lower()

        matching_records = []
        for record in self.search_history:
            if search_term_lower in record["user_query"].lower():
                matching_records.append(record)

        def get_sortable_timestamp(record):
            timestamp = record.get("timestamp")
            if isinstance(timestamp, datetime):
                return timestamp
            elif isinstance(timestamp, str):
                try:
                    return datetime.fromisoformat(timestamp)
                except ValueError:
                    return datetime.min
            else:
                return datetime.min

        sorted_records = sorted(
            matching_records, key=get_sortable_timestamp, reverse=True
        )

        for record in sorted_records:
            clean_record = {
                "query": record.get("user_query", ""),
                "ai_response": record.get("ai_response", ""),
            }
            results.append(clean_record)

        return results

    def get_statistics(self):
        """Get statistical information about searches for this specific user only."""
        if not self.search_history:
            return {"message": f"No search history available for user {self.user_id}"}

        total_searches = len(self.search_history)
        top_queries_raw = sorted(
            self.query_frequency.items(), key=lambda x: x[1], reverse=True
        )[:5]

        top_queries = [
            {"query": query, "count": count} for query, count in top_queries_raw
        ]

        return {
            "total_searches": total_searches,
            "top_queries": top_queries,
        }

    async def cleanup(self):
        """Proper cleanup of database connections and tasks"""
        try:
            logging.info(
                f"🧹 Starting RyanRecallManager cleanup for user {self.user_id}"
            )

            # Cancel background tasks
            if (
                hasattr(self, "_db_save_task")
                and self._db_save_task
                and not self._db_save_task.done()
            ):
                logging.info("🔄 Cancelling database save task")
                self._db_save_task.cancel()
                try:
                    await self._db_save_task
                except asyncio.CancelledError:
                    logging.info("✅ Database save task cancelled")
                except Exception as e:
                    logging.error(f"❌ Error cancelling save task: {e}")

            if (
                hasattr(self, "_db_fetch_task")
                and self._db_fetch_task
                and not self._db_fetch_task.done()
            ):
                logging.info("🔄 Cancelling database fetch task")
                self._db_fetch_task.cancel()
                try:
                    await self._db_fetch_task
                except asyncio.CancelledError:
                    logging.info("✅ Database fetch task cancelled")
                except Exception as e:
                    logging.error(f"❌ Error cancelling fetch task: {e}")

            # Close database connection
            if self.db_manager:
                try:
                    logging.info("🔄 Closing database connection")
                    await self.db_manager.close_pool()
                    logging.info("✅ Database connection closed")
                except Exception as e:
                    logging.error(f"❌ Error closing database: {e}")

            # Clear references
            self.db_manager = None
            self._db_save_task = None
            self._db_fetch_task = None

            logging.info(
                f"✅ RyanRecallManager cleanup completed for user {self.user_id}"
            )

        except Exception as e:
            logging.error(f"❌ Error in RyanRecallManager cleanup: {e}")
