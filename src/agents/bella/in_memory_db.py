from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict
import uuid
import pytz


@dataclass
class TodoTask:
    id: str
    title: str
    completed: bool = False
    created_at: str = ""
    completed_at: str | None = None
    priority: str = "medium"
    note: str = ""
    deadline: str = ""
    user_id: str = ""
    updated_at: str | None = None  # 👈 added here


class InMemoryToDoClient:
    def __init__(self, db_config: dict, user_id: int):
        try:
            self.user_id = str(user_id)  # normalize to str
            self._tasks: dict[str, TodoTask] = {}
        except Exception as err:
            print(f"[ERROR] in __init__: {err}")

    def _normalize_task_data(self, data: dict) -> dict:
        try:

            def normalize_dt(value):
                if isinstance(value, datetime):
                    # Preserve timezone information if present
                    if value.tzinfo is not None:
                        return value.isoformat()
                    else:
                        # If naive datetime, assume it's local timezone (Asia/Karachi)
                        local_tz = pytz.timezone("Asia/Karachi")
                        return local_tz.localize(value).isoformat()
                return str(value) if value else None

            return {
                "id": str(data.get("id", "")),
                "title": data.get("title", ""),
                "completed": bool(data.get("completed", False)),
                "created_at": normalize_dt(data.get("created_at")),
                "completed_at": normalize_dt(data.get("completed_at")),
                "priority": data.get("priority", "medium"),
                "note": data.get("note", ""),
                "deadline": normalize_dt(data.get("deadline")),
                "user_id": str(data.get("user_id", "")),
                "updated_at": normalize_dt(data.get("updated_at")),
            }
        except Exception as err:
            print(f"[ERROR] in _normalize_task_data: {err}")
            return {
                "id": str(data.get("id", "")),
                "title": data.get("title", ""),
                "completed": False,
                "created_at": None,
                "completed_at": None,
                "priority": "medium",
                "note": "",
                "deadline": None,
                "user_id": str(data.get("user_id", "")),
                "updated_at": None,
            }

    def initialize_cache(self, tasks: List[Dict]):
        """Populate cache with initial tasks (list of dicts)."""
        try:
            for t in tasks:
                task = TodoTask(**self._normalize_task_data(t))
                self._tasks[task.id] = task
        except Exception as err:
            print(f"[ERROR] in initialize_cache: {err}")

    async def add_task(self, task_data: dict) -> TodoTask | None:
        try:
            task = TodoTask(**self._normalize_task_data(task_data))
            self._tasks[task.id] = task
            print("TASK AFTER BEING ADDED")
            print(self._tasks)
            print("NOW U HAVE SEEN IT")
            return task
        except Exception as err:
            print(f"[ERROR] in add_task: {err}")
            return None

    async def update_task(self, task_data: dict) -> TodoTask | None:
        try:
            task = TodoTask(**self._normalize_task_data(task_data))
            self._tasks[task.id] = task
            print("TASK AFTER BEING UPDATED")
            print(self._tasks)
            print("NOW U HAVE SEEN IT")
            return task
        except Exception as err:
            print(f"[ERROR] in update_task: {err}")
            return None

    async def complete_task(
        self,
        task_id: str,
        completed: bool,
        completed_at: datetime,
        updated_at: datetime,
    ) -> TodoTask | None:
        task_id = str(task_id)
        try:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.completed = completed

                # normalize completed_at with timezone preservation
                if isinstance(completed_at, datetime):
                    if completed_at.tzinfo is not None:
                        task.completed_at = completed_at.isoformat()
                    else:
                        # If naive datetime, assume it's local timezone (Asia/Karachi)
                        local_tz = pytz.timezone("Asia/Karachi")
                        task.completed_at = local_tz.localize(completed_at).isoformat()
                else:
                    task.completed_at = str(completed_at or "")

                # normalize updated_at (coming from RPC) with timezone preservation
                if isinstance(updated_at, datetime):
                    if updated_at.tzinfo is not None:
                        task.updated_at = updated_at.isoformat()
                    else:
                        # If naive datetime, assume it's local timezone (Asia/Karachi)
                        local_tz = pytz.timezone("Asia/Karachi")
                        task.updated_at = local_tz.localize(updated_at).isoformat()
                else:
                    task.updated_at = str(updated_at or "")

                print(
                    f"[DEBUG] Task {task_id} completed_at={task.completed_at}, updated_at={task.updated_at}"
                )
                return task
            return None
        except Exception as err:
            print(f"[ERROR] in complete_task: {err}")
            return None

    async def delete_task(self, task_id: str) -> TodoTask | None:
        task_id = str(task_id)
        try:
            return self._tasks.pop(task_id, None)
        except Exception as err:
            print(f"[ERROR] in delete_task: {err}")
            return None

    async def get_todos(self, include_completed: bool = False) -> List[TodoTask]:
        try:
            tasks = [t for t in self._tasks.values() if str(t.user_id) == self.user_id]
            if not include_completed:
                tasks = [t for t in tasks if not t.completed]
            return sorted(tasks, key=lambda x: x.created_at, reverse=True)
        except Exception as err:
            print(f"[ERROR] in get_todos: {err}")
            return []

    async def get_all_tasks_for_deletion(
        self,
        status: str | None = None,  # "completed" or "pending"
        priorities: list[str] | None = None,  # list of priorities
    ) -> List[TodoTask]:
        """
        Return tasks that match the filters for deletion.
        - status: "completed" or "pending"
        - priorities: list of allowed priorities
        """
        try:
            filtered_tasks = []
            for task in self._tasks.values():
                # Status filter
                if status == "completed" and not task.completed:
                    continue
                if status == "pending" and task.completed:
                    continue

                # Priority filter
                if priorities and task.priority not in priorities:
                    continue

                filtered_tasks.append(task)

            return filtered_tasks

        except Exception as err:
            print(f"[ERROR] in get_all_tasks_for_deletion: {err}")
            return []

    async def delete_all_todos(
        self, status: str | None = None, priorities: list[str] | None = None
    ):
        """
        Delete tasks based on filters.
        - status: "completed" or "pending"
        - priorities: list of allowed priorities
        """
        try:
            to_delete_ids = [
                task.id
                for task in self._tasks.values()
                if (
                    status is None
                    or (status == "completed" and task.completed)
                    or (status == "pending" and not task.completed)
                )
                and (priorities is None or task.priority in priorities)
            ]

            for task_id in to_delete_ids:
                self._tasks.pop(task_id)

            print(f"[DEBUG] Deleted task IDs: {to_delete_ids}")

        except Exception as err:
            print(f"[ERROR] in delete_all_todos: {err}")

    async def get_task_stats(self) -> dict:
        """Return task stats for the current user from the in-memory cache."""
        try:
            # filter tasks belonging to this user
            all_tasks = [
                t for t in self._tasks.values() if str(t.user_id) == self.user_id
            ]

            completed = [t for t in all_tasks if t.completed]
            pending = [t for t in all_tasks if not t.completed]

            return {
                "total": len(all_tasks),
                "completed": len(completed),
                "pending": len(pending),
                "high_priority": len([t for t in pending if t.priority == "high"]),
                "medium_priority": len([t for t in pending if t.priority == "medium"]),
                "low_priority": len([t for t in pending if t.priority == "low"]),
            }
        except Exception as err:
            print(f"[ERROR] in get_task_stats: {err}")
            return {
                "total": 0,
                "completed": 0,
                "pending": 0,
                "high_priority": 0,
                "medium_priority": 0,
                "low_priority": 0,
            }

    async def get_tasks_by_ids(self, ids: List[str]) -> List[dict]:
        """Return normalized task dicts for the given list of task IDs."""
        try:
            return [
                self._normalize_task_data(self._tasks[tid].__dict__)
                for tid in ids
                if tid in self._tasks
            ]
        except Exception as err:
            print(f"[ERROR] in get_tasks_by_ids: {err}")
            return []
