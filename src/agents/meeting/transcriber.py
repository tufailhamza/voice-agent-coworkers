# import asyncio
# import logging
# import os
# from datetime import datetime, timezone, timedelta
# from typing import Dict, Set, Optional

# from livekit import rtc
# from livekit.agents import (
#     AgentSession,
#     AutoSubscribe,
#     JobContext,
#     RoomInputOptions,
#     RoomIO,
#     RoomOutputOptions,
#     utils,
# )
# import httpx

# from .config import Config
# from .transcriber_agent import TranscriberAgent
# from .summary import SummaryService
# from .transliteration import patch_agent_activity_on_final_transcript
# from .db_client import DBClient
# from src.utils.exceptions import TranscriberError

# logger = logging.getLogger(__name__)


# class MultiUserTranscriber:
#     """Multi-user transcriber service"""

#     def __init__(
#         self,
#         ctx: JobContext,
#         config: Config,
#         local_time_profile: dict,
#         environment: str,
#         recording_id: str,
#     ):
#         self.ctx = ctx
#         self.config = config
#         self.summary_service = SummaryService(config)
#         self.local_time_profile = local_time_profile
#         self.environment = environment
#         self.recording_id = recording_id

#         self._sessions: Dict[str, AgentSession] = {}
#         self._tasks: Set[asyncio.Task] = set()
#         self._recording_active: bool = True  # Flag to control transcript storage
#         self._recording_stop_triggered: bool = (
#             False  # Flag to prevent duplicate summary generation
#         )
#         self._transcripts: list = []  # Store transcripts directly in instance
#         self._user_id_to_name_map: Dict[int, str] = (
#             {}
#         )  # Map user ID to name for LLM prompts
#         self._user_mapping_ready: asyncio.Event = (
#             asyncio.Event()
#         )  # Event to signal mapping is ready

#         self.room_id = ctx.room.name or "default_room"
#         self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

#         logger.info(
#             f"🎙️ Initialized transcriber for room: {self.room_id}, session: {self.session_id}, recording_id: {self.recording_id}"
#         )

#     def start(self):
#         """Start the transcriber service"""
#         try:
#             logger.debug(
#                 "Binding room event handlers: participant_connected, participant_disconnected, room_updated"
#             )
#             self.ctx.room.on("participant_connected", self.on_participant_connected)
#             self.ctx.room.on(
#                 "participant_disconnected", self.on_participant_disconnected
#             )
#             self.ctx.room.on("room_updated", self.on_room_updated)

#             # Assume recording is active when process starts (process is spawned when recording starts)
#             initial_recording_state = self.ctx.room.is_recording
#             logger.info(
#                 f"📹 Initial recording state: {'🔴 Recording' if initial_recording_state else '⏸️ Not Recording'}"
#             )
#             # If somehow not recording at start, we still assume it's active (process was spawned for recording)
#             if not initial_recording_state:
#                 logger.warning(
#                     "⚠️ Room shows not recording at start, but assuming recording is active (process spawned for recording)"
#                 )

#             # Initialize user ID to name mapping (async, non-blocking)
#             task = asyncio.create_task(self._initialize_user_mapping())
#             self._tasks.add(task)
#             task.add_done_callback(lambda _: self._tasks.discard(task))

#             # Log currently known participants at startup
#             try:
#                 current = list(self.ctx.room.remote_participants.values())
#                 logger.info(
#                     f"📋 Existing participants at start: {[p.identity for p in current]}"
#                 )
#             except Exception as e:
#                 logger.debug(f"Could not list existing participants at start: {e}")
#             logger.info("🚀 Transcriber service started")
#         except Exception as e:
#             logger.error(f"❌ Error starting transcriber: {e}")
#             raise TranscriberError(f"Failed to start transcriber: {e}")

#     async def aclose(self):
#         """Close transcriber service"""
#         try:
#             # Cancel all tasks
#             await utils.aio.cancel_and_wait(*self._tasks)

#             # Close all sessions
#             await asyncio.gather(
#                 *[self._close_session(session) for session in self._sessions.values()]
#             )

#             # Remove event listeners
#             self.ctx.room.off("participant_connected", self.on_participant_connected)
#             self.ctx.room.off(
#                 "participant_disconnected", self.on_participant_disconnected
#             )
#             self.ctx.room.off("room_updated", self.on_room_updated)

#             logger.info("🔌 Transcriber service closed")

#         except Exception as e:
#             logger.error(f"❌ Error closing transcriber: {e}")
#             raise TranscriberError(f"Failed to close transcriber: {e}")

#     def on_participant_connected(self, participant: rtc.RemoteParticipant):
#         """Handle participant connection"""
#         try:
#             logger.info(
#                 f"🔔 Participant connected: id={participant.identity} sid={getattr(participant, 'sid', 'n/a')}"
#             )
#             # Debug participant tracks snapshot
#             try:
#                 pubs = getattr(participant, "_tracks", {}) or {}
#                 logger.debug(
#                     f"Participant {participant.identity} current track publications: {list(pubs.keys())}"
#                 )
#             except Exception as e:
#                 logger.debug(
#                     f"Track introspection failed for {participant.identity}: {e}"
#                 )
#             if participant.identity in self._sessions:
#                 logger.warning(
#                     f"Participant {participant.identity} already has a session"
#                 )
#                 return

#             logger.info(f"👤 Starting session for {participant.identity}")
#             logger.debug("Creating task to start session")
#             task = asyncio.create_task(self._start_session(participant))
#             self._tasks.add(task)

#             def on_task_done(task: asyncio.Task):
#                 try:
#                     result = task.result()
#                     self._sessions[participant.identity] = result
#                     logger.info(f"✅ Session started for {participant.identity}")
#                 except Exception as e:
#                     logger.error(
#                         f"❌ Failed to start session for {participant.identity}: {e}"
#                     )
#                 finally:
#                     self._tasks.discard(task)

#             task.add_done_callback(on_task_done)

#         except Exception as e:
#             logger.error(f"❌ Error handling participant connection: {e}")

#     def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
#         """Handle participant disconnection"""
#         try:
#             session = self._sessions.pop(participant.identity, None)
#             if session is None:
#                 logger.warning(
#                     f"No session found for disconnected participant: {participant.identity}"
#                 )
#                 return

