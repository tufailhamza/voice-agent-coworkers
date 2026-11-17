from livekit import rtc
import time
import asyncio
import json
import re
from livekit.agents import (
    AgentSession,
    Agent,
    JobContext,
    WorkerOptions,
    cli,
    AutoSubscribe,
    function_tool,
    RunContext,
    AgentSession,
    Agent,
    JobContext,
    WorkerOptions,
    cli,
    AutoSubscribe,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
    AgentSession,
    Agent,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
    metrics,
    MetricsCollectedEvent,
)
from src.agents.ryan.networking.socket import RyanClient
from livekit.plugins import google, deepgram
from google.genai import types
from google.genai.types import (
    Modality,
    RealtimeInputConfig,
    StartSensitivity,
    EndSensitivity,
    AutomaticActivityDetection,
)
from livekit.agents import llm, stt, tts, utils, vad
from livekit.rtc import AudioStream
from agents.ryan.networking.socket import RyanClient
from src.agents.ryan.core.prompts import RYAN_SYSTEM_PROMPT
from google.genai.types import (
    Modality,
    RealtimeInputConfig,
    StartSensitivity,
    EndSensitivity,
    AutomaticActivityDetection,
)
from livekit.plugins import google, deepgram, openai, groq
from src.agents.ryan.core.models import RyanAgentSession
from src.agents.ryan.core.tools import RyanAgent
from livekit.agents.voice.room_io import RoomIO, RoomInputOptions, RoomOutputOptions
from livekit import rtc
from livekit.rtc import AudioSource, TrackPublishOptions
from src.utils.logger import Logger
from livekit.agents.metrics import RealtimeModelMetrics, TTSMetrics
from src.agents.ryan.core.utils import translate, is_english
from datetime import datetime
from livekit.agents import AgentSession, llm, ConversationItemAddedEvent
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.voice.agent_session import SessionConnectOptions
from src.agents.ryan.core.session_storage import UserData

logging = Logger(__name__)

from dotenv import load_dotenv
import os

load_dotenv()
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

alt_open_api_key = os.environ.get("RYAN_OPENAI_API_KEY")

# DG_KEYS = {
#     "1": os.getenv("DEEPGRAM_API_KEY"),
#     "2": os.getenv("DEEPGRAM_API_KEY_2"),
#     "3": os.getenv("DEEPGRAM_API_KEY_3"),
# }

# key_name = "1"


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


class TranslatingRoomIO(RoomIO):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_transcript_buffer: str = ""

    async def _forward_user_transcript(self) -> None:
        async for ev in self._user_transcript_ch:
            if self._user_tr_output is None:
                continue

            if ev.transcript:
                self._user_transcript_buffer = ev.transcript

            if not ev.is_final:
                continue

            full_text = self._user_transcript_buffer
            self._user_transcript_buffer = ""

            try:
                if full_text and not is_english(full_text):
                    full_text = translate(full_text)
            except Exception as e:
                logging.warning(
                    f"translation error, publishing original transcript: {e}"
                )

            await self._user_tr_output.capture_text(full_text)
            self._user_tr_output.flush()


