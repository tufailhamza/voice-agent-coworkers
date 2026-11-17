# import asyncio
# import logging
# import re
# import types
# from typing import Callable, Dict, List, Optional
# from openai import OpenAI
# from livekit.agents import llm
# from livekit.agents.voice import events
# import os

# logger = logging.getLogger(__name__)
# client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY_2"))

# # Global transcript cache (last 3 from all users)
# _global_transcript_cache: List[str] = []

# # User-specific transcript cache (last 3 per user)
# _user_transcript_cache: Dict[str, List[str]] = {}


# def transliterate_with_llm(
#     original_text: str,
#     global_transcripts: Optional[List[str]] = None,
#     user_transcripts: Optional[List[str]] = None,
# ) -> str:
#     """
#     Uses GPT-4o-mini to transliterate non-English text to English alphabet.
#     Only transliterates non-English parts, keeps English parts unchanged.

#     Args:
#         original_text: The original text which may contain non-English content
#         global_transcripts: Last 3 transcripts from all users (for context)
#         user_transcripts: Last 3 transcripts from this specific user (for context)
#     """
#     # Build context section
#     context_section = ""

#     if global_transcripts and len(global_transcripts) > 0:
#         context_section += "\n\nContext - Last 3 transcripts from all participants:\n"
#         for i, transcript in enumerate(global_transcripts, 1):
#             context_section += f"  {i}. {transcript}\n"

#     if user_transcripts and len(user_transcripts) > 0:
#         context_section += "\nContext - Last 3 transcripts from this user:\n"
#         for i, transcript in enumerate(user_transcripts, 1):
#             context_section += f"  {i}. {transcript}\n"

#     prompt = f"""You are a transliteration assistant that converts non-English text to English alphabet (Roman script).

# Input text (may contain English, non-English, or a mix of both):
# {original_text}
# {context_section}

# Instructions:
# - If the text is 100% English alphabet words, return it as-is without any changes.
# - If the text contains non-English characters (e.g., Hindi Devanagari, Arabic, etc.), transliterate ONLY the non-English parts to English alphabet.
# - Keep all English words exactly as they are - do NOT modify English text.
# - For mixed text: transliterate non-English parts, keep English parts unchanged.
# - Do NOT translate the meaning - only convert the script (non-English to Roman alphabet).
# - Keep names, places, brands, and technical terms as they appear (if in English, keep as-is; if non-English, transliterate).
# - Use the context from previous transcripts to better understand the conversation flow and improve transliteration accuracy.
# - Output ONLY the final transliterated text in Latin (English) alphabet without any explanation, formatting, or additional text.
# - Maintain the same word order and structure as the input.
# - If the text is already 100% English, return it unchanged.

# Examples:
# - Pure English: "I have headache" -> "I have headache" (unchanged)
# - Pure Hindi: "मुझे सिरदर्द है" -> "mujhe sirdard hai"
# - Mixed: "मुझे headache है" -> "mujhe headache hai"
# - Mixed: "I need paracetamol पैरासिटामोल" -> "I need paracetamol paracetamol"
# """

#     try:
#         response = client.chat.completions.create(
#             model="gpt-4o-mini",
#             messages=[{"role": "user", "content": prompt}],
#             temperature=0,
#         )
#         return response.choices[0].message.content.strip()
#     except Exception as e:
#         logger.error(f"Error in LLM transliteration: {e}")
#         raise


# def is_pure_english(text: str) -> bool:
#     """Check if text is 100% English alphabet (including spaces, punctuation, numbers)"""
#     return bool(re.match(r'^[a-zA-Z0-9\s.,!?;:\-\'"]+$', text))


# def patch_agent_activity_on_final_transcript(
#     activity,
#     participant_identity: str,
#     room_id: str,
#     session_id: str,
#     transcripts_store: List,  # Reference to the transcript cache
#     should_store_transcript: Callable[
#         [], bool
#     ] = lambda: True,  # Function to check if storage is allowed
# ):
#     """
#     Monkey patch the on_final_transcript method of AgentActivity
#     to transliterate non-English text to Roman alphabet.

