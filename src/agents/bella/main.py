# # MONKEY PATCH GOOGLE LLM PARSE
import os

from src.agents.bella.prompts import TODO_SYSTEM_PROMPT, CALENDAR_SYSTEM_PROMPT
import sys
from pathlib import Path
import dotenv
import json

src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))
from datetime import datetime
from dataclasses import asdict
import pytz
from livekit import agents
from livekit.agents import (
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    AutoSubscribe,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
)
from livekit.agents.voice.events import AgentState
from livekit.plugins import google, deepgram, openai, groq, silero, assemblyai
from src.agents.bella.shared import UserData
from src.agents.bella.todo_agent import TodoAgent
from src.agents.bella.calendar_agent import CalendarAgent
from src.agents.bella.memory_manager import BellaMemoryManager

# from src.agents.bella.meeting_query_agent import MeetingQueryAgent
from google.genai.types import (
    Modality,
    RealtimeInputConfig,
    StartSensitivity,
    EndSensitivity,
    AutomaticActivityDetection,
)
from livekit.agents.voice.room_io import RoomIO, RoomInputOptions, RoomOutputOptions
from src.agents.bella.utils import is_english, translate
import copy
import time
from livekit.agents.llm.chat_context import ChatContext
from utils.logger import Logger
from src.agents.bella.db_client import DBToDoClient
import asyncio
from google.genai.types import (
    ThinkingConfig,
    HttpOptions,
    AutomaticFunctionCallingConfig,
)
from livekit.agents.voice.events import CloseReason

import asyncio
import copy
import json
from typing import TypeVar, Generic
from livekit import rtc
from livekit.agents import llm, stt, tts, utils, vad
from livekit.agents.cli import cli
from livekit.agents.job import get_job_context
from livekit.agents.llm import ChatContext
from livekit.agents.log import logger
from livekit.agents.telemetry import trace_types, tracer
from livekit.agents.types import NOT_GIVEN
from livekit.agents.utils.misc import is_given
from livekit.agents.voice import io, room_io
from livekit.agents.voice.agent import Agent
from livekit.agents.voice.agent_activity import AgentActivity
from livekit.agents.voice.events import CloseReason
from livekit.agents.voice.run_result import RunResult
from opentelemetry import context as otel_context, trace

from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.voice.agent_session import SessionConnectOptions

from google.genai import types
from livekit.agents import APIConnectionError, APIStatusError, llm, utils
from livekit.agents.llm import (
    FunctionTool,
    RawFunctionTool,
    ToolChoice,
    utils as llm_utils,
)
from livekit.agents.llm.tool_context import (
    get_function_info,
    get_raw_function_info,
    is_function_tool,
    is_raw_function_tool,
)
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given
from google.genai.errors import APIError, ClientError, ServerError
from livekit.plugins.google.utils import (
    create_tools_config,
    to_fnc_ctx,
    to_response_format,
)
from livekit.plugins.google.tools import _LLMTool

# Create enhanced connection options for each service
enhanced_llm_options = APIConnectOptions(
    max_retry=3,  # Increase from 3 to 10
    retry_interval=5.0,  # Increase from 2.0 to 5.0
    timeout=20.0,  # Increase from 10.0 to 300.0 (5 minutes)
)

enhanced_stt_options = APIConnectOptions(
    max_retry=10,  # Increase from 3 to 8
    retry_interval=3.0,  # Increase from 2.0 to 3.0
    timeout=20.0,  # Increase from 10.0 to 180.0 (3 minutes)
)

enhanced_tts_options = APIConnectOptions(
    max_retry=5,  # Increase from 3 to 6
    retry_interval=3.0,  # Increase from 2.0 to 4.0
    timeout=20.0,  # Increase from 10.0 to 120.0 (2 minutes)
)

# Create custom session connection options
custom_conn_options = SessionConnectOptions(
    stt_conn_options=enhanced_stt_options,
    llm_conn_options=enhanced_llm_options,
    tts_conn_options=enhanced_tts_options,
    max_unrecoverable_errors=5000,  # Increase from 3 to 5
)

logging = Logger(__name__)

from dotenv import load_dotenv
import os

# load values into os.environ first
load_dotenv()

from src.utils.openai_env import get_openai_api_key_v2

alt_open_api_key = get_openai_api_key_v2()
M2M_TOKEN = os.getenv("M2M_TOKEN")

# grab all keys once
DG_KEYS = {
    "1": os.getenv("DEEPGRAM_API_KEY"),
    "2": os.getenv("DEEPGRAM_API_KEY_2"),
    "3": os.getenv("DEEPGRAM_API_KEY_3"),
}


