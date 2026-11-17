import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from openai import AsyncOpenAI
import asyncpg
from src.utils.logger import Logger
from src.utils.openai_env import get_openai_api_key_v2_or_primary

logging = Logger(__name__)

BATCH_SIZE = 2
BATCH_INTERVAL_SECONDS = 120
MAX_MEMORIES = 40
MEMORIES_TO_DELETE_WHEN_FULL = 5

# Save to DB interval (seconds) - save after this many operations or time
DB_SAVE_INTERVAL_SECONDS = 30
DB_SAVE_OPERATION_COUNT = 3

# Protected memory keys that should NEVER be deleted (user name is from org_participants, not stored here)
PROTECTED_MEMORY_KEYS = [
    "preferred_address",
    "timezone",
    "location",
    "allergies",
    "medical_conditions",
]

# Protected categories that should NEVER be deleted
PROTECTED_CATEGORIES = [
    "personal_info",
    "critical_info",
]

EXTRACTION_PROMPT = """Analyze the following conversation turns and extract any important information that the user EXPLICITLY stated and wants remembered for future conversations.

CRITICAL: Only extract information that the user EXPLICITLY mentioned or clearly stated. Do NOT infer, assume, or add default values. Do NOT extract information that was not directly stated by the user.

IMPORTANT: If the user mentions information that contradicts or updates existing information, mark it as an UPDATE by using the same key. If the user explicitly says to forget something, mark it as DELETE.

KEY CONSISTENCY (STRICT): Use the SAME key for the same type of fact so updates replace the old value instead of creating duplicates.
- Use stable, consistent keys: lowercase with underscores (e.g. pet_name, profession, timezone, preferred_name).
- If the user corrects or restates the same kind of information (e.g. "my pet name is Lolo" then later "my pet name is Mojo"), you MUST use the EXACT SAME key and action "update". Do NOT create a new key (e.g. "pet", "my_pet") for the same concept.
- Same concept = same key: pet name -> always "pet_name"; job/profession -> "profession"; where they live -> "location"; etc. When in doubt, prefer reusing a key with action "update" over inventing a new key with action "add".

HIGH PRIORITY — ALWAYS extract preferences, attitude, and behavioral/relationship context when the user states them explicitly:
- How the user wants to be addressed: e.g. "Bella always address me as Sir", "call me Alex", "address me as Dr. Smith" -> key preferred_address_form or how_to_address_me, value exactly what they said.
- Relationship/role the user assigns to Bella or to themselves: e.g. "you are my assistant manager", "you are my PA", "I am your boss", "treat me as your client" -> key relationship_context or my_role / bella_role, value what they said.
- Attitude and tone preferences: e.g. "be more formal", "keep it casual", "don't be too chatty", "always be brief" -> key communication_style or tone_preference.
- Behavioral rules: e.g. "always confirm before creating tasks", "never mention my calendar to others", "remind me at 5" (as a standing preference) -> key interaction_preference or behavioral_rule.
These are critical for how Bella should behave; do not skip them.

DO NOT extract or save (these are handled by tool calls, not memory):
- Task names, todo titles, or any specific task/todo content
- Meeting names, event titles, calendar event names, or scheduled event details
- Anything the user is adding/updating/deleting via tasks or calendar — only the tools manage those

Extract ONLY what the user explicitly stated (personal/preference context, not task/calendar data):
- Preferences and how to address them (e.g. "always address me as …", "call me …") — STRONG emphasis, always extract
- Relationship/role context (e.g. "you are my assistant manager", "I am your …", "treat me as …") — STRONG emphasis, always extract
- Attitude, tone, and communication style (formal/casual, brief/verbose, etc.) — STRONG emphasis
- Behavioral rules (how Bella should or should not act, what to always/never do) — STRONG emphasis
- Personal facts (profession, location, family, role, etc.) - if user explicitly mentioned
- Habits/routines (preferred meeting times, work patterns, etc.) - if user explicitly mentioned
- Important context (allergies, constraints, etc.) - if user explicitly stated

Output as JSON array. If nothing important was explicitly stated, return empty array: []

Format example (these are format examples only, extract actual data from conversation):
[
  {{
    "key": "how_to_address_me",
    "value": "Always address me as Sir",
    "category": "preferences",
    "action": "add"
  }},
  {{
    "key": "relationship_context",
    "value": "You are my assistant manager",
    "category": "preferences",
    "action": "add"
  }},
  {{
    "key": "communication_style",
    "value": "Keep it casual and brief",
    "category": "preferences",
    "action": "add"
  }},
  {{
    "key": "profession",
    "value": "software engineer",
    "category": "personal_info",
    "action": "add"
  }}
]

If user explicitly says to forget or delete something, use action: "delete" and the value can be empty.

User messages (extract only from these):
{conversations}
"""


