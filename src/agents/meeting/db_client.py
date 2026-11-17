import asyncpg
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


class DBClient:
    """Database client for meeting transcriber - fetches user information for ID to name mapping"""

    def __init__(self, db_config: dict, organization_id: int):
        self.db_config = db_config
        self.organization_id = organization_id
        self.pool: asyncpg.pool.Pool | None = None

    async def _init_pool(self):
        """Initialize database connection pool"""
        if not self.pool:
            self.pool = await asyncpg.create_pool(**self.db_config)

    async def fetch_qlu_users(self) -> List[Dict]:
        """
        Fetch users for ID → name mapping from:
        1) users (organization_id + private_room_id IS NOT NULL)
        2) temp_user (organization_id + created_at within last 12 hours)
        Returns one combined list of dicts with id and name.
        """
        try:
            await self._init_pool()
            async with self.pool.acquire() as conn:
                users_query = """
                    SELECT id, name
                    FROM users
                    WHERE organization_id = $1
                      AND private_room_id IS NOT NULL
                """
                users_rows = await conn.fetch(users_query, self.organization_id)
                temp_query = """
                    SELECT id, name
                    FROM temp_user
                    WHERE organization_id = $1
                      AND created_at >= NOW() - INTERVAL '12 hour'
                """
                temp_rows = await conn.fetch(temp_query, self.organization_id)
                users_list = [dict(row) for row in users_rows]
                temp_list = [dict(row) for row in temp_rows]
                return users_list + temp_list
        except Exception as e:
            logger.error(f"❌ Error fetching users from database: {e}")
            return []