async def entrypoint(
    ctx: JobContext,
    host_name: str,
    user_id: str,
    modality: str,
    reconnect: bool,
    local_time_profile: dict | None = None,
    environment: str | None = None,
    initial_message: str | None = None,
    organization_id: int | None = None,
):
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    logging.info("FFFFFFFFFFFff")
    logging.info(initial_message)
    logging.info("🎙️ Agent connected to LiveKit room!")
    logging.info(f"📋 Room Name: {ctx.room.name}")
    logging.info(f"👤 Local Participant SID: {ctx.room.local_participant.sid}")
    logging.info(
        f"👤 Local Participant Identity: {ctx.room.local_participant.identity}"
    )
    logging.info(f"👥 Total Participants: {ctx.room.num_participants}")
    logging.info(f"👥 Creation Time: {ctx.room.creation_time}")
    logging.info(f"👥 Is Recording: {ctx.room.is_recording}")
    logging.info(f"👥 Empty Timeout: {ctx.room.empty_timeout}")
    logging.info(f"👥 is connected: {ctx.room.isconnected()}")
    logging.info(f"👥 metadata: {ctx.room.metadata}")
    logging.info(f"👥 remote participants: {ctx.room.num_publishers}")
    logging.info(f"👥 Connection State: {ctx.room.connection_state}")

    local_timezone = ""
    if local_time_profile:
        local_timezone = local_time_profile.get("timezone")
        logging.info(local_timezone)

    logging.info(f"Bella is running in {environment}")

    logging.info("🎵 Available tracks:")
    for participant in ctx.room.remote_participants.values():
        logging.info(f"👥 Participant: {participant.identity} | {participant.sid}")

    # ─────────────────────────────────────────────────────────────────────────
    # Resolve `host_name` to the participant's REAL LiveKit identity.
    #
    # The dispatch metadata carries a label like "1388-active", but the frontend
    # mints participant identities as "{user_id}-{context}-{session}", e.g.
    # "1388-active-165da149". RoomIO compares identities with `!=` (exact), and
    # every agent->client RPC passes this value as `destination_identity`, so an
    # unresolved label leaves the session silently unable to send or receive.
    #
    # Resolving here fixes every consumer at once, because userdata.host_name,
    # TodoAgent, CalendarAgent, the idle-state RPC and RoomIO are all assigned
    # further down using this same variable.
    #
    # Guard: a candidate must belong to THIS user (prefix `{user_id}-`), so the
    # agent can never attach itself to a different user's participant. The
    # trailing hyphen matters: "13880-active-x" does NOT start with "1388-".
    # ─────────────────────────────────────────────────────────────────────────
    def _match_participant_identity(label):
        identities = [p.identity for p in ctx.room.remote_participants.values()]
        if not identities:
            return None
        # 1. already the exact identity
        if label in identities:
            return label
        # 2. same label plus a session suffix: "1388-active" -> "1388-active-165da149"
        for ident in identities:
            if label and ident.startswith(f"{label}-"):
                return ident
        # 3. same user, different context label: "1388-active" -> "1388-meeting-165da149"
        for ident in identities:
            if user_id and ident.startswith(f"{user_id}-"):
                return ident
        return None

    try:
        _original_host_name = host_name
        _resolved = _match_participant_identity(host_name)

        # Only wait if the room is genuinely empty; if someone is already in the
        # room but nothing matched, waiting cannot help.
        if _resolved is None and not ctx.room.remote_participants:
            logging.info(
                f"⏳ No participant yet - waiting up to 10s to resolve {host_name!r}"
            )
            try:
                await asyncio.wait_for(ctx.wait_for_participant(), timeout=10)
            except asyncio.TimeoutError:
                logging.info("⏳ Timed out waiting for a participant")
            except Exception as _e:
                logging.info(f"⏳ wait_for_participant failed: {_e}")
            _resolved = _match_participant_identity(host_name)

        if _resolved and _resolved != _original_host_name:
            host_name = _resolved
            logging.info(
                f"🔗 host_name resolved: {_original_host_name!r} -> {host_name!r}"
            )
        elif _resolved:
            logging.info(f"🔗 host_name already exact: {host_name!r}")
        else:
            logging.warning(
                f"⚠️ Could not resolve host_name={host_name!r} to any participant "
                f"of user {user_id!r} - keeping it unchanged. Participants in room: "
                f"{[p.identity for p in ctx.room.remote_participants.values()]}"
            )
    except Exception as _e:
        logging.warning(
            f"⚠️ host_name resolution failed ({_e}) - keeping original {host_name!r}"
        )

    if environment == "STAGING":
        db_config = {
            "user": os.getenv("COWORKING_USER_STAGING"),
            "password": os.getenv("COWORKING_PASSWORD_STAGING"),
            "database": os.getenv("COWORKING_DB_STAGING"),
            "host": os.getenv("COWORKING_HOST_STAGING"),
        }
        user_away_time = 240
        QLU_M2M_CALENDAR_API_URL = os.getenv("QLU_M2M_CALENDAR_API_URL_STAGING")
        logging.info("⚙️ Using STAGING database configuration")
    else:
        db_config = {
            "user": os.getenv("COWORKING_USER"),
            "password": os.getenv("COWORKING_PASSWORD"),
            "database": os.getenv("COWORKING_DB"),
            "host": os.getenv("COWORKING_HOST"),
        }
        user_away_time = 900
        QLU_M2M_CALENDAR_API_URL = os.getenv("QLU_M2M_CALENDAR_API_URL")
        logging.info("⚙️ Using PRODUCTION database configuration")

    db_client = None

    userdata = UserData()

    logging.info(
        f"🔍 DEBUG: entrypoint called - reconnect={reconnect}, initial={userdata.initial}"
    )

    if reconnect:
        userdata.reconnect = True
        logging.info(f"🔍 DEBUG: reconnect flag set to True")

    userdata.modality = modality
    userdata.host_name = host_name
    userdata.environment = environment
    userdata.user_id = user_id

    # Initialize memory manager with org_id from request
    # If DB unavailable, this will raise exception - Bella can work without memories
    # Use same DB config as other DB clients (based on environment)
    org_id = str(organization_id)
    memory_manager = None
    try:
        memory_manager = BellaMemoryManager(
            user_id=user_id, org_id=org_id, db_config=db_config
        )
        # Initialize DB connection and load memories (await here, like other DB clients)
        await memory_manager.initialize()
        userdata.memory_manager = memory_manager
        userdata.first_time_user = memory_manager.is_first_time_user
        logging.info(
            f"🧠 Initialized memory manager for user {user_id} (org: {org_id})"
        )
    except Exception as e:
        logging.error(f"❌ Failed to initialize memory manager: {e}")
        logging.warning(
            f"⚠️  Memory manager unavailable - Bella will work without memories"
        )
        userdata.memory_manager = None
        userdata.first_time_user = True  # Greet when memories unavailable

    if initial_message:
        userdata.initial_msg = initial_message
        logging.info(f"🔍 DEBUG: initial_message set to: {initial_message}")

    if local_timezone:
        logging.info(userdata.local_timezone)
        userdata.local_timezone = local_timezone
        logging.info(userdata.local_timezone)

    logging.info(f"modality set to {userdata.modality}")

    # Fetch tasks, org_participants, and calendar token before creating agents (so we have user_name)
    try:
        db_client = DBToDoClient(db_config, user_id, organization_id)
        raw_tasks_from_db, org_participants, calendar_token_result = (
            await asyncio.gather(
                db_client.fetch_all_tasks(),
                db_client.fetch_qlu_users(),
                asyncio.to_thread(
                    get_access_token,
                    api_url=QLU_M2M_CALENDAR_API_URL,
                    user_id=user_id,
                    token=M2M_TOKEN,
                ),
            )
        )
        logging.info(
            f"📋 org_participants (first 2): {org_participants[:2] if org_participants else 'EMPTY'}"
        )
        calendar_read_token, calendar_type = calendar_token_result
        if calendar_read_token is not None:
            try:
                calendar_read_token = verify_correct_token(
                    org_participants,
                    user_id,
                    calendar_read_token,
                    calendar_type or "google",
                )
            except Exception as e:
                logging.error(f"❌ verify_correct_token error: {e}")
                calendar_read_token = None
        userdata.org_participants = org_participants
        userdata.access_token = calendar_read_token
        userdata.calendar_type = (
            (calendar_type or "google") if calendar_read_token else None
        )
        user = next(
            (u for u in org_participants if str(u.get("id")) == str(user_id)), None
        )
        userdata.user_name = (user.get("name") or "").strip() if user else ""
        logging.info(f"👤 user_name set to: '{userdata.user_name}'")
    except Exception as err:
        logging.error(f"Error fetching data (agents will run without user name): {err}")
        raw_tasks_from_db = []
        org_participants = []
        calendar_read_token = None
        userdata.access_token = None
        userdata.calendar_type = None
        userdata.user_name = ""

    todo_agent = TodoAgent(
        room=ctx.room,
        ctx=ctx,
        host_name=host_name,
        user_id=user_id,
        local_time_zone=userdata.local_timezone,
        memory_manager=memory_manager,
        user_name=userdata.user_name,
    )
    calendar_agent = CalendarAgent(
        room=ctx.room,
        ctx=ctx,
        host_name=host_name,
        user_id=user_id,
        local_time_zone=userdata.local_timezone,
        environment=environment,
        memory_manager=memory_manager,
        user_name=userdata.user_name,
        calendar_type=userdata.calendar_type or "google",
    )

    from livekit.agents.llm.utils import build_strict_openai_schema

    # def debug_tool_schemas(agent_obj):
    #     print(f"\n🧩 Checking tools for: {agent_obj.__class__.__name__}")
    #     for name, fn in agent_obj.__class__.__dict__.items():
    #         if hasattr(fn, "__livekit_tool__"):
    #             try:
    #                 build_strict_openai_schema(fn)
    #                 print(f"✅ Schema OK: {name}")
    #             except Exception as e:
    #                 print(f"❌ Schema FAIL in {name}: {e}")

    # debug_tool_schemas(todo_agent)
    # debug_tool_schemas(calendar_agent)
    # meeting_query_agent = MeetingQueryAgent(room=ctx.room)

    userdata.personas.update(
        {
            "todo": todo_agent,
            "calendar": calendar_agent,
            # "meeting_query": meeting_query_agent
        }
    )

    http_config_options = HttpOptions(
        timeout=360000  # timeout in milliseconds (30 seconds)
    )

    logging.info(f"TIME is {user_away_time}")
    session = BellaAgentSession[UserData](
        # stt=assemblyai.STT(),
        # stt=deepgram.STT(language="en-US", interim_results=True),
        # llm=google.LLM(model="gemini-2.5-flash", temperature=0.1, http_options=http_config_options),
        stt=assemblyai.STT(),
        llm=openai.LLM(
            api_key=alt_open_api_key,
            model="gpt-4.1",
            temperature=0.1,
            parallel_tool_calls=False,
        ),
        tts=openai.TTS(
            api_key=alt_open_api_key,
            model="gpt-4o-mini-tts",
            voice="shimmer",
            instructions="Read the text naturally as normal speech. Do NOT pronounce any formatting symbols such as asterisks (*), hashes (#), backslashes (\\), or newline indicators (\\n). Do NOT pronounce ellipses (...). Pronounce abbreviations like 'A.M.' or 'P.M.' as 'AM' or 'PM'. Read the actual content only, ignoring all formatting markers.",
        ),
        userdata=userdata,
        allow_interruptions=True,
        preemptive_generation=False,
        turn_detection="stt",
        min_endpointing_delay=2.5,
        user_away_timeout=user_away_time,
        conn_options=custom_conn_options,
        use_tts_aligned_transcript=False,
        # vad=silero.VAD.load(min_speech_duration=0.4, min_silence_duration=1.75)
    )

    logging.info(f"The MODEL BEING USED Is {session.llm.model}")

    # Monkey patch the OpenAI LLMStream class after session is created
    from livekit.plugins.openai.llm import LLMStream as OpenAILLMStream

    original_parse_choice = OpenAILLMStream._parse_choice

    def patched_parse_choice(self, id: str, choice, thinking):
        # Check if this is a tool call
        if choice.delta and choice.delta.tool_calls:
            for tool in choice.delta.tool_calls:
                if tool.function and tool.function.name:
                    # Set tool_invoked in session userdata
                    session.userdata.tool_invoked = tool.function.name
                    logging.info(f"🔧 Tool detected: {tool.function.name}")
                    # if tool.function.name not in ("handle_todo_request", "handle_calendar_request"):
                    #     # Array of filler words (1-2 words each)
                    #     filler_words = [
                    #         "One moment",
                    #         "Just a sec",
                    #         "Hang on",
                    #         "Almost done",
                    #         "Just a moment",
                    #     ]

                    #     # Randomly pick one filler word
                    #     import random
                    #     selected_filler = random.choice(filler_words)
                    #     session.say(selected_filler, add_to_chat_ctx=False)

                    from datetime import datetime

                    logging.info(
                        f"🕒 Timestamp of Tool Call Detection: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
                    )
                    break  # Only need to detect once per tool call

        return original_parse_choice(self, id, choice, thinking)

    # Apply the patch
    OpenAILLMStream._parse_choice = patched_parse_choice

    # Fallback function
    async def trigger_fallback_message():
        """Trigger fallback when empty response is detected"""
        try:
            logging.info("🔄 Empty response detected - triggering fallback")
            await session.say("Please have patience, I'm processing your request.")
        except Exception as e:
            logging.error(f"❌ Error in fallback: {e}")

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        logging.info(
            f"User input transcribed: {event.transcript}, final: {event.is_final}, speaker id: {event.speaker_id}"
        )

    @session.on("function_tools_executed")
    def on_function_tool_execution_completed(event):
        logging.info(f"clearning {session.userdata.tool_invoked}")
        session.userdata.tool_invoked = ""

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        current_agent = userdata.current_agent
        memory_manager = userdata.memory_manager

        # Extract messages for memory extraction (track last user message, extract when assistant responds)
        if (
            memory_manager
            and hasattr(event.item, "role")
            and hasattr(event.item, "content")
        ):
            content = event.item.content
            # Handle different content formats
            if isinstance(content, list):
                content = " ".join(str(c) for c in content if c)
            elif content:
                content = str(content)
            else:
                content = ""

            if event.item.role == "user":
                # Store user message for later extraction
                if not hasattr(userdata, "_pending_user_msg"):
                    userdata._pending_user_msg = ""
                userdata._pending_user_msg = content
            elif (
                event.item.role == "assistant"
                and hasattr(userdata, "_pending_user_msg")
                and userdata._pending_user_msg
            ):
                # Assistant responded, extract memory from this turn
                asyncio.create_task(
                    memory_manager.on_conversation_turn(
                        userdata._pending_user_msg, content
                    )
                )
                userdata._pending_user_msg = ""  # Clear after extraction

        if isinstance(current_agent, CalendarAgent):
            logging.info("🗓️ Current agent is CalendarAgent")
            new_prompt = build_calendar_system_prompt1(
                modality=session.userdata.modality,
                tz=userdata.local_timezone,
                memory_manager=memory_manager,
                user_name=getattr(userdata, "user_name", "") or "",
            )
        elif isinstance(current_agent, TodoAgent):
            new_prompt = build_todo_system_prompt1(
                modality=session.userdata.modality,
                tz=userdata.local_timezone,
                memory_manager=memory_manager,
                user_name=getattr(userdata, "user_name", "") or "",
            )
            logging.info("✅ Current agent is TodoAgent")

        # Update the first system message content
        if session._agent._chat_ctx.items:
            first_item = session._agent._chat_ctx.items[0]
            if first_item.role == "system":
                first_item.content = [new_prompt]

        # Only perform the key switch if the item is from the assistant
        if hasattr(event.item, "role") and event.item.role == "assistant":
            # Process cached transcript and clear tool_invoked after assistant speaks
            session.userdata.tool_invoked = ""
            session._process_cached_transcript()

            # shift logic
            if session.userdata.key_name == "1":
                session.userdata.key_name = "2"
            elif session.userdata.key_name == "2":
                session.userdata.key_name = "3"
            else:
                session.userdata.key_name = "2"

            next_api_key = DG_KEYS[session.userdata.key_name]
            os.environ["DEEPGRAM_API_KEY"] = next_api_key

        # Count tokens only from items with content attribute
        total_chars = 0
        for item in session._agent.chat_ctx.items:
            if hasattr(item, "content") and item.content:
                total_chars += len(str(item.content))

        estimated_tokens = total_chars // 4
        logging.info(
            f"📊 Context: {len(session._agent.chat_ctx.items)} messages, ~{estimated_tokens} tokens"
        )

        if estimated_tokens > 6000:  # Trim if over 5000 tokens
            logging.info(f"🧹 Trimming context - too many tokens ({estimated_tokens})")

            # Start with last 6, then reduce if still over 5k
            keep_start = session._chat_ctx.items[:2]  # Keep first 2 (system + 1 more)

            for last_count in [10, 9, 8]:  # Try 6, then 5, then 4 (minimum)
                keep_end = session._chat_ctx.items[-last_count:]
                trimmed = keep_start + keep_end

                # Check if this combination is under 5k tokens
                trimmed_chars = 0
                for item in trimmed:
                    if hasattr(item, "content") and item.content:
                        trimmed_chars += len(str(item.content))

                trimmed_tokens = trimmed_chars // 4

                if trimmed_tokens <= 6000:
                    session._chat_ctx = ChatContext(trimmed)
                    logging.info(
                        f"✅ Context trimmed to {len(trimmed)} messages, ~{trimmed_tokens} tokens"
                    )
                    break
            else:
                # If even last 4 is over 5k, just keep the first 2
                session._chat_ctx = ChatContext(keep_start)
                logging.warning(
                    f"⚠️ Even last 4 messages are over 5k, keeping only first {len(keep_start)} messages"
                )

    @session.on("agent_state_changed")
    def state_changed(event):
        if event.new_state == "listening":
            logging.info(f"\nBella is listening 👂")
        elif event.new_state == "speaking":
            logging.info(f"\nBella is speaking 💬")
        elif event.new_state == "thinking":
            logging.info(f"\nBella is thinking 🧠")
        else:
            logging.info(f"Bella has gone crazy and {event.new_state} 🐱")

    @session.on("user_state_changed")
    def user_state_away_detected(event):
        if session.userdata.environment in ("STAGING", "PRODUCTION"):
            logging.info(session.userdata.environment)
            if event.new_state == "away":

                async def _call_rpc():
                    try:
                        response = await ctx.room.local_participant.perform_rpc(
                            destination_identity=host_name,
                            method="set_agent_state_idle",
                            payload=json.dumps({"state": "idle"}),
                        )
                        logging.info(f"RPC response: {response}")
                    except Exception as e:
                        logging.error(f"❌ RPC call failed: {e}")

                asyncio.create_task(_call_rpc())
        else:
            logging.info(session.userdata.environment)

    # Store references for cleanup
    _session_ref = session
    _room_io_ref = None  # Will be set after room_io.start()

    @session.on("close")
    def close_agent(event):
        logging.info("CLOSING The AGENT")
        # Cleanup will be handled by shutdown callback

    @ctx.room.local_participant.register_rpc_method("interrupt_response")
    async def interrupt_response(data):
        """FORCIBLY interrupt the current agent response/speech generation.

        This RPC will ALWAYS stop the agent, regardless of any settings.
        """
        logging.info(f"🛑 FORCE Interrupt RPC called by: {data.caller_identity}")

        try:
            # Check if session is running
            if session._activity is None:
                logging.error("❌ Cannot interrupt: AgentSession is not running")
                return  # just exit silently, no value returned

            # FORCE interrupt - this will ALWAYS work
            interrupt_future = session.interrupt(force=True)  # 🚨 FORCE=True

            # Wait for the interruption to complete
            await interrupt_future

            logging.info("✅ Agent response FORCIBLY interrupted")

        except Exception as e:
            logging.error(f"❌ Error interrupting agent response: {e}")

    @ctx.room.local_participant.register_rpc_method("change_modality_rpc")
    async def change_modality_rpc(data):
        logging.info(f"RPC called by: {data.caller_identity}")

        try:
            payload = data.payload

            # Ensure payload is a dict
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    logging.warn(f"❌ Failed to decode payload: {payload}")
                    return

            # Extract modality from payload (must happen before it's used)
            modality = payload.get("modality") if isinstance(payload, dict) else None

            if modality is None:
                logging.warn("⚠️ No modality provided in payload")
                return

            # Now safe to use modality
            current_agent = userdata.current_agent

            if isinstance(current_agent, CalendarAgent):
                new_prompt = build_calendar_system_prompt1(
                    modality=modality.lower(),
                    tz=userdata.local_timezone,
                    user_name=getattr(userdata, "user_name", "") or "",
                )
                logging.info("🗓️ Current agent is CalendarAgent")
            elif isinstance(current_agent, TodoAgent):
                new_prompt = build_todo_system_prompt1(
                    modality=modality.lower(),
                    tz=userdata.local_timezone,
                    user_name=getattr(userdata, "user_name", "") or "",
                )
                logging.info("✅ Current agent is TodoAgent")

            # Handle modality switching
            if modality == "TEXT":
                userdata.modality = "TEXT"
                session.input.set_audio_enabled(False)
                session.output.set_audio_enabled(False)
                logging.info("🔇 Switched to TEXT mode (audio disabled).")

            elif modality == "AUDIO":
                userdata.modality = "AUDIO"
                session.input.set_audio_enabled(True)
                session.output.set_audio_enabled(True)
                logging.info("🎙️ Switched to AUDIO mode (audio enabled).")

            else:
                logging.warn(f"⚠️ Invalid modality received: {payload}")
                return

            # Update system prompt if present
            if session._agent._chat_ctx.items:
                first_item = session._agent._chat_ctx.items[0]
                if first_item.role == "system":
                    logging.info("📝 Updating system prompt...")
                    first_item.content = [new_prompt]

        except Exception as err:
            logging.info(f"RPC FAILURE CAUSED BY {err}")

        logging.info(f"✅ Userdata modality set to: {userdata.modality}")

    def safe_get_payload(data) -> dict:
        """Extract and JSON-decode payload if needed, always return a dict."""
        payload = data.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                logging.warning(f"❌ Failed to decode payload: {payload}")
                return {}
        return payload if isinstance(payload, dict) else {}

    async def add_task_update_message_to_context(session, payload: dict, method: str):
        """
        Create a formatted message and add it to session chat context.

        Args:
            session: The agent session
            payload: Task payload dictionary
            method: Either "update" or "complete"
        """
        try:
            # Check if agent exists and has chat context
            if not hasattr(session, "_agent") or not session._agent:
                return

            if not hasattr(session._agent, "_chat_ctx"):
                return

            task_title = payload.get("title", "a task")

            if method == "complete":
                completed = payload.get("completed", False)
                status = "complete" if completed else "incomplete"
                message = f"The task {task_title} is marked as {status}."

            elif method == "update":
                priority = payload.get("priority", "medium")
                deadline = payload.get("deadline")
                note = payload.get("note")

                # Format deadline
                deadline_str = "no deadline"
                if deadline:
                    try:
                        if isinstance(deadline, str):
                            if deadline.endswith("Z"):
                                dt = datetime.fromisoformat(
                                    deadline.replace("Z", "+00:00")
                                )
                            elif "T" in deadline:
                                dt = datetime.fromisoformat(
                                    deadline.replace("Z", "+00:00")
                                )
                            else:
                                dt = datetime.fromisoformat(deadline)

                            if dt.tzinfo:
                                local_tz = pytz.timezone(
                                    session.userdata.local_timezone
                                )
                                dt = dt.astimezone(local_tz)

                            deadline_str = dt.strftime("%I:%M %p")
                            if deadline_str.startswith("0"):
                                deadline_str = deadline_str[1:]
                    except Exception as e:
                        logging.info(f"[DEBUG] Failed to format deadline: {e}")
                        deadline_str = "no deadline"

                # Format note
                note_str = note if note else "no note"

                message = f"The task {task_title} has a {priority} priority with a deadline of {deadline_str} with a note of {note_str}."
            else:
                return

            # Add message to chat context
            chat_ctx = session._agent._chat_ctx
            user_message = chat_ctx.add_message(role="user", content=message)

            # Trigger conversation item added event
            if hasattr(session, "_conversation_item_added"):
                session._conversation_item_added(user_message)

            logging.info(f"[DEBUG] Added to chat context: {message}")

        except Exception as e:
            logging.error(
                f"[ERROR] Failed to add task update message to chat context: {e}"
            )

    @ctx.room.local_participant.register_rpc_method("add_todo_manual")
    async def add_todo_manual(data):
        payload = safe_get_payload(data)
        if not payload:
            return
        logging.info(f"[DEBUG] add_todo_manual payload: {payload}")
        await session.userdata.todo_client.add_task(payload)

    @ctx.room.local_participant.register_rpc_method("update_todo_manual")
    async def update_todo_manual(data):
        payload = safe_get_payload(data)
        if not payload:
            return
        logging.info(f"[DEBUG] update_todo_manual payload: {payload}")
        await session.userdata.todo_client.update_task(payload)

        # Fetch full task state after update to get complete current state
        task_id = payload.get("id") or payload.get("todoId")
        if task_id and hasattr(session.userdata.todo_client, "get_tasks_by_ids"):
            try:
                tasks = await session.userdata.todo_client.get_tasks_by_ids([task_id])
                if tasks and len(tasks) > 0:
                    await add_task_update_message_to_context(
                        session, tasks[0], "update"
                    )
                else:
                    await add_task_update_message_to_context(session, payload, "update")
            except Exception as e:
                logging.error(f"[ERROR] Failed to fetch task for message: {e}")
                await add_task_update_message_to_context(session, payload, "update")
        else:
            await add_task_update_message_to_context(session, payload, "update")

    @ctx.room.local_participant.register_rpc_method("complete_todo_manual")
    async def complete_todo_manual(data):
        payload = safe_get_payload(data)
        if not payload:
            return
        logging.info(f"[DEBUG] complete_todo_manual payload: {payload}")

        task_id = payload.get("id")
        completed = payload.get("completed", False)
        completed_at = payload.get("completed_at")
        updated_at = payload.get("updated_at")

        await session.userdata.todo_client.complete_task(
            task_id=task_id,
            completed=completed,
            completed_at=completed_at,
            updated_at=updated_at,
        )
        await add_task_update_message_to_context(session, payload, "complete")

    @ctx.room.local_participant.register_rpc_method("delete_todo_manual")
    async def delete_todo_manual(data):
        payload = safe_get_payload(data)
        if not payload:
            return
        logging.info(f"[DEBUG] delete_todo_manual payload: {payload}")

        ids = payload.get("ids")
        if ids and isinstance(ids, list):
            for task_id in ids:
                await session.userdata.todo_client.delete_task(task_id=task_id)
        else:
            task_id = payload.get("id")
            if task_id:
                await session.userdata.todo_client.delete_task(task_id=task_id)

    @ctx.room.local_participant.register_rpc_method("delete_all_todos_manual")
    async def delete_all_todos_manual(data):
        payload = safe_get_payload(data)
        logging.info(f"[DEBUG] delete_all_todos_manual payload: {payload}")
        await session.userdata.todo_client.delete_all_todos()

    room_io = RoomIO(
        agent_session=session,
        room=ctx.room,
        participant=host_name,
        input_options=RoomInputOptions(
            audio_enabled=True,
            close_on_disconnect=False,  # Don't shut down when user leaves
            pre_connect_audio_timeout=10.0,  # Wait longer for audio
            # transcription_enabled=True,
            # text_enabled=True
        ),
        output_options=RoomOutputOptions(
            audio_enabled=True,
            sync_transcription=False,
            # transcription_enabled=True,
            # text_enabled=True
        ),
    )

    if modality == "TEXT":
        userdata.modality = "TEXT"
        session.input.set_audio_enabled(False)
        session.output.set_audio_enabled(False)
        logging.info("🔇 Switched to TEXT mode (audio disabled).")

    elif modality == "AUDIO":
        userdata.modality = "AUDIO"
        session.input.set_audio_enabled(True)
        session.output.set_audio_enabled(True)
        logging.info("🎙️ Switched to AUDIO mode (audio enabled).")

    # Store room_io reference for cleanup
    _room_io_ref = room_io

    await room_io.start()
    await session.start(agent=todo_agent, room=ctx.room)

    # Now that both session and room_io are started, register shutdown callback
    # Register shutdown callback to properly clean up resources
    async def cleanup_resources(shutdown_reason: str = ""):
        """Clean up session and room_io when job is terminated (e.g., on undispatch)"""
        logging.info(f"🧹 Cleaning up resources (reason: {shutdown_reason})")

        cleanup_errors = []

        try:
            # Cleanup memory manager (save any remaining buffered conversations)
            if userdata.memory_manager:
                try:
                    await userdata.memory_manager.cleanup()
                    logging.info("✅ Memory manager cleaned up")
                except Exception as e:
                    error_msg = f"Memory manager cleanup error: {e}"
                    logging.warning(f"⚠️ {error_msg}")
                    cleanup_errors.append(error_msg)

            # Close session first if it's still running
            # Session should be closed before room_io to ensure proper cleanup order
            if _session_ref:
                # Check if session is started (not just if _started exists)
                is_started = getattr(_session_ref, "_started", False)
                if is_started:
                    logging.info("🔌 Closing agent session...")
                    try:
                        await _session_ref.aclose()
                        logging.info("✅ Agent session closed")
                    except Exception as e:
                        error_msg = f"Error closing session: {e}"
                        logging.warning(f"⚠️ {error_msg}")
                        cleanup_errors.append(error_msg)
                else:
                    logging.info("ℹ️ Session was not started, skipping close")

            # Close room_io after session
            if _room_io_ref:
                logging.info("🔌 Closing room IO...")
                try:
                    await _room_io_ref.aclose()
                    logging.info("✅ Room IO closed")
                except Exception as e:
                    error_msg = f"Error closing room_io: {e}"
                    logging.warning(f"⚠️ {error_msg}")
                    cleanup_errors.append(error_msg)

            if cleanup_errors:
                logging.warning(
                    f"⚠️ Cleanup completed with {len(cleanup_errors)} error(s)"
                )
            else:
                logging.info("✅ Cleanup completed successfully")
        except Exception as e:
            logging.error(f"❌ Fatal error during cleanup: {e}")
            raise

    ctx.add_shutdown_callback(cleanup_resources)

    # Use already-fetched data (fetched before agent creation)
    tasks_from_db = convert_utc_to_local(
        raw_tasks_from_db, session.userdata.local_timezone
    )
    todo_agent.initialize_tasks(tasks_from_db)

    # Set up transcript caching for tool execution
    session._setup_tool_transcript_caching()

    await ctx.wait_for_participant()
    logging.info(f"🥵 participant host_name:{host_name}, user_id:{user_id} joined!")

    # Keep the entrypoint alive until the session is closed or job is terminated
    # This ensures proper cleanup when undispatch is called
    try:
        # Wait for the session to complete (it will be closed on shutdown)
        # We use a future that gets set when session closes
        session_closed = asyncio.Future()

        def on_session_close(event):
            if not session_closed.done():
                session_closed.set_result(None)

        # Register one-time listener for session close
        session.once("close", on_session_close)

        # Wait for session to close
        # The shutdown callback will close the session when undispatch happens
        # This keeps the entrypoint function alive until cleanup is complete
        try:
            await asyncio.wait_for(
                session_closed, timeout=86400
            )  # 24 hour timeout (effectively forever)
            logging.info("👋 Session closed, agent entrypoint exiting")
        except asyncio.TimeoutError:
            logging.info("⏰ Session timeout reached (this should not happen normally)")
        except Exception as e:
            logging.warning(f"⚠️ Error waiting for session close: {e}")

    except asyncio.CancelledError:
        logging.info("🚫 Agent entrypoint cancelled")
        # Ensure cleanup happens even on cancellation
        if _session_ref and getattr(_session_ref, "_started", False):
            try:
                await _session_ref.aclose()
            except Exception:
                pass
        if _room_io_ref:
            try:
                await _room_io_ref.aclose()
            except Exception:
                pass
        raise
    except Exception as e:
        logging.error(f"❌ Error in agent entrypoint: {e}")
        # Ensure cleanup on error
        if _session_ref and getattr(_session_ref, "_started", False):
            try:
                await _session_ref.aclose()
            except Exception:
                pass
        if _room_io_ref:
            try:
                await _room_io_ref.aclose()
            except Exception:
                pass
        raise


