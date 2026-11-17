# import logging
# import os

# from livekit.agents import Agent, StopResponse, llm, stt
# from livekit.plugins import openai, silero, assemblyai, google

# from .config import Config
# from .models import Transcript
# from .summary import SummaryService
# from src.utils.exceptions import TranscriberError

# logger = logging.getLogger(__name__)

# # In-memory cache for transcripts (key: (room_id, session_id), value: list of Transcript)
# _transcript_cache: dict[tuple[str, str], list[Transcript]] = {}


# class TranscriberAgent(Agent):
#     """Main transcriber agent"""

#     def __init__(
#         self,
#         config: Config,
#         summary_service: SummaryService,
#         participant_identity: str,
#         room_id: str,
#         session_id: str,
#     ):
#         # # OpenAI Whisper (gpt-4o-transcribe) doesn't support streaming
#         # # Use VAD + StreamAdapter to buffer audio until speech ends
#         # whisper_stt = openai.STT(model="gpt-4o-transcribe")
#         # vad = silero.VAD.load(
#         #     min_speech_duration=0.1,
#         #     min_silence_duration=0.5,
#         # )
#         # # StreamAdapter buffers audio until VAD detects end of speech
#         # stt_adapter = stt.StreamAdapter(stt=whisper_stt, vad=vad)

#         # Build Google STT credentials from environment variables
#         # Map environment variable names to JSON keys
#         env_var_mapping = {
#             "GOOGLE_TYPE": "type",
#             "GOOGLE_PROJECT_ID": "project_id",
#             "GOOGLE_PRIVATE_KEY_ID": "private_key_id",
#             "GOOGLE_PRIVATE_KEY": "private_key",
#             "GOOGLE_CLIENT_EMAIL": "client_email",
#             "GOOGLE_CLIENT_ID": "client_id",
#             "GOOGLE_AUTH_URI": "auth_uri",
#             "GOOGLE_TOKEN_URI": "token_uri",
#             "GOOGLE_AUTH_PROVIDER_X509_CERT_URL": "auth_provider_x509_cert_url",
#             "GOOGLE_CLIENT_X509_CERT_URL": "client_x509_cert_url",
#         }

#         # Build credentials dict from environment variables
#         credentials_info = {}
#         for env_var, json_key in env_var_mapping.items():
#             value = os.getenv(env_var)
#             if not value:
#                 raise ValueError(f"Missing required environment variable: {env_var}")
#             credentials_info[json_key] = value

#         # Add optional universe_domain if present
#         universe_domain = os.getenv("GOOGLE_UNIVERSE_DOMAIN")
#         if universe_domain:
#             credentials_info["universe_domain"] = universe_domain

#         # Configure Google STT with multilingual support
#         # All parameters are explicitly set for multilingual support
#         stt_instance = google.STT(
#             credentials_info=credentials_info,
#             languages=["hi-IN", "en-US"],  # English and Hindi - MULTILINGUAL
#             detect_language=True,  # Automatically detect which language is being spoken - REQUIRED for multilingual
#             model="latest_long",  # Model that supports multilingual
#             interim_results=True,  # Return interim results
#             punctuate=False,  # Enable punctuation
#             enable_word_time_offsets=True,  # Enable word time offsets
#             location="global",  # Use global location for multilingual support
#         )

#         logger.info(
#             f"Google STT configured - Model: {stt_instance.model}, Provider: {stt_instance.provider}"
#         )

#         super().__init__(instructions="Real-time transcription agent", stt=stt_instance)

#         self.config = config
#         self.summary_service = summary_service
#         self.participant_identity = participant_identity
#         self.room_id = room_id
#         self.session_id = session_id

#     async def on_user_turn_completed(
#         self, chat_ctx: llm.ChatContext, new_message: llm.ChatMessage
#     ):
#         """Handle completed user turn

#         Note: Transcript storage is handled by the transliteration patch in transliteration.py
#         which stores the transliterated version. We don't store here to avoid duplicates.
#         """
#         try:
#             user_transcript = new_message.text_content
#             logger.info(f"{self.participant_identity} -> {user_transcript}")

#             # Transcript storage is handled by the transliteration patch
#             # which stores the transliterated version in the cache.
#             # No need to store here to avoid duplicates.

#         except Exception as e:
#             logger.error(f"❌ Error processing transcript: {e}")
#             raise TranscriberError(f"Failed to process transcript: {e}")
#         finally:
#             raise StopResponse()

# V2

import logging
import os

from livekit.agents import Agent, StopResponse, llm, stt
from livekit.plugins import openai, silero
from .config import Config
from .models import Transcript
from .summary import SummaryService
from src.utils.exceptions import TranscriberError

logger = logging.getLogger(__name__)

# In-memory cache for transcripts (key: (room_id, session_id), value: list of Transcript)
_transcript_cache: dict[tuple[str, str], list[Transcript]] = {}


class TranscriberAgent(Agent):
    """Main transcriber agent"""

    def __init__(
        self,
        config: Config,
        summary_service: SummaryService,
        participant_identity: str,
        room_id: str,
        session_id: str,
    ):
        # Get OpenAI API key from environment
        from src.utils.openai_env import get_openai_api_key_v2

        openai_api_key = get_openai_api_key_v2()
        if not openai_api_key:
            raise ValueError("Missing required environment variable: OPENAI_API_KEY_2")

        # OpenAI STT doesn't support streaming, so we need to wrap it in StreamAdapter
        # Create OpenAI STT instance
        whisper_stt = openai.STT(
            api_key=openai_api_key,
            model="gpt-4o-transcribe",  # OpenAI's latest transcription model
            language="en",  # Primary language (can detect others)
        )

        # Use Silero VAD for speech detection
        # Note: Silero VAD downloads model weights on first use (one-time, then cached)
        vad_instance = silero.VAD.load(
            min_speech_duration=0.1,  # Minimum speech duration to consider
            min_silence_duration=0.5,  # 0.5 seconds of silence = end of speech
        )

        # Wrap OpenAI STT with StreamAdapter using Silero VAD
        # StreamAdapter buffers audio until VAD detects end of speech, then sends to OpenAI
        stt_instance = stt.StreamAdapter(stt=whisper_stt, vad=vad_instance)

        # Log configuration
        logger.info(
            "✅ OpenAI STT configured - Model: gpt-4o-transcribe, Provider: OpenAI, VAD: silero (0.5s silence)"
        )

        # Pass OpenAI STT instance to agent
        # Configure faster endpointing for quicker transcription
        super().__init__(
            instructions="Real-time transcription agent",
            stt=stt_instance,
            min_endpointing_delay=1.0,  # Reduced from default (~5s) to 1 second for faster transcription
            turn_detection="stt",  # Use STT-based turn detection for more responsive endpointing
        )

        self.config = config
        self.summary_service = summary_service
        self.participant_identity = participant_identity
        self.room_id = room_id
        self.session_id = session_id

    async def on_user_turn_completed(
        self, chat_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ):
        """Handle completed user turn

        Note: Transcript storage is handled by the transliteration patch in transliteration.py
        which stores the transliterated version. We don't store here to avoid duplicates.
        """
        try:
            user_transcript = new_message.text_content
            logger.info(f"{self.participant_identity} -> {user_transcript}")

            # Transcript storage is handled by the transliteration patch
            # which stores the transliterated version in the cache.
            # No need to store here to avoid duplicates.

        except Exception as e:
            logger.error(f"❌ Error processing transcript: {e}")
            raise TranscriberError(f"Failed to process transcript: {e}")
        finally:
            raise StopResponse()