class BellaMemoryManager:
    """Manages user memories stored in PostgreSQL with in-memory cache and background extraction."""

    def __init__(self, user_id: str, org_id: str, db_config: Dict):
        # Convert to int for database (DB expects integer columns)
        self.user_id = int(user_id) if isinstance(user_id, str) else user_id
        self.org_id = int(org_id) if isinstance(org_id, str) else org_id
        self.is_first_time_user = True  # Set in _load_memories_from_db after DB check
        self.conversation_buffer: List[Dict] = []
        self.last_batch_time = datetime.now()
        self.memories_data: Dict = {}
        self._extraction_task: Optional[asyncio.Task] = None
        self._db_pool: Optional[asyncpg.Pool] = None
        self._db_save_pending = False
        self._operation_count = 0
        self._last_db_save = datetime.now()
        # Use provided db_config (from main.py based on environment)
        self.db_config = db_config.copy()
        # Ensure connection pool settings are present
        if "min_size" not in self.db_config:
            self.db_config["min_size"] = 1
        if "max_size" not in self.db_config:
            self.db_config["max_size"] = 5
        # Initialize empty structure immediately (will be loaded from DB)
        self.memories_data = {
            self.user_id: {
                "memories": [],
                "last_updated": datetime.now().isoformat(),
            }
        }

    async def initialize(self):
        """Initialize DB connection and load memories. Must be called after __init__."""
        # Initialize DB pool - raise exception if fails (no fallback)
        self._db_pool = await asyncpg.create_pool(**self.db_config)
        logging.info(f"✅ Connected to PostgreSQL database for user {self.user_id}")

        # Load memories from DB - raise exception if fails (no fallback)
        await self._load_memories_from_db()

    async def _load_memories_from_db(self):
        """Load memories from PostgreSQL database. Raises exception if DB unavailable."""
        async with self._db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT memories FROM bella_memories WHERE org_id = $1 AND user_id = $2",
                self.org_id,
                self.user_id,
            )
            self.is_first_time_user = row is None

            if row is not None:
                # Row exists - load from DB (even if memories is empty)
                db_data = row["memories"]

                # Parse if it's a string (shouldn't happen with asyncpg, but safety check)
                if isinstance(db_data, str):
                    try:
                        db_data = json.loads(db_data)
                    except json.JSONDecodeError:
                        logging.error(f"❌ Failed to parse memories JSON: {db_data}")
                        db_data = {
                            "memories": [],
                            "last_updated": datetime.now().isoformat(),
                        }

                # Ensure it's a dict structure
                if not isinstance(db_data, dict):
                    logging.warning(
                        f"⚠️  Unexpected memories format, initializing empty: {type(db_data)}"
                    )
                    db_data = {
                        "memories": [],
                        "last_updated": datetime.now().isoformat(),
                    }

                # Ensure it has the expected structure
                if "memories" not in db_data:
                    db_data = {
                        "memories": [],
                        "last_updated": datetime.now().isoformat(),
                    }

                self.memories_data = {self.user_id: db_data}
                memory_count = len(db_data.get("memories", []))
                logging.info(
                    f"✅ Loaded {memory_count} memories from DB for user {self.user_id}"
                )
            else:
                # First-time user - no row exists, create initial DB entry
                self.memories_data = {
                    self.user_id: {
                        "memories": [],
                        "last_updated": datetime.now().isoformat(),
                    }
                }
                # Create initial DB entry for first-time user (only if doesn't exist)
                existing = await conn.fetchrow(
                    "SELECT id FROM bella_memories WHERE org_id = $1 AND user_id = $2",
                    self.org_id,
                    self.user_id,
                )
                if not existing:
                    await conn.execute(
                        """
                        INSERT INTO bella_memories (org_id, user_id, memories)
                        VALUES ($1, $2, $3::jsonb)
                        """,
                        self.org_id,
                        self.user_id,
                        json.dumps(self.memories_data[self.user_id]),
                    )
                    logging.info(
                        f"✅ Created new DB entry for first-time user {self.user_id}"
                    )

    async def _save_memories_to_db(self):
        """Save memories to PostgreSQL database. Raises exception if DB unavailable."""
        if not self._db_pool:
            raise RuntimeError("Database pool not initialized")

        user_data = self.memories_data.get(self.user_id, {})
        user_data["last_updated"] = datetime.now().isoformat()

        async with self._db_pool.acquire() as conn:
            # Check if row exists
            existing = await conn.fetchrow(
                "SELECT id FROM bella_memories WHERE org_id = $1 AND user_id = $2",
                self.org_id,
                self.user_id,
            )

            if existing:
                # Update existing row
                await conn.execute(
                    """
                    UPDATE bella_memories 
                    SET memories = $1::jsonb, updated_at = CURRENT_TIMESTAMP
                    WHERE org_id = $2 AND user_id = $3
                    """,
                    json.dumps(user_data),
                    self.org_id,
                    self.user_id,
                )
            else:
                # Insert new row
                await conn.execute(
                    """
                    INSERT INTO bella_memories (org_id, user_id, memories, updated_at)
                    VALUES ($1, $2, $3::jsonb, CURRENT_TIMESTAMP)
                    """,
                    self.org_id,
                    self.user_id,
                    json.dumps(user_data),
                )
        logging.info(f"💾 Saved memories to DB for user {self.user_id}")
        self._db_save_pending = False
        self._operation_count = 0
        self._last_db_save = datetime.now()

    async def _maybe_save_to_db(self):
        """Check if we should save to DB (periodic save)."""
        now = datetime.now()
        time_since_save = (now - self._last_db_save).total_seconds()

        should_save = self._db_save_pending and (
            self._operation_count >= DB_SAVE_OPERATION_COUNT
            or time_since_save >= DB_SAVE_INTERVAL_SECONDS
        )

        if should_save:
            await self._save_memories_to_db()

    def get_user_memories(self) -> List[Dict]:
        """Get memories for current user."""
        user_data = self.memories_data.get(self.user_id, {})

        # Safety check: ensure user_data is a dict
        if not isinstance(user_data, dict):
            logging.warning(
                f"⚠️  user_data is not a dict (type: {type(user_data)}), initializing empty"
            )
            self.memories_data[self.user_id] = {
                "memories": [],
                "last_updated": datetime.now().isoformat(),
            }
            return []

        return user_data.get("memories", [])

    def format_memories_for_prompt(self) -> str:
        """Format user memories for injection into system prompt."""
        memories = self.get_user_memories()
        if not memories:
            return ""

        formatted = "<user_memories>\n"
        for memory in memories:
            key = memory.get("key", "")
            value = memory.get("value", "")
            formatted += f"- {key}: {value}\n"
        formatted += "</user_memories>\n\n"
        return formatted

    async def on_conversation_turn(self, user_msg: str, assistant_msg: str):
        """Called after each conversation turn - fast, non-blocking."""
        if not user_msg or not assistant_msg:
            return

        # Buffer all conversations - extraction LLM will filter for important memories
        self.conversation_buffer.append(
            {
                "user": user_msg,
                "assistant": assistant_msg,
                "timestamp": datetime.now().isoformat(),
            }
        )
        logging.info(f"📝 Buffered conversation turn for user {self.user_id}")

        # Trigger batch processing if needed (non-blocking)
        asyncio.create_task(self._maybe_process_batch())

    async def _maybe_process_batch(self):
        """Check if batch should be processed and trigger extraction."""
        now = datetime.now()
        time_since_batch = (now - self.last_batch_time).total_seconds()

        should_process = len(self.conversation_buffer) >= BATCH_SIZE or (
            len(self.conversation_buffer) > 0
            and time_since_batch >= BATCH_INTERVAL_SECONDS
        )

        if should_process and self.conversation_buffer:
            # Don't start new task if one is already running
            if self._extraction_task and not self._extraction_task.done():
                return

            self._extraction_task = asyncio.create_task(
                self._extract_and_save_memories()
            )

    async def _extract_and_save_memories(self):
        """Background task: Extract memories from buffer and save to JSON."""
        if not self.conversation_buffer:
            return

        try:
            # Prepare conversation context
            conversations_text = "\n\n".join(
                [f"User: {turn['user']}" for turn in self.conversation_buffer]
            )

            # Call extraction LLM
            api_key = get_openai_api_key_v2_or_primary()
            if not api_key:
                logging.error("❌ No OpenAI API key found for memory extraction")
                return

            client = AsyncOpenAI(api_key=api_key)
            prompt = EXTRACTION_PROMPT.format(conversations=conversations_text)

            response = await client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory extraction assistant. PRIORITIZE and ALWAYS extract: (1) how the user wants to be addressed (e.g. 'address me as Sir', 'call me Alex'), (2) relationship/role context (e.g. 'you are my assistant manager', 'I am your boss'), (3) attitude, tone, and communication style (formal/casual, brief/verbose), (4) behavioral rules (how Bella should or should not act). Also extract personal facts and preferences the user explicitly stated. Do NOT extract: task names, todo titles, meeting names, event titles, or calendar/task content — those are handled by tool calls. Use STABLE KEYS; when the user corrects or restates the same fact, use the SAME key and action 'update'. If user says to forget something, mark as 'delete'. Return valid JSON array only. If nothing to extract, return [].",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )

            response_text = response.choices[0].message.content.strip()

            # Parse JSON from response
            try:
                # Try to extract JSON array from response
                import re

                json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
                if json_match:
                    extracted_memories = json.loads(json_match.group())
                else:
                    extracted_memories = json.loads(response_text)
            except json.JSONDecodeError:
                logging.error(
                    f"❌ Failed to parse extraction response: {response_text}"
                )
                extracted_memories = []

            # Update memories
            if extracted_memories:
                # Ensure user entry exists (in case async init hasn't completed)
                if self.user_id not in self.memories_data:
                    self.memories_data[self.user_id] = {
                        "memories": [],
                        "last_updated": datetime.now().isoformat(),
                    }

                user_memories = self.memories_data[self.user_id]["memories"]
                memory_dict = {m["key"]: m for m in user_memories}

                updates_count = 0
                adds_count = 0
                deletes_count = 0

                for memory in extracted_memories:
                    key = memory.get("key")
                    action = memory.get("action", "add").lower()

                    if not key:
                        continue

                    if action == "delete":
                        # Delete memory if it exists
                        if key in memory_dict:
                            del memory_dict[key]
                            deletes_count += 1
                            logging.info(
                                f"🗑️  Deleted memory '{key}' for user {self.user_id}"
                            )
                    elif action == "update":
                        # Update existing memory or add if doesn't exist
                        if key in memory_dict:
                            updates_count += 1
                            logging.info(
                                f"🔄 Updating memory '{key}' for user {self.user_id}"
                            )
                        else:
                            adds_count += 1
                            logging.info(
                                f"➕ Adding new memory '{key}' for user {self.user_id}"
                            )

                        memory_dict[key] = {
                            "key": key,
                            "value": memory.get("value", ""),
                            "category": memory.get("category", "general"),
                            "last_updated": datetime.now().isoformat(),
                        }
                    else:  # action == "add"
                        # Add new memory or update if exists
                        if key in memory_dict:
                            updates_count += 1
                            logging.info(
                                f"🔄 Updating memory '{key}' for user {self.user_id}"
                            )
                        else:
                            adds_count += 1
                            logging.info(
                                f"➕ Adding new memory '{key}' for user {self.user_id}"
                            )

                        memory_dict[key] = {
                            "key": key,
                            "value": memory.get("value", ""),
                            "category": memory.get("category", "general"),
                            "last_updated": datetime.now().isoformat(),
                        }

                # Convert back to list
                self.memories_data[self.user_id]["memories"] = list(
                    memory_dict.values()
                )

                # Check if we exceed MAX_MEMORIES and prune if needed
                if len(self.memories_data[self.user_id]["memories"]) > MAX_MEMORIES:
                    await self._prune_old_memories()

                self.memories_data[self.user_id][
                    "last_updated"
                ] = datetime.now().isoformat()

                # Mark for DB save and trigger periodic save check
                self._db_save_pending = True
                self._operation_count += 1
                await self._maybe_save_to_db()

                action_summary = []
                if adds_count > 0:
                    action_summary.append(f"{adds_count} added")
                if updates_count > 0:
                    action_summary.append(f"{updates_count} updated")
                if deletes_count > 0:
                    action_summary.append(f"{deletes_count} deleted")

                summary = ", ".join(action_summary) if action_summary else "processed"
                logging.info(
                    f"✅ Memory extraction complete for user {self.user_id}: {summary}"
                )

            # Clear buffer
            self.conversation_buffer.clear()
            self.last_batch_time = datetime.now()

        except Exception as e:
            logging.error(f"❌ Error in memory extraction: {e}")
            import traceback

            logging.error(traceback.format_exc())

    async def _prune_old_memories(self):
        """When memories exceed MAX_MEMORIES, use LLM to identify least relevant ones and delete them."""
        user_memories = self.memories_data[self.user_id]["memories"]

        if len(user_memories) <= MAX_MEMORIES:
            return

        try:
            # Prepare memory list for LLM analysis
            memories_text = "\n".join(
                [
                    f"- {m['key']}: {m['value']} (category: {m.get('category', 'general')}, updated: {m.get('last_updated', 'unknown')})"
                    for m in user_memories
                ]
            )

            prune_prompt = f"""Analyze the following user memories and identify the {MEMORIES_TO_DELETE_WHEN_FULL} LEAST RELEVANT ones that can be safely deleted.

CRITICAL RULES:
- NEVER delete memories with keys: {', '.join(PROTECTED_MEMORY_KEYS)}
- NEVER delete memories with categories: {', '.join(PROTECTED_CATEGORIES)}
- NEVER delete memories about: address, timezone, location, allergies, medical conditions
- Prioritize deleting: old preferences that may have changed, temporary habits, outdated work details (if profession is protected)
- Keep: current profession, important preferences, personal facts, critical info

Return ONLY a JSON array of memory keys to delete, e.g.:
["old_preference", "temporary_habit"]

If you cannot safely identify {MEMORIES_TO_DELETE_WHEN_FULL} memories to delete without risking important information, return fewer keys or an empty array.

User memories:
{memories_text}
"""

            api_key = get_openai_api_key_v2_or_primary()
            if not api_key:
                logging.error("❌ No OpenAI API key found for memory pruning")
                return

            client = AsyncOpenAI(api_key=api_key)
            response = await client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory management assistant. Identify least relevant memories that can be safely deleted. Return ONLY a JSON array of memory keys.",
                    },
                    {"role": "user", "content": prune_prompt},
                ],
                temperature=0.2,  # Lower temperature for more conservative pruning
            )

            response_text = response.choices[0].message.content.strip()

            # Parse JSON array of keys to delete
            try:
                import re

                json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
                if json_match:
                    keys_to_delete = json.loads(json_match.group())
                else:
                    keys_to_delete = json.loads(response_text)
            except json.JSONDecodeError:
                logging.error(f"❌ Failed to parse pruning response: {response_text}")
                keys_to_delete = []

            # Filter out protected keys (safety check)
            keys_to_delete = [
                key for key in keys_to_delete if key not in PROTECTED_MEMORY_KEYS
            ]

            # Filter out protected categories (safety check)
            memory_dict = {m["key"]: m for m in user_memories}
            keys_to_delete = [
                key
                for key in keys_to_delete
                if memory_dict.get(key, {}).get("category", "")
                not in PROTECTED_CATEGORIES
            ]

            # Delete the identified memories
            deleted_count = 0
            for key in keys_to_delete:
                if key in memory_dict:
                    del memory_dict[key]
                    deleted_count += 1
                    logging.info(f"🗑️  Pruned memory '{key}' for user {self.user_id}")

            # Update memories
            self.memories_data[self.user_id]["memories"] = list(memory_dict.values())

            if deleted_count > 0:
                logging.info(
                    f"✅ Pruned {deleted_count} memories for user {self.user_id}. "
                    f"Remaining: {len(self.memories_data[self.user_id]['memories'])}"
                )
            else:
                logging.warning(
                    f"⚠️  Could not safely prune memories for user {self.user_id}. "
                    f"Current count: {len(self.memories_data[self.user_id]['memories'])}"
                )

        except Exception as e:
            logging.error(f"❌ Error in memory pruning: {e}")
            import traceback

            logging.error(traceback.format_exc())

    async def cleanup(self):
        """Save any remaining buffered conversations and final DB save before shutdown."""
        if self.conversation_buffer:
            await self._extract_and_save_memories()

        # Final save to DB
        if self._db_save_pending:
            await self._save_memories_to_db()

        # Close DB pool
        if self._db_pool:
            await self._db_pool.close()
            logging.info(f"🔌 Closed database connection for user {self.user_id}")

        logging.info(f"🧹 Cleaned up memory manager for user {self.user_id}")