#             logger.info(f"👋 Closing session for {participant.identity}")
#             task = asyncio.create_task(self._close_session(session))
#             self._tasks.add(task)
#             task.add_done_callback(lambda _: self._tasks.discard(task))

#         except Exception as e:
#             logger.error(f"❌ Error handling participant disconnection: {e}")

#     def on_room_updated(self):
#         """Handle room updates - detects when recording stops"""
#         try:
#             current_recording_state = self.ctx.room.is_recording

#             # Process starts when recording is active, so we only need to detect when it becomes False
#             # Check: if recording is False AND we're still active AND not already triggered, trigger stop
#             if (
#                 not current_recording_state
#                 and self._recording_active
#                 and not self._recording_stop_triggered
#             ):
#                 logger.info("⏸️ RECORDING STOPPED in room (detected via room update)")
#                 self.on_recording_stopped()

#         except Exception as e:
#             logger.error(f"❌ Error handling room update: {e}")

#     def on_recording_started(self):
#         """Called when recording starts in the room"""
#         logger.info(
#             f"📹 Recording started - Room: {self.room_id}, Recording ID: {self.recording_id}"
#         )
#         # Add your custom logic here when recording starts
#         # For example, you could:
#         # - Log to a database
#         # - Send a notification
#         # - Update internal state
#         pass

#     def on_recording_stopped(self):
#         """Called when recording stops in the room - automatically sends transcripts, generates summary and shuts down"""
#         # Check if already triggered to prevent duplicate summary generation
#         if self._recording_stop_triggered:
#             logger.info(
#                 "⚠️ Stop recording already triggered, ignoring duplicate trigger"
#             )
#             return

#         # Set flag immediately to prevent duplicate triggers (synchronous, no race condition)
#         self._recording_stop_triggered = True

#         # Immediately stop transcript storage (synchronous, no race condition)
#         self._recording_active = False
#         logger.info(
#             f"📹 Recording stopped - Room: {self.room_id}, Recording ID: {self.recording_id}"
#         )
#         logger.info(
#             "🛑 Transcript storage disabled - no new transcripts will be stored"
#         )

#         # Send transcripts immediately (API 01)
#         logger.info("📤 Sending transcripts to API immediately...")
#         task1 = asyncio.create_task(self._send_transcripts_to_api())
#         self._tasks.add(task1)
#         task1.add_done_callback(lambda _: self._tasks.discard(task1))

#         logger.info("🔄 Automatically generating summary and shutting down...")

#         # Create async task to handle summary generation
#         async def handle_recording_stopped():
#             try:
#                 # Generate summary from all cached transcripts
#                 await self.generate_summary()
#                 logger.info("✅ Summary generation completed")
#             except Exception as e:
#                 logger.error(f"❌ Error handling recording stopped: {e}")

#         # Schedule the async task
#         task = asyncio.create_task(handle_recording_stopped())
#         self._tasks.add(task)
#         task.add_done_callback(lambda _: self._tasks.discard(task))

#     async def _start_session(self, participant: rtc.RemoteParticipant) -> AgentSession:
#         """Start session for a participant"""
#         try:
#             if participant.identity in self._sessions:
#                 return self._sessions[participant.identity]

#             logger.debug(f"Creating AgentSession for {participant.identity}")
#             session = AgentSession()
#             logger.debug(
#                 f"Configuring RoomIO for participant={participant.identity} room={self.room_id}"
#             )
#             room_io = RoomIO(
#                 agent_session=session,
#                 room=self.ctx.room,
#                 participant=participant,
#                 input_options=RoomInputOptions(text_enabled=False),
#                 output_options=RoomOutputOptions(
#                     transcription_enabled=True,
#                     audio_enabled=False,
#                 ),
#             )

#             logger.debug("Starting RoomIO...")
#             await room_io.start()
#             logger.info(
#                 f"🎧 RoomIO started for {participant.identity} (transcription_enabled=True, audio_enabled=False)"
#             )

#             # Create agent
#             logger.debug("Instantiating TranscriberAgent...")
#             agent = TranscriberAgent(
#                 config=self.config,
#                 summary_service=self.summary_service,
#                 participant_identity=participant.identity,
#                 room_id=self.room_id,
#                 session_id=self.session_id,
#             )
#             logger.debug("Starting AgentSession with TranscriberAgent...")

#             await session.start(agent=agent)
#             logger.info(f"🟢 AgentSession started for {participant.identity}")

#             # Monkey patch on_final_transcript for transliteration
#             if hasattr(session, "_activity") and session._activity is not None:
#                 patch_agent_activity_on_final_transcript(
#                     activity=session._activity,
#                     participant_identity=participant.identity,
#                     room_id=self.room_id,
#                     session_id=self.session_id,
#                     transcripts_store=self._transcripts,  # Store directly in instance
#                     should_store_transcript=lambda: self._recording_active,
#                 )
#                 logger.info(
#                     f"✅ Transliteration patch applied for {participant.identity}"
#                 )
#             else:
#                 logger.warning(
#                     f"⚠️ Could not access session._activity for {participant.identity}, transliteration patch not applied"
#                 )

#             return session

#         except Exception as e:
#             logger.error(f"❌ Error starting session for {participant.identity}: {e}")
#             raise TranscriberError(f"Failed to start session: {e}")

#     async def _close_session(self, session: AgentSession) -> None:
#         """Close a session"""
#         try:
#             logger.debug("Draining AgentSession before close...")
#             await session.drain()
#             logger.debug("Closing AgentSession...")
#             await session.aclose()
#             logger.info("🔴 AgentSession closed")
#         except Exception as e:
#             logger.error(f"❌ Error closing session: {e}")

#     def get_transcripts(self):
#         """Get all transcripts for the current session"""
#         return self._transcripts

#     async def _initialize_user_mapping(self):
#         """Initialize user ID to name mapping from database"""
#         try:
#             # Setup DB config based on environment
#             if self.environment == "STAGING":
#                 db_config = {
#                     "user": os.getenv("COWORKING_USER_STAGING"),
#                     "password": os.getenv("COWORKING_PASSWORD_STAGING"),
#                     "database": os.getenv("COWORKING_DB_STAGING"),
#                     "host": os.getenv("COWORKING_HOST_STAGING"),
#                 }
#                 logger.info("⚙️ Using STAGING database configuration for user mapping")
#             else:
#                 db_config = {
#                     "user": os.getenv("COWORKING_USER"),
#                     "password": os.getenv("COWORKING_PASSWORD"),
#                     "database": os.getenv("COWORKING_DB"),
#                     "host": os.getenv("COWORKING_HOST"),
#                 }
#                 logger.info(
#                     "⚙️ Using PRODUCTION database configuration for user mapping"
#                 )