#     Args:
#         activity: The AgentActivity instance to patch
#         participant_identity: The identity of the participant
#         room_id: Room ID for transcript storage
#         session_id: Session ID for transcript storage
#         transcripts_store: Reference to transcript cache list
#         should_store_transcript: Callable that returns True if transcripts should be stored
#     """
#     # Store the original method
#     original_on_final_transcript = activity.on_final_transcript

#     def patched_on_final_transcript(self, ev):
#         """Patched version that transliterates non-English to Roman alphabet"""
#         # Early return check (same as original)
#         if (
#             isinstance(self.llm, llm.RealtimeModel)
#             and self.llm.capabilities.user_transcription
#         ):
#             return

#         # Get the language and text
#         language = ev.alternatives[0].language if ev.alternatives else None
#         original_text = ev.alternatives[0].text if ev.alternatives else ""
#         text_to_emit = original_text

#         # Check if text is 100% English
#         is_english = is_pure_english(original_text)

#         # Only transliterate if text contains non-English characters
#         if not is_english:
#             try:
#                 # Get cached transcripts BEFORE adding current one
#                 global_transcripts = _global_transcript_cache.copy()
#                 user_transcripts = _user_transcript_cache.get(
#                     participant_identity, []
#                 ).copy()

#                 # Use LLM to transliterate
#                 logger.info(
#                     f"🔄 Transliterating text with LLM (user: {participant_identity})"
#                 )
#                 logger.debug(f"Original Text: {original_text}")

#                 transliterated_text = transliterate_with_llm(
#                     original_text,
#                     global_transcripts=global_transcripts,
#                     user_transcripts=user_transcripts,
#                 )

#                 logger.debug(f"LLM Transliteration: {transliterated_text}")
#                 text_to_emit = transliterated_text
#                 logger.info(
#                     f"✅ Transliterated: '{original_text[:50]}...' -> '{transliterated_text[:50]}...'"
#                 )

#                 # Update caches AFTER successful transliteration
#                 _global_transcript_cache.append(original_text)
#                 if len(_global_transcript_cache) > 3:
#                     _global_transcript_cache.pop(0)

#                 if participant_identity not in _user_transcript_cache:
#                     _user_transcript_cache[participant_identity] = []
#                 _user_transcript_cache[participant_identity].append(original_text)
#                 if len(_user_transcript_cache[participant_identity]) > 3:
#                     _user_transcript_cache[participant_identity].pop(0)

#             except Exception as e:
#                 logger.warning(
#                     f"Failed to transliterate text '{original_text}': {e}. Using original text."
#                 )
#                 import traceback

#                 logger.error(traceback.format_exc())
#                 text_to_emit = original_text
#                 # Still update caches even on error
#                 _global_transcript_cache.append(original_text)
#                 if len(_global_transcript_cache) > 3:
#                     _global_transcript_cache.pop(0)

#                 if participant_identity not in _user_transcript_cache:
#                     _user_transcript_cache[participant_identity] = []
#                 _user_transcript_cache[participant_identity].append(original_text)
#                 if len(_user_transcript_cache[participant_identity]) > 3:
#                     _user_transcript_cache[participant_identity].pop(0)
#         else:
#             logger.debug(f"Text is 100% English, using as-is: {original_text[:50]}...")
#             # Still update caches for English text
#             _global_transcript_cache.append(original_text)
#             if len(_global_transcript_cache) > 3:
#                 _global_transcript_cache.pop(0)

#             if participant_identity not in _user_transcript_cache:
#                 _user_transcript_cache[participant_identity] = []
#             _user_transcript_cache[participant_identity].append(original_text)
#             if len(_user_transcript_cache[participant_identity]) > 3:
#                 _user_transcript_cache[participant_identity].pop(0)

#         # Store the transcript in the transcripts_store (only if recording is active)
#         if should_store_transcript():
#             try:
#                 from .models import Transcript
#                 from datetime import datetime

#                 transcript = Transcript(
#                     room_id=room_id,
#                     session_id=session_id,
#                     speaker_name=participant_identity,
#                     participant_sid=participant_identity,
#                     text_content=text_to_emit,  # Store the transliterated/final text
#                     timestamp=datetime.now(),
#                 )
#                 transcripts_store.append(transcript)
#                 cache_key_for_storage = (room_id, session_id)
#                 logger.info(
#                     f"📝 Transcript stored: {participant_identity} -> {text_to_emit[:50]}... (cache_key: {cache_key_for_storage}, store_size: {len(transcripts_store)})"
#                 )
#             except Exception as e:
#                 logger.error(f"❌ Error storing transcript: {e}")
#                 import traceback