from livekit.agents import AgentSession, llm, ConversationItemAddedEvent, stt, tts

from typing import TypeVar, Generic, cast
from livekit.agents import AgentSession, llm, ConversationItemAddedEvent
from src.agents.bella.shared import UserData

# Type variable for generic inheritance
Userdata_T = TypeVar("Userdata_T")


class BellaAgentSession(AgentSession[Userdata_T], Generic[Userdata_T]):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cache for transcripts during tool execution
        self._cached_transcript = ""
        self._cached_transcript_confidence = []
        self._original_on_stt_event = None

    #     self._first_start_complete = False  # Track if first start completed

    # def _update_agent_state(self, state: AgentState) -> None:
    #     # Intercept the first transition from "initializing" to "listening"
    #     if (
    #         not self._first_start_complete
    #         and self._agent_state == "initializing"
    #         and state == "listening"
    #     ):
    #         # Change to "speaking" instead of "listening"
    #         state = "speaking"
    #         self._first_start_complete = True

    #     # Call parent implementation with (possibly modified) state
    #     super()._update_agent_state(state)

    def _on_error(
        self,
        error: llm.LLMError | stt.STTError | tts.TTSError | llm.RealtimeModelError,
    ) -> None:
        logging.error(f"🚨 ERROR DETECTED: {error.type} - {error}")
        logging.info(f"🔍 Error recoverable: {error.recoverable}")
        logging.info(f"🔍 Already closing: {self._closing_task is not None}")

        if self._closing_task or error.recoverable:
            logging.info(f"⏭️ Skipping error - already closing or recoverable")
            return

        if error.type == "llm_error":
            self._llm_error_counts += 1
            logging.info(
                f"🤖 LLM Error Count: {self._llm_error_counts}/{self.conn_options.max_unrecoverable_errors}"
            )
            if self._llm_error_counts <= self.conn_options.max_unrecoverable_errors:
                logging.info(f"✅ LLM Error tolerated - continuing session")
                return
            else:
                logging.error(f"❌ LLM Error threshold exceeded")
        elif error.type == "tts_error":
            self._tts_error_counts += 1
            logging.info(
                f"🔊 TTS Error Count: {self._tts_error_counts}/{self.conn_options.max_unrecoverable_errors}"
            )
            if self._tts_error_counts <= self.conn_options.max_unrecoverable_errors:
                logging.info(f"✅ TTS Error tolerated - continuing session")
                return
            else:
                logging.error(f"❌ TTS Error threshold exceeded")
        elif error.type == "stt_error":
            # Give STT errors the same tolerance as LLM/TTS
            self._llm_error_counts += 1  # Use existing counter
            logging.info(
                f"🎤 STT Error Count: {self._llm_error_counts}/{self.conn_options.max_unrecoverable_errors}"
            )
            if self._llm_error_counts <= self.conn_options.max_unrecoverable_errors:
                logging.info(f"✅ STT Error tolerated - continuing session")
                return
            else:
                logging.error(f"❌ STT Error threshold exceeded")
        elif error.type == "realtime_model_error":
            # Give RealtimeModel errors the same tolerance
            self._llm_error_counts += 1  # Use existing counter
            logging.info(
                f"⚡ RealtimeModel Error Count: {self._llm_error_counts}/{self.conn_options.max_unrecoverable_errors}"
            )
            if self._llm_error_counts <= self.conn_options.max_unrecoverable_errors:
                logging.info(f"✅ RealtimeModel Error tolerated - continuing session")
                return
            else:
                logging.error(f"❌ RealtimeModel Error threshold exceeded")

        logging.error(f"💀 SHUTTING DOWN SESSION due to unrecoverable error")
        logging.error(
            "AgentSession is closing due to unrecoverable error", exc_info=error.error
        )

        def on_close_done(_: asyncio.Task[None]) -> None:
            self._closing_task = None

        self._closing_task = asyncio.create_task(
            self._aclose_impl(error=error, reason=CloseReason.ERROR)
        )
        self._closing_task.add_done_callback(on_close_done)

    def _setup_tool_transcript_caching(self):
        """Setup monkey patching for transcript caching during tool execution"""
        if self._activity and self._activity._audio_recognition:
            # Store original method
            if not self._original_on_stt_event:
                self._original_on_stt_event = (
                    self._activity._audio_recognition._on_stt_event
                )

            # Create patched version - WITH CACHING
            async def patched_on_stt_event(ev):
                from livekit.agents import stt

                # Check if tool is being executed
                if (
                    hasattr(self.userdata, "tool_invoked")
                    and self.userdata.tool_invoked
                ):
                    logging.info(
                        f"🔧 Tool '{self.userdata.tool_invoked}' is executing - CACHING TRANSCRIPT"
                    )

                    # Cache the transcript
                    if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                        transcript = ev.alternatives[0].text
                        confidence = ev.alternatives[0].confidence

                        if transcript:
                            self._cached_transcript += f" {transcript}"
                            self._cached_transcript = self._cached_transcript.lstrip()
                            self._cached_transcript_confidence.append(confidence)
                            logging.info(f"📝 Cached: '{self._cached_transcript}'")

                    # Just return - don't process
                    return

                # If no tool is executing, call original method
                return await self._original_on_stt_event(ev)

            # Apply the patch
            self._activity._audio_recognition._on_stt_event = patched_on_stt_event

    def _process_cached_transcript(self):
        """Process the cached transcript after tool execution completes"""
        if (
            self._cached_transcript
            and self._activity
            and self._activity._audio_recognition
        ):
            logging.info(
                f"🔄 Processing cached transcript: '{self._cached_transcript}'"
            )

            # Simply set the transcript in the audio recognition and trigger processing
            self._activity._audio_recognition._audio_transcript = (
                self._cached_transcript
            )
            self._activity._audio_recognition._final_transcript_confidence = (
                self._cached_transcript_confidence.copy()
            )

            # Trigger the end of turn detection manually
            from livekit.agents.llm import ChatContext

            chat_ctx = self._activity._agent.chat_ctx.copy()
            self._activity._audio_recognition._run_eou_detection(chat_ctx)

            # Clear cache
            self._cached_transcript = ""
            self._cached_transcript_confidence = []
        else:
            logging.info(f"🔄 No cached transcript to process")