#             # Initialize DB client and fetch users
#             db_client = DBClient(db_config)
#             users = await db_client.fetch_qlu_users()

#             # Create ID → Name mapping
#             for user in users:
#                 user_id = user.get("id")
#                 user_name = user.get("name")
#                 if user_id and user_name:
#                     self._user_id_to_name_map[int(user_id)] = user_name

#             logger.info(
#                 f"✅ Loaded {len(self._user_id_to_name_map)} user mappings for ID → Name conversion"
#             )
#             # Signal that mapping is ready
#             self._user_mapping_ready.set()

#         except Exception as e:
#             logger.warning(
#                 f"⚠️ Failed to initialize user mapping (will use IDs in summaries): {e}"
#             )
#             # Still signal ready even if failed (so summary generation doesn't wait forever)
#             self._user_mapping_ready.set()

#     def _get_participant_name(self, participant_identity: str) -> str:
#         """
#         Get participant name from ID if available, otherwise return original identity.
#         Extracts numeric ID from formats like "982-meeting" → 982 → "John Doe"
#         """
#         try:
#             # Extract numeric ID (e.g., "982-meeting" → "982")
#             if "-" in participant_identity:
#                 numeric_id_str = participant_identity.split("-")[0]
#             else:
#                 numeric_id_str = participant_identity

#             numeric_id = int(numeric_id_str)
#             # Look up name in mapping
#             name = self._user_id_to_name_map.get(numeric_id)
#             if name:
#                 return name
#         except (ValueError, KeyError):
#             pass

#         # Fallback to original identity if mapping not found
#         return participant_identity

#     def _convert_timestamp_to_local(
#         self, timestamp: Optional[datetime]
#     ) -> Optional[str]:
#         """Convert timestamp to local timezone format with offset based on local_time_profile"""
#         if timestamp is None:
#             return None

#         try:
#             # Get timezone info from local_time_profile
#             timezone_str = self.local_time_profile.get("timezone", "Asia/Karachi")
#             offset_minutes = self.local_time_profile.get("offset_minutes", 300)

#             # Create timezone offset
#             tz_offset = timezone(timedelta(minutes=offset_minutes))

#             # If timestamp is naive, assume it's in the local timezone
#             if timestamp.tzinfo is None:
#                 timestamp = timestamp.replace(tzinfo=tz_offset)
#             else:
#                 # If timestamp has timezone, convert to local timezone
#                 timestamp = timestamp.astimezone(tz_offset)

#             # Format with offset (e.g., 2025-01-19T14:30:22+05:00)
#             # Calculate offset sign and hours/minutes
#             offset_hours = offset_minutes // 60
#             offset_mins = abs(offset_minutes % 60)
#             sign = "+" if offset_minutes >= 0 else "-"
#             offset_str = f"{sign}{abs(offset_hours):02d}:{offset_mins:02d}"

#             # Return ISO format string with local timezone offset
#             return timestamp.strftime(f"%Y-%m-%dT%H:%M:%S{offset_str}")

#         except Exception as e:
#             logger.warning(f"⚠️ Error converting timestamp: {e}, using original")
#             return (
#                 timestamp.isoformat()
#                 if timestamp.tzinfo
#                 else timestamp.isoformat() + "+00:00"
#             )

#     async def generate_summary(self):
#         """Generate meeting summary from cached transcripts, print JSON payload, and send metadata to API"""
#         import json

#         try:
#             # Get transcripts
#             transcripts = self.get_transcripts()

#             if not transcripts:
#                 # No transcripts - send placeholder values to API 02 (skip LLM calls)
#                 logger.info(
#                     "⚠️ No transcripts found - sending placeholder values to API 02 (skipping LLM calls)"
#                 )

#                 metadata_payload = {
#                     "recordingId": self.recording_id,
#                     "roomName": self.room_id,
#                     "meetingTitle": "Meeting Recording",
#                     "summary": "Not enough conversations to generate a summary",
#                     "actionItems": [],
#                 }

#                 # Print the JSON payload
#                 logger.info("=" * 80)
#                 logger.info("📋 MEETING METADATA JSON PAYLOAD (API 02) - PLACEHOLDER")
#                 logger.info("=" * 80)
#                 logger.info(json.dumps(metadata_payload, indent=2, ensure_ascii=False))
#                 logger.info("=" * 80)

#                 # Send POST request to API 02 (metadata)
#                 await self._send_metadata_to_api(metadata_payload)
#                 return

#             # Transcripts exist - generate via LLM (existing flow)
#             logger.info(f"📊 Generating summary from {len(transcripts)} transcripts")

#             # Wait for user mapping to be ready (with timeout to avoid blocking forever)
#             try:
#                 await asyncio.wait_for(self._user_mapping_ready.wait(), timeout=10.0)
#                 logger.debug(
#                     "✅ User mapping ready, proceeding with summary generation"
#                 )
#             except asyncio.TimeoutError:
#                 logger.warning(
#                     "⚠️ User mapping not ready after 10s, proceeding with empty mapping"
#                 )

#             # Generate summary using the service (pass user mapping for name conversion)
#             summary = await self.summary_service.generate_summary(
#                 transcripts, user_id_to_name_map=self._user_id_to_name_map
#             )

#             # Extract action items from summary
#             action_items = await self.summary_service.extract_action_items(
#                 summary.summary_content
#             )

#             # Format summary with GPT-4.1 according to strict markdown rules
#             logger.info("🎨 Formatting summary with GPT-4.1...")
#             formatted_summary = await self.summary_service.format_summary(
#                 summary.summary_content
#             )

#             # Generate short meeting title from formatted summary (under 100 chars)
#             logger.info("📝 Generating short meeting title from formatted summary...")
#             meeting_title = await self.summary_service.generate_short_meeting_title(
#                 formatted_summary
#             )

