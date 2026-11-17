import sys
from pathlib import Path
import token
import dotenv

src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))

import pytz
import os
import json
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Literal
import dotenv
import re
import uuid
import json
from typing import List, Tuple, Optional
import re
from datetime import datetime, timezone
from dateutil import parser
from openai import OpenAI

# Internal application imports
from src.agents.bella.base_agent import BaseAgent
from livekit.agents import Agent, function_tool, RunContext, ChatContext, JobContext
from src.agents.bella.prompts import CALENDAR_SYSTEM_PROMPT
from zoneinfo import ZoneInfo

# from src.utils.logger import Logger


from pydantic import BaseModel, Field
from typing import Optional, Literal, List

import sys
from pathlib import Path
import dotenv

src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))

import pytz
import os
import json
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Literal
import dotenv
import re
import uuid
import json
from typing import List, Tuple, Optional
import re
from datetime import datetime, timezone
from dateutil import parser
from openai import OpenAI

# Internal application imports
from src.agents.bella.base_agent import BaseAgent
from livekit.agents import Agent, function_tool, RunContext, ChatContext, JobContext
from src.agents.bella.prompts import CALENDAR_SYSTEM_PROMPT
from zoneinfo import ZoneInfo
from src.agents.bella.utils import get_time_range

# from src.utils.logger import Logger

import os

from dotenv import load_dotenv

# load values into os.environ first
load_dotenv()

from src.utils.openai_env import get_openai_api_key_v2

alt_open_api_key = get_openai_api_key_v2()


from pydantic import BaseModel, Field
from typing import Optional, Literal, List

RFC_3339_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2})$"
from typing import List, Dict, Any
from rapidfuzz import fuzz, process
import json
import re

dotenv.load_dotenv()
import phonetics
import json
import random
import string
import requests


def normalize(name: str) -> str:
    return name.lower().strip()


def phonetic_code(name: str) -> str:
    tokens = name.split()
    return " ".join(phonetics.metaphone(t) for t in tokens if t)


# logging = Logger('calendar_agent')

from typing_extensions import TypedDict, NotRequired


class ConfirmedEvent(TypedDict, total=False):
    """A confirmed calendar event used for deletion or modification."""

    id: str  # Fake ID (mapped internally to real ID)
    title: str  # Event title (from LLM or calendar)
    startTime: str  # RFC 3339 timestamp (e.g., "2025-10-10T15:00:00+05:00")


class Recurrence(BaseModel):
    """Recurrence rules for meeting scheduling. All fields required when using recurrence."""

    freq: str = Field(description="Frequency: Daily, Weekly, or Monthly")
    until: str = Field(default="", description="End date in RFC 3339 format (optional)")
    daysIncluded: List[str] = Field(
        default_factory=list,
        description="Days for weekly recurrence like ['MO', 'WE', 'FR'] (optional)",
    )
    day: int = Field(
        default=0,
        description="Day of month for monthly recurrence 1-31 (optional, 0 means not set)",
    )


CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

M2M_TOKEN = os.getenv("M2M_TOKEN")

if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
    logging.error("Missing Google Calendar environment variables!")


