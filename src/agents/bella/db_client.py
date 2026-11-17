import asyncpg
from typing import List, Dict


class DBToDoClient:
    def __init__(self, db_config: dict, user_id: str, organization_id: int):
        self.user_id = int(user_id)
        self.organization_id = organization_id
        self.db_config = db_config
        self.pool: asyncpg.pool.Pool | None = None

    async def _init_pool(self):
        if not self.pool:
            self.pool = await asyncpg.create_pool(**self.db_config)

    async def fetch_all_tasks(self) -> List[Dict]:
        """Fetch all tasks for the user from DB as list of dicts."""
        await self._init_pool()
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM todo_task WHERE user_id=$1", self.user_id
            )
        return [dict(row) for row in rows]

    async def fetch_qlu_users(self) -> List[Dict]:
        """
        Fetch users from the given organization_id with private_room_id.
        """
        await self._init_pool()
        async with self.pool.acquire() as conn:
            query = """
                SELECT id, email, name 
                FROM users 
                WHERE organization_id = $1 
                  AND private_room_id IS NOT NULL
            """
            rows = await conn.fetch(query, self.organization_id)
        return [dict(row) for row in rows]