#                 logger.error(traceback.format_exc())
#         else:
#             logger.debug(
#                 f"⏸️ Recording stopped - skipping transcript storage: {participant_identity} -> {text_to_emit[:50]}..."
#             )

#         # Call original method with modified text
#         self._session._user_input_transcribed(
#             events.UserInputTranscribedEvent(
#                 language=language,
#                 transcript=text_to_emit,
#                 is_final=True,
#                 speaker_id=(
#                     ev.alternatives[0].speaker_id
#                     if ev.alternatives and hasattr(ev.alternatives[0], "speaker_id")
#                     else None
#                 ),
#             ),
#         )

#         # Also call the interrupt logic from the original method
#         self._interrupt_paused_speech_task = asyncio.create_task(
#             self._interrupt_paused_speech(old_task=self._interrupt_paused_speech_task)
#         )

#     # Bind the patched method to the activity instance
#     activity.on_final_transcript = types.MethodType(
#         patched_on_final_transcript, activity
#     )
#     logger.debug(
#         f"✅ Monkey patched on_final_transcript for transliteration (user: {participant_identity})"
#     )

# V2

import asyncio
import logging
import re
import types
from typing import Callable, Dict, List, Optional
from openai import OpenAI
from livekit.agents import llm
from livekit.agents.voice import events
import os
from livekit.plugins import google

from src.utils.openai_env import get_openai_api_key_v2

logger = logging.getLogger(__name__)
client = OpenAI(api_key=get_openai_api_key_v2())

# Keep below LiveKit job ping_timeout (60s) so the event loop stays responsive.
TRANSLITERATION_TIMEOUT_SECONDS = 40

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_ARABIC_SCRIPT_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)

# Global transcript cache (last 3 from all users)
_global_transcript_cache: List[str] = []

# User-specific transcript cache (last 3 per user)
_user_transcript_cache: Dict[str, List[str]] = {}