class CalendarAgent(BaseAgent):
    def __init__(
        self,
        ctx: JobContext,
        host_name: str,
        user_id: str,
        local_time_zone: str,
        chat_ctx: ChatContext = None,
        room=None,
        environment: str | None = None,
        memory_manager=None,
        user_name: str = "",
        calendar_type: str = "google",
    ):
        super().__init__(
            instructions=build_calendar_system_prompt2(
                tz=local_time_zone,
                memory_manager=memory_manager,
                user_name=user_name,
            ),
            chat_ctx=chat_ctx,
        )
        self.ctx = ctx
        self.host_name = host_name
        self.user_id = user_id
        self.room = room
        self.temp_event_map = {}
        self.environment = environment
        self.local_timezone = local_time_zone
        self._calendar_type_fallback = (calendar_type or "google").lower()

        # ✅ Choose API credentials based on environment
        if environment == "STAGING":
            api_url = os.getenv("QLU_M2M_CALENDAR_API_URL_STAGING")
            meeting_url = os.getenv("QLU_MEETING_URL_STAGING")
            print("⚙️ Using STAGING calendar API configuration")
        else:
            api_url = os.getenv("QLU_M2M_CALENDAR_API_URL")
            meeting_url = os.getenv("QLU_MEETING_URL")
            print("⚙️ Using PRODUCTION calendar API configuration")

        # ✅ Always create both managers; .calendar property picks the active one from userdata.calendar_type (mid-session connect works)
        self.google_calendar = GoogleCalendarManager(
            user_id=user_id,
            token=M2M_TOKEN,
            api_url=api_url,
            qlu_meeting_url=meeting_url,
            local_timezone=local_time_zone,
        )
        self.outlook_calendar = OutlookCalendarManager(
            user_id=user_id,
            token=M2M_TOKEN,
            api_url=api_url,
            qlu_meeting_url=meeting_url,
            local_timezone=local_time_zone,
        )

    @property
    def calendar(self):
        """Resolve to the active calendar manager from userdata.calendar_type (so mid-session connect + type switch work)."""
        try:
            session = getattr(self, "session", None)
            if session and getattr(session, "userdata", None):
                ct = (
                    getattr(session.userdata, "calendar_type", None)
                    or self._calendar_type_fallback
                    or "google"
                ).lower()
                return (
                    self.outlook_calendar if ct == "outlook" else self.google_calendar
                )
        except Exception:
            pass
        return self.google_calendar

    async def on_enter(self):
        """Override on_enter to share services via userdata before calling super()."""
        userdata = self.session.userdata
        userdata.calendar_manager = self  # store agent so .calendar property resolves dynamically by userdata.calendar_type
        userdata.user_id = self.user_id
        await super().on_enter()

    def _generate_fake_event_id(self):
        """Generate a short temporary fake ID for events."""
        return "".join(random.choices(string.ascii_letters + string.digits, k=5))

    async def _try_fetch_and_verify_token(self) -> str | None:
        """
        Try to fetch and verify calendar access token once.
        Parses API response as token string or { "token", "type" }; verifies with Google or Outlook based on calendar_type.
        Returns token if successful, None otherwise.
        """
        try:
            payload = {"userIds": [self.user_id]}
            headers = {
                "Authorization": f"Bearer {M2M_TOKEN}",
                "Content-Type": "application/json",
            }
            api_url = (
                os.getenv("QLU_M2M_CALENDAR_API_URL_STAGING")
                if self.environment == "STAGING"
                else os.getenv("QLU_M2M_CALENDAR_API_URL")
            )
            response = requests.post(api_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            payload_list = data.get("data") or []
            if not isinstance(payload_list, list):
                return None
            raw = None
            for item in payload_list:
                if str(item.get("userId")) == str(self.user_id):
                    raw = item
                    break
            if not raw:
                return None

            calendar_token = raw.get("token")
            api_type = (raw.get("calendarType") or raw.get("type") or "google").lower()
            if api_type not in ("google", "outlook"):
                api_type = "google"
            if not calendar_token or (
                isinstance(calendar_token, str) and not calendar_token.strip()
            ):
                return None

            org_participants = getattr(self.session.userdata, "org_participants", [])
            user = next(
                (u for u in org_participants if str(u.get("id")) == str(self.user_id)),
                None,
            )
            if not user or not user.get("email"):
                return None
            expected_email = user["email"].strip().lower()

            # Verify with the type we have (from API or current userdata)
            calendar_type = api_type

            if calendar_type == "outlook":
                # Microsoft Graph GET /me
                res = requests.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={
                        "Authorization": f"Bearer {calendar_token}",
                        "Content-Type": "application/json",
                    },
                    params={"$select": "mail,userPrincipalName"},
                    timeout=8,
                )
                if res.status_code != 200:
                    return None
                info = res.json()
                token_email = (
                    (info.get("mail") or info.get("userPrincipalName") or "")
                    .strip()
                    .lower()
                )
            else:
                # Google userinfo
                res = requests.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {calendar_token}"},
                    timeout=8,
                )
                if res.status_code != 200:
                    return None
                info = res.json()
                token_email = info.get("email", "").strip().lower()

            if token_email == expected_email:
                print(f"Token verified ({calendar_type})")
                # Update userdata so .calendar property resolves to the right manager (mid-session connect)
                try:
                    self.session.userdata.calendar_type = calendar_type
                except Exception:
                    pass
                return calendar_token
            return None
        except Exception as e:
            print(f"⚠️ Failed to fetch and verify token: {e}")
            return None

    # ---------------- FIND EVENT ----------------
    @function_tool()
    async def find_event_by_description(
        self,
        event_description: str,
    ) -> Dict[str, Any]:
        """Find events by description with intelligent LLM-based matching."""
        if not self.session.userdata.access_token:
            # Try fetching once more
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                # Still no token - RPC + return error
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return "Calendar is not connected"

        self.temp_event_map.clear()
        print(
            f"[TOOL] CalendarAgent: Finding event for description '{event_description}'."
        )

        time_min, time_max = get_time_range(
            days_before=10, days_after=30, timezone=self.local_timezone
        )

        print(f"[TOOL] CalendarAgent: Querying events from {time_min} to {time_max}")

        events = await self.calendar.list_events(
            user_calendar_access_token=self.session.userdata.access_token,
            time_min=time_min,
            time_max=time_max,
        )

        raw_events = events.get("items", [])
        if not raw_events:
            return {"confirmed_event": [], "ambiguity_between": []}

        available_events = []
        for event in raw_events:
            if event.get("eventType") == "workingLocation":
                continue

            summary = event.get("summary", "")
            start_time = event.get("start", {}).get("dateTime", "")
            recurring_event_id = event.get("recurringEventId")

            fake_id = self._generate_fake_event_id()
            is_recurring = bool(recurring_event_id)

            available_events.append(
                {
                    "id": fake_id,
                    "title": summary,
                    "startTime": start_time,
                    "recurring": is_recurring,
                }
            )

            self.temp_event_map[fake_id] = {
                "id": event.get("id"),
                "recurringEventId": recurring_event_id,
                "recurring": is_recurring,
            }

        if not available_events:
            return {"confirmed_event": [], "ambiguity_between": []}

        print(f"[DEBUG] Available events: {available_events}")
        print(f"[DEBUG] Temp event map: {self.temp_event_map}")

        event_lines = "\n".join(
            f"- ID: {e['id']}, Title: '{e['title']}', Start: {e['startTime']}, Recurring: {e['recurring']}"
            for e in available_events
        )

        llm_prompt = f"""
        User described an event: "{event_description}"

        Available events:
        {event_lines}

        Instructions:
        Step 1 — Matching:
        - Consider only events whose titles match (exactly or approximately) the user description.
        - Ignore all other events.

        Step 2 — Classification of matched events:
        - If there is ONE unique match (title matches exactly, no duplicates, non-recurring) → put in "confirmed_event".
        - If there are multiple events with the same or similar titles, or multiple recurring instances → put all of them in "ambiguity_between".
        - Include all matching recurring instances, not just a few.

        Output rules:
        - Output JSON with exactly two keys: "confirmed_event", "ambiguity_between".
        - Each event entry must include: id, title, startTime, recurring.
        - Do not include unrelated events in either section.

        Expected JSON format:
        {{
            "confirmed_event": [{{"id": "abc12", "title": "Team Meeting", "startTime": "...", "recurring": false}}],
            "ambiguity_between": [{{"id": "def34", "title": "Weekly Sync", "startTime": "...", "recurring": true}}, ...]
        }}
        """

        client = OpenAI(api_key=alt_open_api_key)
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": llm_prompt}],
            temperature=0,
        )

        reply_text = response.choices[0].message.content.strip()
        print("[TOOL] LLM raw reply:", reply_text)

        json_blocks = re.findall(
            r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", reply_text, flags=re.DOTALL
        )

        parsed = None
        for json_block in json_blocks:
            try:
                candidate = json.loads(json_block.strip())
                if all(
                    k in candidate for k in ["confirmed_event", "ambiguity_between"]
                ):
                    parsed = candidate
                    break
            except Exception as e:
                print(f"[DEBUG] Failed JSON parse: {e}")
                continue

        if parsed is None:
            print("[ERROR] No valid JSON found in LLM reply")
            return {"confirmed_event": [], "ambiguity_between": []}

        for section in ["confirmed_event", "ambiguity_between"]:
            for e in parsed.get(section, []):
                fake_id = e.get("id")
                if fake_id not in self.temp_event_map:
                    print(f"[WARNING] Unknown fake ID {fake_id} in {section}")

        print(f"[DEBUG] Final parsed result: {parsed}")
        return parsed

    # ---------------- UPDATE EVENT ----------------
    @function_tool()
    async def update_calendar_event(
        self,
        confirmed_event: ConfirmedEvent,
        recurring: bool,
        summary: str | None = None,
        description: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        emails: List[str] | None = None,
    ) -> str:
        """Update a calendar event (recurring-aware)."""
        if not self.session.userdata.access_token:
            # Try fetching once more
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                # Still no token - RPC + return error
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return "Calendar is not connected"

        if all(
            param is None
            for param in [summary, description, start_time, end_time, emails]
        ):
            return "❌ At least one field must be provided to update the event."

        fake_id = confirmed_event.get("id")
        mapping = self.temp_event_map.get(fake_id)

        if not mapping:
            return "❌ Could not find event mapping. Please search again."

        event_id = mapping["recurringEventId"] if recurring else mapping["id"]
        print(f"[DEBUG] Updating event (recurring={recurring}) with ID: {event_id}")

        try:
            calendar_response = await self.calendar.update_google_meet_event(
                user_calendar_access_token=self.session.userdata.access_token,
                event_id=event_id,
                summary=summary,
                description=description,
                start_time=start_time,
                end_time=end_time,
                emails=emails,
            )

            if not calendar_response or calendar_response.get("error"):
                err_msg = (
                    calendar_response.get(
                        "error", "Empty or invalid response from calendar"
                    )
                    if isinstance(calendar_response, dict)
                    else "Empty response from calendar"
                )
                print(f"❌ [update_calendar_event] Calendar manager error: {err_msg}")
                return f"❌ Error updating event: {err_msg}"

            result = {
                "id": calendar_response["id"],
                "title": calendar_response.get("summary", ""),
                "startTime": calendar_response["start"]["dateTime"],
                "endTime": calendar_response["end"]["dateTime"],
                "participants": [
                    att["email"] for att in calendar_response.get("attendees", [])
                ],
                "platform": (
                    "GOOGLE_MEET" if "hangoutLink" in calendar_response else "QLU_WORLD"
                ),
            }

            print("Before RPC")
            print(f"RPC Payload: {json.dumps(result)}")
            await self.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="update_agenda_agent",
                payload=json.dumps(result),
            )

            print("✅ [update_calendar_event] Event updated successfully.")
            return "✅ Event updated successfully."
        except Exception as err:
            print(f"❌ [update_calendar_event] Exception: {err}")
            return f"❌ Error updating event: {err}"

    # ---------------- DELETE EVENT ----------------
    @function_tool()
    async def delete_calendar_event(
        self, confirmed_event: ConfirmedEvent, recurring: bool
    ) -> str:
        """Delete a calendar event (recurring-aware)."""
        if not self.session.userdata.access_token:
            # Try fetching once more
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                # Still no token - RPC + return error
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return "Calendar is not connected"

        fake_id = confirmed_event.get("id")
        mapping = self.temp_event_map.get(fake_id)

        if not mapping:
            return "❌ Could not find event mapping. Please search again."

        event_id = mapping["recurringEventId"] if recurring else mapping["id"]
        print(f"[DEBUG] Deleting event (recurring={recurring}) with ID: {event_id}")

        try:
            await self.calendar.delete_google_calendar_event(
                user_calendar_access_token=self.session.userdata.access_token,
                event_id=event_id,
            )

            result = {"id": event_id, "startTime": confirmed_event.get("startTime", "")}
            await self.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="delete_agenda_agent",
                payload=json.dumps(result),
            )

            return "✅ Event deleted successfully."
        except Exception as err:
            return f"❌ Error deleting event: {err}"

    async def _get_event_by_description_with_startTime(
        self, meeting_description: str
    ) -> str | None:
        events = await self.calendar.list_events(
            user_calendar_access_token=self.session.userdata.access_token,
            time_min="2025-10-01T00:00:00+00:00",
            time_max="2025-10-31T00:00:00+00:00",
        )

        raw_events = events.get("items", [])
        if not raw_events:
            return None

        # Prepare simplified event list
        duplicated_available_events = []

        for event in raw_events:
            if event.get("eventType") == "workingLocation":
                continue  # skip all-day office / working location events

            summary = event.get("summary", "")
            description = event.get("description", "")
            desc = f"{summary} - {description}".strip(" -")

            duplicated_available_events.append(
                {
                    "text": desc or "<No description>",
                    "id": event.get("id"),
                    "startTime": event.get("start", {}).get("dateTime"),
                }
            )

        # Build prompt with correct numbering
        prompt_lines = [
            f"{idx + 1}. {e['text']}"
            for idx, e in enumerate(duplicated_available_events)
        ]
        llm_prompt = f"""
    You are given a user meeting description: "{meeting_description}"

    Here is a list of events with their summaries and descriptions:
    {chr(10).join(prompt_lines)}

    Which event number (1-{len(duplicated_available_events)}) best matches the user's description?

    Respond with ONLY the event number (e.g., "3"), or "0" if no good match exists.
    """

        try:
            client = OpenAI(api_key=alt_open_api_key)
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": llm_prompt}],
                temperature=0,
            )

            reply_text = response.choices[0].message.content.strip()
            print("Open AI LLM reply:", reply_text)

            match = re.search(r"\b\d+\b", reply_text)
            if not match:
                return None

            chosen_number = int(match.group(0))
            if 1 <= chosen_number <= len(duplicated_available_events):
                selected_event = duplicated_available_events[chosen_number - 1]
                return {
                    "id": selected_event["id"],
                    "startTime": selected_event["startTime"],
                }

            return None

        except Exception as e:
            print("Failed to parse Groq event match:", e)
            return None

    @function_tool()
    async def resolve_participants_from_query(
        self,
        query_names: List[str],
        provided_emails: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Resolves participant names from query_names (org lookup only).
        provided_emails is not used for resolution; it is echoed back in the output
        so the caller can combine it with confirmed_participants when scheduling.

        Args:
            query_names: List of participant names to resolve from org (e.g. ["John", "Nina"]).
            provided_emails: List of emails the user already gave. Pass-through only;
                returned as-is in the "provided_emails" key. No merge into confirmed_participants.

        Returns:
            {
                "confirmed_participants": List[Dict[str, str]] (resolved from query_names only),
                "confused_participants_query": str,
                "confused_participants_data": list,
                "provided_emails": list of strings (same as input provided_emails)
            }
        """
        provided_emails = provided_emails or []
        provided_emails = [e.strip() for e in provided_emails if e and str(e).strip()]

        if not self.session.userdata.access_token:
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                print("Calling RPC of calendar not connected")
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return {
                    "confirmed_participants": [],
                    "confused_participants_query": "Calendar is not connected",
                    "confused_participants_data": [],
                    "provided_emails": provided_emails,
                }

        try:
            org_participants = self.session.userdata.org_participants
            print("✅ Loaded org participants (first 5):", org_participants[:5])

            org_names = [p["name"] for p in org_participants]
            name_to_participant = {p["name"]: p for p in org_participants}

            query_tokens = [t.strip() for t in query_names if t and t.strip()]

            if not query_tokens:
                return {
                    "confirmed_participants": [],
                    "confused_participants_query": "I couldn't detect any participant names in your request.",
                    "confused_participants_data": [],
                    "provided_emails": provided_emails,
                }

            confirmed = []
            confused = []
            not_found = []

            # --- Step 1: Word-by-word matching for each query ---
            for token in query_tokens:
                print(f"\n🔎 Processing query: {token}")

                # Split query into words
                query_words = token.split()
                print(f"  Split into words: {query_words}")

                # Get candidates for each word
                word_candidates = []
                for word in query_words:
                    word_cands = self._get_word_candidates(
                        word, org_names, name_to_participant
                    )
                    word_candidates.append(word_cands)
                    print(
                        f"  Word '{word}' candidates: {[c['name'] for c in word_cands[:3]]}"
                    )

                # Combine word matches to get overall candidates
                overall_candidates = self._combine_word_candidates(
                    word_candidates, name_to_participant
                )
                print(
                    f"  Overall candidates: {[c['name'] for c in overall_candidates[:5]]}"
                )

                if len(overall_candidates) == 0:
                    not_found.append(token)
                elif len(overall_candidates) == 1:
                    participant = overall_candidates[0]
                    confirmed.append(
                        {"name": participant["name"], "email": participant["email"]}
                    )
                    print(f"  ✅ Auto-resolved: {participant['name']}")
                else:
                    # Multiple candidates - need LLM disambiguation
                    confused.append(
                        {"query_name": token, "pick_from": overall_candidates[:8]}
                    )
                    print(
                        f"  🤔 Needs LLM disambiguation: {len(overall_candidates)} candidates"
                    )

            print("\n🧩 Initial confused matches:", json.dumps(confused, indent=2))

            # --- Step 2: Batch LLM refinement ---
            if confused:
                try:
                    from openai import OpenAI

                    client = OpenAI(api_key=alt_open_api_key)

                    # Batch process all confused queries in a single LLM call
                    print(f"\n🤖 LLM batch processing {len(confused)} queries...")
                    resolved_items, remaining_confused = (
                        await self._llm_batch_refine_candidates(client, confused)
                    )

                    for item in resolved_items:
                        participant = item["participant"]
                        confirmed.append(
                            {"name": participant["name"], "email": participant["email"]}
                        )
                        print(f"  ✅ LLM resolved: {participant['name']}")

                    # Update confused list with remaining items
                    confused = remaining_confused
                    for item in confused:
                        print(f"  🤔 LLM needs clarification for: {item['query_name']}")
                except asyncio.CancelledError:
                    print(
                        "⚠️ LLM refinement cancelled (async interruption) - returning original confused list"
                    )
                    # Don't re-raise - just continue with original confused list
                    pass
                except Exception as e:
                    print(f"⚠️ LLM refinement skipped due to error: {e}")
                    import traceback

                    traceback.print_exc()

            # --- Step 3: Build human-readable confused query ---
            confused_query = ""
            if confused:
                parts = []
                for c in confused:
                    query_name = c["query_name"]
                    options = [m["name"] for m in c["pick_from"]]

                    # Intelligently filter options to only show similar candidates
                    llm_reasoning = c.get("llm_reasoning", "")
                    reduced_candidates = c.get("reduced_candidates", [])

                    if reduced_candidates:
                        # Use LLM's reduced candidates directly
                        filtered_options = reduced_candidates
                        print(f"  🔍 Using LLM reduced candidates: {filtered_options}")
                    else:
                        # Fallback to intelligent filtering
                        filtered_options = self._filter_similar_candidates(
                            query_name, options, llm_reasoning
                        )

                    if len(filtered_options) == 2:
                        parts.append(
                            f"Did you mean {filtered_options[0]} or {filtered_options[1]} for '{query_name}'?"
                        )
                    elif len(filtered_options) > 2:
                        formatted = (
                            ", ".join(filtered_options[:-1])
                            + f", or {filtered_options[-1]}"
                        )
                        parts.append(f"Did you mean {formatted} for '{query_name}'?")
                    else:
                        # Fallback to original options if filtering fails
                        if len(options) == 2:
                            parts.append(
                                f"Did you mean {options[0]} or {options[1]} for '{query_name}'?"
                            )
                        else:
                            formatted = ", ".join(options[:-1]) + f", or {options[-1]}"
                            parts.append(
                                f"Did you mean {formatted} for '{query_name}'?"
                            )
                confused_query = " ".join(parts)

            if not_found:
                nf_text = f"I couldn't find: {', '.join(not_found)}."
                confused_query = (confused_query + " " + nf_text).strip()

            print("\n✅ Confirmed participants:", json.dumps(confirmed, indent=2))
            print("🌀 Confused query:", confused_query.strip())

            return {
                "confirmed_participants": confirmed,
                "confused_participants_query": confused_query.strip(),
                "confused_participants_data": confused,
                "provided_emails": provided_emails,
            }

        except Exception as e:
            print("❌ Failed to resolve participants:", e)
            return {
                "confirmed_participants": [],
                "confused_participants_query": (
                    "I had trouble understanding the participant names. "
                    "Could you clarify who you'd like to invite?"
                ),
                "confused_participants_data": [],
                "provided_emails": provided_emails,
            }

    def _get_word_candidates(
        self, word: str, org_names: List[str], name_to_participant: Dict
    ) -> List[Dict]:
        """Get top candidates for a single word using multiple matching strategies."""
        candidates = []

        # Strategy 1: Exact match (case-insensitive)
        for name in org_names:
            name_words = name.split()
            for name_word in name_words:
                if word.lower() == name_word.lower():
                    participant = name_to_participant[name]
                    candidates.append(
                        {
                            "name": participant["name"],
                            "email": participant["email"],
                            "score": 100,
                            "strategy": "exact",
                        }
                    )

        # Strategy 2: Phonetic match
        try:
            word_ph = phonetic_code(word.lower())
            for name in org_names:
                name_words = name.split()
                for name_word in name_words:
                    name_ph = phonetic_code(name_word.lower())
                    ph_score = fuzz.ratio(word_ph, name_ph)
                    if ph_score >= 80:
                        participant = name_to_participant[name]
                        candidates.append(
                            {
                                "name": participant["name"],
                                "email": participant["email"],
                                "score": ph_score,
                                "strategy": "phonetic",
                            }
                        )
        except:
            pass

        # Strategy 3: Spelling variation (fuzzy with higher threshold)
        for name in org_names:
            name_words = name.split()
            for name_word in name_words:
                fuzzy_score = fuzz.ratio(word.lower(), name_word.lower())
                if fuzzy_score >= 85:
                    participant = name_to_participant[name]
                    candidates.append(
                        {
                            "name": participant["name"],
                            "email": participant["email"],
                            "score": fuzzy_score,
                            "strategy": "spelling",
                        }
                    )

        # Strategy 4: Fuzzy match (lower threshold)
        for name in org_names:
            name_words = name.split()
            for name_word in name_words:
                fuzzy_score = fuzz.ratio(word.lower(), name_word.lower())
                if fuzzy_score >= 70:
                    participant = name_to_participant[name]
                    candidates.append(
                        {
                            "name": participant["name"],
                            "email": participant["email"],
                            "score": fuzzy_score,
                            "strategy": "fuzzy",
                        }
                    )

        # Remove duplicates and sort by score
        unique_candidates = {}
        for candidate in candidates:
            key = candidate["name"]
            if (
                key not in unique_candidates
                or candidate["score"] > unique_candidates[key]["score"]
            ):
                unique_candidates[key] = candidate

        sorted_candidates = sorted(
            unique_candidates.values(), key=lambda x: x["score"], reverse=True
        )

        # If all candidates have the same score (exact matches), return all of them
        # Otherwise, return top 5
        if len(sorted_candidates) > 0:
            top_score = sorted_candidates[0]["score"]
            all_same_score = all(c["score"] == top_score for c in sorted_candidates)
            if all_same_score:
                return sorted_candidates  # Return all when scores are equal
            else:
                return sorted_candidates[:5]  # Return top 5 when scores differ

        return sorted_candidates

    def _combine_word_candidates(
        self, word_candidates: List[List[Dict]], name_to_participant: Dict
    ) -> List[Dict]:
        """Combine word candidates to get overall candidates for a multi-word query."""
        if not word_candidates:
            return []

        if len(word_candidates) == 1:
            return word_candidates[0]

        # For multi-word queries, find participants that match multiple words
        combined_candidates = {}

        for word_cands in word_candidates:
            for candidate in word_cands:
                name = candidate["name"]
                if name in combined_candidates:
                    # Increase score for multi-word matches
                    combined_candidates[name]["score"] += candidate["score"] * 0.5
                else:
                    combined_candidates[name] = candidate.copy()

        return sorted(
            combined_candidates.values(), key=lambda x: x["score"], reverse=True
        )

    def _try_alternative_matching(
        self, query_name: str, org_names: List[str], name_to_participant: Dict
    ) -> List[Dict]:
        """Try alternative matching strategies when initial matching fails."""
        print(f"  🔄 Trying alternative strategies for: {query_name}")

        # Try substring matching
        candidates = []
        query_lower = query_name.lower()

        for name in org_names:
            name_lower = name.lower()
            if query_lower in name_lower or name_lower in query_lower:
                participant = name_to_participant[name]
                candidates.append(
                    {
                        "name": participant["name"],
                        "email": participant["email"],
                        "score": 90,
                        "strategy": "substring",
                    }
                )

        # Try common name variations (phonetic-based)
        try:
            query_lower = query_name.lower()
            for name in org_names:
                name_lower = name.lower()
                # Check if query is a phonetic variation of any part of the name
                name_parts = name_lower.split()
                for name_part in name_parts:
                    # Use phonetic matching for variations
                    if len(query_lower) >= 3 and len(name_part) >= 3:
                        phonetic_score = fuzz.ratio(
                            phonetic_code(query_lower), phonetic_code(name_part)
                        )
                        if phonetic_score >= 85:
                            participant = name_to_participant[name]
                            candidates.append(
                                {
                                    "name": participant["name"],
                                    "email": participant["email"],
                                    "score": phonetic_score,
                                    "strategy": "phonetic_variation",
                                }
                            )
                            break
        except:
            pass

        return candidates[:5]

    async def _llm_batch_refine_candidates(
        self, client, confused_items: List[Dict]
    ) -> tuple[List[Dict], List[Dict]]:
        """Batch process all confused queries in a single LLM call for better performance."""
        if not confused_items:
            return [], []

        batch_prompt = f"""
        You are a name matching assistant. You will process multiple name-matching queries together and decide for each one whether it can be confidently resolved or needs clarification.

        Each query contains:
        - A "query_name" (the name typed by the user)
        - A list of "candidates" (people with similar names)

        Your job for EACH query:
        1. Choose the **most likely match** when confident.
        2. Ask for **clarification** only when multiple strong candidates exist or none are clearly correct.

        Guidelines:
        1. Prefer **exact matches** immediately.
        2. Prefer **strong phonetic or spelling similarity** (e.g., John ≈ Jon, Sara ≈ Sarah).
        3. **Do not** pick random names when unsure — only ask for clarification.
        4. **Clarifications should only include 2–3 most similar candidates**.
        5. When reducing candidates, remove any that clearly do **not** resemble the query (e.g., "Merk" → remove "Nathan Miles").
        6. Only skip if all candidates are clearly unrelated.
        7. **Ask for clarification only when multiple candidates share the same or very similar core name** (e.g., multiple Johns or multiple Smiths).

        Examples:

        Query: "john"
        Candidates: ["John Doe", "Johnny Smith"]
        → Choose "John Doe" (Exact match; high confidence)

        Query: "joseph"
        Candidates: ["Joseph Brown", "Joshua Wilson"]
        → Choose "Joseph Brown" (Exact first name match)

        Query: "sara"
        Candidates: ["Sarah Johnson", "Michael Smith"]
        → Choose "Sarah Johnson" (Spelling resemblance: sara ≈ sarah)

        Query: "tom"
        Candidates: ["Tom Wilson", "Tim Johnson"]
        → Choose "Tom Wilson" (Exact partial match: tom ≈ tom)

        Query: "martha jones"
        Candidates: ["Martha Jones"]
        → Choose "Martha Jones" (Perfect match)

        Query: "john"
        Candidates: ["Jon Smith", "Jon Wilson", "Jane Perry", "Mike Johnson"]
        → Ask for clarification (Multiple similar candidates Jon Smith and Jon Wilson; other names are unrelated)
        → reduced_candidates: ["Jon Smith", "Jon Wilson"]

        Query: "smith"
        Candidates: ["John Smith", "Jane Smith", "Mike Johnson", "Sarah Wilson"]
        → Ask for clarification (Multiple strong matches with same surname)
        → reduced_candidates: ["John Smith", "Jane Smith"]

        Query: "felope"
        Candidates: ["Felipe Johns", "Alex Felipe", "Maria Fel"]
        → Ask for clarification (Felipe Johns and Alex Felipe both phonetically close)
        → reduced_candidates: ["Felipe Johns", "Alex Felipe"]

        Now, process these queries:

        {json.dumps([{"query_name": item["query_name"], "candidates": [c["name"] for c in item["pick_from"]]} for item in confused_items], indent=2)}

        Respond in JSON format:
        {{
        "resolved": [
        {{"query_name": "query", "chosen": "Full Name", "reasoning": "why chosen"}}
        ],
        "clarifications": [
        {{"query_name": "query", "reasoning": "why clarification needed", "reduced_candidates": ["Name1", "Name2"]}}
        ]
        }}
        """

        try:
            # Run the synchronous OpenAI API call in a thread with timeout to prevent indefinite hangs
            # Timeout set to 30 seconds - should be more than enough for API response
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-4.1",
                    messages=[{"role": "user", "content": batch_prompt}],
                    temperature=0,
                ),
                timeout=30.0,
            )

            reply_text = response.choices[0].message.content.strip()
            print(f"  🔍 LLM batch response: {reply_text}")

            import re

            json_match = re.search(r"\{[\s\S]*\}", reply_text)
            if json_match:
                result = json.loads(json_match.group(0))

                resolved_items = []
                remaining_confused = []

                # Process resolved items
                for item in result.get("resolved", []):
                    query_name = item["query_name"]
                    chosen_name = item["chosen"]

                    # Find the original confused item
                    for confused_item in confused_items:
                        if confused_item["query_name"] == query_name:
                            # Find the chosen candidate
                            for candidate in confused_item["pick_from"]:
                                if candidate["name"] == chosen_name:
                                    resolved_items.append(
                                        {
                                            "query_name": query_name,
                                            "participant": candidate,
                                            "reasoning": item.get("reasoning", ""),
                                        }
                                    )
                                    break
                            break

                # Process clarification items
                for item in result.get("clarifications", []):
                    query_name = item["query_name"]
                    reasoning = item.get("reasoning", "")
                    reduced_candidates = item.get("reduced_candidates", [])

                    # Find the original confused item
                    for confused_item in confused_items:
                        if confused_item["query_name"] == query_name:
                            # Update with LLM reasoning and reduced candidates
                            confused_item["llm_reasoning"] = reasoning
                            confused_item["reduced_candidates"] = reduced_candidates
                            remaining_confused.append(confused_item)
                            break

                return resolved_items, remaining_confused

        except asyncio.TimeoutError:
            print(f"  ⚠️ LLM batch error: API call timed out after 30 seconds")
            return [], confused_items
        except Exception as e:
            print(f"  ⚠️ LLM batch error: {e}")
            return [], confused_items

    def _filter_similar_candidates(
        self, query_name: str, options: List[str], llm_reasoning: str = ""
    ) -> List[str]:
        """Intelligently filter candidates to only show similar ones for clarification."""
        if len(options) <= 2:
            return options

        # Try to parse LLM reasoning for specific candidates mentioned
        if llm_reasoning and "between" in llm_reasoning.lower():
            import re

            # Extract names mentioned in "between X and Y" pattern
            between_match = re.search(
                r"between\s+([^,]+?)\s+and\s+([^,]+)", llm_reasoning, re.IGNORECASE
            )
            if between_match:
                mentioned_names = [
                    between_match.group(1).strip(),
                    between_match.group(2).strip(),
                ]
                # Filter to only show mentioned candidates
                filtered = [
                    opt
                    for opt in options
                    if any(mentioned in opt for mentioned in mentioned_names)
                ]
                if filtered:
                    return filtered

        # Fallback: Extract first names from options
        first_names = []
        for option in options:
            name_parts = option.split()
            if name_parts:
                first_names.append(name_parts[0].lower())

        # Find the most common first name pattern
        from collections import Counter

        name_counts = Counter(first_names)
        most_common_name = name_counts.most_common(1)[0][0] if name_counts else None

        if most_common_name and name_counts[most_common_name] >= 2:
            # Filter to only show candidates with the most common first name
            filtered = [
                opt for opt in options if opt.split()[0].lower() == most_common_name
            ]
            return filtered[:3]  # Limit to 3 similar candidates

        # If no clear pattern, return top 3 candidates
        return options[:3]

    @function_tool
    async def schedule_meeting_with_participants(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        participant_emails: List[str],
        description: str = "",
        platform_type: str = "QLU_WORLD",
        recurrence_freq: str = "",
        recurrence_until: str = "",
        recurrence_days: Optional[List[str]] = None,
        recurrence_day: int = 0,
    ) -> str:
        """
        Creates a calendar event with given participants on the selected platform.

        Args:
            summary (str): Title of the meeting.
            start_time (str): RFC 3339 datetime string with offset.
            end_time (str): RFC 3339 datetime string with offset.
            participant_emails (List[str]): List of participant emails.
            description (str): Optional description of the meeting.
            platform_type (str): "GOOGLE_MEET" or "QLU_WORLD" (default: "QLU_WORLD").
            recurrence_freq (str): Recurrence frequency: "Daily", "Weekly", or "Monthly". Empty string for no recurrence.
            recurrence_until (str): End date in RFC 3339 format (optional).
            recurrence_days (List[str]): Days for weekly recurrence like ["MO", "WE", "FR"] (optional).
            recurrence_day (int): Day of month for monthly recurrence 1-31 (optional, 0 means not set).

        Returns:
            str: Confirmation message with meeting details.
        """
        if not self.session.userdata.access_token:
            # Try fetching once more
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                # Still no token - RPC + return error
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return "Calendar is not connected"

        print(start_time, end_time)

        # Build recurrence dict from flat parameters
        if recurrence_freq:
            recurrence_dict = {
                "freq": recurrence_freq,
                "until": recurrence_until if recurrence_until else None,
                "daysIncluded": recurrence_days if recurrence_days else None,
                "day": recurrence_day if recurrence_day > 0 else None,
            }
        else:
            recurrence_dict = {"freq": None}

        user_email = next(
            (
                p["email"]
                for p in self.session.userdata.org_participants
                if int(p["id"]) == int(self.user_id)
            ),
            None,
        )

        # Append only if found and not already in the list
        if user_email and user_email not in participant_emails:
            participant_emails.append(user_email)

        print(participant_emails)

        try:
            platform_type = platform_type.upper()
            if platform_type not in ("GOOGLE_MEET", "QLU_WORLD"):
                return f"❌ Unsupported platform: {platform_type}"

            # Call the appropriate calendar function
            if platform_type == "GOOGLE_MEET":
                calendar_response = await self.calendar.create_google_meet_event(
                    user_calendar_access_token=self.session.userdata.access_token,
                    start_time=start_time,
                    end_time=end_time,
                    summary=summary,
                    description=description,
                    emails=participant_emails,
                    recurrence=recurrence_dict,
                )

                # Extract Google Meet link from conferenceData
                conference = calendar_response.get("conferenceData", {})
                entry_points = conference.get("entryPoints", [])
                meet_link = ""
                for ep in entry_points:
                    if ep.get("entryPointType") == "video":
                        meet_link = ep.get("uri")
                        break

                location = meet_link
                platform_name = conference.get("conferenceSolution", {}).get("name", "")

            else:  # QLU_WORLD
                calendar_response = await self.calendar.create_qlu_meet_event(
                    user_calendar_access_token=self.session.userdata.access_token,
                    start_time=start_time,
                    end_time=end_time,
                    summary=summary,
                    description=description,
                    emails=participant_emails,
                    recurrence=recurrence_dict,
                )
                location = calendar_response.get("location", "")
                platform_name = "QLU_WORLD"

            # Construct result payload
            result = {
                "id": calendar_response.get("id", ""),
                "title": calendar_response.get("summary", ""),
                "startTime": calendar_response.get("start", {}).get("dateTime", ""),
                "endTime": calendar_response.get("end", {}).get("dateTime", ""),
                "participants": [
                    att["email"] for att in calendar_response.get("attendees", [])
                ],
                "platform": platform_name,
                "location": location,
            }

            print("RPC Payload")
            print(json.dumps(result))

            await self.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="schedule_agenda_agent",
                payload=json.dumps(result),
            )

            if "error" in calendar_response:
                return f"❌ Failed to schedule a meeting"
            else:
                return f"✅ Event '{summary}' was created successfully"

        except Exception as err:
            return f"❌ Error in scheduling event '{summary}': {err}"

    @function_tool
    async def list_events(
        self, start_time: str, end_time: str, search: str | None = None
    ) -> str:
        """
        Lists calendar events within a specific time range, optionally filtered by a search keyword.

        Args:
            start_time: RFC 3339 datetime string with offset (e.g., '2025-08-06T00:00:00+05:00').
            end_time: RFC 3339 datetime string with offset (e.g., '2025-08-06T00:00:00+05:00').
            search: Optional keyword to filter events by title or description.

        Returns:
            A formatted string of events with index, event title, start/end time, participants, and platform.
            If no events are found or input is invalid, returns a friendly message.
        """
        if not self.session.userdata.access_token:
            # Try fetching once more
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                # Still no token - RPC + return error
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return "Calendar is not connected"

        # speech_handle = self.session.say(text="One Moment", add_to_chat_ctx=False)

        # Validate RFC3339 format
        for label, value in [("start_time", start_time), ("end_time", end_time)]:
            if value and not re.match(RFC_3339_REGEX, value):
                return f"❌ Invalid {label}. Please use 'YYYY-MM-DDTHH:MM:SS±HH:MM' format."

        # Normalize to ISO with timezone offset (keep local tz intact)
        try:
            if start_time:
                start_time_dt = datetime.fromisoformat(start_time)
                start_time = start_time_dt.isoformat()
            if end_time:
                end_time_dt = datetime.fromisoformat(end_time)
                end_time = end_time_dt.isoformat()
        except Exception as e:
            return f"❌ Error parsing dates: {str(e)}"

        try:
            events = await self.calendar.list_events(
                user_calendar_access_token=self.session.userdata.access_token,
                time_min=start_time,
                time_max=end_time,
                query=search,
            )

            items = events.get("items", [])
            if not items:
                # Still make RPC call with empty data
                print("RPC PAYLOAD (empty)")
                print(json.dumps([]))
                response = await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="fetch_agendas_agent",
                    payload=json.dumps([]),
                )
                print(response)
                return "🟡 No events found in the given time range."

            # Build email → name lookup from session participants
            participant_lookup = {
                p["email"]: p["name"] for p in self.session.userdata.org_participants
            }

            formatted = []
            filtered_events = []

            for i, e in enumerate(items, 1):

                if e.get("eventType") == "workingLocation":
                    continue  # skip all-day office / working location events

                id = e.get("id")
                summary = e.get("summary", "")
                start = e.get("start", {}).get("dateTime") or e.get("start", {}).get(
                    "date", ""
                )
                end = e.get("end", {}).get("dateTime") or e.get("end", {}).get(
                    "date", ""
                )

                # Map attendees to names
                attendees = e.get("attendees", [])
                if attendees:
                    participants = [
                        participant_lookup.get(a.get("email", ""), a.get("email", ""))
                        for a in attendees
                    ]
                    participants_str = ", ".join(participants)
                else:
                    participants = []
                    participants_str = "None"

                # Determine platform name: Check for QLU_WORLD first, then GOOGLE_MEET, default to GOOGLE_MEET
                platform = "GOOGLE_MEET"  # Default fallback

                # Check for QLU_WORLD indicators
                is_qlu_world = False

                # Check location/description for QLU World links (for events without conferenceData)
                location = e.get("location", "").lower()
                description = e.get("description", "").lower()
                if (
                    "world.qlu.ai" in location
                    or "world.qlu.ai" in description
                    or "world.staging.qlu.ai" in location
                    or "world.staging.qlu.ai" in description
                ):
                    is_qlu_world = True

                # Check conferenceData for QLU_WORLD
                conference_data = e.get("conferenceData", {})
                if conference_data:
                    key_type = (
                        conference_data.get("conferenceSolution", {})
                        .get("key", {})
                        .get("type", "")
                    )
                    name = (
                        conference_data.get("conferenceSolution", {})
                        .get("name", "")
                        .lower()
                    )

                    # Check entryPoints URIs for QLU World domains
                    entry_points = conference_data.get("entryPoints", [])
                    for entry_point in entry_points:
                        uri = entry_point.get("uri", "").lower()
                        if "world.qlu.ai" in uri or "world.staging.qlu.ai" in uri:
                            is_qlu_world = True
                            break

                    # Check if it's QLU_WORLD based on key type (addOn is QLU, hangoutsMeet is Google Meet)
                    if key_type == "addOn":
                        is_qlu_world = True
                    elif key_type == "hangoutsMeet":
                        # Explicitly Google Meet, don't set is_qlu_world
                        pass
                    elif "qlu world" in name:
                        is_qlu_world = True

                # If QLU_WORLD is confirmed, set it
                if is_qlu_world:
                    platform = "QLU_WORLD"
                else:
                    # Check for GOOGLE_MEET indicators
                    if conference_data:
                        key_type = (
                            conference_data.get("conferenceSolution", {})
                            .get("key", {})
                            .get("type", "")
                        )
                        name = (
                            conference_data.get("conferenceSolution", {})
                            .get("name", "")
                            .lower()
                        )

                        if key_type == "hangoutsMeet":
                            platform = "GOOGLE_MEET"
                        elif "google meet" in name or "hangouts meet" in name:
                            platform = "GOOGLE_MEET"
                        # else: already defaulted to GOOGLE_MEET
                    # else: already defaulted to GOOGLE_MEET (no conferenceData and no QLU World link)

                # Append formatted string
                formatted.append(
                    f"{i}. {summary} ({start} → {end})\n   Participants: {participants_str}\n   Platform: {platform or 'None'}"
                )

                # Append to filtered_events list with participant names
                filtered_events.append(
                    {
                        "id": id,
                        "title": summary,
                        "startTime": start,
                        "endTime": end,
                        "participants": attendees,
                        "platform": platform,
                        "colorId": e.get("colorId", "1"),
                    }
                )

            # Print filtered events for debugging
            print(filtered_events)

            # await speech_handle.wait_for_playout()

            print("RPC PAYLOAD")
            print(json.dumps(filtered_events))
            # Send to RPC
            response = await self.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="fetch_agendas_agent",
                payload=json.dumps(filtered_events),
            )

            print(response)

            return "✅ Here are your events:\n" + "\n".join(formatted)

        except Exception as e:
            return f"❌ Error fetching events: {str(e)}"

    @function_tool
    async def find_free_time(
        self,
        date: str,
        min_duration_minutes: int | None = None,
    ) -> str:
        """
        Find available free time slots on a given date.

        Parameters:
            date (str): Date to check for availability. Must be an RFC 3339 formatted string
                with timezone offset (e.g., "2025-10-07T00:00:00+05:00").
            min_duration_minutes (int | None): Minimum duration in minutes for a free slot.
                Defaults to None, meaning any duration is acceptable.

        Returns:
            str: A list or summary of available time slots. If none are available, a descriptive message is returned.
        """
        if not self.session.userdata.access_token:
            # Try fetching once more
            new_token = await self._try_fetch_and_verify_token()
            if new_token:
                self.session.userdata.access_token = new_token
            else:
                # Still no token - RPC + return error
                await self.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="calendar_connection_status_agent",
                    payload=json.dumps({"calendar_connected": False}),
                )
                return "Calendar is not connected"

        if not re.match(RFC_3339_REGEX, date):
            return "❌ Invalid date. Please use 'YYYY-MM-DDT00:00:00+00:00' format with offset."

        try:
            # self.session.say(text="Alright", add_to_chat_ctx=False)

            # Parse input date and fix timezone
            local_start = datetime.fromisoformat(date)
            tz = pytz.timezone(self.local_timezone)

            # Full day in local tz
            start_of_day = local_start.replace(hour=0, minute=0, second=0, tzinfo=tz)
            end_of_day = local_start.replace(hour=23, minute=59, second=59, tzinfo=tz)

            # Restrict to working hours (9–19)
            work_start = start_of_day.replace(hour=9, minute=0, second=0)
            work_end = start_of_day.replace(hour=19, minute=0, second=0)

            time_min = start_of_day.isoformat()
            time_max = end_of_day.isoformat()

        except Exception as e:
            return f"❌ Error parsing date: {str(e)}"

        try:
            events = await self.calendar.list_events(
                user_calendar_access_token=self.session.userdata.access_token,
                time_min=time_min,
                time_max=time_max,
            )

            items = sorted(
                events.get("items", []),
                key=lambda e: e.get("start", {}).get("dateTime", ""),
            )

            busy = []
            for e in items:
                if e.get("eventType") == "workingLocation":
                    continue  # skip office/working location

                start = e.get("start", {}).get("dateTime")
                end = e.get("end", {}).get("dateTime")
                if start and end:
                    s = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(
                        tz
                    )
                    e_ = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(
                        tz
                    )

                    # clip events to working hours
                    if e_ <= work_start or s >= work_end:
                        continue
                    s = max(s, work_start)
                    e_ = min(e_, work_end)
                    busy.append((s, e_))

            # Compute free slots inside 9–7
            free_slots = []
            current = work_start
            for b_start, b_end in busy:
                if current < b_start:
                    free_slots.append((current, b_start))
                current = max(current, b_end)
            if current < work_end:
                free_slots.append((current, work_end))

            # Filter by duration
            if min_duration_minutes:
                free_slots = [
                    (s, e)
                    for s, e in free_slots
                    if (e - s).total_seconds() >= min_duration_minutes * 60
                ]

            if not free_slots:
                return "🔴 No free time slots available in working hours (9 AM – 7 PM)."

            # Format nicely in AM/PM
            def fmt(dt: datetime) -> str:
                return dt.strftime("%I:%M %p").lstrip("0")

            def format_duration(minutes: int) -> str:
                if minutes < 60:
                    return f"{minutes} mins free"
                hours, mins = divmod(minutes, 60)
                if mins == 0:
                    return f"{hours} hr{'s' if hours > 1 else ''} free"
                return f"{hours} hr{'s' if hours > 1 else ''} {mins} mins free"

            formatted = []
            for i, (s, e) in enumerate(free_slots, 1):
                duration_min = int((e - s).total_seconds() // 60)
                formatted.append(
                    f"{i}. {fmt(s)} → {fmt(e)}  ({format_duration(duration_min)})"
                )

            return (
                "✅ (Please generate a friendly conversational response from this output to user) => You have these free slots:\n"
                + "\n".join(formatted)
            )

        except Exception as e:
            return f"❌ Error finding free time: {str(e)}"

    @function_tool()
    async def handle_todo_request(self, context: RunContext):
        """Handles any user requests related to their to-do list or tasks."""
        return await self._transfer_to_agent("todo", context)


#     # @function_tool()
#     # async def handle_meeting_query_request(self, context: RunContext):
#     #     """Handles user requests about PAST MEETINGS: searching meeting transcripts, finding what was discussed, extracting action items, or analyzing previous meeting content."""
#     #     return await self._transfer_to_agent("meeting_query", context)


class GoogleCalendarManager:
    """Manages Google Calendar API interactions using a single, persistent client."""

    def __init__(
        self,
        user_id: str,
        token: str,
        api_url: str,
        qlu_meeting_url: str,
        local_timezone: str,
    ):
        self.user_id = user_id
        self.client = httpx.AsyncClient()
        self.api_url = api_url
        self.qlu_meeting_url = qlu_meeting_url
        self.token = token
        self.local_timezone = local_timezone

    async def list_events(
        self,
        user_calendar_access_token: str,
        calendar_id: str = "primary",
        time_min: str = None,
        time_max: str = None,
        max_results: int = 200,
        order_by: str = "startTime",
        single_events: bool = True,
        query: str | None = None,
        show_deleted: bool = False,
        updated_min: str | None = None,
    ) -> Dict[str, Any]:

        query = None
        params = {
            k: v
            for k, v in locals().items()
            if k != "self" and k != "user_calendar_access_token"
        }
        print(" | ".join(f"{k}={v}" for k, v in params.items()))

        print("S\n\n")

        """
        List events from Google Calendar API with optional search and time range.

        Args:
            user_calendar_access_token: User's Google Calendar access token
            time_min: RFC3339 start datetime (required)
            time_max: RFC3339 end datetime (required)
            query: Optional keyword
            max_results, order_by, single_events, show_deleted, updated_min: Google API params
        """
        access_token = user_calendar_access_token

        params = {
            "maxResults": max_results,
            "orderBy": order_by,
            "singleEvents": str(single_events).lower(),
            "showDeleted": str(show_deleted).lower(),
            "timeMin": time_min,
            "timeMax": time_max,
        }

        if query:
            params["q"] = query
        if updated_min:
            params["updatedMin"] = updated_min

        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            response = await self.client.get(url, headers=headers, params=params)

            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            print(f"HTTP error: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            print(f"Request error: {e}")
        return {}

    async def create_google_meet_event(
        self,
        user_calendar_access_token: str,
        start_time: str,
        end_time: str,
        summary: str,
        description: str,
        emails: List[str],
        recurrence: dict,
    ) -> dict:
        """
        Create a calendar event with optional recurrence.
        recurrence dict formats explained in schedule_meeting_with_participants

        Args:
            user_calendar_access_token: User's Google Calendar access token
        """

        print("ENTERING the actual creation")
        print(recurrence)

        try:
            access_token = user_calendar_access_token
            request_id = "".join(filter(str.isdigit, str(uuid.uuid4())[:20]))
            if len(request_id) < 6:
                request_id = str(int(uuid.uuid4().int % 1e10))

            attendees = [{"email": email} for email in emails]

            event_payload = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_time, "timeZone": self.local_timezone},
                "end": {"dateTime": end_time, "timeZone": self.local_timezone},
                "attendees": attendees,
                "conferenceData": {
                    "createRequest": {
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                        "requestId": request_id,
                    }
                },
            }

            # Handle recurrence
            freq = recurrence.get("freq")
            if freq:
                rrule_parts = [f"FREQ={freq.upper()}"]
            until = recurrence.get("until")
            if until:
                dt_until = parser.isoparse(until).astimezone(timezone.utc)
                until_str = dt_until.strftime("%Y%m%dT%H%M%SZ")
                rrule_parts.append(f"UNTIL={until_str}")
                if freq == "Weekly":
                    days = recurrence.get("daysIncluded")
                    if days:
                        rrule_parts.append(f"BYDAY={','.join(days)}")
                elif freq == "Monthly":
                    day = recurrence.get("day")
                    if day:
                        rrule_parts.append(f"BYMONTHDAY={day}")
                # Add RRULE if any
                event_payload["recurrence"] = [f"RRULE:{';'.join(rrule_parts)}"]

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?conferenceDataVersion=1&sendUpdates=all"
            response = await self.client.post(url, headers=headers, json=event_payload)
            response.raise_for_status()

            return response.json()

        except httpx.HTTPStatusError as e:
            print("HTTP error occurred:", e.response.text)
            return {"error": e.response.text}
        except Exception as e:
            print("Unexpected error:", str(e))
            return {"error": str(e)}

    async def create_qlu_meet_event(
        self,
        user_calendar_access_token: str,
        start_time: str,
        end_time: str,
        summary: str,
        description: str,
        emails: List[str],
        recurrence: dict,
    ) -> dict:
        """
        Create a calendar event with a custom QLU World virtual link and optional recurrence.
        recurrence dict formats explained in schedule_meeting_with_participants

        Args:
            user_calendar_access_token: User's Google Calendar access token
        """

        print("ENTERING QLU World event creation")
        print(recurrence)

        try:
            access_token = user_calendar_access_token
            print("MEOW")

            if access_token:
                print("there is sth in access token")

            # Generate unique UUID for QLU World link
            qlu_uuid = uuid.uuid4()
            # qlu_link = f"https://world.staging.qlu.ai/conference/cw_{qlu_uuid}"
            qlu_link = f"{self.qlu_meeting_url}{qlu_uuid}"

            attendees = [{"email": email} for email in emails]

            event_payload = {
                "summary": summary,
                "description": f"{description}\nJoin with QLU World: {qlu_link}",
                "location": qlu_link,
                "start": {"dateTime": start_time, "timeZone": self.local_timezone},
                "end": {"dateTime": end_time, "timeZone": self.local_timezone},
                "attendees": attendees,
            }

            # Handle recurrence
            freq = recurrence.get("freq")
            if freq:
                rrule_parts = [f"FREQ={freq.upper()}"]
                until = recurrence.get("until")
                if until:
                    dt_until = parser.isoparse(until).astimezone(timezone.utc)
                    until_str = dt_until.strftime("%Y%m%dT%H%M%SZ")
                    rrule_parts.append(f"UNTIL={until_str}")

                    if freq.lower() == "weekly":
                        days = recurrence.get("daysIncluded")
                        if days:
                            rrule_parts.append(f"BYDAY={','.join(days)}")
                    elif freq.lower() == "monthly":
                        day = recurrence.get("day")
                        if day:
                            rrule_parts.append(f"BYMONTHDAY={day}")

                # Add RRULE if any
                event_payload["recurrence"] = [f"RRULE:{';'.join(rrule_parts)}"]

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates=all"
            response = await self.client.post(url, headers=headers, json=event_payload)
            response.raise_for_status()

            event = response.json()

            # --- Step 2: Send event details to QLU backend ---
            qlu_event_payload = {
                "conferenceId": f"cw_{event.get('location', '').split('cw_')[-1]}",
                "calendarId": "primary",
                "eventId": event["id"],
                "title": event["summary"],
                "description": event.get("description"),
                "location": event.get("location"),
                "startTime": event["start"]["dateTime"],
                "endTime": event["end"]["dateTime"],
                "isAllDay": False,
                "timeZone": event["start"].get("timeZone"),
                "organizer": event.get("organizer"),
                "attendees": event.get("attendees", []),
                "hostEmail": event.get("organizer", {}).get("email"),
                "status": event["status"],
                "visibility": "default",
                "transparency": "opaque",
                "recurrence": event.get("recurrence", []),
                "reminders": event.get("reminders"),
                "sequence": event["sequence"],
                "conferenceUrl": event["location"],
                "createdAt": event["created"],
                "lastSyncAt": event["updated"],
            }

            qlu_headers = {
                "Authorization": f"Bearer {self.token}",  # M2M token
                "Content-Type": "application/json",
            }

            qlu_url = self.api_url + "/create-event"

            qlu_response = await self.client.post(
                qlu_url, headers=qlu_headers, json=qlu_event_payload
            )
            print(qlu_response)
            qlu_response.raise_for_status()

            print("✅ Successfully created event in Google and synced to QLU backend.")

            return response.json()

        except httpx.HTTPStatusError as e:
            print("HTTP error occurred:", e.response.text)
            return {"error": e.response.text}
        except Exception as e:
            print("Unexpected error:", str(e))
            return {"error": str(e)}

    async def update_google_meet_event(
        self,
        user_calendar_access_token: str,
        event_id: str,
        summary: str | None = None,
        description: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        emails: List[str] | None = None,
    ) -> dict:
        """
        Update an existing Google Calendar event (with optional Google Meet).
        Allowed fields: summary, description, start/end times, attendees.
        Only the passed fields will be updated.

        Args:
            user_calendar_access_token: User's Google Calendar access token
            event_id: ID of the event to update.
        """

        access_token = user_calendar_access_token

        event_payload = {}

        if summary is not None:
            event_payload["summary"] = summary
        if description is not None:
            event_payload["description"] = description
        if start_time is not None and end_time is not None:
            event_payload["start"] = {
                "dateTime": start_time,
                "timeZone": self.local_timezone,
            }
            event_payload["end"] = {
                "dateTime": end_time,
                "timeZone": self.local_timezone,
            }
        elif start_time is not None or end_time is not None:
            raise ValueError("Both start_time and end_time must be provided together.")

        if emails is not None:
            # Fetch existing event to get current attendees
            get_url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"
            get_headers = {"Authorization": f"Bearer {access_token}"}

            try:
                get_response = await self.client.get(get_url, headers=get_headers)
                get_response.raise_for_status()
                existing_event = get_response.json()

                # Get existing attendees (if any)
                existing_attendees = existing_event.get("attendees", [])
                existing_emails = {
                    att.get("email", "").lower()
                    for att in existing_attendees
                    if att.get("email")
                }

                # Merge with new emails (avoid duplicates)
                new_emails_lower = {email.lower() for email in emails}
                all_emails = existing_emails | new_emails_lower

                # Create attendees list preserving original case from existing or new
                attendees = []
                # First add existing attendees (preserve their original case and other properties)
                for att in existing_attendees:
                    email = att.get("email", "")
                    if email and email.lower() in all_emails:
                        attendees.append(att)
                        all_emails.discard(email.lower())

                # Then add new emails that weren't in existing list
                for email in emails:
                    if email.lower() in all_emails:
                        attendees.append({"email": email})
                        all_emails.discard(email.lower())

            except Exception as e:
                # If fetch fails, fallback to just using new emails (original behavior)
                print(f"⚠️ Failed to fetch existing event for attendee merge: {e}")
                attendees = [{"email": email} for email in emails]

            event_payload["attendees"] = attendees

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"

        response = await self.client.patch(url, headers=headers, json=event_payload)

        response.raise_for_status()
        return response.json()

    async def delete_google_calendar_event(
        self, user_calendar_access_token: str, event_id: str
    ) -> None:
        """
        Delete a Google Calendar event.

        Args:
            user_calendar_access_token: User's Google Calendar access token
            event_id: ID of the event to delete.
        """

        access_token = user_calendar_access_token

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"

        response = await self.client.delete(url, headers=headers)

        # Google Calendar API returns 204 No Content if deletion succeeds
        if response.status_code != 204:
            response.raise_for_status()


# ---------------------------------------------------------------------------
# Outlook Calendar (Microsoft Graph) – same interface as Google, normalized response
# ---------------------------------------------------------------------------

MS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# RRULE BYDAY (2-letter) → Microsoft Graph daysOfWeek (lowercase full names)
_OUTLOOK_DAYS_MAP = {
    "MO": "monday",
    "TU": "tuesday",
    "WE": "wednesday",
    "TH": "thursday",
    "FR": "friday",
    "SA": "saturday",
    "SU": "sunday",
}


def _outlook_recurrence(recurrence: dict, start_time: str) -> dict | None:
    """Build Graph recurrence.pattern + range from our recurrence dict (same shape as Google RRULE input)."""
    if not recurrence:
        return None
    freq = (recurrence.get("freq") or "").strip().lower()
    if not freq:
        return None
    until_raw = recurrence.get("until") or start_time
    end_date = (
        until_raw[:10]
        if isinstance(until_raw, str) and len(until_raw) >= 10
        else start_time[:10]
    )
    range_obj = {"type": "endDate", "startDate": start_time[:10], "endDate": end_date}
    if freq == "weekly":
        days = recurrence.get("daysIncluded") or []
        days_of_week = [
            _OUTLOOK_DAYS_MAP.get(
                d[:2].upper(), d.lower() if isinstance(d, str) else "monday"
            )
            for d in days
            if d
        ]
        if not days_of_week:
            days_of_week = ["monday"]
        pattern = {
            "type": "weekly",
            "interval": 1,
            "daysOfWeek": days_of_week,
            "firstDayOfWeek": "sunday",
        }
    elif freq == "monthly":
        day = recurrence.get("day") or 1
        pattern = {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": day}
    elif freq == "daily":
        pattern = {"type": "daily", "interval": 1}
    else:
        pattern = {"type": freq, "interval": 1}
    return {"pattern": pattern, "range": range_obj}


def _outlook_utc_to_local_iso(
    utc_datetime_str: str | None, local_tz: str
) -> str | None:
    """Convert Graph's UTC dateTime (e.g. '2026-03-17T11:00:00.0000000') to local ISO with offset."""
    if not utc_datetime_str or not local_tz:
        return utc_datetime_str
    if "T" not in utc_datetime_str:
        return utc_datetime_str  # date-only (all-day)
    try:
        dt = parser.isoparse(utc_datetime_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(ZoneInfo(local_tz))
        # RFC 3339 style offset e.g. +05:00
        offset = local_dt.strftime("%z")
        if len(offset) == 5:
            offset = offset[:3] + ":" + offset[3:]
        return local_dt.strftime("%Y-%m-%dT%H:%M:%S") + offset
    except Exception:
        return utc_datetime_str


def _outlook_event_to_google_shape(ev: dict, local_timezone: str | None = None) -> dict:
    """Normalize a Microsoft Graph event to Google Calendar-like shape. Converts UTC start/end to local if local_timezone given."""
    start = ev.get("start", {}) or {}
    end = ev.get("end", {}) or {}
    start_dt = start.get("dateTime")
    end_dt = end.get("dateTime")
    if local_timezone:
        start_dt = _outlook_utc_to_local_iso(start_dt, local_timezone) or start_dt
        end_dt = _outlook_utc_to_local_iso(end_dt, local_timezone) or end_dt
    return {
        "id": ev.get("id", ""),
        "summary": ev.get("subject", ""),
        "start": {"dateTime": start_dt, "date": start.get("date")},
        "end": {"dateTime": end_dt, "date": end.get("date")},
        "eventType": (
            "workingLocation"
            if ev.get("isAllDay") and not ev.get("subject")
            else "default"
        ),
        "recurringEventId": ev.get("seriesMasterId"),
        "attendees": [
            {"email": a.get("emailAddress", {}).get("address")}
            for a in (ev.get("attendees") or [])
            if a.get("emailAddress", {}).get("address")
        ],
        "organizer": ev.get("organizer", {}).get("emailAddress", {}),
        "location": (
            ev.get("location", {}).get("displayName")
            if isinstance(ev.get("location"), dict)
            else (ev.get("location") or "")
        ),
        "webLink": ev.get("webLink"),
        "onlineMeeting": ev.get("onlineMeeting"),
        "onlineMeetingUrl": ev.get("onlineMeetingUrl"),
    }


class OutlookCalendarManager:
    """Manages Outlook Calendar via Microsoft Graph. Returns Google-like shapes so callers need no branching."""

    def __init__(
        self,
        user_id: str,
        token: str,
        api_url: str,
        qlu_meeting_url: str,
        local_timezone: str,
    ):
        self.user_id = user_id
        self.client = httpx.AsyncClient()
        self.api_url = api_url
        self.qlu_meeting_url = qlu_meeting_url
        self.token = token
        self.local_timezone = local_timezone

    async def list_events(
        self,
        user_calendar_access_token: str,
        calendar_id: str = "primary",
        time_min: str = None,
        time_max: str = None,
        max_results: int = 200,
        order_by: str = "startTime",
        single_events: bool = True,
        query: str | None = None,
        show_deleted: bool = False,
        updated_min: str | None = None,
    ) -> Dict[str, Any]:
        """List events from Outlook (Graph calendarView). Returns same shape as Google: { items: [...] }."""
        if not time_min or not time_max:
            return {"items": []}
        access_token = user_calendar_access_token
        url = f"{MS_GRAPH_BASE}/me/calendarView"
        params = {
            "startDateTime": time_min,
            "endDateTime": time_max,
            "$top": max_results,
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            raw = data.get("value", [])
            items = [
                _outlook_event_to_google_shape(e, self.local_timezone) for e in raw
            ]
            return {"items": items}
        except httpx.HTTPStatusError as e:
            print(
                f"Outlook list_events HTTP error: {e.response.status_code} - {e.response.text}"
            )
        except httpx.RequestError as e:
            print(f"Outlook list_events request error: {e}")
        return {"items": []}

    async def create_google_meet_event(
        self,
        user_calendar_access_token: str,
        start_time: str,
        end_time: str,
        summary: str,
        description: str,
        emails: List[str],
        recurrence: dict,
    ) -> dict:
        """Create Outlook event with online meeting (Teams). Returns Google-like shape."""
        access_token = user_calendar_access_token
        attendees = [
            {"emailAddress": {"address": email, "name": email}} for email in emails
        ]
        body = {
            "subject": summary,
            "body": {"contentType": "HTML", "content": description or ""},
            "start": {"dateTime": start_time, "timeZone": self.local_timezone},
            "end": {"dateTime": end_time, "timeZone": self.local_timezone},
            "attendees": attendees,
            "isOnlineMeeting": True,
        }
        rec = _outlook_recurrence(recurrence or {}, start_time)
        if rec:
            body["recurrence"] = rec
        url = f"{MS_GRAPH_BASE}/me/events"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        try:
            response = await self.client.post(url, headers=headers, json=body)
            response.raise_for_status()
            ev = response.json()
            start_dt = _outlook_utc_to_local_iso(
                ev.get("start", {}).get("dateTime"), self.local_timezone
            ) or ev.get("start", {}).get("dateTime")
            end_dt = _outlook_utc_to_local_iso(
                ev.get("end", {}).get("dateTime"), self.local_timezone
            ) or ev.get("end", {}).get("dateTime")
            return {
                "id": ev.get("id"),
                "summary": ev.get("subject"),
                "start": {"dateTime": start_dt, "timeZone": self.local_timezone},
                "end": {"dateTime": end_dt, "timeZone": self.local_timezone},
                "attendees": [
                    {"email": a.get("emailAddress", {}).get("address")}
                    for a in ev.get("attendees", [])
                ],
                "conferenceData": {
                    "entryPoints": [
                        {
                            "entryPointType": "video",
                            "uri": ev.get("onlineMeetingUrl")
                            or ev.get("onlineMeeting", {}).get("joinUrl")
                            or "",
                        }
                    ],
                    "conferenceSolution": {"name": "Microsoft Teams"},
                },
                "location": (
                    ev.get("location", {}).get("displayName")
                    if isinstance(ev.get("location"), dict)
                    else ""
                ),
            }
        except Exception as e:
            print(f"Outlook create_google_meet_event error: {e}")
            return {"error": str(e)}

    async def create_qlu_meet_event(
        self,
        user_calendar_access_token: str,
        start_time: str,
        end_time: str,
        summary: str,
        description: str,
        emails: List[str],
        recurrence: dict,
    ) -> dict:
        """Create Outlook event with QLU World link, then sync to QLU backend."""
        access_token = user_calendar_access_token
        qlu_uuid = uuid.uuid4()
        qlu_link = f"{self.qlu_meeting_url}{qlu_uuid}"
        body_content = f"{description or ''}\nJoin with QLU World: {qlu_link}"
        attendees = [
            {"emailAddress": {"address": email, "name": email}} for email in emails
        ]
        body = {
            "subject": summary,
            "body": {"contentType": "HTML", "content": body_content},
            "start": {"dateTime": start_time, "timeZone": self.local_timezone},
            "end": {"dateTime": end_time, "timeZone": self.local_timezone},
            "attendees": attendees,
            "location": {"displayName": qlu_link},
        }
        rec = _outlook_recurrence(recurrence or {}, start_time)
        if rec:
            body["recurrence"] = rec
        url = f"{MS_GRAPH_BASE}/me/events"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        try:
            response = await self.client.post(url, headers=headers, json=body)
            response.raise_for_status()
            ev = response.json()
            # Sync to QLU backend (same payload shape as Google path); use local time for display
            start_local = _outlook_utc_to_local_iso(
                ev.get("start", {}).get("dateTime"), self.local_timezone
            ) or ev.get("start", {}).get("dateTime")
            end_local = _outlook_utc_to_local_iso(
                ev.get("end", {}).get("dateTime"), self.local_timezone
            ) or ev.get("end", {}).get("dateTime")
            qlu_payload = {
                "conferenceId": f"cw_{str(qlu_uuid)}",
                "calendarId": "primary",
                "eventId": ev.get("id"),
                "title": ev.get("subject"),
                "description": ev.get("body", {}).get("content"),
                "location": qlu_link,
                "startTime": start_local,
                "endTime": end_local,
                "isAllDay": False,
                "timeZone": self.local_timezone,
                "organizer": ev.get("organizer"),
                "attendees": ev.get("attendees", []),
                "conferenceUrl": qlu_link,
            }
            qlu_headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
            qlu_url = self.api_url.rstrip("/") + "/create-event"
            await self.client.post(qlu_url, headers=qlu_headers, json=qlu_payload)
            attendees_google = [
                {"email": (a.get("emailAddress") or {}).get("address")}
                for a in ev.get("attendees", [])
                if (a.get("emailAddress") or {}).get("address")
            ]
            return {
                "id": ev.get("id"),
                "summary": ev.get("subject"),
                "start": {"dateTime": start_local, "timeZone": self.local_timezone},
                "end": {"dateTime": end_local, "timeZone": self.local_timezone},
                "attendees": attendees_google,
                "location": qlu_link,
            }
        except Exception as e:
            print(f"Outlook create_qlu_meet_event error: {e}")
            return {"error": str(e)}

    async def update_google_meet_event(
        self,
        user_calendar_access_token: str,
        event_id: str,
        summary: str | None = None,
        description: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        emails: List[str] | None = None,
    ) -> dict:
        """Update Outlook event. PATCH /me/events/{id}. Merges attendees like Google when emails provided."""
        print(f"[Outlook update] START event_id={event_id[:50]}...")
        access_token = user_calendar_access_token
        payload = {}
        if summary is not None:
            payload["subject"] = summary
        if description is not None:
            payload["body"] = {"contentType": "HTML", "content": description}
        if start_time is not None and end_time is not None:
            payload["start"] = {"dateTime": start_time, "timeZone": self.local_timezone}
            payload["end"] = {"dateTime": end_time, "timeZone": self.local_timezone}
        if emails is not None:
            print(f"[Outlook update] Fetching existing event for attendee merge...")
            try:
                get_res = await self.client.get(
                    f"{MS_GRAPH_BASE}/me/events/{event_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                get_res.raise_for_status()
                existing = get_res.json()
                print(f"[Outlook update] GET existing OK, building attendees list")
                existing_attendees = existing.get("attendees", [])
                existing_emails = {
                    (a.get("emailAddress") or {}).get("address", "").lower()
                    for a in existing_attendees
                    if (a.get("emailAddress") or {}).get("address")
                }
                new_lower = {e.lower() for e in emails}
                all_emails = existing_emails | new_lower
                attendees = []
                for a in existing_attendees:
                    addr = (a.get("emailAddress") or {}).get("address", "")
                    if addr and addr.lower() in all_emails:
                        attendees.append(a)
                        all_emails.discard(addr.lower())
                for e in emails:
                    if e.lower() in all_emails:
                        attendees.append({"emailAddress": {"address": e, "name": e}})
                        all_emails.discard(e.lower())
                payload["attendees"] = attendees
            except Exception as e:
                print(f"⚠️ Outlook update: fetch existing for merge failed: {e}")
                payload["attendees"] = [
                    {"emailAddress": {"address": e, "name": e}} for e in emails
                ]
        if not payload:
            print(f"[Outlook update] Empty payload, returning {{}}")
            return {}
        print(f"[Outlook update] PATCH request to Graph...")
        url = f"{MS_GRAPH_BASE}/me/events/{event_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        try:
            response = await self.client.patch(url, headers=headers, json=payload)
            response.raise_for_status()
            ev = response.json()
            print(f"[Outlook update] PATCH OK, building response")
            # Return Google-like shape with local time for callers (att["email"]) and display
            attendees_google = [
                {"email": (a.get("emailAddress") or {}).get("address")}
                for a in ev.get("attendees", [])
                if (a.get("emailAddress") or {}).get("address")
            ]
            start_dt = _outlook_utc_to_local_iso(
                ev.get("start", {}).get("dateTime"), self.local_timezone
            ) or ev.get("start", {}).get("dateTime")
            end_dt = _outlook_utc_to_local_iso(
                ev.get("end", {}).get("dateTime"), self.local_timezone
            ) or ev.get("end", {}).get("dateTime")
            print(
                f"[Outlook update] DONE returning event id={ev.get('id', '')[:50]}..."
            )
            return {
                "id": ev.get("id"),
                "summary": ev.get("subject"),
                "start": {"dateTime": start_dt, "timeZone": self.local_timezone},
                "end": {"dateTime": end_dt, "timeZone": self.local_timezone},
                "attendees": attendees_google,
            }
        except Exception as e:
            print(f"Outlook update_google_meet_event error: {e}")
            return {"error": str(e)}

    async def delete_google_calendar_event(
        self, user_calendar_access_token: str, event_id: str
    ) -> None:
        """Delete Outlook event. DELETE /me/events/{id}."""
        url = f"{MS_GRAPH_BASE}/me/events/{event_id}"
        headers = {"Authorization": f"Bearer {user_calendar_access_token}"}
        response = await self.client.delete(url, headers=headers)
        if response.status_code not in (204, 200):
            response.raise_for_status()


def build_calendar_system_prompt2(
    modality: str = "voice",
    tz: str = "Asia/Karachi",
    memory_manager=None,
    user_name: str = "",
) -> str:
    """
    Build the calendar system prompt dynamically with modality, local time, and user name.

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

    return CALENDAR_SYSTEM_PROMPT.format(
        modality=modality.upper(),
        timestamp=local_time,
        tz=tz,
        user_memories=memories_section,
        user_name=user_name or "the user",
    )
