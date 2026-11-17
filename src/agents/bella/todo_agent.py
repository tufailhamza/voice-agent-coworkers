from dotenv import load_dotenv
import sys
from pathlib import Path
import dotenv
from typing import Optional
import re
from openai import OpenAI
import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from typing_extensions import TypedDict
from livekit.agents import Agent, function_tool, ChatContext, RunContext, JobContext
from src.agents.bella.base_agent import BaseAgent
from agents.bella.in_memory_db import InMemoryToDoClient
from src.agents.bella.prompts import TODO_SYSTEM_PROMPT
from dataclasses import asdict
from src.agents.bella.utils import sanitize_for_rpc, update_tool_info
import asyncio
from utils.logger import Logger
from src.agents.bella.in_memory_db import TodoTask
from datetime import datetime
import pytz
import random
import string
import re

logging = Logger(__name__)

# RFC 3339 regex pattern for validation
RFC_3339_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2})$"

load_dotenv()

user = os.environ["PG_USER"].strip().replace('"', "")
password = os.environ["PG_PASSWORD"].strip().replace('"', "")
host = os.environ["PG_HOST"].strip().replace('"', "")
port = int(os.environ["PG_PORT"].strip().replace('"', ""))
dbname = os.environ["PG_DBNAME"].strip().replace('"', "")

db_config = {
    "user": user,
    "password": password,
    "host": host,
    "port": port,
    "database": dbname,
}


class TaskItem(TypedDict, total=False):
    """A single to-do task."""

    task: str
    priority: str | None
    deadline: str | None  # RFC 3339 timestamp (e.g., "2025-10-11T15:00:00+05:00")
    note: str | None
    completed: bool | None


src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))