#             # Build metadata payload for API 02
#             metadata_payload = {
#                 "recordingId": self.recording_id,
#                 "roomName": self.room_id,
#                 "meetingTitle": meeting_title,  # Use short title from formatted summary
#                 "summary": formatted_summary,  # Use formatted summary
#                 "actionItems": action_items,
#             }

#             # Print the JSON payload
#             logger.info("=" * 80)
#             logger.info("📋 MEETING METADATA JSON PAYLOAD (API 02)")
#             logger.info("=" * 80)
#             logger.info(json.dumps(metadata_payload, indent=2, ensure_ascii=False))
#             logger.info("=" * 80)

#             # Send POST request to API 02 (metadata)
#             await self._send_metadata_to_api(metadata_payload)

#         except Exception as e:
#             logger.error(f"❌ Error generating summary: {e}")
#             raise TranscriberError(f"Failed to generate summary: {e}")

#     async def _send_transcripts_to_api(self):
#         """Send transcripts to API 01 immediately after recording stops"""
#         import json

#         try:
#             # Get transcripts
#             transcripts = self.get_transcripts()

#             # Build transcriptDetails array with converted timestamps (same logic as before)
#             transcript_details = []
#             if transcripts:
#                 for transcript in transcripts:
#                     converted_time = self._convert_timestamp_to_local(
#                         transcript.timestamp
#                     )
#                     # Extract user ID (remove suffix after hyphen, e.g., "1072-meeting" -> "1072")
#                     user_id = (
#                         transcript.speaker_name.split("-")[0]
#                         if "-" in transcript.speaker_name
#                         else transcript.speaker_name
#                     )
#                     transcript_details.append(
#                         {
#                             "userId": user_id,
#                             "content": transcript.text_content,
#                             "time": converted_time,
#                         }
#                     )
#             else:
#                 logger.info(
#                     "⚠️ No transcripts found - sending empty transcriptDetails list to API 01"
#                 )

#             # Build payload for API 01 (always send, even with empty list)
#             payload = {
#                 "recordingId": self.recording_id,
#                 "transcriptDetails": transcript_details,
#             }

#             # Print the JSON payload
#             logger.info("=" * 80)
#             logger.info("📋 TRANSCRIPTS JSON PAYLOAD (API 01)")
#             logger.info("=" * 80)
#             logger.info(json.dumps(payload, indent=2, ensure_ascii=False))
#             logger.info("=" * 80)

#             # API URL based on environment
#             if self.environment == "STAGING":
#                 api_url = "https://api.world.staging.qlu.ai/m2m/agent/recording-transcriptions-details"
#                 logger.info("⚙️ Using STAGING API URL for transcriptions-details")
#             else:
#                 api_url = "https://api.world.qlu.ai/m2m/agent/recording-transcriptions-details"
#                 logger.info("⚙️ Using PRODUCTION API URL for transcriptions-details")

#             # Get M2M token
#             m2m_token = os.getenv("M2M_TOKEN_TRANSCRIPTION")
#             if not m2m_token:
#                 logger.error("❌ M2M_TOKEN_TRANSCRIPTION not configured")
#                 return

#             # Prepare headers
#             headers = {
#                 "Authorization": f"Bearer {m2m_token}",
#                 "Content-Type": "application/json",
#             }

#             # Send POST request
#             async with httpx.AsyncClient(timeout=30.0) as client:
#                 response = await client.post(api_url, headers=headers, json=payload)
#                 response.raise_for_status()

#             logger.info(f"✅ Successfully sent transcripts to API 01: {api_url}")

#         except httpx.HTTPStatusError as e:
#             logger.error(
#                 f"❌ HTTP error sending transcripts to API 01: {e.response.text}"
#             )
#         except Exception as e:
#             logger.error(f"❌ Error sending transcripts to API 01: {e}")

#     async def _send_metadata_to_api(self, payload: dict):
#         """Send metadata (summary, action items, meeting title) to API 02 after LLM generation"""
#         try:
#             # API URL based on environment
#             if self.environment == "STAGING":
#                 api_url = "https://api.world.staging.qlu.ai/m2m/agent/recording-transcriptions-metadata"
#                 logger.info("⚙️ Using STAGING API URL for transcriptions-metadata")
#             else:
#                 api_url = "https://api.world.qlu.ai/m2m/agent/recording-transcriptions-metadata"
#                 logger.info("⚙️ Using PRODUCTION API URL for transcriptions-metadata")

#             # Get M2M token
#             m2m_token = os.getenv("M2M_TOKEN_TRANSCRIPTION")
#             if not m2m_token:
#                 logger.error("❌ M2M_TOKEN_TRANSCRIPTION not configured")
#                 return

#             # Prepare headers
#             headers = {
#                 "Authorization": f"Bearer {m2m_token}",
#                 "Content-Type": "application/json",
#             }

#             # Send POST request
#             async with httpx.AsyncClient(timeout=30.0) as client:
#                 response = await client.post(api_url, headers=headers, json=payload)
#                 response.raise_for_status()

#             logger.info(f"✅ Successfully sent metadata to API 02: {api_url}")

#         except httpx.HTTPStatusError as e:
#             logger.error(f"❌ HTTP error sending metadata to API 02: {e.response.text}")
#         except Exception as e:
#             logger.error(f"❌ Error sending metadata to API 02: {e}")


# V2
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, Optional

from livekit import rtc
from livekit.agents import (
    AgentSession,
    AutoSubscribe,
    JobContext,
    RoomInputOptions,
    RoomIO,
    RoomOutputOptions,
    utils,
)
from livekit.agents.types import TOPIC_TRANSCRIPTION
import httpx

from .config import Config
from .transcriber_agent import TranscriberAgent
from .summary import SummaryService
from .transcript_finalizer import TranscriptFinalizer
from .transliteration import patch_agent_activity_on_final_transcript
from .db_client import DBClient
from src.utils.exceptions import TranscriberError

logger = logging.getLogger(__name__)

# Shutdown callback waits up to this many seconds for transcript finalization and submission
SUMMARY_WAIT_TIMEOUT = 300.0  # 5 minutes