def build_todo_system_prompt1(
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
        memory_manager: BellaMemoryManager instance to inject user memories.
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


def build_calendar_system_prompt1(
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
        memory_manager: BellaMemoryManager instance to inject user memories.
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


import requests


def get_access_token(
    api_url: str, user_id: str, token: str
) -> tuple[str | None, str | None]:
    """
    Fetch the calendar access token and type for this user ID.
    API may return:
      - string -> (token, "google")
      - object {"token": "<access_token>", "type": "google"|"outlook"} -> (token, type)
    Returns (token, calendar_type); (None, None) if request fails or no token.
    """
    try:
        payload = {"userIds": [user_id]}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        logging.info(
            f"🔍 Fetching calendar token for user_id={user_id}, api_url={api_url}"
        )
        response = requests.post(api_url, json=payload, headers=headers, timeout=10)

        logging.info(f"📡 Response status: {response.status_code}")
        if response.status_code != 200:
            logging.error(f"❌ API Error Response (status: {response.status_code})")

        response.raise_for_status()
        data = response.json()
        logging.info(
            f"📦 Response data keys: {data.keys() if isinstance(data, dict) else 'not a dict'}"
        )

        if "data" not in data:
            logging.error(
                f"❌ Response missing 'data' key. Response keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}"
            )
            return (None, None)

        # API returns list: [{"userId": "977", "calendarType": "outlook", "token": "..."}]
        payload_list = data["data"]
        if not isinstance(payload_list, list):
            logging.error(f"❌ Expected 'data' to be a list, got {type(payload_list)}")
            return (None, None)
        raw = None
        for item in payload_list:
            if str(item.get("userId")) == str(user_id):
                raw = item
                break
        if raw is None:
            logging.error(f"❌ User ID {user_id} not found in response data.")
            return (None, None)

        calendar_token = raw.get("token")
        calendar_type = (raw.get("calendarType") or raw.get("type") or "google").lower()
        if calendar_type not in ("google", "outlook"):
            calendar_type = "google"

        if calendar_token is None or (
            isinstance(calendar_token, str) and not calendar_token.strip()
        ):
            logging.warning(f"⚠️ User {user_id} has no calendar token configured")
            return (None, None)

        logging.info(
            f"✅ Fetched token (length: {len(calendar_token)}), type: {calendar_type}"
        )
        return (calendar_token, calendar_type)
    except requests.exceptions.RequestException as err:
        logging.warning(f"⚠️ Request failed to fetch calendar access token: {err}")
        if hasattr(err, "response") and err.response is not None:
            logging.info(f"   Response status: {err.response.status_code}")
        return (None, None)
    except Exception as err:
        logging.warning(f"⚠️ Failed to fetch calendar access token: {err}")
        import traceback

        traceback.print_exc()
        return (None, None)


from datetime import datetime
import zoneinfo


def convert_utc_to_local(tasks, timezone_str: str):
    """
    Convert UTC datetime fields in tasks to the given timezone.
    """
    local_tz = zoneinfo.ZoneInfo(timezone_str)

    datetime_fields = ["created_at", "updated_at", "deadline", "completed_at"]

    for task in tasks:
        for field in datetime_fields:
            value = task.get(field)
            if isinstance(value, datetime) and value.tzinfo is not None:
                task[field] = value.astimezone(local_tz)
    return tasks


import requests

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
MS_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"


def _verify_google_token(org_participants, user_id, calendar_token):
    """Verify Google token via userinfo and compare email. Returns token if ok else None."""
    user = next((u for u in org_participants if str(u["id"]) == str(user_id)), None)
    if not user or not user.get("email"):
        logging.error(f"❌ User not found in org_participants for user_id={user_id}")
        return None
    expected_email = user["email"].strip().lower()
    try:
        res = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {calendar_token}"},
            timeout=8,
        )
        if res.status_code != 200:
            logging.error(f"❌ Google token invalid - Status {res.status_code}")
            return None
        info = res.json()
        token_email = info.get("email", "").strip().lower()
        if token_email == expected_email:
            logging.info(f"✅ Google token verified - emails match: {token_email}")
            return calendar_token
        logging.error(
            f"❌ Google token email mismatch: {token_email} != {expected_email}"
        )
        return None
    except Exception as e:
        logging.error(f"❌ Error verifying Google token: {e}")
        return None


def _verify_outlook_token(org_participants, user_id, calendar_token):
    """Verify Outlook token via Microsoft Graph GET /me and compare email. Returns token if ok else None."""
    # TODO: uncomment later – for now assume Outlook token is always verified
    # user = next((u for u in org_participants if str(u["id"]) == str(user_id)), None)
    # if not user or not user.get("email"):
    #     print(f"❌ User not found in org_participants for user_id={user_id}")
    #     return None
    # expected_email = user["email"].strip().lower()
    # try:
    #     res = requests.get(
    #         MS_GRAPH_ME_URL,
    #         headers={
    #             "Authorization": f"Bearer {calendar_token}",
    #             "Content-Type": "application/json",
    #         },
    #         params={"$select": "mail,userPrincipalName"},
    #         timeout=8,
    #     )
    #     if res.status_code != 200:
    #         print(f"❌ Outlook token invalid - Status {res.status_code}")
    #         return None
    #     info = res.json()
    #     token_email = (info.get("mail") or info.get("userPrincipalName") or "").strip().lower()
    #     if token_email == expected_email:
    #         print(f"✅ Outlook token verified - emails match: {token_email}")
    #         return calendar_token
    #     print(f"❌ Outlook token email mismatch: {token_email} != {expected_email}")
    #     return None
    # except Exception as e:
    #     print(f"❌ Error verifying Outlook token: {e}")
    #     return None
    logging.info("✅ Outlook token accepted (verification skipped for now)")
    return calendar_token


def verify_correct_token(
    org_participants, user_id, calendar_token, calendar_type: str = "google"
):
    """
    Verify calendar token and compare email.
    - calendar_type "google" -> Google userinfo API
    - calendar_type "outlook" -> Microsoft Graph GET /me
    Returns token if correct else None.
    """
    if calendar_token is None:
        logging.error("❌ verify_correct_token called with None token - cannot verify")
        return None

    logging.info(f"🔍 Verifying {calendar_type} token for user_id={user_id}")
    if (calendar_type or "google").lower() == "outlook":
        return _verify_outlook_token(org_participants, user_id, calendar_token)
    return _verify_google_token(org_participants, user_id, calendar_token)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

    # MONKEY PATCH Final Transcript of Agent Activity
    # import os

    # from src.agents.bella.prompts import TODO_SYSTEM_PROMPT, CALENDAR_SYSTEM_PROMPT
    # import sys
    # from pathlib import Path
    # import dotenv
    # import json
    # src_path = Path(__file__).parent.parent.parent
    # sys.path.insert(0, str(src_path))
    # from datetime import datetime
    # from dataclasses import asdict
    # import pytz
    # from livekit import agents
    # from livekit.agents import (
    #     AgentSession,
    #     JobContext,
    #     WorkerOptions,
    #     cli,
    #     AutoSubscribe,
    #     ConversationItemAddedEvent,
    #     UserInputTranscribedEvent
    # )
    # from livekit.agents.voice.chat_cli import ChatCLI
    # from livekit.plugins import google, deepgram, openai, groq, silero, assemblyai
    # from src.agents.bella.shared import UserData
    # from src.agents.bella.todo_agent import TodoAgent
    # from src.agents.bella.calendar_agent import CalendarAgent
    # # from src.agents.bella.meeting_query_agent import MeetingQueryAgent
    # from google.genai.types import Modality, RealtimeInputConfig, StartSensitivity, EndSensitivity, AutomaticActivityDetection
    # from livekit.agents.voice.room_io import RoomIO, RoomInputOptions, RoomOutputOptions
    # from src.agents.bella.utils import is_english, translate, transliterate_text
    # import copy
    # from livekit.plugins.google.beta.realtime.realtime_api import RealtimeSession, RealtimeModel
    # import time
    # from livekit.agents.llm.chat_context import ChatContext
    # from utils.logger import Logger
    # from src.agents.bella.db_client import DBToDoClient
    # import asyncio
    # from google.genai.types import ThinkingConfig, HttpOptions, AutomaticFunctionCallingConfig
    # from livekit.agents.voice.events import CloseReason

    # import asyncio
    # import copy
    # import json
    # from typing import TypeVar, Generic
    # from livekit import rtc
    # from livekit.agents import llm, stt, tts, utils, vad
    # from livekit.agents.cli import cli
    # from livekit.agents.job import get_job_context
    # from livekit.agents.llm import ChatContext
    # from livekit.agents.log import logger
    # from livekit.agents.telemetry import trace_types, tracer
    # from livekit.agents.types import NOT_GIVEN
    # from livekit.agents.utils.misc import is_given
    # from livekit.agents.voice import io, room_io
    # from livekit.agents.voice.agent import Agent
    # from livekit.agents.voice.agent_activity import AgentActivity
    # from livekit.agents.voice.events import CloseReason
    # from livekit.agents.voice.run_result import RunResult
    # from opentelemetry import context as otel_context, trace

    # from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
    # from livekit.agents.voice.agent_session import SessionConnectOptions

    # from google.genai import types
    # from livekit.agents import APIConnectionError, APIStatusError, llm, utils
    # from livekit.agents.llm import FunctionTool, RawFunctionTool, ToolChoice, utils as llm_utils
    # from livekit.agents.llm.tool_context import (
    #     get_function_info,
    #     get_raw_function_info,
    #     is_function_tool,
    #     is_raw_function_tool,
    # )
    # from livekit.agents.types import (
    #     DEFAULT_API_CONNECT_OPTIONS,
    #     NOT_GIVEN,
    #     APIConnectOptions,
    #     NotGivenOr,
    # )
    # from livekit.agents.utils import is_given
    # from google.genai.errors import APIError, ClientError, ServerError
    # from livekit.plugins.google.utils import create_tools_config, to_fnc_ctx, to_response_format
    # from livekit.plugins.google.tools import _LLMTool

    # # Create enhanced connection options for each service
    # enhanced_llm_options = APIConnectOptions(
    #     max_retry=3,           # Increase from 3 to 10
    #     retry_interval=5.0,     # Increase from 2.0 to 5.0
    #     timeout=20.0           # Increase from 10.0 to 300.0 (5 minutes)
    # )

    # enhanced_stt_options = APIConnectOptions(
    #     max_retry=10,            # Increase from 3 to 8
    #     retry_interval=3.0,     # Increase from 2.0 to 3.0
    #     timeout=20.0          # Increase from 10.0 to 180.0 (3 minutes)
    # )

    # enhanced_tts_options = APIConnectOptions(
    #     max_retry=5,            # Increase from 3 to 6
    #     retry_interval=3.0,     # Increase from 2.0 to 4.0
    #     timeout=20.0          # Increase from 10.0 to 120.0 (2 minutes)
    # )

    # # Create custom session connection options
    # custom_conn_options = SessionConnectOptions(
    #     stt_conn_options=enhanced_stt_options,
    #     llm_conn_options=enhanced_llm_options,
    #     tts_conn_options=enhanced_tts_options,
    #     max_unrecoverable_errors=5000  # Increase from 3 to 5
    # )

    # logging = Logger(__name__)

    # from dotenv import load_dotenv
    # import os

    # # load values into os.environ first
    # load_dotenv()

    # alt_open_api_key = os.environ.get("OPENAI_API_KEY_2")
    # M2M_TOKEN = os.getenv("M2M_TOKEN")

    # from indic_transliteration import sanscript
    # from indic_transliteration.sanscript import transliterate

    # # hindi_text = "नमस्ते"
    # # transliterated_text = transliterate(hindi_text, sanscript.DEVANAGARI, sanscript.ITRANS)
    # # print("TRANSLITERATION")
    # # print(transliterated_text)  # Output: namaste

    # # grab all keys once
    # DG_KEYS = {
    #     "1": os.getenv("DEEPGRAM_API_KEY"),
    #     "2": os.getenv("DEEPGRAM_API_KEY_2"),
    #     "3": os.getenv("DEEPGRAM_API_KEY_3"),
    # }

    # async def entrypoint(
    #     ctx: JobContext,
    #     host_name: str,
    #     user_id: str,
    #     modality: str,
    #     reconnect: bool,
    #     local_time_profile: dict | None = None,
    #     environment: str | None = None,
    # ):
    #     await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    #     print("🎙️ Agent connected to LiveKit room!")
    #     print(f"📋 Room Name: {ctx.room.name}")
    #     print(f"👤 Local Participant SID: {ctx.room.local_participant.sid}")
    #     print(f"👤 Local Participant Identity: {ctx.room.local_participant.identity}")
    #     print(f"👥 Total Participants: {ctx.room.num_participants}")
    #     print(f"👥 Creation Time: {ctx.room.creation_time}")
    #     print(f"👥 Is Recording: {ctx.room.is_recording}")
    #     print(f"👥 Empty Timeout: {ctx.room.empty_timeout}")
    #     print(f"👥 is connected: {ctx.room.isconnected()}")
    #     print(f"👥 metadata: {ctx.room.metadata}")
    #     print(f"👥 remote participants: {ctx.room.num_publishers}")
    #     print(f"👥 Connection State: {ctx.room.connection_state}")

    #     print(f"Bella is running in {environment}")

    #     print("🎵 Available tracks:")
    #     for participant in ctx.room.remote_participants.values():
    #         print(f"👥 Participant: {participant.identity} | {participant.sid}")

    #     if environment == "STAGING":
    #         db_config = {
    #             "user": os.getenv("COWORKING_USER_STAGING"),
    #             "password": os.getenv("COWORKING_PASSWORD_STAGING"),
    #             "database": os.getenv("COWORKING_DB_STAGING"),
    #             "host": os.getenv("COWORKING_HOST_STAGING"),
    #         }
    #         QLU_M2M_CALENDAR_API_URL = os.getenv("QLU_M2M_CALENDAR_API_URL_STAGING")
    #         print("⚙️ Using STAGING database configuration")
    #     else:
    #         db_config = {
    #             "user": os.getenv("COWORKING_USER"),
    #             "password": os.getenv("COWORKING_PASSWORD"),
    #             "database": os.getenv("COWORKING_DB"),
    #             "host": os.getenv("COWORKING_HOST"),
    #         }
    #         QLU_M2M_CALENDAR_API_URL = os.getenv("QLU_M2M_CALENDAR_API_URL")
    #         print("⚙️ Using PRODUCTION database configuration")

    #     db_client = None

    #     userdata = UserData()

    #     if reconnect:
    #         userdata.reconnect = True

    #     userdata.modality = modality
    #     userdata.host_name = host_name

    #     print(f"modality set to {userdata.modality}")

    #     todo_agent = TodoAgent(room=ctx.room, ctx=ctx, host_name=host_name, user_id=user_id)
    #     calendar_agent = CalendarAgent(room=ctx.room, ctx=ctx, host_name=host_name, user_id=user_id, environment=environment)

    #     from livekit.agents.llm.utils import build_strict_openai_schema

    #     def debug_tool_schemas(agent_obj):
    #         print(f"\n🧩 Checking tools for: {agent_obj.__class__.__name__}")
    #         for name, fn in agent_obj.__class__.__dict__.items():
    #             if hasattr(fn, "__livekit_tool__"):
    #                 try:
    #                     build_strict_openai_schema(fn)
    #                     print(f"✅ Schema OK: {name}")
    #                 except Exception as e:
    #                     print(f"❌ Schema FAIL in {name}: {e}")

    #     debug_tool_schemas(todo_agent)
    #     debug_tool_schemas(calendar_agent)
    #     # meeting_query_agent = MeetingQueryAgent(room=ctx.room)

    #     userdata.personas.update({
    #         "todo": todo_agent,
    #         "calendar": calendar_agent,
    #         # "meeting_query": meeting_query_agent
    #     })

    #     http_config_options = HttpOptions(
    #     timeout=360000  # timeout in milliseconds (30 seconds)
    #     )

    #     session = BellaAgentSession[UserData](
    #         # stt=assemblyai.STT(),
    #         stt=deepgram.STT(language="en", interim_results=True),
    #         # llm=google.LLM(model="gemini-2.5-flash", temperature=0.1, http_options=http_config_options),
    #         llm=openai.LLM(api_key=alt_open_api_key, model="gpt-4.1", temperature=0.1, parallel_tool_calls=False),
    #         tts=openai.TTS(
    #             api_key=alt_open_api_key,
    #             model="gpt-4o-mini-tts",
    #             voice="shimmer",
    #             instructions = "Read the text naturally as normal speech. Do NOT pronounce any formatting symbols such as asterisks (*), hashes (#), backslashes (\\), or newline indicators (\\n). Do NOT pronounce ellipses (...). Pronounce abbreviations like 'A.M.' or 'P.M.' as 'AM' or 'PM'. Read the actual content only, ignoring all formatting markers."
    #         ),
    #         userdata=userdata,
    #         allow_interruptions=True,
    #         preemptive_generation=False,
    #         turn_detection="vad",
    #         min_endpointing_delay=2.25,
    #         user_away_timeout=3000,
    #         conn_options=custom_conn_options,
    #         use_tts_aligned_transcript=False
    #         # vad=silero.VAD.load(min_speech_duration=0.4, min_silence_duration=1.75)
    #     )

    #     print(f"The MODEL BEING USED Is {session.llm.model}")

    #     # Monkey patch the OpenAI LLMStream class after session is created
    #     from livekit.plugins.openai.llm import LLMStream as OpenAILLMStream
    #     original_parse_choice = OpenAILLMStream._parse_choice

    #     def patched_parse_choice(self, id: str, choice, thinking):
    #         # Check if this is a tool call
    #         if choice.delta and choice.delta.tool_calls:
    #             for tool in choice.delta.tool_calls:
    #                 if tool.function and tool.function.name:
    #                     # Set tool_invoked in session userdata
    #                     session.userdata.tool_invoked = tool.function.name
    #                     print(f"🔧 Tool detected: {tool.function.name}")
    #                     # if tool.function.name not in ("handle_todo_request", "handle_calendar_request"):
    #                     #     # Array of filler words (1-2 words each)
    #                     #     filler_words = [
    #                     #         "One moment",
    #                     #         "Just a sec",
    #                     #         "Hang on",
    #                     #         "Almost done",
    #                     #         "Just a moment",
    #                     #     ]

    #                     #     # Randomly pick one filler word
    #                     #     import random
    #                     #     selected_filler = random.choice(filler_words)
    #                     #     session.say(selected_filler, add_to_chat_ctx=False)

    #                     from datetime import datetime
    #                     print(f"🕒 Timestamp of Tool Call Detection: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    #                     break  # Only need to detect once per tool call

    #         return original_parse_choice(self, id, choice, thinking)

    #     # Apply the patch
    #     OpenAILLMStream._parse_choice = patched_parse_choice

    #     # Fallback function
    #     async def trigger_fallback_message():
    #         """Trigger fallback when empty response is detected"""
    #         try:
    #             print("🔄 Empty response detected - triggering fallback")
    #             await session.say("Please have patience, I'm processing your request.")
    #         except Exception as e:
    #             print(f"❌ Error in fallback: {e}")

    #     @session.on("user_input_transcribed")
    #     def on_user_input_transcribed(event: UserInputTranscribedEvent):
    #         print(f"User input transcribed: {event.transcript}, final: {event.is_final}, speaker id: {event.speaker_id}")

    #     @session.on("function_tools_executed")
    #     def on_function_tool_execution_completed(event):
    #         print(f"clearning {session.userdata.tool_invoked}")
    #         session.userdata.tool_invoked = ''

    #     @session.on("conversation_item_added")
    #     def on_conversation_item_added(event: ConversationItemAddedEvent):
    #         current_agent = userdata.current_agent

    #         if isinstance(current_agent, CalendarAgent):
    #             print("🗓️ Current agent is CalendarAgent")
    #             new_prompt = build_calendar_system_prompt1(modality=session.userdata.modality)
    #         elif isinstance(current_agent, TodoAgent):
    #             new_prompt = build_todo_system_prompt1(modality=session.userdata.modality)
    #             print("✅ Current agent is TodoAgent")

    #         # Update the first system message content
    #         if session._agent._chat_ctx.items:
    #             first_item = session._agent._chat_ctx.items[0]
    #             if first_item.role == "system":
    #                 first_item.content = [new_prompt]

    #         # Only perform the key switch if the item is from the assistant
    #         if hasattr(event.item, "role") and event.item.role == "assistant":
    #             # Process cached transcript and clear tool_invoked after assistant speaks
    #             session.userdata.tool_invoked = ''
    #             session._process_cached_transcript()

    #             # shift logic
    #             if session.userdata.key_name == "1":
    #                 session.userdata.key_name = "2"
    #             elif session.userdata.key_name == "2":
    #                 session.userdata.key_name = "3"
    #             else:
    #                 session.userdata.key_name = "2"

    #             next_api_key = DG_KEYS[session.userdata.key_name]
    #             os.environ["DEEPGRAM_API_KEY"] = next_api_key

    #         # Count tokens only from items with content attribute
    #         total_chars = 0
    #         for item in session._agent.chat_ctx.items:
    #             if hasattr(item, 'content') and item.content:
    #                 total_chars += len(str(item.content))

    #         estimated_tokens = total_chars // 4
    #         print(f"📊 Context: {len(session._agent.chat_ctx.items)} messages, ~{estimated_tokens} tokens")

    #         if estimated_tokens > 6000:  # Trim if over 5000 tokens
    #             print(f"🧹 Trimming context - too many tokens ({estimated_tokens})")

    #             # Start with last 6, then reduce if still over 5k
    #             keep_start = session._chat_ctx.items[:2]  # Keep first 2 (system + 1 more)

    #             for last_count in [10, 9, 8]:  # Try 6, then 5, then 4 (minimum)
    #                 keep_end = session._chat_ctx.items[-last_count:]
    #                 trimmed = keep_start + keep_end

    #                 # Check if this combination is under 5k tokens
    #                 trimmed_chars = 0
    #                 for item in trimmed:
    #                     if hasattr(item, 'content') and item.content:
    #                         trimmed_chars += len(str(item.content))

    #                 trimmed_tokens = trimmed_chars // 4

    #                 if trimmed_tokens <= 6000:
    #                     session._chat_ctx = ChatContext(trimmed)
    #                     print(f"✅ Context trimmed to {len(trimmed)} messages, ~{trimmed_tokens} tokens")
    #                     break
    #             else:
    #                 # If even last 4 is over 5k, just keep the first 2
    #                 session._chat_ctx = ChatContext(keep_start)
    #                 print(f"⚠️ Even last 4 messages are over 5k, keeping only first {len(keep_start)} messages")

    #     @session.on("agent_state_changed")
    #     def state_changed(event):
    #         if event.new_state == "listening":
    #             print(f"\nBella is listening 👂")
    #         elif event.new_state == "speaking":
    #             print(f"\nBella is speaking 💬")
    #         elif event.new_state == "thinking":
    #             print(f"\nBella is thinking 🧠")
    #         else:
    #             print(f"Bella has gone crazy and {event.new_state} 🐱")

    #     @session.on("close")
    #     def close_agent(event):
    #         print("CLOSING The AGENT")

    #     @ctx.room.local_participant.register_rpc_method("interrupt_response")
    #     async def interrupt_response(data):
    #         """FORCIBLY interrupt the current agent response/speech generation.

    #         This RPC will ALWAYS stop the agent, regardless of any settings.
    #         """
    #         print(f"🛑 FORCE Interrupt RPC called by: {data.caller_identity}")

    #         try:
    #             # Check if session is running
    #             if session._activity is None:
    #                 print("❌ Cannot interrupt: AgentSession is not running")
    #                 return  # just exit silently, no value returned

    #             # FORCE interrupt - this will ALWAYS work
    #             interrupt_future = session.interrupt(force=True)  # 🚨 FORCE=True

    #             # Wait for the interruption to complete
    #             await interrupt_future

    #             print("✅ Agent response FORCIBLY interrupted")

    #         except Exception as e:
    #             print(f"❌ Error interrupting agent response: {e}")

    #     @ctx.room.local_participant.register_rpc_method("change_modality_rpc")
    #     async def change_modality_rpc(data):
    #         print(f"RPC called by: {data.caller_identity}")

    #         try:
    #             payload = data.payload

    #             # Ensure payload is a dict
    #             if isinstance(payload, str):
    #                 try:
    #                     payload = json.loads(payload)
    #                 except json.JSONDecodeError:
    #                     logging.warn(f"❌ Failed to decode payload: {payload}")
    #                     return

    #             # Extract modality from payload (must happen before it's used)
    #             modality = payload.get("modality") if isinstance(payload, dict) else None

    #             if modality is None:
    #                 logging.warn("⚠️ No modality provided in payload")
    #                 return

    #             # Now safe to use modality
    #             current_agent = userdata.current_agent

    #             if isinstance(current_agent, CalendarAgent):
    #                 new_prompt = build_calendar_system_prompt1(modality=modality.lower())
    #                 print("🗓️ Current agent is CalendarAgent")
    #             elif isinstance(current_agent, TodoAgent):
    #                 new_prompt = build_todo_system_prompt1(modality=modality.lower())
    #                 print("✅ Current agent is TodoAgent")

    #             # Handle modality switching
    #             if modality == "TEXT":
    #                 userdata.modality = "TEXT"
    #                 session.input.set_audio_enabled(False)
    #                 session.output.set_audio_enabled(False)
    #                 print("🔇 Switched to TEXT mode (audio disabled).")

    #             elif modality == "AUDIO":
    #                 userdata.modality = "AUDIO"
    #                 session.input.set_audio_enabled(True)
    #                 session.output.set_audio_enabled(True)
    #                 print("🎙️ Switched to AUDIO mode (audio enabled).")

    #             else:
    #                 logging.warn(f"⚠️ Invalid modality received: {payload}")
    #                 return

    #             # Update system prompt if present
    #             if session._agent._chat_ctx.items:
    #                 first_item = session._agent._chat_ctx.items[0]
    #                 if first_item.role == "system":
    #                     print("📝 Updating system prompt...")
    #                     first_item.content = [new_prompt]

    #         except Exception as err:
    #             print(f"RPC FAILURE CAUSED BY {err}")

    #         print(f"✅ Userdata modality set to: {userdata.modality}")

    #     def safe_get_payload(data) -> dict:
    #         """Extract and JSON-decode payload if needed, always return a dict."""
    #         payload = data.payload
    #         if isinstance(payload, str):
    #             try:
    #                 payload = json.loads(payload)
    #             except json.JSONDecodeError:
    #                 logging.warning(f"❌ Failed to decode payload: {payload}")
    #                 return {}
    #         return payload if isinstance(payload, dict) else {}

    #     @ctx.room.local_participant.register_rpc_method("add_todo_manual")
    #     async def add_todo_manual(data):
    #         payload = safe_get_payload(data)
    #         if not payload:
    #             return
    #         print(f"[DEBUG] add_todo_manual payload: {payload}")
    #         await session.userdata.todo_client.add_task(payload)

    #     @ctx.room.local_participant.register_rpc_method("update_todo_manual")
    #     async def update_todo_manual(data):
    #         payload = safe_get_payload(data)
    #         if not payload:
    #             return
    #         print(f"[DEBUG] update_todo_manual payload: {payload}")
    #         await session.userdata.todo_client.update_task(payload)

    #     @ctx.room.local_participant.register_rpc_method("complete_todo_manual")
    #     async def complete_todo_manual(data):
    #         payload = safe_get_payload(data)
    #         if not payload:
    #             return
    #         print(f"[DEBUG] complete_todo_manual payload: {payload}")

    #         task_id = payload.get("id")
    #         completed = payload.get("completed", False)
    #         completed_at = payload.get("completed_at")
    #         updated_at = payload.get("updated_at")

    #         await session.userdata.todo_client.complete_task(
    #             task_id=task_id,
    #             completed=completed,
    #             completed_at=completed_at,
    #             updated_at=updated_at,
    #         )

    #     @ctx.room.local_participant.register_rpc_method("delete_todo_manual")
    #     async def delete_todo_manual(data):
    #         payload = safe_get_payload(data)
    #         if not payload:
    #             return
    #         print(f"[DEBUG] delete_todo_manual payload: {payload}")

    #         ids = payload.get("ids")
    #         if ids and isinstance(ids, list):
    #             for task_id in ids:
    #                 await session.userdata.todo_client.delete_task(task_id=task_id)
    #         else:
    #             task_id = payload.get("id")
    #             if task_id:
    #                 await session.userdata.todo_client.delete_task(task_id=task_id)

    #     @ctx.room.local_participant.register_rpc_method("delete_all_todos_manual")
    #     async def delete_all_todos_manual(data):
    #         payload = safe_get_payload(data)
    #         print(f"[DEBUG] delete_all_todos_manual payload: {payload}")
    #         await session.userdata.todo_client.delete_all_todos()

    #     room_io = RoomIO(
    #         agent_session=session,
    #         room=ctx.room,
    #         participant=host_name,
    #         input_options=RoomInputOptions(
    #             audio_enabled=True,
    #             close_on_disconnect=False,  # Don't shut down when user leaves
    #             pre_connect_audio_timeout=10.0,  # Wait longer for audio
    #             # transcription_enabled=True,
    #             # text_enabled=True
    #         ),
    #         output_options=RoomOutputOptions(
    #             audio_enabled=True,
    #             sync_transcription=False
    #             # transcription_enabled=True,
    #             # text_enabled=True    @ctx.room.local_participant.register_rpc_method("complete_todo_manual")
    # async def complete_todo_manual(data):
    #     payload = safe_get_payload(data)
    #     if not payload:
    #         return
    #     print(f"[DEBUG] complete_todo_manual payload: {payload}")

    #     task_id = payload.get("id")
    #     completed = payload.get("completed", False)
    #     completed_at = payload.get("completed_at")
    #     updated_at = payload.get("updated_at")

    #     await session.userdata.todo_client.complete_task(
    #         task_id=task_id,
    #         completed=completed,
    #         completed_at=completed_at,
    #         updated_at=updated_at,
    #     )


#         )
#     )

#     await room_io.start()
#     await session.start(agent=todo_agent, room=ctx.room)

#     try:
#         db_client = DBToDoClient(db_config, user_id)
#         raw_tasks_from_db, org_participants, calendar_read_token = await asyncio.gather(
#             db_client.fetch_all_tasks(),
#             db_client.fetch_qlu_users(),
#             asyncio.to_thread(
#                 get_access_token,
#                 api_url=QLU_M2M_CALENDAR_API_URL,
#                 user_id=user_id,
#                 token=M2M_TOKEN
#             )
#         )
#     except Exception as err:
#         print("Error fetching data:", err)


#     timezone = "Asia/Karachi"

#     tasks_from_db = convert_utc_to_local(raw_tasks_from_db, timezone)


#     todo_agent.initialize_tasks(tasks_from_db)

#     userdata.org_participants = org_participants

#     session.userdata.access_token = calendar_read_token

#     # Set up transcript caching for tool execution
#     session._setup_tool_transcript_caching()

#     await ctx.wait_for_participant()
#     print(f"🥵 participant host_name:{host_name}, user_id:{user_id} joined!")

# from livekit.agents import AgentSession, llm, ConversationItemAddedEvent, stt, tts

# from typing import TypeVar, Generic, cast
# from livekit.agents import AgentSession, llm, ConversationItemAddedEvent
# from src.agents.bella.shared import UserData

# # Type variable for generic inheritance
# Userdata_T = TypeVar("Userdata_T")

# # Monkey patch AgentActivity.on_final_transcript to add transliteration
# _original_on_final_transcript = AgentActivity.on_final_transcript

# def _patched_on_final_transcript(self, ev: stt.SpeechEvent) -> None:
#     """Monkey patched version that transliterates transcripts before processing."""
#     if isinstance(self.llm, llm.RealtimeModel) and self.llm.capabilities.user_transcription:
#         # skip stt transcription if user_transcription is enabled on the realtime model
#         return

#     # Get original text and language
#     original_text = ev.alternatives[0].text
#     raw_language = ev.alternatives[0].language or "en"

#     # Normalize language code (e.g., 'en-US' -> 'en', 'hi-IN' -> 'hi')
#     language_code = raw_language.split('-')[0].lower() if raw_language else "en"

#     # Transliterate the text
#     transliterated_text = transliterate_text(original_text, language_code)

#     print(f"🌐 Original text: '{original_text}' (lang: {language_code})")
#     print(f"🔤 Transliterated text: '{transliterated_text}'")

#     # Create the event with transliterated text
#     self._session._user_input_transcribed(
#         UserInputTranscribedEvent(
#             language=language_code,
#             transcript=transliterated_text,
#             is_final=True,
#             speaker_id=ev.alternatives[0].speaker_id,
#         ),
#     )

#     self._interrupt_paused_speech_task = asyncio.create_task(
#         self._interrupt_paused_speech(old_task=self._interrupt_paused_speech_task)
#     )

# # Apply the monkey patch
# AgentActivity.on_final_transcript = _patched_on_final_transcript


# class BellaAgentSession(AgentSession[Userdata_T], Generic[Userdata_T]):

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         # Cache for transcripts during tool execution
#         self._cached_transcript = ""
#         self._cached_transcript_confidence = []
#         self._original_on_stt_event = None

#     def _on_error(
#         self,
#         error: llm.LLMError | stt.STTError | tts.TTSError | llm.RealtimeModelError,
#     ) -> None:
#         print(f"🚨 ERROR DETECTED: {error.type} - {error}")
#         print(f"🔍 Error recoverable: {error.recoverable}")
#         print(f"🔍 Already closing: {self._closing_task is not None}")

#         if self._closing_task or error.recoverable:
#             print(f"⏭️ Skipping error - already closing or recoverable")
#             return

#         if error.type == "llm_error":
#             self._llm_error_counts += 1
#             print(f"🤖 LLM Error Count: {self._llm_error_counts}/{self.conn_options.max_unrecoverable_errors}")
#             if self._llm_error_counts <= self.conn_options.max_unrecoverable_errors:
#                 print(f"✅ LLM Error tolerated - continuing session")
#                 return
#             else:
#                 print(f"❌ LLM Error threshold exceeded")
#         elif error.type == "tts_error":
#             self._tts_error_counts += 1
#             print(f"🔊 TTS Error Count: {self._tts_error_counts}/{self.conn_options.max_unrecoverable_errors}")
#             if self._tts_error_counts <= self.conn_options.max_unrecoverable_errors:
#                 print(f"✅ TTS Error tolerated - continuing session")
#                 return
#             else:
#                 print(f"❌ TTS Error threshold exceeded")
#         elif error.type == "stt_error":
#             # Give STT errors the same tolerance as LLM/TTS
#             self._llm_error_counts += 1  # Use existing counter
#             print(f"🎤 STT Error Count: {self._llm_error_counts}/{self.conn_options.max_unrecoverable_errors}")
#             if self._llm_error_counts <= self.conn_options.max_unrecoverable_errors:
#                 print(f"✅ STT Error tolerated - continuing session")
#                 return
#             else:
#                 print(f"❌ STT Error threshold exceeded")
#         elif error.type == "realtime_model_error":
#             # Give RealtimeModel errors the same tolerance
#             self._llm_error_counts += 1  # Use existing counter
#             print(f"⚡ RealtimeModel Error Count: {self._llm_error_counts}/{self.conn_options.max_unrecoverable_errors}")
#             if self._llm_error_counts <= self.conn_options.max_unrecoverable_errors:
#                 print(f"✅ RealtimeModel Error tolerated - continuing session")
#                 return
#             else:
#                 print(f"❌ RealtimeModel Error threshold exceeded")

#         print(f"💀 SHUTTING DOWN SESSION due to unrecoverable error")
#         print("AgentSession is closing due to unrecoverable error", exc_info=error.error)

#         def on_close_done(_: asyncio.Task[None]) -> None:
#             self._closing_task = None

#         self._closing_task = asyncio.create_task(
#             self._aclose_impl(error=error, reason=CloseReason.ERROR)
#         )
#         self._closing_task.add_done_callback(on_close_done)

#     def _setup_tool_transcript_caching(self):
#         """Setup monkey patching for transcript caching during tool execution"""
#         if self._activity and self._activity._audio_recognition:
#             # Store original method
#             if not self._original_on_stt_event:
#                 self._original_on_stt_event = self._activity._audio_recognition._on_stt_event

#             # Create patched version - WITH CACHING
#             async def patched_on_stt_event(ev):
#                 from livekit.agents import stt

#                 # Check if tool is being executed
#                 if hasattr(self.userdata, 'tool_invoked') and self.userdata.tool_invoked:
#                     print(f"🔧 Tool '{self.userdata.tool_invoked}' is executing - CACHING TRANSCRIPT")

#                     # Cache the transcript
#                     if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
#                         transcript = ev.alternatives[0].text
#                         confidence = ev.alternatives[0].confidence

#                         if transcript:
#                             self._cached_transcript += f" {transcript}"
#                             self._cached_transcript = self._cached_transcript.lstrip()
#                             self._cached_transcript_confidence.append(confidence)
#                             print(f"📝 Cached: '{self._cached_transcript}'")

#                     # Just return - don't process
#                     return

#                 # If no tool is executing, call original method
#                 return await self._original_on_stt_event(ev)

#             # Apply the patch
#             self._activity._audio_recognition._on_stt_event = patched_on_stt_event

#     def _process_cached_transcript(self):
#         """Process the cached transcript after tool execution completes"""
#         if self._cached_transcript and self._activity and self._activity._audio_recognition:
#             print(f"🔄 Processing cached transcript: '{self._cached_transcript}'")

#             # Simply set the transcript in the audio recognition and trigger processing
#             self._activity._audio_recognition._audio_transcript = self._cached_transcript
#             self._activity._audio_recognition._final_transcript_confidence = self._cached_transcript_confidence.copy()

#             # Trigger the end of turn detection manually
#             from livekit.agents.llm import ChatContext
#             chat_ctx = self._activity._agent.chat_ctx.copy()
#             self._activity._audio_recognition._run_eou_detection(chat_ctx)

#             # Clear cache
#             self._cached_transcript = ""
#             self._cached_transcript_confidence = []
#         else:
#             print(f"🔄 No cached transcript to process")


# def build_todo_system_prompt1(modality: str = "voice", tz: str = "Asia/Karachi") -> str:
#     """
#     Build the todo system prompt dynamically with modality and local time.

#     Args:
#         modality (str): Conversation modality (e.g., 'voice', 'text').
#         tz (str): Timezone string (default: 'Asia/Karachi').

#     Returns:
#         str: The fully formatted system prompt.
#     """
#     tz="Asia/Karachi"
#     # include timezone offset explicitly to prevent double conversions
#     local_time = datetime.now(pytz.timezone(tz)).isoformat()
#     return TODO_SYSTEM_PROMPT.format(
#         modality=modality.upper(),
#         timestamp=local_time,
#         tz=tz
#     )


# def build_calendar_system_prompt1(modality: str = "voice", tz: str = "Asia/Karachi") -> str:
#     """
#     Build the calendar system prompt dynamically with modality and local time.

#     Args:
#         modality (str): Conversation modality (e.g., 'voice', 'text').
#         tz (str): Timezone string (default: 'Asia/Karachi').

#     Returns:
#         str: The fully formatted system prompt.
#     """
#     tz="Asia/Karachi"
#     # include timezone offset explicitly to prevent double conversions
#     local_time = datetime.now(pytz.timezone(tz)).isoformat()
#     return CALENDAR_SYSTEM_PROMPT.format(
#         modality=modality.upper(),
#         timestamp=local_time,
#         tz=tz
#     )

# import requests

# def get_access_token(api_url: str, user_id: str, token: str) -> str | None:
#     """
#     Fetch the calendar access token for this user ID.
#     Returns None if request fails.
#     """

#     try:
#         payload = {"userIds": [user_id]}
#         headers = {
#             "Authorization": f"Bearer {token}",
#             "Content-Type": "application/json"
#         }

#         response = requests.post(api_url, json=payload, headers=headers)
#         response.raise_for_status()
#         data = response.json()
#         return data["data"].get(user_id)
#     except Exception as err:
#         print(f"⚠️ Failed to fetch calendar access token: {err}")
#         return None

# from datetime import datetime
# import zoneinfo

# def convert_utc_to_local(tasks, timezone_str: str):
#     """
#     Convert UTC datetime fields in tasks to the given timezone.
#     """
#     local_tz = zoneinfo.ZoneInfo(timezone_str)
#     datetime_fields = ["created_at", "updated_at", "deadline", "completed_at"]

#     for task in tasks:
#         for field in datetime_fields:
#             value = task.get(field)
#             if isinstance(value, datetime) and value.tzinfo is not None:
#                 task[field] = value.astimezone(local_tz)
#     return tasks

# if __name__ == "__main__":
#     cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


# VERSION 02 with audio monitoring gemini stt

# import os

# from src.agents.bella.prompts import TODO_SYSTEM_PROMPT, CALENDAR_SYSTEM_PROMPT
# import sys
# from pathlib import Path
# import dotenv
# import json
# src_path = Path(__file__).parent.parent.parent
# sys.path.insert(0, str(src_path))
# from datetime import datetime
# import pytz
# from livekit import agents
# from livekit.agents import (
#     AgentSession,
#     JobContext,
#     WorkerOptions,
#     cli,
#     AutoSubscribe,
#     ConversationItemAddedEvent,
#     UserInputTranscribedEvent
# )
# from livekit.plugins import google, deepgram, openai, groq, silero, assemblyai
# from src.agents.bella.shared import UserData
# from src.agents.bella.todo_agent import TodoAgent
# from src.agents.bella.calendar_agent import CalendarAgent
# # from src.agents.bella.meeting_query_agent import MeetingQueryAgent
# from google.genai.types import Modality, RealtimeInputConfig, StartSensitivity, EndSensitivity, AutomaticActivityDetection
# from livekit.agents.voice.room_io import RoomIO, RoomInputOptions, RoomOutputOptions
# from src.agents.bella.utils import is_english, translate
# import copy
# from livekit.plugins.google.beta.realtime.realtime_api import RealtimeSession, RealtimeModel
# import time
# from livekit.agents.llm.chat_context import ChatContext
# from utils.logger import Logger
# from src.agents.bella.db_client import DBToDoClient

# from google.genai.types import ThinkingConfig, HttpOptions, AutomaticFunctionCallingConfig

# logging = Logger(__name__)

# from dotenv import load_dotenv
# import os

# import random

# filler_words_list = [
#     "uh one sec",
#     "gimme a sec",
#     "hold up",
#     "just a sec",
#     "hang on",
#     "wait up",
#     "lemme check",
#     "sec please",
#     "hold on",
#     "wait a sec",
#     "hang tight",
#     "just wait",
#     "one moment",
#     "real quick",
#     "wait a bit",
# ]

# # load values into os.environ first
# load_dotenv()

# alt_open_api_key = os.environ.get("OPENAI_API_KEY_2")
# M2M_TOKEN = os.getenv("M2M_TOKEN")
# # grab all keys once
# DG_KEYS = {
#     "1": os.getenv("DEEPGRAM_API_KEY"),
#     "2": os.getenv("DEEPGRAM_API_KEY_2"),
#     "3": os.getenv("DEEPGRAM_API_KEY_3"),
# }

# async def entrypoint(ctx: JobContext, host_name: str, user_id: str, modality: str, local_time_profile):
#     await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

#     print("🎙️ Agent connected to LiveKit room!")
#     print(f"📋 Room Name: {ctx.room.name}")
#     print(f"👤 Local Participant SID: {ctx.room.local_participant.sid}")
#     print(f"👤 Local Participant Identity: {ctx.room.local_participant.identity}")
#     print(f"👥 Total Participants: {ctx.room.num_participants}")
#     print(f"👥 Creation Time: {ctx.room.creation_time}")
#     print(f"👥 Is Recording: {ctx.room.is_recording}")
#     print(f"👥 Empty Timeout: {ctx.room.empty_timeout}")
#     print(f"👥 is connected: {ctx.room.isconnected()}")
#     print(f"👥 metadata: {ctx.room.metadata}")
#     print(f"👥 remote participants: {ctx.room.num_publishers}")
#     print(f"👥 Connection State: {ctx.room.connection_state}")
#     print("🎵 Available tracks:")
#     for participant in ctx.room.remote_participants.values():
#         print(f"👥 Participant: {participant.identity} | {participant.sid}")

#     db_config = {
#         "user": os.getenv("COWORKING_USER"),
#         "password": os.getenv("COWORKING_PASSWORD"),
#         "database": os.getenv("COWORKING_DB"),
#         "host": os.getenv("COWORKING_HOST"),
#     }

#     try:
#         db_client = DBToDoClient(db_config, user_id)
#         tasks_from_db = await db_client.fetch_all_tasks()
#         org_participants = await db_client.fetch_qlu_users()
#     except Exception as err:
#         print(err)

#     userdata = UserData()
#     userdata.modality = modality
#     userdata.org_participants = org_participants

#     print(f"modality set to {userdata.modality}")

# try:
#     calendar_read_token = get_access_token(
#         api_url=QLU_M2M_CALENDAR_API_URL,
#         user_id=user_id,
#         token=M2M_TOKEN
#     )
# except Exception as err:
#     print(err)


#     todo_agent = TodoAgent(room=ctx.room, ctx=ctx, host_name=host_name, user_id=user_id, tasks_list=tasks_from_db)
#     calendar_agent = CalendarAgent(room=ctx.room, ctx=ctx, host_name=host_name, user_id=user_id, access_token=calendar_read_token)
#     # meeting_query_agent = MeetingQueryAgent(room=ctx.room)

#     userdata.personas.update({
#         "todo": todo_agent,
#         "calendar": calendar_agent,
#         # "meeting_query": meeting_query_agent
#     })

#     # --- Your existing session initialization ---
#     # session = AgentSession[UserData](
#     #     stt=openai.STT(api_key=alt_open_api_key, language="en", noise_reduction_type="near_field", model="gpt-4o-transcribe"),
#     #     llm=google.LLM(model="gemini-2.5-flash", temperature=0.1),
#     #     tts=openai.TTS(
#     #         api_key=alt_open_api_key,
#     #         model="gpt-4o-mini-tts",
#     #         voice="shimmer",
#     #         instructions="Don’t spell out any special character or punctuation marks",
#     #     ),
#     #     userdata=userdata,
#     #     allow_interruptions=True
#     # )


#     # session = agents.AgentSession[UserData](
#     #     # stt=agents.stt.StreamAdapter(   # ✅ pass as keyword-only args
#     #     #     stt=openai.STT(
#     #     #         api_key=alt_open_api_key,
#     #     #         language="en",
#     #     #         model="gpt-4o-transcribe",
#     #     #     ),
#     #     #     vad=silero.VAD.load(
#     #     #         min_speech_duration=1,
#     #     #         min_silence_duration=1,
#     #     #     ),
#     #     # ),
#     #     stt=assemblyai.STT(),
#     #     llm=google.LLM(model="gemini-2.5-flash", temperature=0.1),
#     #     tts=openai.TTS(
#     #         api_key=alt_open_api_key,
#     #         model="gpt-4o-mini-tts",
#     #         voice="shimmer",
#     #         instructions="Don’t spell out any special character or punctuation marks",
#     #     ),
#     #     userdata=userdata,
#     #     allow_interruptions=True,

#     # )

#     thinking_config_options = ThinkingConfig(
#         include_thoughts=False,   # Let the model output its reasoning
#         thinking_budget=500      # Token budget for "thinking". -1=automatic, 0=disabled
#     )

#     automatic_function_calling_config_options = AutomaticFunctionCallingConfig(
#         disable=True
#     )

#     http_config_options = HttpOptions(
#     timeout=180000  # timeout in milliseconds (30 seconds)
#     )

#     # Import the new Gemini STT adapter
#     from src.agents.bella.gemini_stt_custom_adapter import GeminiSTT, GeminiSTTConfig

#     # Create Gemini STT configuration
#     gemini_stt_config = GeminiSTTConfig(
#         model="gemini-2.0-flash-live-001",
#         language="en-US",
#         interim_results=False,
#         api_key=os.environ.get("GOOGLE_API_KEY"),
#         vertexai=False
#     )

#     session = BellaAgentSession[UserData](
#         # Use Gemini STT instead of Deepgram
#         stt=GeminiSTT(config=gemini_stt_config),
#         # Add VAD for proper turn detection
#         vad=silero.VAD.load(
#             min_speech_duration=0.25,  # Minimum speech duration
#             min_silence_duration=0.25,
#         ),
#         llm=google.LLM(model="gemini-2.0-flash-001", temperature=0.1, http_options=http_config_options, automatic_function_calling_config=automatic_function_calling_config_options),
#         tts=openai.TTS(
#             api_key=alt_open_api_key,
#             model="gpt-4o-mini-tts",
#             voice="shimmer",
#             instructions = "Read the text naturally as normal speech. Do NOT pronounce any formatting symbols such as asterisks (*), hashes (#), backslashes (\\), or newline indicators (\\n). Do NOT pronounce ellipses (...). Pronounce abbreviations like 'A.M.' or 'P.M.' as 'AM' or 'PM'. Read the actual content only, ignoring all formatting markers."
#         ),
#         userdata=userdata,
#         allow_interruptions=True,
#         # Optimized parameters for better responsiveness with custom STT
#         min_endpointing_delay=0.15,      # Faster response to speech end
#         max_endpointing_delay=1.0,      # Don't wait too long
#         min_consecutive_speech_delay=0.1,  # Allow quick follow-ups
#         turn_detection="stt",
#         preemptive_generation=False,
#         resume_false_interruption=False
#     )

#     session.userdata.access_token = calendar_read_token

#     # Monkey patch the LLMStream class after session is created
#     from livekit.plugins.google.llm import LLMStream as GoogleLLMStream
#     original_parse_part = GoogleLLMStream._parse_part

#     def patched_parse_part(self, id: str, part):
#         if part.function_call:
#             # Set tool_invoked in session userdata
#             session.userdata.tool_invoked = part.function_call.name
#             print(f"🔧 Tool detected: {part.function_call.name}")
#             from datetime import datetime
#             print(f"🕒 Timestamp of Tool Call Detection: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
#         return original_parse_part(self, id, part)

#     # Apply the patch
#     GoogleLLMStream._parse_part = patched_parse_part

#     # @session.on("user_input_transcribed")
#     # def on_user_input_transcribed(event: UserInputTranscribedEvent):
#     #     print(session._chat_ctx.to_dict())
#     #     print(f"User input transcribed: {event.transcript}, final: {event.is_final}, speaker id: {event.speaker_id}")

#     @session.on("function_tools_executed")
#     def on_function_tool_execution_completed(event):
#         print(f"clearning {session.userdata.tool_invoked}")
#         session.userdata.tool_invoked = ''

#     @session.on("conversation_item_added")
#     def on_conversation_item_added(event: ConversationItemAddedEvent):


#         current_agent = userdata.current_agent

#         if isinstance(current_agent, CalendarAgent):
#             print("🗓️ Current agent is CalendarAgent")
#             new_prompt = build_calendar_system_prompt1(modality=session.userdata.modality)
#         elif isinstance(current_agent, TodoAgent):
#             new_prompt = build_todo_system_prompt1(modality=session.userdata.modality)
#             print("✅ Current agent is TodoAgent")

#         # Update the first system message content
#         if session._agent._chat_ctx.items:
#             first_item = session._agent._chat_ctx.items[0]
#             if first_item.role == "system":
#                 first_item.content = [new_prompt]

#         # Only perform the key switch if the item is from the assistant
#         if hasattr(event.item, "role") and event.item.role == "assistant":
#             # Process cached transcript and clear tool_invoked after assistant speaks
#             session.userdata.tool_invoked = ''
#             session._process_cached_transcript()

#             # shift logic
#             if session.userdata.key_name == "1":
#                 session.userdata.key_name = "2"
#             elif session.userdata.key_name == "2":
#                 session.userdata.key_name = "3"
#             else:
#                 session.userdata.key_name = "2"

#             next_api_key = DG_KEYS[session.userdata.key_name]

#             os.environ["DEEPGRAM_API_KEY"] = next_api_key

#             # session._tts = deepgram.TTS()

#         # if hasattr(event.item, "role"):
#         #     print(
#         #         f"CONVO - {event.item.role.capitalize()}: {event.item.text_content}"
#         #     )

#         MAX_CTX = 20

#         if len(session._chat_ctx.items) > MAX_CTX:
#             keep_start = session._chat_ctx.items[:3]
#             keep_end   = session._chat_ctx.items[-9:]
#             trimmed    = keep_start + keep_end

#             session._chat_ctx = ChatContext(trimmed)

#     @session.on("agent_state_changed")
#     def state_changed(event):
#         if event.new_state == "listening":
#             print(f"\nBella is listening 👂")
#         elif event.new_state == "speaking":
#             print(f"\nBella is speaking 💬")
#         elif event.new_state == "thinking":
#             print(f"\nBella is thinking 🧠")
#         else:
#             print(f"Bella has gone crazy and {event.new_state} 🐱")

#     @ctx.room.local_participant.register_rpc_method("interrupt_response")
#     async def interrupt_response(data):
#         """FORCIBLY interrupt the current agent response/speech generation.

#         This RPC will ALWAYS stop the agent, regardless of any settings.
#         """
#         print(f"🛑 FORCE Interrupt RPC called by: {data.caller_identity}")

#         try:
#             # Check if session is running
#             if session._activity is None:
#                 print("❌ Cannot interrupt: AgentSession is not running")
#                 return  # just exit silently, no value returned

#             # FORCE interrupt - this will ALWAYS work
#             interrupt_future = session.interrupt(force=True)  # 🚨 FORCE=True

#             # Wait for the interruption to complete
#             await interrupt_future

#             print("✅ Agent response FORCIBLY interrupted")

#         except Exception as e:
#             print(f"❌ Error interrupting agent response: {e}")


#     @ctx.room.local_participant.register_rpc_method("change_modality_rpc")
#     async def change_modality_rpc(data):
#         print(f"RPC called by: {data.caller_identity}")

#         try:
#             payload = data.payload

#             # Ensure payload is a dict
#             if isinstance(payload, str):
#                 try:
#                     payload = json.loads(payload)
#                 except json.JSONDecodeError:
#                     logging.warn(f"❌ Failed to decode payload: {payload}")
#                     return

#             # Extract modality from payload (must happen before it's used)
#             modality = payload.get("modality") if isinstance(payload, dict) else None

#             if modality is None:
#                 logging.warn("⚠️ No modality provided in payload")
#                 return

#             # Now safe to use modality
#             current_agent = userdata.current_agent

#             if isinstance(current_agent, CalendarAgent):
#                 new_prompt = build_calendar_system_prompt1(modality=modality.lower())
#                 print("🗓️ Current agent is CalendarAgent")
#             elif isinstance(current_agent, TodoAgent):
#                 new_prompt = build_todo_system_prompt1(modality=modality.lower())
#                 print("✅ Current agent is TodoAgent")

#             # Handle modality switching
#             if modality == "TEXT":
#                 userdata.modality = "TEXT"
#                 session.input.set_audio_enabled(False)
#                 session.output.set_audio_enabled(False)
#                 print("🔇 Switched to TEXT mode (audio disabled).")

#             elif modality == "AUDIO":
#                 userdata.modality = "AUDIO"
#                 session.input.set_audio_enabled(True)
#                 session.output.set_audio_enabled(True)
#                 print("🎙️ Switched to AUDIO mode (audio enabled).")

#             else:
#                 logging.warn(f"⚠️ Invalid modality received: {payload}")
#                 return

#             # Update system prompt if present
#             if session._agent._chat_ctx.items:
#                 first_item = session._agent._chat_ctx.items[0]
#                 if first_item.role == "system":
#                     print("📝 Updating system prompt...")
#                     first_item.content = [new_prompt]

#         except Exception as err:
#             print(f"RPC FAILURE CAUSED BY {err}")

#         print(f"✅ Userdata modality set to: {userdata.modality}")


#     def safe_get_payload(data) -> dict:
#         """Extract and JSON-decode payload if needed, always return a dict."""
#         payload = data.payload
#         if isinstance(payload, str):
#             try:
#                 payload = json.loads(payload)
#             except json.JSONDecodeError:
#                 logging.warning(f"❌ Failed to decode payload: {payload}")
#                 return {}
#         return payload if isinstance(payload, dict) else {}


#     @ctx.room.local_participant.register_rpc_method("add_todo_manual")
#     async def add_todo_manual(data):
#         payload = safe_get_payload(data)
#         if not payload:
#             return
#         print(f"[DEBUG] add_todo_manual payload: {payload}")
#         await session.userdata.todo_client.add_task(payload)


#     @ctx.room.local_participant.register_rpc_method("update_todo_manual")
#     async def update_todo_manual(data):
#         payload = safe_get_payload(data)
#         if not payload:
#             return
#         print(f"[DEBUG] update_todo_manual payload: {payload}")
#         await session.userdata.todo_client.update_task(payload)


#     @ctx.room.local_participant.register_rpc_method("complete_todo_manual")
#     async def complete_todo_manual(data):
#         payload = safe_get_payload(data)
#         if not payload:
#             return
#         print(f"[DEBUG] complete_todo_manual payload: {payload}")

#         task_id = payload.get("id")
#         completed = payload.get("completed", False)
#         completed_at = payload.get("completed_at")
#         updated_at = payload.get("updated_at")

#         await session.userdata.todo_client.complete_task(
#             task_id=task_id,
#             completed=completed,
#             completed_at=completed_at,
#             updated_at=updated_at,
#         )


#     @ctx.room.local_participant.register_rpc_method("delete_todo_manual")
#     async def delete_todo_manual(data):
#         payload = safe_get_payload(data)
#         if not payload:
#             return
#         print(f"[DEBUG] delete_todo_manual payload: {payload}")

#         ids = payload.get("ids")
#         if ids and isinstance(ids, list):
#             for task_id in ids:
#                 await session.userdata.todo_client.delete_task(task_id=task_id)
#         else:
#             task_id = payload.get("id")
#             if task_id:
#                 await session.userdata.todo_client.delete_task(task_id=task_id)


#     @ctx.room.local_participant.register_rpc_method("delete_all_todos_manual")
#     async def delete_all_todos_manual(data):
#         payload = safe_get_payload(data)
#         print(f"[DEBUG] delete_all_todos_manual payload: {payload}")
#         await session.userdata.todo_client.delete_all_todos()


#     room_io = RoomIO(
#         agent_session=session,
#         room=ctx.room,
#         participant=host_name,
#         input_options=RoomInputOptions(
#             audio_enabled=True,
#             # transcription_enabled=True,
#             # text_enabled=True
#         ),
#         output_options=RoomOutputOptions(
#             audio_enabled=True,
#             # transcription_enabled=True,
#             # text_enabled=True
#         )
#     )

#     await room_io.start()
#     await session.start(agent=todo_agent, room=ctx.room)

#     # Set up transcript caching for tool execution
#     session._setup_tool_transcript_caching()

#     await ctx.wait_for_participant()
#     print(f"🥵 participant host_name:{host_name}, user_id:{user_id} joined!")

# from livekit.agents import AgentSession, llm, ConversationItemAddedEvent

# from typing import TypeVar, Generic
# from livekit.agents import AgentSession, llm, ConversationItemAddedEvent
# from src.agents.bella.shared import UserData

# # Type variable for generic inheritance
# Userdata_T = TypeVar("Userdata_T")

# class BellaAgentSession(AgentSession[Userdata_T], Generic[Userdata_T]):

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         # Cache for transcripts during tool execution
#         self._cached_transcript = ""
#         self._cached_transcript_confidence = []
#         self._original_on_stt_event = None

#     def _setup_tool_transcript_caching(self):
#         """Setup monkey patching for transcript caching during tool execution"""
#         if self._activity and self._activity._audio_recognition:
#             # Store original method
#             if not self._original_on_stt_event:
#                 self._original_on_stt_event = self._activity._audio_recognition._on_stt_event

#             # Create patched version - WITH CACHING
#             async def patched_on_stt_event(ev):
#                 from livekit.agents import stt

#                 # Check if tool is being executed
#                 if hasattr(self.userdata, 'tool_invoked') and self.userdata.tool_invoked:
#                     print(f"🔧 Tool '{self.userdata.tool_invoked}' is executing - CACHING TRANSCRIPT")

#                     # Cache the transcript
#                     if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
#                         transcript = ev.alternatives[0].text
#                         confidence = ev.alternatives[0].confidence

#                         if transcript:
#                             self._cached_transcript += f" {transcript}"
#                             self._cached_transcript = self._cached_transcript.lstrip()
#                             self._cached_transcript_confidence.append(confidence)
#                             print(f"📝 Cached: '{self._cached_transcript}'")

#                     # Just return - don't process
#                     return

#                 # If no tool is executing, call original method
#                 return await self._original_on_stt_event(ev)

#             # Apply the patch
#             self._activity._audio_recognition._on_stt_event = patched_on_stt_event

#     def _process_cached_transcript(self):
#         """Process the cached transcript after tool execution completes"""
#         if self._cached_transcript and self._activity and self._activity._audio_recognition:
#             print(f"🔄 Processing cached transcript: '{self._cached_transcript}'")

#             # Simply set the transcript in the audio recognition and trigger processing
#             self._activity._audio_recognition._audio_transcript = self._cached_transcript
#             self._activity._audio_recognition._final_transcript_confidence = self._cached_transcript_confidence.copy()

#             # Trigger the end of turn detection manually
#             from livekit.agents.llm import ChatContext
#             chat_ctx = self._activity._agent.chat_ctx.copy()
#             self._activity._audio_recognition._run_eou_detection(chat_ctx)

#             # Clear cache
#             self._cached_transcript = ""
#             self._cached_transcript_confidence = []
#         else:
#             print(f"🔄 No cached transcript to process")

# def build_todo_system_prompt1(modality: str = "voice", tz: str = "Asia/Karachi") -> str:
#     """
#     Build the TODO system prompt dynamically with modality and local time.

#     Args:
#         modality (str): Conversation modality (e.g., 'voice', 'text').
#         tz (str): Timezone string (default: 'Asia/Karachi').

#     Returns:
#         str: The fully formatted system prompt.
#     """
#     local_time = datetime.now(pytz.timezone(tz)).strftime('%Y-%m-%d %H:%M:%S')
#     return TODO_SYSTEM_PROMPT.format(
#         modality=modality,
#         timestamp=local_time
#     )

# def build_calendar_system_prompt1(modality: str = "voice", tz: str = "Asia/Karachi") -> str:
#     """
#     Build the calendar system prompt dynamically with modality and local time.

#     Args:
#         modality (str): Conversation modality (e.g., 'voice', 'text').
#         tz (str): Timezone string (default: 'Asia/Karachi').

#     Returns:
#         str: The fully formatted system prompt.
#     """
#     local_time = datetime.now(pytz.timezone(tz)).strftime('%Y-%m-%d %H:%M:%S')
#     return CALENDAR_SYSTEM_PROMPT.format(
#         modality=modality,
#         timestamp=local_time
#     )

# import requests

# def get_access_token(api_url: str, user_id: str, token: str) -> str | None:
#     """
#     Fetch the calendar access token for this user ID.
#     Returns None if request fails.
#     """

#     try:
#         payload = {"userIds": [user_id]}
#         headers = {
#             "Authorization": f"Bearer {token}",
#             "Content-Type": "application/json"
#         }

#         response = requests.post(api_url, json=payload, headers=headers)
#         response.raise_for_status()
#         data = response.json()
#         return data["data"].get(user_id)
#     except Exception as err:
#         print(f"⚠️ Failed to fetch calendar access token: {err}")
#         return None

# if __name__ == "__main__":
#     cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