class TodoAgent(BaseAgent):
    def __init__(
        self,
        ctx: JobContext,
        host_name: str,
        user_id: str,
        local_time_zone: str,
        chat_ctx: ChatContext = None,
        room=None,
        memory_manager=None,
        user_name: str = "",
    ):
        super().__init__(
            instructions=build_todo_system_prompt2(
                tz=local_time_zone,
                memory_manager=memory_manager,
                user_name=user_name,
            ),
            chat_ctx=chat_ctx,
        )
        self.ctx = ctx
        self.host_name = host_name
        self.user_id = user_id
        self.in_memory_todo_client = InMemoryToDoClient(
            db_config=db_config, user_id=user_id
        )
        self.room = room
        self.temp_task_map = {}
        self.local_timezone = local_time_zone

    async def on_enter(self):
        """Override on_enter to share services via userdata before calling super()."""
        userdata = self.session.userdata
        userdata.todo_client = self.in_memory_todo_client
        userdata.user_id = self.user_id
        await super().on_enter()

    def initialize_tasks(self, initial_task_list):
        """Initialize the in-memory task cache."""
        if not initial_task_list:
            print("⚠️ No initial tasks provided — skipping cache initialization.")
            return
        self.in_memory_todo_client.initialize_cache(initial_task_list)
        print(f"✅ Initialized {len(initial_task_list)} tasks into cache.")

    @function_tool
    async def get_todos(
        self,
        include_completed: bool = True,
        deadline: str | None = None,
        completed_before: str | None = None,
        completed_after: str | None = None,
        completed_on: str | None = None,
        priority: List[str] | None = None,
        status: str | None = None,
        created_before: str | None = None,
        created_after: str | None = None,
    ) -> str:
        """
        Get a list of to-do items with advanced filtering.

        Args:
            include_completed (bool): Whether to include completed tasks.
            deadline (str | None): Exact deadline filter (YYYY-MM-DD or RFC 3339 timestamp).
            completed_before (str | None): Include tasks completed before this date (YYYY-MM-DD or RFC 3339).
            completed_after (str | None): Include tasks completed after this date (YYYY-MM-DD or RFC 3339).
            completed_on (str | None): Only tasks completed exactly on this date (YYYY-MM-DD or RFC 3339).
            priority (List[str] | None): List of priorities (e.g., ["high", "low"]).
            status (str | None): Either "completed" or "pending" or "overdue"
            created_before (str | None): Only tasks created before this date (YYYY-MM-DD or RFC 3339).
            created_after (str | None): Only tasks created after this date (YYYY-MM-DD or RFC 3339).

        Returns:
            str: A formatted list of filtered to-do items.
        """
        self.session.userdata.tool_invoked = "get_todos"
        print("[TOOL] TodoAgent: Getting to-do list with advanced filters.")

        # Determine status value for RPC: "all" if None or empty, otherwise use the provided value
        rpc_status = "all" if not status or status.strip() == "" else status.lower()

        try:
            tasks = await self.in_memory_todo_client.get_todos(include_completed=True)

            if not tasks:
                try:
                    rpc_payload = {"tasks": [], "status": rpc_status}
                    print(json.dumps(rpc_payload))
                    response = await self.room.local_participant.perform_rpc(
                        destination_identity=self.host_name,
                        method="fetch_todos_agent",
                        payload=json.dumps(rpc_payload),
                    )
                    print(f"RPC response: {response}")
                except Exception as e:
                    logging.error(f"RPC call failed: {e}")
                return "Your to-do list is currently empty."

            def parse_date(d: str):
                """Parse date from either YYYY-MM-DD or RFC 3339 format."""
                if "T" in d:
                    # RFC 3339 format: extract date portion
                    dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                    return dt.date()
                else:
                    # YYYY-MM-DD format
                    return datetime.strptime(d, "%Y-%m-%d").date()

            def format_datetime_to_human(dt_str):
                """Convert datetime string to human readable format"""
                if not dt_str:
                    return None
                try:
                    if isinstance(dt_str, str):
                        if "T" in dt_str:
                            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        else:
                            dt = datetime.fromisoformat(dt_str)
                    else:
                        dt = dt_str

                    # Convert to local timezone if timezone-aware
                    if dt.tzinfo:
                        local_tz = pytz.timezone(self.local_timezone)
                        dt = dt.astimezone(local_tz)

                    return dt.strftime("%B %d, %Y at %I:%M %p")
                except:
                    return dt_str

            # --- Apply Filters ---
            if deadline:
                d = parse_date(deadline)
                tasks = [
                    t
                    for t in tasks
                    if t.deadline
                    and (
                        (
                            t.deadline.date()
                            if isinstance(t.deadline, datetime)
                            else datetime.fromisoformat(
                                t.deadline.replace("Z", "+00:00")
                            ).date()
                        )
                        == d
                    )
                ]

            if completed_on:
                d = parse_date(completed_on)
                tasks = [
                    t
                    for t in tasks
                    if t.completed
                    and (
                        (
                            t.completed.date()
                            if isinstance(t.completed, datetime)
                            else datetime.fromisoformat(
                                t.completed.replace("Z", "+00:00")
                            ).date()
                        )
                        == d
                    )
                ]

            if completed_before:
                d = parse_date(completed_before)
                tasks = [
                    t
                    for t in tasks
                    if t.completed
                    and (
                        (
                            t.completed.date()
                            if isinstance(t.completed, datetime)
                            else datetime.fromisoformat(
                                t.completed.replace("Z", "+00:00")
                            ).date()
                        )
                        < d
                    )
                ]

            if completed_after:
                d = parse_date(completed_after)
                tasks = [
                    t
                    for t in tasks
                    if t.completed
                    and (
                        (
                            t.completed.date()
                            if isinstance(t.completed, datetime)
                            else datetime.fromisoformat(
                                t.completed.replace("Z", "+00:00")
                            ).date()
                        )
                        > d
                    )
                ]
            if priority:
                priorities = {p.lower() for p in priority}
                tasks = [
                    t for t in tasks if t.priority and t.priority.lower() in priorities
                ]

            if status:
                # Use timezone-aware current date for filtering
                now = datetime.now(pytz.timezone(self.local_timezone)).date()
                s = status.lower()

                if s == "completed":
                    tasks = [t for t in tasks if t.completed]
                elif s == "pending":
                    # Include all tasks that are not completed, ignore deadlines
                    tasks = [t for t in tasks if not t.completed]
                elif s == "overdue":
                    # Use current datetime in Karachi
                    now = datetime.now(pytz.timezone(self.local_timezone))

                    tasks = [
                        t
                        for t in tasks
                        if not t.completed
                        and t.deadline
                        and (
                            (
                                t.deadline
                                if isinstance(t.deadline, datetime)
                                else datetime.fromisoformat(
                                    t.deadline.replace("Z", "+00:00")
                                )
                            )
                            < now
                        )
                    ]

            if created_before:
                d = parse_date(created_before)
                tasks = [t for t in tasks if t.created_at.date() < d]

            if created_after:
                d = parse_date(created_after)
                tasks = [t for t in tasks if t.created_at.date() > d]

            if not tasks:
                # Still make RPC call with empty data
                try:
                    rpc_payload = {"tasks": [], "status": rpc_status}
                    print(json.dumps(rpc_payload))
                    response = await self.room.local_participant.perform_rpc(
                        destination_identity=self.host_name,
                        method="fetch_todos_agent",
                        payload=json.dumps(rpc_payload),
                    )
                    print(f"RPC response: {response}")
                except Exception as e:
                    logging.error(f"RPC call failed: {e}")
                return "Your to-do list is currently empty."

            pending_tasks = [t for t in tasks if not t.completed]
            completed_tasks = [t for t in tasks if t.completed]

            result = []

            if pending_tasks:
                result.append("Pending Tasks:")
                for i, task in enumerate(pending_tasks, 1):
                    result.append(f"{i}. {task.title}")

                    if task.priority:
                        result.append(f"   Priority: {task.priority.capitalize()}")

                    if task.deadline:
                        deadline_formatted = format_datetime_to_human(task.deadline)
                        result.append(f"   Deadline: {deadline_formatted}")

                    if task.note:
                        result.append(f"   Note: {task.note}")

                    result.append("")

            if include_completed and completed_tasks:
                result.append("Completed Tasks:")
                for task in completed_tasks:
                    result.append(f"• {task.title}")

                    if task.completed_at:
                        completed_formatted = format_datetime_to_human(
                            task.completed_at
                        )
                        result.append(f"   Completed: {completed_formatted}")

                    result.append("")

            stats = await self.in_memory_todo_client.get_task_stats()
            result.append(
                f"Stats: {stats['pending']} pending, {stats['completed']} completed"
            )

            try:
                sanitized_tasks = [asdict(t) for t in tasks]
                rpc_payload = {"tasks": sanitized_tasks, "status": rpc_status}
                print(json.dumps(rpc_payload))
                response = await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="fetch_todos_agent",
                    payload=json.dumps(rpc_payload),
                )
                print(f"RPC response: {response}")
            except Exception as e:
                logging.error(f"RPC call failed: {e}")
                return f"Sorry, I was unable to fetch your tasks list"

            print("Returning from get todos succesfully")
            print(result)
            return "\n".join(result)

        except Exception as e:
            logging.error(f"[ERROR] TodoAgent: Failed to get todos - {e}")

            return "Sorry, I encountered an error while retrieving your to-do list."

    @function_tool()
    async def add_todo(
        self,
        tasks: List[TaskItem],
    ) -> str:
        """
        Add one or more tasks to the to-do list.

        Parameters:
            tasks (List[TaskItem]): A list of task objects. Each task must include the following fields:
                - task (str): Task description.
                - priority (str | None): Priority level; one of ["low", "medium", "high"]. Defaults to "medium".
                - deadline (str | None): RFC 3339 timestamp (e.g., "2025-10-11T15:00:00+05:00").
                - note (str | None): Additional notes related to the task.
                - completed (bool | None): Whether the task is already completed.

                ⚠️ No other keys are allowed. (additionalProperties = false)

        Returns:
            str: Confirmation message indicating tasks were added successfully.
        """
        self.session.userdata.tool_invoked = "add_todo"
        print(f"[TOOL] TodoAgent: Adding {len(tasks)} tasks in bulk.")

        added_tasks = []
        failed_tasks = []

        for t in tasks:
            task_title = t.get("task")
            priority = t.get("priority", "medium")
            deadline = t.get("deadline", "")
            note = t.get("note", "")

            # Validate priority
            if priority not in ["low", "medium", "high"]:
                priority = "medium"

            # Validate deadline and handle RFC 3339 format
            deadline_iso = None
            if deadline:
                try:
                    # Check if it's RFC 3339 format
                    if re.match(RFC_3339_REGEX, deadline):
                        # Already in RFC 3339 format, validate and use as-is
                        deadline_dt = datetime.fromisoformat(deadline)
                        deadline_iso = deadline_dt.isoformat()
                    else:
                        # Legacy format: parse as local time and convert to timezone-aware
                        deadline_dt = datetime.strptime(deadline, "%Y-%m-%d %H:%M:%S")
                        deadline_dt = deadline_dt.replace(
                            tzinfo=pytz.timezone(self.local_timezone)
                        )
                        deadline_iso = deadline_dt.isoformat()
                except ValueError:
                    logging.error(
                        f"Invalid deadline format for task '{task_title}': {deadline}"
                    )
                    deadline = ""

            new_task = {
                "title": task_title,
                "completed": False,
                "created_at": datetime.now(
                    pytz.timezone(self.local_timezone)
                ).isoformat(),
                "completed_at": None,
                "priority": priority,
                "note": note,
                "deadline": deadline_iso,
                "user_id": self.user_id,
                "updated_at": None,
            }

            # Send RPC call for this task
            try:
                sanitized_new_task = sanitize_for_rpc(new_task)
                print("SANITIZED NEW TASK")
                print(sanitized_new_task)
                print("CLOSE ADD TODO")
                print(
                    f"[DEBUG] Sending RPC for task '{task_title}': {json.dumps(sanitized_new_task)}"
                )
                response = await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="add_todo_agent",
                    payload=json.dumps(sanitized_new_task),
                )

                response_data = json.loads(response)
                added_task = response_data

                print("ADDED TASK RECIEVED")
                print(added_task)
                print("CLOSURE OF ADDED TASK")

                print(
                    f"[DEBUG] Received RPC response for task '{task_title}': {added_task}"
                )

                await self.in_memory_todo_client.add_task(added_task)
                added_tasks.append(task_title)

            except Exception as e:
                logging.error(f"Failed to add task '{task_title}': {e}")
                failed_tasks.append(task_title)

        # await speech_handle.wait_for_playout()
        await update_tool_info(
            self.session.userdata,
            "bulk_add_todo",
            "Success" if not failed_tasks else "Partial Success",
        )

        result_msg = f"✅ Added {len(added_tasks)} tasks successfully."
        if failed_tasks:
            result_msg += f"\n⚠️ Failed to add: {', '.join(failed_tasks)}"

        return result_msg

    def _generate_fake_id(self):
        """Generate a short temporary fake ID"""
        return "".join(random.choices(string.ascii_letters + string.digits, k=5))

    @function_tool()
    async def find_task_by_description(
        self,
        task_description: str,
    ) -> Dict[str, Any]:
        """
        Find tasks by description with intelligent LLM-based matching.

        Args:
            task_description (str): Partial or full task description to search.

        Returns:
            Dict[str, Any]: Contains matched tasks:
                {
                    "confirmed_task": List[Dict[str, str]],
                    "ambiguity_between": List[Dict[str, str]]
                }
        """
        self.session.userdata.tool_invoked = "find_task"

        import re
        import json

        print(f"[TOOL] TodoAgent: Finding task for description '{task_description}'.")

        # Fetch tasks
        tasks = await self.in_memory_todo_client.get_todos(include_completed=True)
        if not tasks:
            print("[TOOL] No tasks found.")
            return {"confirmed_task": [], "ambiguity_between": []}

        # Prepare tasks for LLM
        available_tasks = [
            {
                "number": i + 1,
                "id": task.id,
                "title": task.title,
                "note": task.note,
                "priority": task.priority,
            }
            for i, task in enumerate(tasks)
        ]

        print(f"[DEBUG] Available tasks:\n{available_tasks}")

        # Build LLM prompt
        task_lines = "\n".join(
            f"{t['number']}. Title: '{t['title']}', Note: '{t['note']}', Priority: {t['priority']}"
            for t in available_tasks
        )

        llm_prompt = f"""
    User described a task: "{task_description}"

    Available tasks:
    {task_lines}

    Instructions:
    - Analyze the user's task description and match it against available tasks using intelligent reasoning
    - Return ONLY a JSON object with keys: confirmed_task, ambiguity_between
    - Each task entry must include a fake id (e.g., 'abc12') and title

    CONFIRMED_TASK rules:
    - Put tasks here when you are confident about the match
    - Single exact title match -> confirmed_task
    - Multiple tasks with same exact title -> ALL go in confirmed_task (user wants all duplicates)
    - Multi-task requests like "X and Y" or "X, Y, Z" -> if you find clear matches for each, put ALL in confirmed_task
    - Pattern matches like "all shopping tasks" -> if you can confidently identify the pattern, put ALL matches in confirmed_task

    AMBIGUITY_BETWEEN rules:
    - Put tasks here when you're unsure which specific task the user means
    - Multiple possible matches that could fit the description
    - When user says something generic like "the important task" and multiple tasks could be important
    - Limit to 2-4 most likely candidates

    Key principles:
    - Be intelligent about context and user intent
    - If user mentions multiple tasks explicitly ("X and Y", "X, Y, Z"), try to find all of them
    - If all tasks have the exact same title, they ALL go in confirmed_task (user likely wants all duplicates)
    - Only use ambiguity_between when genuinely unsure which task the user means
    - Pay attention to conjunctions (and, plus, also) and comma-separated lists
    - Consider task content, notes, and priority for better matching

    Expected JSON format:
    {{
        "confirmed_task": [{{"id": "abc12", "title": "Buy milk"}}, {{"id": "def34", "title": "Buy bread"}}],
        "ambiguity_between": [{{"id": "ghi56", "title": "Important meeting"}}, {{"id": "jkl78", "title": "Important call"}}]
    }}
    """
        print("[DEBUG] LLM Prompt:\n", llm_prompt)

        # Call LLM
        from src.utils.openai_env import get_openai_api_key_v2

        client = OpenAI(api_key=get_openai_api_key_v2())
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": llm_prompt}],
            temperature=0,
        )

        reply_text = response.choices[0].message.content.strip()
        print("[TOOL] LLM raw reply:", reply_text)

        # Extract JSON from LLM reply
        json_blocks = re.findall(
            r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", reply_text, flags=re.DOTALL
        )

        parsed = None
        for json_block in json_blocks:
            try:
                candidate = json.loads(json_block.strip())
                # Validate that it has the expected structure
                if all(
                    key in candidate for key in ["confirmed_task", "ambiguity_between"]
                ):
                    parsed = candidate
                    print(f"[DEBUG] Successfully parsed JSON: {parsed}")
                    break
            except Exception as e:
                print(f"[DEBUG] Failed to parse JSON block: {e}")
                continue

        if parsed is None:
            print("[ERROR] No valid JSON found in LLM reply")
            print(f"[DEBUG] Raw reply was: {reply_text}")
            return {"confirmed_task": [], "ambiguity_between": []}

        # Map fake IDs to real task IDs with validation
        self.temp_task_map.clear()
        for section in ["confirmed_task", "ambiguity_between"]:
            for task in parsed.get(section, []):
                fake_id = task.get("id")
                title = task.get("title")

                if not fake_id or not title:
                    print(f"[WARNING] Skipping invalid task entry: {task}")
                    continue

                # Find real task by exact title match (case-insensitive)
                real_task = None
                for available_task in available_tasks:
                    if available_task["title"].lower() == title.lower():
                        real_task = available_task
                        break

                if real_task:
                    self.temp_task_map[fake_id] = real_task["id"]
                    print(
                        f"[DEBUG] Mapped fake ID {fake_id} -> real ID {real_task['id']} for task '{title}'"
                    )
                else:
                    print(f"[WARNING] Could not find real task for title: '{title}'")
                    # Still map it to avoid key errors, but flag it
                    self.temp_task_map[fake_id] = f"MISSING_{fake_id}"

        print(f"[DEBUG] Temporary task map: {self.temp_task_map}")
        return parsed

    @function_tool()
    async def complete_todo(
        self,
        task_ids: List[str],
        completed: bool = True,
    ) -> str:
        """
        Complete or mark incomplete tasks using fake IDs.

        Args:
            task_ids (List[str]): List of task fake IDs to update.
            completed (bool): True to mark complete, False to mark incomplete.

        Returns:
            str: Summary of completion updates.
        """
        self.session.userdata.tool_invoked = "complete_todo"

        if not task_ids:
            return "No valid task IDs provided."

        if not isinstance(task_ids, list):
            task_ids = [task_ids]

        # STEP 0: Let user know we're processing
        # speech_handle = self.session.say(text="Just a minute please", add_to_chat_ctx=False)

        completed_titles = []
        already_done_messages = []
        errors = []

        print(f"[DEBUG] Attempting to complete tasks with fake IDs: {task_ids}")

        for fake_id in task_ids:
            real_id = self.temp_task_map.get(fake_id)
            if not real_id or real_id.startswith("MISSING_"):
                print(f"[ERROR] No valid real ID found for fake ID: {fake_id}")
                errors.append(f"Task with ID '{fake_id}' not found")
                continue

            try:
                # STEP 1: Fetch normalized task info
                tasks = await self.in_memory_todo_client.get_tasks_by_ids([real_id])
                if not tasks:
                    errors.append(f"Task with ID '{fake_id}' not found in memory")
                    continue

                task = tasks[0]
                task_title = task["title"]

                # STEP 2: Skip RPC/cache if task already in desired state
                if task["completed"] == completed:
                    state_str = "completed" if completed else "pending"
                    already_done_messages.append(
                        f"Task '{task_title}' is already {state_str}."
                    )
                    print(f"[INFO] Task already in desired state: {task_title}")
                    continue

                # STEP 3: Prepare payload for RPC
                now_iso = datetime.now(pytz.timezone(self.local_timezone)).isoformat()
                task_payload = {
                    "id": real_id,
                    "user_id": task["user_id"],
                    "completed": completed,
                    "completed_at": now_iso if completed else None,
                    "updated_at": now_iso,
                }

                sanitized_payload = sanitize_for_rpc(task_payload)
                print(
                    f"[DEBUG] Sending RPC for task '{task_title}': {sanitized_payload}"
                )

                # STEP 4: Call RPC
                response = await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="complete_todo_agent",
                    payload=json.dumps(sanitized_payload),
                )
                response_data = json.loads(response)

                # STEP 5: Update in-memory cache
                await self.in_memory_todo_client.complete_task(
                    response_data["id"],
                    response_data["completed"],
                    response_data.get("completed_at"),
                    response_data.get("updated_at"),
                )

                completed_titles.append(task_title)
                print(f"[SUCCESS] Updated task: {task_title}")

            except Exception as e:
                logging.error(f"Failed to update task {fake_id}: {e}")
                errors.append(f"Task '{task_title}' update failed")

        messages = []
        if completed_titles:
            messages.append(
                f"I've marked {len(completed_titles)} task(s) {'complete' if completed else 'incomplete'}."
            )
        if already_done_messages:
            messages.extend(already_done_messages)
        if errors:
            messages.append(f"There were issues updating {len(errors)} task(s).")

        return " ".join(messages) if messages else "No tasks were updated."

    @function_tool()
    async def delete_todo(
        self,
        task_ids: List[str],
    ) -> str:
        """
        Delete one or more tasks using fake IDs.

        Args:
            task_ids (List[str]): List of task fake IDs to delete.

        Returns:
            str: Summary of deletion results.
        """
        self.session.userdata.tool_invoked = "delete_todo"

        if not task_ids:
            return "No valid task IDs provided to delete."

        if not isinstance(task_ids, list):
            task_ids = [task_ids]

        deleted_titles = []
        errors = []

        print(f"[DEBUG] Attempting to delete tasks with fake IDs: {task_ids}")

        for fake_id in task_ids:
            real_id = self.temp_task_map.get(fake_id)
            if not real_id or real_id.startswith("MISSING_"):
                print(f"[ERROR] No valid real ID found for fake ID: {fake_id}")
                errors.append("Task not found")
                continue

            try:
                print(f"[DEBUG] Deleting task with real ID: {real_id}")

                payload = {"id": real_id, "user_id": self.user_id}
                response = await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="delete_todo_agent",
                    payload=json.dumps(payload),
                )

                response_data = json.loads(response)
                deleted_ids = response_data.get("ids", [])

                if deleted_ids:
                    # Update in-memory cache (using first deleted ID as RPC handles one at a time)
                    await self.in_memory_todo_client.delete_task(task_id=deleted_ids[0])

                    task_title = response_data.get("title", f"Task {real_id}")
                    deleted_titles.append(task_title)
                    print(f"[SUCCESS] Deleted task: {task_title}")
                else:
                    error_msg = f"Task not found or already deleted"
                    errors.append(error_msg)
                    print(f"[ERROR] {error_msg} for ID: {real_id}")

            except Exception as e:
                error_msg = f"Task deletion failed"
                logging.error(f"Failed to delete task {real_id}: {e}")
                errors.append(error_msg)
                print(f"[ERROR] {error_msg}: {e}")

        # Generate response message
        if deleted_titles and not errors:
            if len(deleted_titles) == 1:
                return "I've deleted the task."
            else:
                return "I've deleted all the selected tasks."
        elif deleted_titles and errors:
            if len(deleted_titles) == 1:
                return f"I've deleted one task. However, there were some issues with other tasks."
            else:
                return f"I've deleted {len(deleted_titles)} tasks. However, there were some issues with other tasks."
        else:
            return "I couldn't delete any of the tasks. Please check if they exist and try again."

    # EXTENDED DELETE WITH EXCEPT FUNCTIONALITY

    # @function_tool()
    # async def delete_todo(self, context: RunContext, task_ids: list, except_mode: bool = False) -> str:
    #     ...
    #     self.session.userdata.tool_invoked = "delete_todo"

    #     if not task_ids:
    #         return "No valid task IDs provided to delete."

    #     if not isinstance(task_ids, list):
    #         task_ids = [task_ids]

    #     deleted_titles = []
    #     errors = []

    #     if except_mode:
    #         print(f"[DEBUG] Deleting all tasks EXCEPT fake IDs: {task_ids}")

    #         all_tasks = await self.in_memory_todo_client.get_todos(include_completed=True)
    #         exclude_real_ids = set()
    #         for fake_id in task_ids:
    #             real_id = self.temp_task_map.get(fake_id)
    #             if real_id and not real_id.startswith("MISSING_"):
    #                 exclude_real_ids.add(real_id)

    #         tasks_to_delete = [t for t in all_tasks if t.id not in exclude_real_ids]

    #         for task in tasks_to_delete:
    #             real_id = task.id
    #             try:
    #                 print(f"[DEBUG] Deleting task with real ID: {real_id}")

    #                 payload = {"id": real_id, "user_id": self.user_id}
    #                 response = await self.room.local_participant.perform_rpc(
    #                     destination_identity=self.host_name,
    #                     method='delete_todo_agent',
    #                     payload=json.dumps(payload)
    #                 )

    #                 response_data = json.loads(response)
    #                 deleted_ids = response_data.get("ids", [])

    #                 if deleted_ids:
    #                     await self.in_memory_todo_client.delete_task(task_id=deleted_ids[0])
    #                     task_title = response_data.get("title", f"Task {real_id}")
    #                     deleted_titles.append(task_title)
    #                     print(f"[SUCCESS] Deleted task: {task_title}")
    #                 else:
    #                     error_msg = f"Task not found or already deleted"
    #                     errors.append(error_msg)
    #                     print(f"[ERROR] {error_msg} for ID: {real_id}")

    #             except Exception as e:
    #                 error_msg = f"Task deletion failed"
    #                 logging.error(f"Failed to delete task {real_id}: {e}")
    #                 errors.append(error_msg)
    #                 print(f"[ERROR] {error_msg}: {e}")

    #     else:
    #         print(f"[DEBUG] Attempting to delete tasks with fake IDs: {task_ids}")

    #         for fake_id in task_ids:
    #             real_id = self.temp_task_map.get(fake_id)
    #             if not real_id or real_id.startswith("MISSING_"):
    #                 print(f"[ERROR] No valid real ID found for fake ID: {fake_id}")
    #                 errors.append("Task not found")
    #                 continue

    #             try:
    #                 print(f"[DEBUG] Deleting task with real ID: {real_id}")

    #                 payload = {"id": real_id, "user_id": self.user_id}
    #                 response = await self.room.local_participant.perform_rpc(
    #                     destination_identity=self.host_name,
    #                     method='delete_todo_agent',
    #                     payload=json.dumps(payload)
    #                 )

    #                 response_data = json.loads(response)
    #                 deleted_ids = response_data.get("ids", [])

    #                 if deleted_ids:
    #                     # Update in-memory cache (using first deleted ID as RPC handles one at a time)
    #                     await self.in_memory_todo_client.delete_task(task_id=deleted_ids[0])

    #                     task_title = response_data.get("title", f"Task {real_id}")
    #                     deleted_titles.append(task_title)
    #                     print(f"[SUCCESS] Deleted task: {task_title}")
    #                 else:
    #                     error_msg = f"Task not found or already deleted"
    #                     errors.append(error_msg)
    #                     print(f"[ERROR] {error_msg} for ID: {real_id}")

    #             except Exception as e:
    #                 error_msg = f"Task deletion failed"
    #                 logging.error(f"Failed to delete task {real_id}: {e}")
    #                 errors.append(error_msg)
    #                 print(f"[ERROR] {error_msg}: {e}")

    #     # Clear temp map after processing all tasks
    #     self.temp_task_map.clear()

    #     # Generate response message
    #     if deleted_titles and not errors:
    #         if len(deleted_titles) == 1:
    #             return "I've deleted the task."
    #         else:
    #             return "I've deleted all the selected tasks."
    #     elif deleted_titles and errors:
    #         if len(deleted_titles) == 1:
    #             return f"I've deleted one task. However, there were some issues with other tasks."
    #         else:
    #             return f"I've deleted {len(deleted_titles)} tasks. However, there were some issues with other tasks."
    #     else:
    #         return "I couldn't delete any of the tasks. Please check if they exist and try again."

    @function_tool
    async def update_todo(
        self,
        task_id: str,
        title: str | None = None,
        note: str | None = None,
        priority: str | None = None,
        deadline: str | None = None,
    ) -> str:
        """
        Update a single task using fake ID.

        Args:
            task_id (str): The fake ID of the task to update.
            title (str | None): Updated title for the task.
            note (str | None): Updated note for the task.
            priority (str | None): Updated priority ("low", "medium", "high").
            deadline (str | None): Updated deadline (RFC 3339 timestamp, e.g., "2025-10-11T15:00:00+05:00").

        Returns:
            str: Confirmation message after updating.
        """
        print("Q")
        print(deadline)
        self.session.userdata.tool_invoked = "update_todo"

        if not task_id:
            return "No task ID provided for update."

        real_id = self.temp_task_map.get(task_id)
        if not real_id or real_id.startswith("MISSING_"):
            print(f"[ERROR] No valid real ID found for fake ID: {task_id}")

            return "I couldn't find the task to update. Please try again."

        try:
            print(f"[DEBUG] Updating task with real ID: {real_id}")

            # Build update payload with only provided fields
            update_data = {
                "id": real_id,
                "user_id": self.user_id,
                "updated_at": datetime.now(
                    pytz.timezone(self.local_timezone)
                ).isoformat(),
            }

            # Add only the fields that were provided
            if title is not None:
                update_data["title"] = title
            if note is not None:
                update_data["note"] = note
            if priority is not None:
                update_data["priority"] = priority
            if deadline is not None:
                # Check if user wants to remove deadline
                if deadline.lower() == "remove":
                    update_data["deadline"] = None
                else:
                    # Validate and handle RFC 3339 format for deadline
                    try:
                        if re.match(RFC_3339_REGEX, deadline):
                            # Already in RFC 3339 format, use as-is
                            update_data["deadline"] = deadline
                        else:
                            # Legacy format: convert to RFC 3339
                            deadline_dt = datetime.strptime(
                                deadline, "%Y-%m-%d %H:%M:%S"
                            )
                            deadline_dt = deadline_dt.replace(
                                tzinfo=pytz.timezone(self.local_timezone)
                            )
                            update_data["deadline"] = deadline_dt.isoformat()
                    except ValueError:
                        logging.error(f"Invalid deadline format: {deadline}")
                        return f"Invalid deadline format. Please use RFC 3339 format (e.g., '2025-10-11T15:00:00+05:00')."

            sanitized_task = sanitize_for_rpc(update_data)
            response = await self.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="update_todo_agent",
                payload=json.dumps(sanitized_task),
            )

            response_data = json.loads(response)
            db_updated_task = response_data

            # Update in-memory cache
            await self.in_memory_todo_client.update_task(db_updated_task)

            task_title = response_data.get("title", f"Task {real_id}")
            print(f"[SUCCESS] Updated task: {task_title}")

            # Clear temp map after successful update
            return "I've updated the task."

        except Exception as e:
            logging.error(f"Failed to update task {real_id}: {e}")
            print(f"[ERROR] Task update failed: {e}")
            return "I couldn't update the task. Please try again."

    @function_tool
    async def delete_all_todos(
        self,
        all_with_status: str | None = None,  # "completed", "pending", or "overdue"
        all_with_priority: List[str] | None = None,  # ["low", "medium", "high"]
    ) -> str:
        """
        Delete tasks based on filters.

        Args:
            all_with_status (str | None): "completed", "pending", or "overdue".
            all_with_priority (List[str] | None): List of priorities to delete.

        Returns:
            str: Summary of deletions performed.
        """
        self.session.userdata.tool_invoked = "delete_all_todos"
        print(
            f"[TOOL] TodoAgent: Deleting tasks with filters: status={all_with_status}, priority={all_with_priority}"
        )

        try:
            # Get all tasks from cache
            all_tasks = await self.in_memory_todo_client.get_todos(
                include_completed=True
            )

            # Filter tasks locally based on criteria
            filtered_tasks = []

            # Current datetime in Karachi timezone
            now = datetime.now(pytz.timezone(self.local_timezone))

            for task in all_tasks:
                # Status filter
                if all_with_status == "completed" and not task.completed:
                    continue
                if all_with_status == "pending" and task.completed:
                    continue
                if all_with_status == "overdue":
                    if task.completed or not task.deadline:
                        continue
                    # Check if task is overdue using full datetime
                    try:
                        deadline_dt = (
                            task.deadline
                            if isinstance(task.deadline, datetime)
                            else datetime.fromisoformat(
                                task.deadline.replace("Z", "+00:00")
                            )
                        )
                        local_tz = pytz.timezone(self.local_timezone)
                        if deadline_dt.tzinfo is not None:
                            deadline_dt = deadline_dt.astimezone(local_tz)
                        else:
                            deadline_dt = local_tz.localize(deadline_dt)

                        if deadline_dt >= now:
                            continue  # Not overdue
                    except Exception as e:
                        print(f"[ERROR] Error parsing deadline for overdue check: {e}")
                        continue

                # Priority filter
                if all_with_priority and task.priority not in all_with_priority:
                    continue

                filtered_tasks.append(task)

            if not filtered_tasks:
                await update_tool_info(
                    self.session.userdata, "delete_all_todos", "NoTasks"
                )
                return "You don't have any tasks matching the given filters."

            deleted_payload = {
                "ids": [t.id for t in filtered_tasks],
                "user_id": filtered_tasks[0].user_id,
            }

            try:
                print(
                    f"[DEBUG] payload being sent for delete_all_todos {deleted_payload}"
                )
                response = await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="delete_all_todos_agent",
                    payload=json.dumps(deleted_payload),
                )

                response_data = json.loads(response)
                deleted_ids = response_data.get("ids", [])

                # delete tasks in memory - delete the specific filtered tasks
                for task in filtered_tasks:
                    await self.in_memory_todo_client.delete_task(task.id)

                print(f"[DEBUG] payload received for delete_all_todos {deleted_ids}")
                print(f"RPC response: {response}")

            except Exception as rpc_error:
                logging.error(f"RPC call failed: {rpc_error}")
                return f"Sorry, I was unable to delete the requested tasks."

            await update_tool_info(self.session.userdata, "delete_all_todos", "Success")
            return f"🗑️ I've permanently deleted {len(deleted_payload['ids'])} tasks from your list."

        except Exception as e:
            await update_tool_info(self.session.userdata, "delete_all_todos", "Error")
            logging.error(f"[ERROR] TodoAgent: Failed to delete tasks - {e}")
            return "Sorry, I encountered an error while deleting tasks."

    @function_tool()
    async def get_task_stats(self) -> str:
        """
        Get statistics about your tasks.

        Returns:
            str: Task count summaries and completion metrics.
        """
        print("[TOOL] TodoAgent: Getting task statistics.")
        self.session.userdata.tool_invoked = "get_task_stats"
        try:
            stats = await self.in_memory_todo_client.get_task_stats()

            result = [
                "📊 **Your Task Statistics:**",
                f"📋 Total tasks: {stats['total']}",
                f"⏳ Pending: {stats['pending']}",
                f"✅ Completed: {stats['completed']}",
                "",
                "**Priority Breakdown (Pending):**",
                f"🔴 High: {stats['high_priority']}",
                f"🟡 Medium: {stats['medium_priority']}",
                f"🟢 Low: {stats['low_priority']}",
            ]

            print("Returning from get task status succesfully")
            return "\n".join(result)

        except Exception as e:
            logging.error(f"[ERROR] TodoAgent: Failed to get stats - {e}")
            return "Sorry, I encountered an error while getting your task statistics."

    @function_tool()
    async def handle_calendar_request(self, context: RunContext):
        """Handles user requests about SCHEDULING: calendar appointments, booking meetings, setting reminders, checking availability, or managing future events."""
        print("Returning from calendar switching succesfully")
        return await self._transfer_to_agent("calendar", context)


def build_todo_system_prompt2(
    modality: str = "voice",
    tz: str = "Asia/Karachi",
    memory_manager=None,
    user_name: str = "",
) -> str:
    """
    Build the todo system prompt dynamically with modality, local time, and user name.

    Args:
        modality (str): Conversation modality (e.g., 'voice', 'text').
        tz (str): Timezone string (default: 'Asia/Karachi').
        memory_manager: Optional BellaMemoryManager instance to inject user memories.
        user_name (str): The current user's display name (from org_participants).

    Returns:
        str: The fully formatted system prompt.
    """

    # include timezone offset explicitly to prevent double conversions
    local_time = datetime.now(pytz.timezone(tz)).isoformat()

    # Inject user memories if available
    memories_section = ""
    if memory_manager:
        memories_section = memory_manager.format_memories_for_prompt()

    return TODO_SYSTEM_PROMPT.format(
        modality=modality.upper(),
        timestamp=local_time,
        tz=tz,
        user_memories=memories_section,
        user_name=user_name or "the user",
    )