def transliterate_with_llm(
    original_text: str,
    global_transcripts: Optional[List[str]] = None,
    user_transcripts: Optional[List[str]] = None,
) -> str:
    """
    Uses GPT-4o-mini to transliterate non-English text to English alphabet.
    Only transliterates non-English parts, keeps English parts unchanged.

    Args:
        original_text: The original text which may contain non-English content
        global_transcripts: Last 3 transcripts from all users (for context)
        user_transcripts: Last 3 transcripts from this specific user (for context)
    """
    # Build context section
    context_section = ""

    if global_transcripts and len(global_transcripts) > 0:
        context_section += "\n\nContext - Last 3 transcripts from all participants:\n"
        for i, transcript in enumerate(global_transcripts, 1):
            context_section += f"  {i}. {transcript}\n"

    if user_transcripts and len(user_transcripts) > 0:
        context_section += "\nContext - Last 3 transcripts from this user:\n"
        for i, transcript in enumerate(user_transcripts, 1):
            context_section += f"  {i}. {transcript}\n"

    prompt = f"""You are a transliteration assistant that converts non-English text to English alphabet (Roman script).

Input text (may contain English, non-English, or a mix of both):
{original_text}
{context_section}

Instructions:
- If the text is 100% English alphabet words, return it as-is without any changes.
- If the text contains non-English characters (e.g., Hindi Devanagari, Arabic, etc.), transliterate ONLY the non-English parts to English alphabet.
- Keep all English words exactly as they are - do NOT modify English text.
- For mixed text: transliterate non-English parts, keep English parts unchanged.
- Do NOT translate the meaning - only convert the script (non-English to Roman alphabet).
- Keep names, places, brands, and technical terms as they appear (if in English, keep as-is; if non-English, transliterate).
- Use the context from previous transcripts to better understand the conversation flow and improve transliteration accuracy.
- Output ONLY the final transliterated text in Latin (English) alphabet without any explanation, formatting, or additional text.
- Maintain the same word order and structure as the input.
- If the text is already 100% English, return it unchanged.

Examples:
- Pure English: "I have headache" -> "I have headache" (unchanged)
- Pure Hindi: "मुझे सिरदर्द है" -> "mujhe sirdard hai"
- Mixed: "मुझे headache है" -> "mujhe headache hai"
- Mixed: "I need paracetamol पैरासिटामोल" -> "I need paracetamol paracetamol"
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=TRANSLITERATION_TIMEOUT_SECONDS,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error in LLM transliteration: {e}")
        raise


def _script_kind(char: str) -> str:
    if _DEVANAGARI_RE.match(char):
        return "devanagari"
    if _ARABIC_SCRIPT_RE.match(char):
        return "arabic"
    return "other"


def _split_script_segments(text: str) -> List[tuple[str, str]]:
    if not text:
        return []

    segments: List[tuple[str, str]] = []
    current_kind = _script_kind(text[0])
    current = text[0]

    for char in text[1:]:
        kind = _script_kind(char)
        if kind == current_kind:
            current += char
        else:
            segments.append((current_kind, current))
            current_kind = kind
            current = char

    segments.append((current_kind, current))
    return segments


def _transliterate_devanagari_segment(text: str) -> str:
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate as indic_transliterate

    return indic_transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)


def _transliterate_arabic_script_segment(
    text: str, language: Optional[str] = None
) -> str:
    from aksharamukha import transliterate as akshara_transliterate

    # Urdu Nastaliq is the common case; Arabic script segments use the Urdu converter.
    source_script = "Urdu"
    if language and language.lower().startswith(("ar", "fa", "ps")):
        source_script = "Arabic"

    for target in ("Itrans", "ISO", "Roman"):
        try:
            result = akshara_transliterate.process(source_script, target, text)
            if result and result.strip():
                return result.strip()
        except Exception:
            continue

    raise ValueError(f"aksharamukha could not romanize {source_script} segment")


def _contains_indic_or_arabic_script(text: str) -> bool:
    return bool(_DEVANAGARI_RE.search(text) or _ARABIC_SCRIPT_RE.search(text))


def transliterate_locally(text: str, language: Optional[str] = None) -> str:
    """
    Rule-based transliteration for Hindi/Devanagari and Urdu/Arabic script segments.
    Latin segments are left unchanged.
    """
    if is_pure_english(text):
        return text

    parts: List[str] = []
    for kind, segment in _split_script_segments(text):
        if kind == "devanagari":
            parts.append(_transliterate_devanagari_segment(segment))
        elif kind == "arabic":
            parts.append(_transliterate_arabic_script_segment(segment, language))
        else:
            parts.append(segment)

    result = "".join(parts).strip()
    if not result:
        raise ValueError("local transliteration produced empty output")
    if _contains_indic_or_arabic_script(result):
        raise ValueError("local transliteration still contains Indic/Arabic script")
    return result


async def _resolve_transliteration(
    original_text: str,
    language: Optional[str],
    global_transcripts: List[str],
    user_transcripts: List[str],
) -> tuple[str, str]:
    """
    Try LLM, then local rule-based transliteration, then raw script as last resort.

    Returns (text_to_emit, method) where method is 'llm', 'local', or 'raw'.
    """
    try:
        transliterated_text = await asyncio.wait_for(
            asyncio.to_thread(
                transliterate_with_llm,
                original_text,
                global_transcripts,
                user_transcripts,
            ),
            timeout=TRANSLITERATION_TIMEOUT_SECONDS,
        )
        if transliterated_text:
            return transliterated_text, "llm"
    except Exception as e:
        logger.warning(f"LLM transliteration failed: {e}. Trying local fallback.")

    try:
        local_text = await asyncio.to_thread(
            transliterate_locally, original_text, language
        )
        return local_text, "local"
    except Exception as e:
        logger.warning(
            f"Local transliteration failed: {e}. Using original script as last resort."
        )

    return original_text, "raw"


def _update_transcript_caches(participant_identity: str, original_text: str) -> None:
    _global_transcript_cache.append(original_text)
    if len(_global_transcript_cache) > 3:
        _global_transcript_cache.pop(0)

    if participant_identity not in _user_transcript_cache:
        _user_transcript_cache[participant_identity] = []
    _user_transcript_cache[participant_identity].append(original_text)
    if len(_user_transcript_cache[participant_identity]) > 3:
        _user_transcript_cache[participant_identity].pop(0)


def _store_transcript(
    *,
    participant_identity: str,
    room_id: str,
    session_id: str,
    transcripts_store: List,
    should_store_transcript: Callable[[], bool],
    text_to_emit: str,
) -> None:
    if not should_store_transcript():
        logger.debug(
            f"⏸️ Recording stopped - skipping transcript storage: {participant_identity} -> {text_to_emit[:50]}..."
        )
        return

    try:
        from .models import Transcript
        from datetime import datetime

        transcript = Transcript(
            room_id=room_id,
            session_id=session_id,
            speaker_name=participant_identity,
            participant_sid=participant_identity,
            text_content=text_to_emit,
            timestamp=datetime.now(),
        )
        transcripts_store.append(transcript)
        cache_key_for_storage = (room_id, session_id)
        logger.info(
            f"📝 Transcript stored: {participant_identity} -> {text_to_emit[:50]}... (cache_key: {cache_key_for_storage}, store_size: {len(transcripts_store)})"
        )
    except Exception as e:
        logger.error(f"❌ Error storing transcript: {e}")
        import traceback

        logger.error(traceback.format_exc())


def _emit_final_transcript(activity, ev, language, text_to_emit: str) -> None:
    activity._session._user_input_transcribed(
        events.UserInputTranscribedEvent(
            language=language,
            transcript=text_to_emit,
            is_final=True,
            speaker_id=(
                ev.alternatives[0].speaker_id
                if ev.alternatives and hasattr(ev.alternatives[0], "speaker_id")
                else None
            ),
        ),
    )

    activity._interrupt_paused_speech_task = asyncio.create_task(
        activity._interrupt_paused_speech(
            old_task=activity._interrupt_paused_speech_task
        )
    )


async def _transliterate_and_emit(
    activity,
    ev,
    *,
    participant_identity: str,
    room_id: str,
    session_id: str,
    transcripts_store: List,
    should_store_transcript: Callable[[], bool],
    original_text: str,
    language,
) -> None:
    global_transcripts = _global_transcript_cache.copy()
    user_transcripts = _user_transcript_cache.get(participant_identity, []).copy()

    logger.info(f"🔄 Transliterating text with LLM (user: {participant_identity})")
    logger.debug(f"Original Text: {original_text}")

    text_to_emit, method = await _resolve_transliteration(
        original_text,
        language,
        global_transcripts,
        user_transcripts,
    )

    if method == "llm":
        logger.info(
            f"✅ Transliterated (LLM): '{original_text[:50]}...' -> '{text_to_emit[:50]}...'"
        )
    elif method == "local":
        logger.info(
            f"✅ Transliterated (local): '{original_text[:50]}...' -> '{text_to_emit[:50]}...'"
        )
    else:
        logger.warning(
            f"⚠️ Using original script (all transliteration failed): '{original_text[:50]}...'"
        )

    _update_transcript_caches(participant_identity, original_text)
    _store_transcript(
        participant_identity=participant_identity,
        room_id=room_id,
        session_id=session_id,
        transcripts_store=transcripts_store,
        should_store_transcript=should_store_transcript,
        text_to_emit=text_to_emit,
    )
    _emit_final_transcript(activity, ev, language, text_to_emit)


def is_pure_english(text: str) -> bool:
    """Check if text is 100% English alphabet (including spaces, punctuation, numbers)"""
    return bool(re.match(r'^[a-zA-Z0-9\s.,!?;:\-\'"]+$', text))


def patch_agent_activity_on_final_transcript(
    activity,
    participant_identity: str,
    room_id: str,
    session_id: str,
    transcripts_store: List,  # Reference to the transcript cache
    should_store_transcript: Callable[
        [], bool
    ] = lambda: True,  # Function to check if storage is allowed
):
    """
    Monkey patch the on_final_transcript method of AgentActivity
    to transliterate non-English text to Roman alphabet.

    Args:
        activity: The AgentActivity instance to patch
        participant_identity: The identity of the participant
        room_id: Room ID for transcript storage
        session_id: Session ID for transcript storage
        transcripts_store: Reference to transcript cache list
        should_store_transcript: Callable that returns True if transcripts should be stored
    """
    # Store the original method
    original_on_final_transcript = activity.on_final_transcript

    def patched_on_final_transcript(self, ev):
        """Patched version that transliterates non-English to Roman alphabet"""
        # Early return check (same as original)
        if (
            isinstance(self.llm, llm.RealtimeModel)
            and self.llm.capabilities.user_transcription
        ):
            return

        # Get the language and text
        language = ev.alternatives[0].language if ev.alternatives else None
        original_text = ev.alternatives[0].text if ev.alternatives else ""

        if not original_text:
            return

        # Non-English: run LLM off the event loop so LiveKit ping/pong stays healthy.
        if not is_pure_english(original_text):
            # Capture permission now so a transliteration already in flight is still
            # stored if recording stops before its LLM request completes.
            store_allowed = should_store_transcript()
            task = asyncio.create_task(
                _transliterate_and_emit(
                    self,
                    ev,
                    participant_identity=participant_identity,
                    room_id=room_id,
                    session_id=session_id,
                    transcripts_store=transcripts_store,
                    should_store_transcript=lambda: store_allowed,
                    original_text=original_text,
                    language=language,
                )
            )
            if not hasattr(activity, "_transliteration_tasks"):
                activity._transliteration_tasks = set()
            activity._transliteration_tasks.add(task)
            task.add_done_callback(activity._transliteration_tasks.discard)
            return

        logger.debug(f"Text is 100% English, using as-is: {original_text[:50]}...")
        _update_transcript_caches(participant_identity, original_text)
        _store_transcript(
            participant_identity=participant_identity,
            room_id=room_id,
            session_id=session_id,
            transcripts_store=transcripts_store,
            should_store_transcript=should_store_transcript,
            text_to_emit=original_text,
        )
        _emit_final_transcript(self, ev, language, original_text)

    # Bind the patched method to the activity instance
    activity.on_final_transcript = types.MethodType(
        patched_on_final_transcript, activity
    )
    logger.debug(
        f"✅ Monkey patched on_final_transcript for transliteration (user: {participant_identity})"
    )


def patch_realtime_session_to_disable_llm_responses(rt_session):
    """
    Monkey patch RealtimeSession to prevent LLM responses while keeping STT transcription.

    This patches _handle_server_content to skip processing model_turn (LLM responses)
    while still processing input_transcription (STT).

    Args:
        rt_session: The RealtimeSession instance to patch
    """

    import time

    if not isinstance(rt_session, google.beta.realtime.realtime_api.RealtimeSession):
        logger.warning(
            f"⚠️ Cannot patch: {type(rt_session)} is not a Google RealtimeSession"
        )
        return

    # Store original method
    if not hasattr(rt_session, "_original_handle_server_content"):
        rt_session._original_handle_server_content = rt_session._handle_server_content

        def patched_handle_server_content(self, server_content):
            """
            Patched version that skips model_turn (LLM responses) but keeps input_transcription (STT)
            """
            current_gen = self._current_generation
            if not current_gen:
                logger.warning("received server content but no active generation.")
                return

            # SKIP model_turn processing (LLM responses) - this is what we want to disable
            # Original code would process model_turn here, but we skip it
            # This prevents the LLM from generating text/audio responses

            # KEEP input_transcription processing (STT) - this is what we want to keep
            if input_transcription := server_content.input_transcription:
                text = input_transcription.text
                if text:
                    if current_gen.input_transcription == "":
                        text = text.lstrip()
                    current_gen.input_transcription += text
                    self.emit(
                        "input_audio_transcription_completed",
                        llm.InputTranscriptionCompleted(
                            item_id=current_gen.input_id,
                            transcript=current_gen.input_transcription,
                            is_final=False,
                        ),
                    )

            # SKIP output_transcription processing (TTS transcription)
            # We don't need TTS transcription since we're not generating responses

            if server_content.generation_complete or server_content.turn_complete:
                current_gen._completed_timestamp = time.time()

            if server_content.interrupted:
                self._handle_input_speech_started()

            if server_content.turn_complete:
                self._mark_current_generation_done()

        # Apply the patch
        rt_session._handle_server_content = types.MethodType(
            patched_handle_server_content, rt_session
        )
        logger.info(
            "✅ Monkey patched RealtimeSession._handle_server_content to disable LLM responses (STT only mode)"
        )
    else:
        logger.debug("RealtimeSession already patched to disable LLM responses")