class MultiUserTranscriber:
    """Multi-user transcriber service"""

    def __init__(
        self,
        ctx: JobContext,
        config: Config,
        local_time_profile: dict,
        environment: str,
        recording_id: str,
        organization_id: int | None = None,
    ):
        self.ctx = ctx
        self.config = config
        self.summary_service = SummaryService(config)
        self.local_time_profile = local_time_profile
        self.environment = environment
        self.recording_id = recording_id
        self.organization_id = organization_id

        self._sessions: Dict[str, AgentSession] = {}
        self._tasks: Set[asyncio.Task] = set()
        self._transcription_activities: list = []
        self._transcript_finalizer = TranscriptFinalizer(config)
        self._recording_active: bool = True  # Flag to control transcript storage
        self._recording_stop_triggered: bool = (
            False  # Flag to prevent duplicate summary generation
        )
        self._transcripts: list = []  # Store transcripts directly in instance
        self._user_id_to_name_map: Dict[int, str] = (
            {}
        )  # Map user ID to name for LLM prompts
        self._user_mapping_ready: asyncio.Event = (
            asyncio.Event()
        )  # Event to signal mapping is ready
        self._completion_task: Optional[asyncio.Task] = (
            None  # Transcript finalization plus API submission (waited on shutdown)
        )

        self.room_id = ctx.room.name or "default_room"
        self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.info(
            f"🎙️ Initialized transcriber for room: {self.room_id}, session: {self.session_id}, recording_id: {self.recording_id}"
        )

    def start(self):
        """Start the transcriber service"""
        try:
            logger.debug(
                "Binding room event handlers: participant_connected, participant_disconnected, room_updated"
            )
            self.ctx.room.on("participant_connected", self.on_participant_connected)
            self.ctx.room.on(
                "participant_disconnected", self.on_participant_disconnected
            )
            self.ctx.room.on("room_updated", self.on_room_updated)

            # Assume recording is active when process starts (process is spawned when recording starts)
            initial_recording_state = self.ctx.room.is_recording
            logger.info(
                f"📹 Initial recording state: {'🔴 Recording' if initial_recording_state else '⏸️ Not Recording'}"
            )
            # If somehow not recording at start, we still assume it's active (process was spawned for recording)
            if not initial_recording_state:
                logger.warning(
                    "⚠️ Room shows not recording at start, but assuming recording is active (process spawned for recording)"
                )

            # Initialize user ID to name mapping (async, non-blocking)
            task = asyncio.create_task(self._initialize_user_mapping())
            self._tasks.add(task)
            task.add_done_callback(lambda _: self._tasks.discard(task))

            # Log currently known participants at startup
            try:
                current = list(self.ctx.room.remote_participants.values())
                logger.info(
                    f"📋 Existing participants at start: {[p.identity for p in current]}"
                )
            except Exception as e:
                logger.debug(f"Could not list existing participants at start: {e}")
            logger.info("🚀 Transcriber service started")
        except Exception as e:
            logger.error(f"❌ Error starting transcriber: {e}")
            raise TranscriberError(f"Failed to start transcriber: {e}")

    async def wait_for_completion_task(
        self, timeout: float = SUMMARY_WAIT_TIMEOUT
    ) -> None:
        """Wait for transcript finalization and both API submissions."""
        if self._completion_task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(self._completion_task), timeout=timeout
            )
            logger.info("✅ Shutdown: transcript processing and submission completed")
        except asyncio.TimeoutError:
            logger.warning(
                f"⚠️ Shutdown: transcript processing did not complete within {timeout}s"
            )
        except asyncio.CancelledError:
            logger.warning("⚠️ Shutdown: transcript processing wait was cancelled")
        except Exception as e:
            logger.warning(f"⚠️ Shutdown: transcript processing failed: {e}")

    async def aclose(self):
        """Close transcriber service"""
        try:
            # Cancel all tasks
            await utils.aio.cancel_and_wait(*self._tasks)

            # Close all sessions
            await asyncio.gather(
                *[self._close_session(session) for session in self._sessions.values()]
            )

            # Remove event listeners
            self.ctx.room.off("participant_connected", self.on_participant_connected)
            self.ctx.room.off(
                "participant_disconnected", self.on_participant_disconnected
            )
            self.ctx.room.off("room_updated", self.on_room_updated)

            logger.info("🔌 Transcriber service closed")

        except Exception as e:
            logger.error(f"❌ Error closing transcriber: {e}")
            raise TranscriberError(f"Failed to close transcriber: {e}")

    def on_participant_connected(self, participant: rtc.RemoteParticipant):
        """Handle participant connection"""
        try:
            logger.info(
                f"🔔 Participant connected: id={participant.identity} sid={getattr(participant, 'sid', 'n/a')}"
            )
            # Debug participant tracks snapshot
            try:
                pubs = getattr(participant, "_tracks", {}) or {}
                logger.debug(
                    f"Participant {participant.identity} current track publications: {list(pubs.keys())}"
                )
            except Exception as e:
                logger.debug(
                    f"Track introspection failed for {participant.identity}: {e}"
                )
            if participant.identity in self._sessions:
                logger.warning(
                    f"Participant {participant.identity} already has a session"
                )
                return

            logger.info(f"👤 Starting session for {participant.identity}")
            logger.debug("Creating task to start session")
            task = asyncio.create_task(self._start_session(participant))
            self._tasks.add(task)

            def on_task_done(task: asyncio.Task):
                try:
                    result = task.result()
                    self._sessions[participant.identity] = result
                    logger.info(f"✅ Session started for {participant.identity}")
                except Exception as e:
                    logger.error(
                        f"❌ Failed to start session for {participant.identity}: {e}"
                    )
                finally:
                    self._tasks.discard(task)

            task.add_done_callback(on_task_done)

        except Exception as e:
            logger.error(f"❌ Error handling participant connection: {e}")

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Handle participant disconnection"""
        try:
            session = self._sessions.pop(participant.identity, None)
            if session is None:
                logger.warning(
                    f"No session found for disconnected participant: {participant.identity}"
                )
                return

            logger.info(f"👋 Closing session for {participant.identity}")
            task = asyncio.create_task(self._close_session(session))
            self._tasks.add(task)
            task.add_done_callback(lambda _: self._tasks.discard(task))

        except Exception as e:
            logger.error(f"❌ Error handling participant disconnection: {e}")

    def on_room_updated(self):
        """Handle room updates - detects when recording stops"""
        try:
            current_recording_state = self.ctx.room.is_recording

            # Process starts when recording is active, so we only need to detect when it becomes False
            # Check: if recording is False AND we're still active AND not already triggered, trigger stop
            if (
                not current_recording_state
                and self._recording_active
                and not self._recording_stop_triggered
            ):
                logger.info("⏸️ RECORDING STOPPED in room (detected via room update)")
                self.on_recording_stopped()

        except Exception as e:
            logger.error(f"❌ Error handling room update: {e}")

    def on_recording_started(self):
        """Called when recording starts in the room"""
        logger.info(
            f"📹 Recording started - Room: {self.room_id}, Recording ID: {self.recording_id}"
        )
        # Add your custom logic here when recording starts
        # For example, you could:
        # - Log to a database
        # - Send a notification
        # - Update internal state
        pass

    def on_recording_stopped(self):
        """Called when recording stops in the room - automatically sends transcripts, generates summary and shuts down"""
        # Check if already triggered to prevent duplicate summary generation
        if self._recording_stop_triggered:
            logger.info(
                "⚠️ Stop recording already triggered, ignoring duplicate trigger"
            )
            return

        # Set flag immediately to prevent duplicate triggers (synchronous, no race condition)
        self._recording_stop_triggered = True

        # Immediately stop transcript storage (synchronous, no race condition)
        self._recording_active = False
        logger.info(
            f"📹 Recording stopped - Room: {self.room_id}, Recording ID: {self.recording_id}"
        )
        logger.info(
            "🛑 Transcript storage disabled - no new transcripts will be stored"
        )

        logger.info(
            "🔄 Finalizing transcripts, generating summary, and shutting down..."
        )

        # Create one task so API 01 and API 02 use the same finalized transcripts.
        async def handle_recording_stopped():
            try:
                await self._wait_for_pending_transliterations()
                raw_count = len(self._transcripts)
                self._transcripts = await self._transcript_finalizer.finalize(
                    list(self._transcripts)
                )
                logger.info(
                    "✅ Transcript finalization completed: %s raw chunks → %s final segments",
                    raw_count,
                    len(self._transcripts),
                )

                await asyncio.gather(
                    self._send_transcripts_to_api(),
                    self.generate_summary(),
                )
                logger.info("✅ Transcript and summary submission completed")
                # If room is still connected (close_on_disconnect=False), trigger shutdown so we exit cleanly
                try:
                    self.ctx.shutdown("summary_completed")
                except Exception as e:
                    logger.debug("ctx.shutdown after summary: %s", e)
            except Exception as e:
                logger.error(f"❌ Error handling recording stopped: {e}")

        # Schedule the async task and store reference so shutdown can wait for API 02
        task = asyncio.create_task(handle_recording_stopped())
        self._completion_task = task
        self._tasks.add(task)
        task.add_done_callback(lambda _: self._tasks.discard(task))

    async def _wait_for_pending_transliterations(self) -> None:
        """Wait for live transliterations that started before recording stopped."""
        pending_tasks = []
        for activity in self._transcription_activities:
            pending_tasks.extend(
                task
                for task in list(getattr(activity, "_transliteration_tasks", set()))
                if not task.done()
            )

        if not pending_tasks:
            return

        logger.info(
            "⏳ Waiting for %s pending live transliteration task(s)",
            len(pending_tasks),
        )
        results = await asyncio.gather(*pending_tasks, return_exceptions=True)
        failures = sum(isinstance(result, Exception) for result in results)
        if failures:
            logger.warning(
                "⚠️ %s pending transliteration task(s) failed before finalization",
                failures,
            )

    async def _start_session(self, participant: rtc.RemoteParticipant) -> AgentSession:
        """Start session for a participant"""
        try:
            if participant.identity in self._sessions:
                return self._sessions[participant.identity]

            logger.debug(f"Creating AgentSession for {participant.identity}")
            session = AgentSession()
            logger.debug(
                f"Configuring RoomIO for participant={participant.identity} room={self.room_id}"
            )
            room_io = RoomIO(
                agent_session=session,
                room=self.ctx.room,
                participant=participant,
                input_options=RoomInputOptions(
                    text_enabled=False,
                    close_on_disconnect=False,
                ),
                output_options=RoomOutputOptions(
                    transcription_enabled=True,
                    audio_enabled=False,
                ),
            )

            logger.debug("Starting RoomIO...")
            await room_io.start()
            logger.info(
                f"🎧 RoomIO started for {participant.identity} (transcription_enabled=True, audio_enabled=False)"
            )

            # Create agent
            logger.debug("Instantiating TranscriberAgent...")
            agent = TranscriberAgent(
                config=self.config,
                summary_service=self.summary_service,
                participant_identity=participant.identity,
                room_id=self.room_id,
                session_id=self.session_id,
            )
            logger.debug("Starting AgentSession with TranscriberAgent...")

            await session.start(agent=agent)
            logger.info(f"🟢 AgentSession started for {participant.identity}")

            # # Register handler for transcription text streams to avoid "no callback attached" warnings
            # def on_transcription_text_stream(reader: rtc.TextStreamReader, sender_identity: str):
            #     """Handle incoming transcription text streams (ignore them, we generate our own)"""
            #     # Just read and discard the stream silently
            #     async def _read_and_discard():
            #         try:
            #             await reader.read_all()
            #         except Exception:
            #             pass  # Silently ignore errors

            #     task = asyncio.create_task(_read_and_discard())
            #     self._tasks.add(task)
            #     task.add_done_callback(lambda _: self._tasks.discard(task))

            # # Register the transcription topic handler
            # try:
            #     self.ctx.room.register_text_stream_handler(TOPIC_TRANSCRIPTION, on_transcription_text_stream)
            #     logger.debug(f"✅ Registered transcription text stream handler for {participant.identity}")
            # except ValueError:
            #     # Handler already registered, that's fine
            #     logger.debug(f"Transcription handler already registered for {participant.identity}")

            # Note: Transcript storage is handled by the patch_agent_activity_on_final_transcript
            # which stores transcripts and then triggers the user_input_transcribed event.
            # We don't need a separate handler here to avoid duplicate storage.

            # Monkey patch on_final_transcript for transliteration
            if hasattr(session, "_activity") and session._activity is not None:
                patch_agent_activity_on_final_transcript(
                    activity=session._activity,
                    participant_identity=participant.identity,
                    room_id=self.room_id,
                    session_id=self.session_id,
                    transcripts_store=self._transcripts,  # Store directly in instance
                    should_store_transcript=lambda: self._recording_active,
                )
                self._transcription_activities.append(session._activity)
                logger.info(
                    f"✅ Transliteration patch applied for {participant.identity}"
                )
            else:
                logger.warning(
                    f"⚠️ Could not access session._activity for {participant.identity}, transliteration patch not applied"
                )

            return session

        except Exception as e:
            logger.error(f"❌ Error starting session for {participant.identity}: {e}")
            raise TranscriberError(f"Failed to start session: {e}")

    async def _close_session(self, session: AgentSession) -> None:
        """Close a session"""
        try:
            logger.debug("Draining AgentSession before close...")
            await session.drain()
            logger.debug("Closing AgentSession...")
            await session.aclose()
            logger.info("🔴 AgentSession closed")
        except Exception as e:
            # Session may already be closed by LiveKit on room/participant disconnect
            if "isn't running" in str(e).lower() or "not running" in str(e).lower():
                logger.debug(
                    "AgentSession already closed (expected during shutdown): %s", e
                )
            else:
                logger.error(f"❌ Error closing session: {e}")

    def get_transcripts(self):
        """Get all transcripts for the current session"""
        return self._transcripts

    async def _initialize_user_mapping(self):
        """Initialize user ID to name mapping from database"""
        try:
            # Setup DB config based on environment
            if self.environment == "STAGING":
                db_config = {
                    "user": os.getenv("COWORKING_USER_STAGING"),
                    "password": os.getenv("COWORKING_PASSWORD_STAGING"),
                    "database": os.getenv("COWORKING_DB_STAGING"),
                    "host": os.getenv("COWORKING_HOST_STAGING"),
                }
                logger.info("⚙️ Using STAGING database configuration for user mapping")
            else:
                db_config = {
                    "user": os.getenv("COWORKING_USER"),
                    "password": os.getenv("COWORKING_PASSWORD"),
                    "database": os.getenv("COWORKING_DB"),
                    "host": os.getenv("COWORKING_HOST"),
                }
                logger.info(
                    "⚙️ Using PRODUCTION database configuration for user mapping"
                )

            # Initialize DB client and fetch users (scoped by organization_id)
            if self.organization_id is None:
                logger.warning(
                    "⚠️ organization_id not set; skipping user mapping (will use IDs in summaries)"
                )
                self._user_mapping_ready.set()
                return
            db_client = DBClient(db_config, self.organization_id)
            users = await db_client.fetch_qlu_users()

            # Create ID → Name mapping
            for user in users:
                user_id = user.get("id")
                user_name = user.get("name")
                if user_id and user_name:
                    self._user_id_to_name_map[int(user_id)] = user_name

            logger.info(
                f"✅ Loaded {len(self._user_id_to_name_map)} user mappings for ID → Name conversion"
            )
            # Signal that mapping is ready
            self._user_mapping_ready.set()

        except Exception as e:
            logger.warning(
                f"⚠️ Failed to initialize user mapping (will use IDs in summaries): {e}"
            )
            # Still signal ready even if failed (so summary generation doesn't wait forever)
            self._user_mapping_ready.set()

    def _get_participant_name(self, participant_identity: str) -> str:
        """
        Get participant name from ID if available, otherwise return original identity.
        Extracts numeric ID from formats like "982-meeting" → 982 → "John Doe"
        """
        try:
            # Extract numeric ID (e.g., "982-meeting" → "982")
            if "-" in participant_identity:
                numeric_id_str = participant_identity.split("-")[0]
            else:
                numeric_id_str = participant_identity

            numeric_id = int(numeric_id_str)
            # Look up name in mapping
            name = self._user_id_to_name_map.get(numeric_id)
            if name:
                return name
        except (ValueError, KeyError):
            pass

        # Fallback to original identity if mapping not found
        return participant_identity

    def _convert_timestamp_to_local(
        self, timestamp: Optional[datetime]
    ) -> Optional[str]:
        """Convert timestamp to local timezone format with offset based on local_time_profile"""
        if timestamp is None:
            return None

        try:
            # Get timezone info from local_time_profile
            timezone_str = self.local_time_profile.get("timezone", "Asia/Karachi")
            offset_minutes = self.local_time_profile.get("offset_minutes", 300)

            # Create timezone offset
            tz_offset = timezone(timedelta(minutes=offset_minutes))

            # If timestamp is naive, assume it's in the local timezone
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=tz_offset)
            else:
                # If timestamp has timezone, convert to local timezone
                timestamp = timestamp.astimezone(tz_offset)

            # Format with offset (e.g., 2025-01-19T14:30:22+05:00)
            # Calculate offset sign and hours/minutes
            offset_hours = offset_minutes // 60
            offset_mins = abs(offset_minutes % 60)
            sign = "+" if offset_minutes >= 0 else "-"
            offset_str = f"{sign}{abs(offset_hours):02d}:{offset_mins:02d}"

            # Return ISO format string with local timezone offset
            return timestamp.strftime(f"%Y-%m-%dT%H:%M:%S{offset_str}")

        except Exception as e:
            logger.warning(f"⚠️ Error converting timestamp: {e}, using original")
            return (
                timestamp.isoformat()
                if timestamp.tzinfo
                else timestamp.isoformat() + "+00:00"
            )

    async def generate_summary(self):
        """Generate meeting summary from cached transcripts, print JSON payload, and send metadata to API"""
        import json

        try:
            # Get transcripts
            transcripts = self.get_transcripts()

            if not transcripts:
                # No transcripts - send placeholder values to API 02 (skip LLM calls)
                logger.info(
                    "⚠️ No transcripts found - sending placeholder values to API 02 (skipping LLM calls)"
                )

                metadata_payload = {
                    "recordingId": self.recording_id,
                    "roomName": self.room_id,
                    "meetingTitle": "Meeting Recording",
                    "summary": "Not enough conversations to generate a summary",
                    "actionItems": [],
                }

                # Print the JSON payload
                logger.info("=" * 80)
                logger.info("📋 MEETING METADATA JSON PAYLOAD (API 02) - PLACEHOLDER")
                logger.info("=" * 80)
                logger.info(json.dumps(metadata_payload, indent=2, ensure_ascii=False))
                logger.info("=" * 80)

                # Send POST request to API 02 (metadata)
                await self._send_metadata_to_api(metadata_payload)
                return

            # Transcripts exist - generate via LLM (existing flow)
            logger.info(f"📊 Generating summary from {len(transcripts)} transcripts")

            # Wait for user mapping to be ready (with timeout to avoid blocking forever)
            try:
                await asyncio.wait_for(self._user_mapping_ready.wait(), timeout=10.0)
                logger.debug(
                    "✅ User mapping ready, proceeding with summary generation"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "⚠️ User mapping not ready after 10s, proceeding with empty mapping"
                )

            # Generate summary using the service (pass user mapping for name conversion)
            summary = await self.summary_service.generate_summary(
                transcripts, user_id_to_name_map=self._user_id_to_name_map
            )

            # Extract action items from summary
            action_items = await self.summary_service.extract_action_items(
                summary.summary_content
            )

            # Format summary with GPT-4.1 according to strict markdown rules
            logger.info("🎨 Formatting summary with GPT-4.1...")
            formatted_summary = await self.summary_service.format_summary(
                summary.summary_content
            )

            # Generate short meeting title from formatted summary (under 100 chars)
            logger.info("📝 Generating short meeting title from formatted summary...")
            meeting_title = await self.summary_service.generate_short_meeting_title(
                formatted_summary
            )

            # Build metadata payload for API 02
            metadata_payload = {
                "recordingId": self.recording_id,
                "roomName": self.room_id,
                "meetingTitle": meeting_title,  # Use short title from formatted summary
                "summary": formatted_summary,  # Use formatted summary
                "actionItems": action_items,
            }

            # Print the JSON payload
            logger.info("=" * 80)
            logger.info("📋 MEETING METADATA JSON PAYLOAD (API 02)")
            logger.info("=" * 80)
            logger.info(json.dumps(metadata_payload, indent=2, ensure_ascii=False))
            logger.info("=" * 80)

            # Send POST request to API 02 (metadata)
            await self._send_metadata_to_api(metadata_payload)

        except Exception as e:
            logger.error(f"❌ Error generating summary: {e}")
            raise TranscriberError(f"Failed to generate summary: {e}")

    async def _send_transcripts_to_api(self):
        """Send transcripts to API 01 immediately after recording stops"""
        import json

        try:
            # Get transcripts
            transcripts = self.get_transcripts()

            # Build transcriptDetails array with converted timestamps (same logic as before)
            transcript_details = []
            if transcripts:
                for transcript in transcripts:
                    converted_time = self._convert_timestamp_to_local(
                        transcript.timestamp
                    )
                    # Extract user ID (remove suffix after hyphen, e.g., "1072-meeting" -> "1072")
                    user_id = (
                        transcript.speaker_name.split("-")[0]
                        if "-" in transcript.speaker_name
                        else transcript.speaker_name
                    )
                    transcript_details.append(
                        {
                            "userId": user_id,
                            "content": transcript.text_content,
                            "time": converted_time,
                        }
                    )
            else:
                logger.info(
                    "⚠️ No transcripts found - sending empty transcriptDetails list to API 01"
                )

            # Build payload for API 01 (always send, even with empty list)
            payload = {
                "recordingId": self.recording_id,
                "transcriptDetails": transcript_details,
            }

            # Print the JSON payload
            logger.info("=" * 80)
            logger.info("📋 TRANSCRIPTS JSON PAYLOAD (API 01)")
            logger.info("=" * 80)
            logger.info(json.dumps(payload, indent=2, ensure_ascii=False))
            logger.info("=" * 80)

            # API URL based on environment
            if self.environment == "STAGING":
                api_url = "https://api.world.staging.qlu.ai/m2m/agent/recording-transcriptions-details"
                logger.info("⚙️ Using STAGING API URL for transcriptions-details")
            else:
                api_url = "https://api.world.qlu.ai/m2m/agent/recording-transcriptions-details"
                logger.info("⚙️ Using PRODUCTION API URL for transcriptions-details")

            # Get M2M token
            m2m_token = os.getenv("M2M_TOKEN_TRANSCRIPTION")
            if not m2m_token:
                logger.error("❌ M2M_TOKEN_TRANSCRIPTION not configured")
                return

            # Prepare headers
            headers = {
                "Authorization": f"Bearer {m2m_token}",
                "Content-Type": "application/json",
            }

            # Send POST request
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(api_url, headers=headers, json=payload)
                response.raise_for_status()

            logger.info(f"✅ Successfully sent transcripts to API 01: {api_url}")

        except httpx.HTTPStatusError as e:
            logger.error(
                f"❌ HTTP error sending transcripts to API 01: {e.response.text}"
            )
        except Exception as e:
            logger.error(f"❌ Error sending transcripts to API 01: {e}")

    async def _send_metadata_to_api(self, payload: dict):
        """Send metadata (summary, action items, meeting title) to API 02 after LLM generation"""
        try:
            # API URL based on environment
            if self.environment == "STAGING":
                api_url = "https://api.world.staging.qlu.ai/m2m/agent/recording-transcriptions-metadata"
                logger.info("⚙️ Using STAGING API URL for transcriptions-metadata")
            else:
                api_url = "https://api.world.qlu.ai/m2m/agent/recording-transcriptions-metadata"
                logger.info("⚙️ Using PRODUCTION API URL for transcriptions-metadata")

            # Get M2M token
            m2m_token = os.getenv("M2M_TOKEN_TRANSCRIPTION")
            if not m2m_token:
                logger.error("❌ M2M_TOKEN_TRANSCRIPTION not configured")
                return

            # Prepare headers
            headers = {
                "Authorization": f"Bearer {m2m_token}",
                "Content-Type": "application/json",
            }

            # Send POST request
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(api_url, headers=headers, json=payload)
                response.raise_for_status()

            logger.info(f"✅ Successfully sent metadata to API 02: {api_url}")

        except httpx.HTTPStatusError as e:
            logger.error(f"❌ HTTP error sending metadata to API 02: {e.response.text}")
        except Exception as e:
            logger.error(f"❌ Error sending metadata to API 02: {e}")