async def entrypoint(
    ctx: JobContext, host_name: str, user_id: str, modality: str, cdp_url: str
):
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

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
    logging.info("🎵 Available tracks:")
    for participant in ctx.room.remote_participants.values():
        logging.info(f"👥 Participant: {participant.identity} | {participant.sid}")

    agent_metrics = metrics.UsageCollector()
    t_ttft = 0.0
    t_ttfb = 0.0
    turns = 0

    session = RyanAgentSession[UserData](
        stt=deepgram.STT(language="en-US", interim_results=True),
        llm=openai.LLM(model="gpt-4.1", temperature=0.1, api_key=alt_open_api_key),
        tts=openai.TTS(
            api_key=alt_open_api_key,
            model="gpt-4o-mini-tts",
            voice="onyx",
            instructions="Speak in a playful, expressive and friendly tone. Read the text naturally as normal speech. Do NOT pronounce any formatting symbols including: asterisks (*), hashes (#), backslashes (\\), newline indicators (\\n), or ellipses (...). When you encounter three dots (...) or ellipses, simply pause briefly instead of saying 'dot dot dot'. Do NOT say 'dot dot dot' or 'ellipses'. Pronounce abbreviations like 'A.M.' or 'P.M.' as 'AM' or 'PM'. Read only the actual spoken content, completely ignoring all formatting markers and symbols.",
        ),
        allow_interruptions=True,
        preemptive_generation=False,
        turn_detection="vad",
        min_endpointing_delay=2.25,
        user_away_timeout=3000,
        conn_options=custom_conn_options,
        userdata=UserData(),
        use_tts_aligned_transcript=False,
    )

    async def on_toggle_input(data: rtc.RpcInvocationData) -> None:
        logging.info(f"RPC called by: {data.caller_identity}")

        payload = data.payload

        # Ensure payload is a dict
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                logging.error(f"❌ Failed to decode payload: {payload}")
                return

        modality = payload.get("modality") if isinstance(payload, dict) else None

        if modality == "TEXT":
            session.input.set_audio_enabled(False)
            session.output.set_audio_enabled(False)

            logging.info("🔇 Switched to TEXT mode (audio disabled).")

        elif modality == "AUDIO":
            session.input.set_audio_enabled(True)
            session.output.set_audio_enabled(True)

            logging.info("🎙️ Switched to AUDIO mode (audio enabled).")

        else:
            logging.info(f"⚠️ Invalid modality received: {payload}")
            return

    ctx.room.local_participant.register_rpc_method("change_modality_rpc")(
        on_toggle_input
    )

    @ctx.room.local_participant.register_rpc_method("operator_interrupt")
    async def interrupt_response(data):
        """FORCIBLY interrupt the current agent response/speech generation.

        This RPC will ALWAYS stop the agent, regardless of any settings.
        """
        try:
            logging.info("RYAN INTERRUPT RPC called")
            # Check if session is running
            if session._activity is None:
                print("RYAN IS NOT RUNNING")
                return  # just exit silently, no value returned

            # FORCE interrupt - this will ALWAYS work
            interrupt_future = session.interrupt(force=True)  # 🚨 FORCE=True
            # FORCE interrupt - this will ALWAYS work

            await RyanClient.emit(
                "operator.execute/cancel_agent", {"query": "cancel the search"}
            )
            # Wait for the interruption to complete
            await interrupt_future

            logging.info("RYAN RESPONSE FORCIBLY interrupted")

        except Exception as e:
            print(f"Error interrupting RYAN response: {e}")

    agent = RyanAgent(
        instructions=RYAN_SYSTEM_PROMPT,
        ctx=ctx,
        host_name=host_name,
        user_id=user_id,
        socket_client=RyanClient(),
        cdp_url=cdp_url,
    )

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        nonlocal turns  # belongs to entrypoint()
        global key_name  # belongs to module level

        timestamp = datetime.now().strftime("%H:%M:%S")
        role = event.item.role
        text_content = event.item.text_content

        if role == "user":
            logging.info(f"\n[{timestamp}] 👤 User: {text_content}")
        elif role == "assistant":
            logging.info(f"[{timestamp}] 🤖 Ryan: {text_content}")
        elif role == "system":
            logging.info(f"[{timestamp}] ⚙️ System: {text_content}")

        turns += 0.5

        # if role == "assistant":
        #     # shift logic
        #     if key_name == "1":
        #         key_name = "2"
        #     elif key_name == "2":
        #         key_name = "3"
        #     else:
        #         key_name = "2"

        #     next_api_key = DG_KEYS[key_name]

        #     os.environ["DEEPGRAM_API_KEY"] = next_api_key
        #     logging.info(f"🔄 Deepgram API key: {key_name}")
        #     session._tts = deepgram.TTS(model="aura-2-orpheus-en")

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        timestamp = datetime.now().strftime("%H:%M:%S")
        logging.info(f"[{timestamp}] 🎤 Transcribed: {event.transcript}")

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        nonlocal t_ttft, t_ttfb, turns
        agent_metrics.collect(ev.metrics)

        if isinstance(ev.metrics, RealtimeModelMetrics):
            t_ttft += float(ev.metrics.duration)
        elif isinstance(ev.metrics, TTSMetrics):
            t_ttfb += float(ev.metrics.ttfb)

    async def log_usage():
        summary = agent_metrics.get_summary()
        logging.info(f"Usage: {summary}")
        logging.info(f"avg vtvl: {(t_ttft + t_ttfb) / turns}")

    rpc_data = rtc.RpcInvocationData(
        request_id="initial_setup",
        caller_identity=host_name,
        payload={"modality": "AUDIO"} if modality == "AUDIO" else {"modality": "TEXT"},
        response_timeout=5.0,
    )

    room_io = TranslatingRoomIO(
        agent_session=session,
        room=ctx.room,
        participant=host_name,
        input_options=RoomInputOptions(
            audio_enabled=True,
        ),
        output_options=RoomOutputOptions(audio_enabled=True, sync_transcription=False),
    )

    await room_io.start()
    await session.start(agent=agent, room=ctx.room)
    await ctx.wait_for_participant()
    logging.info(f"🥵 participant host_name:{host_name}, user_id:{user_id} joined!")
    await on_toggle_input(rpc_data)

    # Add comprehensive cleanup callback
    async def cleanup():
        logging.info("🧹 Starting Ryan agent cleanup...")
        try:
            # 1. Cleanup agent (includes socket, database, search history)
            if hasattr(agent, "on_exit"):
                await agent.on_exit()
                logging.info("✅ Agent cleanup completed")

            # 2. Cleanup session
            if hasattr(session, "aclose"):
                await session.aclose()
                logging.info("✅ Session cleanup completed")

            # 3. Cleanup room_io
            if hasattr(room_io, "aclose"):
                await room_io.aclose()
                logging.info("✅ RoomIO cleanup completed")

            # 4. Log usage statistics
            await log_usage()

            logging.info("✅ Ryan agent cleanup completed")

        except Exception as e:
            logging.error(f"❌ Error in Ryan agent cleanup: {e}")

    ctx.add_shutdown_callback(cleanup)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
