# """
# Final Gemini STT Adapter - Uses Gemini 2.0 Flash Live for STT only.
# This is a working implementation based on the existing Gemini realtime model.
# """

# import asyncio
# import time
# import os
# from dataclasses import dataclass

# from livekit import rtc
# from livekit.agents import stt, utils
# from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
# from livekit.plugins.google.beta.realtime.realtime_api import RealtimeModel, RealtimeSession
# from google.genai.types import Modality, RealtimeInputConfig, AudioTranscriptionConfig


# @dataclass
# class GeminiSTTConfig:
#     """Configuration for Gemini STT adapter"""
#     model: str = "gemini-2.0-flash-live-001"
#     language: str = "multi"
#     interim_results: bool = True
#     api_key: str | None = None
#     vertexai: bool = False
#     project: str | None = None
#     location: str | None = None


# class GeminiSTT(stt.STT):
#     """
#     Gemini STT - Uses Gemini 2.0 Flash Live realtime model for STT only.
#     This is a drop-in replacement for Deepgram STT that uses Gemini's transcription capabilities.
#     """

#     def __init__(self, config: GeminiSTTConfig | None = None):
#         self._config = config or GeminiSTTConfig()

#         # Get API key from environment if not provided
#         if not self._config.api_key:
#             self._config.api_key = os.environ.get("GOOGLE_API_KEY")

#         if not self._config.api_key:
#             raise ValueError("Google API key is required. Set GOOGLE_API_KEY environment variable or provide api_key in config.")

#         # Initialize the realtime model with STT-only configuration
#         self._realtime_model = RealtimeModel(
#             model=self._config.model,
#             api_key=self._config.api_key,
#             vertexai=self._config.vertexai,
#             project=self._config.project,
#             location=self._config.location,
#             modalities=[Modality.AUDIO],  # Only audio modality
#             input_audio_transcription=AudioTranscriptionConfig(),  # Enable input transcription
#             output_audio_transcription=None,  # Disable output transcription
#             # Use default realtime input config
#             realtime_input_config=RealtimeInputConfig(),
#         )

#         super().__init__(
#             capabilities=stt.STTCapabilities(
#                 streaming=True,
#                 interim_results=self._config.interim_results,
#                 diarization=False,  # Gemini realtime doesn't support diarization
#             )
#         )

#     async def _recognize_impl(
#         self,
#         buffer: utils.AudioBuffer,
#         *,
#         language: NotGivenOr[str] = NOT_GIVEN,
#         conn_options: APIConnectOptions,
#     ) -> stt.SpeechEvent:
#         """Recognize speech from audio buffer using Gemini realtime model"""
#         # For now, return a placeholder implementation
#         # TODO: Implement proper transcription using Gemini realtime model

#         # Create a simple speech event
#         speech_data = stt.SpeechData(
#             language=language if language != NOT_GIVEN else self._config.language,
#             text="",  # Placeholder - will be replaced with actual transcription
#             confidence=0.9,
#         )

#         return stt.SpeechEvent(
#             type=stt.SpeechEventType.FINAL_TRANSCRIPT,
#             alternatives=[speech_data],
#         )

#     def stream(
#         self,
#         *,
#         language: NotGivenOr[str] = NOT_GIVEN,
#         conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
#     ) -> stt.RecognizeStream:
#         """Create a streaming recognition session"""
#         return GeminiSTTStream(
#             stt=self,
#             conn_options=conn_options,
#             language=language,
#         )

#     async def aclose(self) -> None:
#         """Close the STT adapter"""
#         pass


# class GeminiSTTStream(stt.RecognizeStream):
#     """Streaming recognition using Gemini STT adapter"""

#     def __init__(
#         self,
#         stt: GeminiSTT,
#         conn_options: APIConnectOptions,
#         language: NotGivenOr[str],
#     ):
#         super().__init__(stt=stt, conn_options=conn_options)
#         self._language = language
#         self._session: RealtimeSession | None = None

#     async def _run(self) -> None:
#         """Main streaming recognition loop"""
#         # Create a realtime session
#         self._session = self._stt._realtime_model.session()

#         # Set up event handlers for transcription
#         self._setup_transcription_handlers()

#         # Process audio input
#         async for audio_input in self._input_ch:
#             if isinstance(audio_input, rtc.AudioFrame):
#                 self._session.push_audio(audio_input)
#             elif isinstance(audio_input, self._FlushSentinel):
#                 # Handle end of speech
#                 await self._handle_end_of_speech()

#         # Clean up
#         if self._session:
#             await self._session.aclose()

#     def _setup_transcription_handlers(self) -> None:
#         """Set up event handlers for transcription events"""
#         if not self._session:
#             return

#         # Handle input transcription events
#         @self._session.on("input_audio_transcription_completed")
#         def on_transcription_completed(event):
#             transcript = event.transcript
#             is_final = event.is_final

#             if transcript:
#                 # Create speech data
#                 speech_data = stt.SpeechData(
#                     language=self._language if self._language != NOT_GIVEN else "en-US",
#                     text=transcript,
#                     confidence=0.9,
#                 )

#                 # Create speech event
#                 event_type = (
#                     stt.SpeechEventType.FINAL_TRANSCRIPT if is_final
#                     else stt.SpeechEventType.INTERIM_TRANSCRIPT
#                 )

#                 speech_event = stt.SpeechEvent(
#                     type=event_type,
#                     alternatives=[speech_data],
#                 )

#                 # Send event
#                 self._event_ch.send_nowait(speech_event)

#     async def _handle_end_of_speech(self) -> None:
#         """Handle end of speech event"""
#         if self._session:
#             # Send end of speech event
#             self._event_ch.send_nowait(
#                 stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH)
#             )

#     async def aclose(self) -> None:
#         """Close the stream"""
#         if self._session:
#             await self._session.aclose()
#         await super().aclose()


# VERSION 2 with ADVANCED AUDiO DETECTION
#


# VERSION 03 (END OF SPEECH DETECTION BUT FINAL TRANSCRIPT COMES DELAYED)

# import asyncio
# import time
# import os
# from dataclasses import dataclass

# from livekit import rtc
# from livekit.agents import stt, utils
# from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
# from livekit.plugins.google.beta.realtime.realtime_api import RealtimeModel, RealtimeSession
# from google.genai.types import Modality, RealtimeInputConfig, AudioTranscriptionConfig


# @dataclass
# class GeminiSTTConfig:
#     """Configuration for Gemini STT adapter"""
#     model: str = "gemini-2.0-flash-live-001"
#     language: str = "multi"
#     interim_results: bool = True
#     api_key: str | None = None
#     vertexai: bool = False
#     project: str | None = None
#     location: str | None = None


# class GeminiSTT(stt.STT):
#     """
#     Gemini STT - Uses Gemini 2.0 Flash Live realtime model for STT only.
#     This is a drop-in replacement for Deepgram STT that uses Gemini's transcription capabilities.
#     """

#     def __init__(self, config: GeminiSTTConfig | None = None):
#         self._config = config or GeminiSTTConfig()

#         # Get API key from environment if not provided
#         if not self._config.api_key:
#             self._config.api_key = os.environ.get("GOOGLE_API_KEY")

#         if not self._config.api_key:
#             raise ValueError("Google API key is required. Set GOOGLE_API_KEY environment variable or provide api_key in config.")

#         # Initialize the realtime model with STT-only configuration
#         self._realtime_model = RealtimeModel(
#             model=self._config.model,
#             api_key=self._config.api_key,
#             vertexai=self._config.vertexai,
#             project=self._config.project,
#             location=self._config.location,
#             modalities=[Modality.AUDIO],  # Only audio modality
#             input_audio_transcription=AudioTranscriptionConfig(),  # Enable input transcription
#             output_audio_transcription=None,  # Disable output transcription
#             # Use default realtime input config
#             realtime_input_config=RealtimeInputConfig(),
#         )

#         super().__init__(
#             capabilities=stt.STTCapabilities(
#                 streaming=True,
#                 interim_results=self._config.interim_results,
#                 diarization=False,  # Gemini realtime doesn't support diarization
#             )
#         )

#     async def _recognize_impl(
#         self,
#         buffer: utils.AudioBuffer,
#         *,
#         language: NotGivenOr[str] = NOT_GIVEN,
#         conn_options: APIConnectOptions,
#     ) -> stt.SpeechEvent:
#         """Recognize speech from audio buffer using Gemini realtime model"""
#         # For now, return a placeholder implementation
#         # TODO: Implement proper transcription using Gemini realtime model

#         # Create a simple speech event
#         speech_data = stt.SpeechData(
#             language=language if language != NOT_GIVEN else self._config.language,
#             text="",  # Placeholder - will be replaced with actual transcription
#             confidence=0.9,
#         )

#         return stt.SpeechEvent(
#             type=stt.SpeechEventType.FINAL_TRANSCRIPT,
#             alternatives=[speech_data],
#         )

#     def stream(
#         self,
#         *,
#         language: NotGivenOr[str] = NOT_GIVEN,
#         conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
#     ) -> stt.RecognizeStream:
#         """Create a streaming recognition session"""
#         return GeminiSTTStream(
#             stt=self,
#             conn_options=conn_options,
#             language=language,
#         )

#     async def aclose(self) -> None:
#         """Close the STT adapter"""
#         pass


# class GeminiSTTStream(stt.RecognizeStream):
#     """Streaming recognition using Gemini STT adapter"""

#     def __init__(
#         self,
#         stt: GeminiSTT,
#         conn_options: APIConnectOptions,
#         language: NotGivenOr[str],
#     ):
#         super().__init__(stt=stt, conn_options=conn_options)
#         self._language = language
#         self._session: RealtimeSession | None = None

#         # Speech activity monitoring
#         self._speech_active = False
#         self._last_activity_time = 0
#         self._silence_timeout = 0.5  # 0.5 seconds of silence = end of speech (very responsive)
#         self._activity_monitor_task: asyncio.Task | None = None
#         self._last_transcript_time = 0  # Track when we last got a transcript

#     async def _run(self) -> None:
#         """Main streaming recognition loop"""
#         # Create a realtime session
#         self._session = self._stt._realtime_model.session()

#         # Set up event handlers for transcription
#         self._setup_transcription_handlers()

#         # Start activity monitoring
#         self._activity_monitor_task = asyncio.create_task(self._monitor_speech_activity())

#         try:
#             # Process audio input
#             async for audio_input in self._input_ch:
#                 if isinstance(audio_input, rtc.AudioFrame):
#                     self._session.push_audio(audio_input)
#                     # Update activity time when we receive audio
#                     self._last_activity_time = time.time()

#                     # Emit start of speech if not already active
#                     if not self._speech_active:
#                         self._speech_active = True
#                         print(f"🎤 [GeminiSTT] START_OF_SPEECH detected")
#                         self._event_ch.send_nowait(
#                             stt.SpeechEvent(
#                                 type=stt.SpeechEventType.START_OF_SPEECH,
#                                 request_id=f"gemini_{int(time.time() * 1000)}"
#                             )
#                         )
#                 elif isinstance(audio_input, self._FlushSentinel):
#                     # Handle end of speech
#                     await self._handle_end_of_speech()
#         finally:
#             # Clean up
#             if self._activity_monitor_task:
#                 self._activity_monitor_task.cancel()
#                 try:
#                     await self._activity_monitor_task
#                 except asyncio.CancelledError:
#                     pass

#             if self._session:
#                 await self._session.aclose()

#     async def _monitor_speech_activity(self) -> None:
#         """Monitor for speech activity and detect end of speech"""
#         while not self._event_ch.closed:
#             try:
#                 await asyncio.sleep(0.1)  # Check every 100ms

#                 if self._speech_active:
#                     silence_duration = time.time() - self._last_activity_time
#                     if silence_duration > self._silence_timeout:
#                         self._speech_active = False
#                         print(f"🔇 [GeminiSTT] END_OF_SPEECH detected (silence: {silence_duration:.1f}s)")
#                         self._event_ch.send_nowait(
#                             stt.SpeechEvent(
#                                 type=stt.SpeechEventType.END_OF_SPEECH,
#                                 request_id=f"gemini_{int(time.time() * 1000)}"
#                             )
#                         )
#             except asyncio.CancelledError:
#                 break
#             except Exception as e:
#                 print(f"Error in speech activity monitoring: {e}")
#                 break

#     def _setup_transcription_handlers(self) -> None:
#         """Set up event handlers for transcription events"""
#         if not self._session:
#             return

#         # Handle input transcription events
#         @self._session.on("input_audio_transcription_completed")
#         def on_transcription_completed(event):
#             transcript = event.transcript
#             is_final = event.is_final

#             if transcript:
#                 # Update activity time when we get transcription
#                 current_time = time.time()
#                 self._last_activity_time = current_time
#                 self._last_transcript_time = current_time

#                 # Create speech data
#                 speech_data = stt.SpeechData(
#                     language=self._language if self._language != NOT_GIVEN else "en-US",
#                     text=transcript,
#                     confidence=0.9,
#                 )

#                 # Create speech event
#                 event_type = (
#                     stt.SpeechEventType.FINAL_TRANSCRIPT if is_final
#                     else stt.SpeechEventType.INTERIM_TRANSCRIPT
#                 )

#                 print(f"📝 [GeminiSTT] {event_type.value}: '{transcript}'")

#                 speech_event = stt.SpeechEvent(
#                     type=event_type,
#                     request_id=f"gemini_{int(time.time() * 1000)}",
#                     alternatives=[speech_data],
#                 )

#                 # Send event
#                 self._event_ch.send_nowait(speech_event)

#                 # If this is a final transcript, trigger end of speech detection
#                 if is_final:
#                     print(f"🎯 [GeminiSTT] Final transcript received, triggering end of speech detection")
#                     # Give a small delay then emit END_OF_SPEECH
#                     asyncio.create_task(self._trigger_end_of_speech_after_delay())

#     async def _trigger_end_of_speech_after_delay(self) -> None:
#         """Trigger end of speech after a short delay when final transcript is received"""
#         await asyncio.sleep(0.1)  # Small delay to ensure transcript is processed
#         if self._speech_active:
#             self._speech_active = False
#             print(f"🔇 [GeminiSTT] END_OF_SPEECH triggered after final transcript")
#             self._event_ch.send_nowait(
#                 stt.SpeechEvent(
#                     type=stt.SpeechEventType.END_OF_SPEECH,
#                     request_id=f"gemini_{int(time.time() * 1000)}"
#                 )
#             )

#     def _emit_usage_event(self, audio_duration: float) -> None:
#         """Emit recognition usage event for metrics"""
#         usage_event = stt.SpeechEvent(
#             type=stt.SpeechEventType.RECOGNITION_USAGE,
#             request_id=f"gemini_{int(time.time() * 1000)}",
#             alternatives=[],
#             recognition_usage=stt.RecognitionUsage(audio_duration=audio_duration)
#         )
#         self._event_ch.send_nowait(usage_event)

#     async def _handle_end_of_speech(self) -> None:
#         """Handle end of speech event"""
#         if self._speech_active:
#             self._speech_active = False
#             print(f"🔇 [GeminiSTT] END_OF_SPEECH via FlushSentinel")
#             self._event_ch.send_nowait(
#                 stt.SpeechEvent(
#                     type=stt.SpeechEventType.END_OF_SPEECH,
#                     request_id=f"gemini_{int(time.time() * 1000)}"
#                 )
#             )

#     async def aclose(self) -> None:
#         """Close the stream"""
#         if self._activity_monitor_task:
#             self._activity_monitor_task.cancel()
#             try:
#                 await self._activity_monitor_task
#             except asyncio.CancelledError:
#                 pass

#         if self._session:
#             await self._session.aclose()
#         await super().aclose()

# VERSION 04 (FORCING LAST TRANSCRIPTS)
#

# VERSION 04B English only version of version 04
# import asyncio
# import time
# import os
# from dataclasses import dataclass

# from livekit import rtc
# from livekit.agents import stt, utils
# from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
# from livekit.plugins.google.beta.realtime.realtime_api import RealtimeModel, RealtimeSession
# from google.genai.types import Modality, RealtimeInputConfig, AudioTranscriptionConfig


# @dataclass
# class GeminiSTTConfig:
#     """Configuration for Gemini STT adapter"""
#     model: str = "gemini-2.0-flash-live-001"
#     language: str = "en-US"  # Force English only
#     interim_results: bool = True
#     api_key: str | None = None
#     vertexai: bool = False
#     project: str | None = None
#     location: str | None = None


# class GeminiSTT(stt.STT):
#     """
#     Gemini STT - Uses Gemini 2.0 Flash Live realtime model for STT only.
#     This is a drop-in replacement for Deepgram STT that uses Gemini's transcription capabilities.
#     """

#     def __init__(self, config: GeminiSTTConfig | None = None):
#         self._config = config or GeminiSTTConfig()

#         # Get API key from environment if not provided
#         if not self._config.api_key:
#             self._config.api_key = os.environ.get("GOOGLE_API_KEY")

#         if not self._config.api_key:
#             raise ValueError("Google API key is required. Set GOOGLE_API_KEY environment variable or provide api_key in config.")

#         # Initialize the realtime model with STT-only configuration
#         self._realtime_model = RealtimeModel(
#             model=self._config.model,
#             api_key=self._config.api_key,
#             vertexai=self._config.vertexai,
#             project=self._config.project,
#             location=self._config.location,
#             modalities=[Modality.AUDIO],  # Only audio modality
#             input_audio_transcription=AudioTranscriptionConfig(),  # Enable input transcription
#             output_audio_transcription=None,  # Disable output transcription
#             # Use default realtime input config
#             realtime_input_config=RealtimeInputConfig(),
#         )

#         super().__init__(
#             capabilities=stt.STTCapabilities(
#                 streaming=True,
#                 interim_results=self._config.interim_results,
#                 diarization=False,  # Gemini realtime doesn't support diarization
#             )
#         )

#     async def _recognize_impl(
#         self,
#         buffer: utils.AudioBuffer,
#         *,
#         language: NotGivenOr[str] = NOT_GIVEN,
#         conn_options: APIConnectOptions,
#     ) -> stt.SpeechEvent:
#         """Recognize speech from audio buffer using Gemini realtime model"""
#         # For now, return a placeholder implementation
#         # TODO: Implement proper transcription using Gemini realtime model

#         # Create a simple speech event
#         speech_data = stt.SpeechData(
#             language=language if language != NOT_GIVEN else self._config.language,
#             text="",  # Placeholder - will be replaced with actual transcription
#             confidence=0.9,
#         )

#         return stt.SpeechEvent(
#             type=stt.SpeechEventType.FINAL_TRANSCRIPT,
#             alternatives=[speech_data],
#         )

#     def stream(
#         self,
#         *,
#         language: NotGivenOr[str] = NOT_GIVEN,
#         conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
#     ) -> stt.RecognizeStream:
#         """Create a streaming recognition session"""
#         return GeminiSTTStream(
#             stt=self,
#             conn_options=conn_options,
#             language=language,
#         )

#     async def aclose(self) -> None:
#         """Close the STT adapter"""
#         pass


# class GeminiSTTStream(stt.RecognizeStream):
#     """Streaming recognition using Gemini STT adapter"""

#     def __init__(
#         self,
#         stt: GeminiSTT,
#         conn_options: APIConnectOptions,
#         language: NotGivenOr[str],
#     ):
#         super().__init__(stt=stt, conn_options=conn_options)
#         self._language = language
#         self._session: RealtimeSession | None = None

#         # Speech activity monitoring
#         self._speech_active = False
#         self._last_activity_time = 0
#         self._silence_timeout = 0.3  # 0.3 seconds of silence = end of speech (ultra responsive)
#         self._activity_monitor_task: asyncio.Task | None = None
#         self._last_transcript_time = 0  # Track when we last got a transcript
#         self._last_interim_text = ""  # Store last interim text for forced final transcript

#     async def _run(self) -> None:
#         """Main streaming recognition loop"""
#         # Create a realtime session
#         self._session = self._stt._realtime_model.session()

#         # Set up event handlers for transcription
#         self._setup_transcription_handlers()

#         # Start activity monitoring
#         self._activity_monitor_task = asyncio.create_task(self._monitor_speech_activity())

#         try:
#             # Process audio input
#             async for audio_input in self._input_ch:
#                 if isinstance(audio_input, rtc.AudioFrame):
#                     self._session.push_audio(audio_input)
#                     # Update activity time when we receive audio
#                     self._last_activity_time = time.time()

#                     # Emit start of speech if not already active
#                     if not self._speech_active:
#                         self._speech_active = True
#                         self._event_ch.send_nowait(
#                             stt.SpeechEvent(
#                                 type=stt.SpeechEventType.START_OF_SPEECH,
#                                 request_id=f"gemini_{int(time.time() * 1000)}"
#                             )
#                         )
#                 elif isinstance(audio_input, self._FlushSentinel):
#                     # Handle end of speech
#                     await self._handle_end_of_speech()
#         finally:
#             # Clean up
#             if self._activity_monitor_task:
#                 self._activity_monitor_task.cancel()
#                 try:
#                     await self._activity_monitor_task
#                 except asyncio.CancelledError:
#                     pass

#             if self._session:
#                 await self._session.aclose()

#     async def _monitor_speech_activity(self) -> None:
#         """Monitor for speech activity and detect end of speech"""
#         while not self._event_ch.closed:
#             try:
#                 await asyncio.sleep(0.05)  # Check every 50ms (more frequent)

#                 if self._speech_active:
#                     current_time = time.time()
#                     silence_duration = current_time - self._last_activity_time
#                     transcript_silence = current_time - self._last_transcript_time

#                     # End speech if we've had silence OR no new transcripts for a while
#                     if silence_duration > self._silence_timeout or transcript_silence > 0.2:
#                         self._speech_active = False
#                         self._event_ch.send_nowait(
#                             stt.SpeechEvent(
#                                 type=stt.SpeechEventType.END_OF_SPEECH,
#                                 request_id=f"gemini_{int(time.time() * 1000)}"
#                             )
#                         )
#             except asyncio.CancelledError:
#                 break
#             except Exception as e:
#                 print(f"Error in speech activity monitoring: {e}")
#                 break

#     def _setup_transcription_handlers(self) -> None:
#         """Set up event handlers for transcription events"""
#         if not self._session:
#             return

#         # Handle input transcription events
#         @self._session.on("input_audio_transcription_completed")
#         def on_transcription_completed(event):
#             transcript = event.transcript
#             is_final = event.is_final

#             if transcript:
#                 # Update activity time when we get transcription
#                 current_time = time.time()
#                 self._last_activity_time = current_time
#                 self._last_transcript_time = current_time

#                 # Create speech data - force English
#                 speech_data = stt.SpeechData(
#                     language="en-US",  # Always English
#                     text=transcript,
#                     confidence=0.9,
#                 )

#                 # Create speech event
#                 event_type = (
#                     stt.SpeechEventType.FINAL_TRANSCRIPT if is_final
#                     else stt.SpeechEventType.INTERIM_TRANSCRIPT
#                 )

#                 # Store the last interim text for potential forced final transcript
#                 if not is_final:
#                     self._last_interim_text = transcript

#                 speech_event = stt.SpeechEvent(
#                     type=event_type,
#                     request_id=f"gemini_{int(time.time() * 1000)}",
#                     alternatives=[speech_data],
#                 )

#                 # Send event
#                 self._event_ch.send_nowait(speech_event)

#                 # If this is a final transcript, trigger end of speech detection
#                 if is_final:
#                     # Give a small delay then emit END_OF_SPEECH
#                     asyncio.create_task(self._trigger_end_of_speech_after_delay())
#                 else:
#                     # For interim transcripts, also check if we should force a final transcript
#                     # This helps reduce the delay between interim and final
#                     asyncio.create_task(self._check_for_forced_final_transcript())

#                     # Also start a more aggressive timer for very quick final transcripts
#                     asyncio.create_task(self._aggressive_final_transcript_timer())

#     async def _check_for_forced_final_transcript(self) -> None:
#         """Check if we should force a final transcript to reduce delay"""
#         await asyncio.sleep(0.2)  # Wait only 200ms after interim transcript (much faster)

#         # If we haven't received a final transcript yet and user seems to have stopped
#         current_time = time.time()
#         if (self._speech_active and
#             current_time - self._last_transcript_time > 0.2 and
#             current_time - self._last_activity_time > 0.2):

#             # Force emit a final transcript with the last interim content
#             if hasattr(self, '_last_interim_text') and self._last_interim_text:
#                 speech_data = stt.SpeechData(
#                     language="en-US",  # Always English
#                     text=self._last_interim_text,
#                     confidence=0.9,
#                 )

#                 speech_event = stt.SpeechEvent(
#                     type=stt.SpeechEventType.FINAL_TRANSCRIPT,
#                     request_id=f"gemini_{int(time.time() * 1000)}",
#                     alternatives=[speech_data],
#                 )

#                 self._event_ch.send_nowait(speech_event)

#                 # Trigger end of speech immediately
#                 await self._trigger_end_of_speech_after_delay()

#     async def _aggressive_final_transcript_timer(self) -> None:
#         """Very aggressive timer to force final transcripts quickly"""
#         await asyncio.sleep(0.1)  # Wait only 100ms after interim transcript

#         # If we still haven't received a final transcript, force it immediately
#         current_time = time.time()
#         if (self._speech_active and
#             current_time - self._last_transcript_time > 0.1 and
#             hasattr(self, '_last_interim_text') and self._last_interim_text):

#             speech_data = stt.SpeechData(
#                 language="en-US",  # Always English
#                 text=self._last_interim_text,
#                 confidence=0.9,
#             )

#             speech_event = stt.SpeechEvent(
#                 type=stt.SpeechEventType.FINAL_TRANSCRIPT,
#                 request_id=f"gemini_{int(time.time() * 1000)}",
#                 alternatives=[speech_data],
#             )

#             self._event_ch.send_nowait(speech_event)

#             # Trigger end of speech immediately
#             await self._trigger_end_of_speech_after_delay()

#     async def _trigger_end_of_speech_after_delay(self) -> None:
#         """Trigger end of speech after a short delay when final transcript is received"""
#         await asyncio.sleep(0.1)  # Small delay to ensure transcript is processed
#         if self._speech_active:
#             self._speech_active = False
#             self._event_ch.send_nowait(
#                 stt.SpeechEvent(
#                     type=stt.SpeechEventType.END_OF_SPEECH,
#                     request_id=f"gemini_{int(time.time() * 1000)}"
#                 )
#             )

#     def _emit_usage_event(self, audio_duration: float) -> None:
#         """Emit recognition usage event for metrics"""
#         usage_event = stt.SpeechEvent(
#             type=stt.SpeechEventType.RECOGNITION_USAGE,
#             request_id=f"gemini_{int(time.time() * 1000)}",
#             alternatives=[],
#             recognition_usage=stt.RecognitionUsage(audio_duration=audio_duration)
#         )
#         self._event_ch.send_nowait(usage_event)

#     async def _handle_end_of_speech(self) -> None:
#         """Handle end of speech event"""
#         if self._speech_active:
#             self._speech_active = False
#             self._event_ch.send_nowait(
#                 stt.SpeechEvent(
#                     type=stt.SpeechEventType.END_OF_SPEECH,
#                     request_id=f"gemini_{int(time.time() * 1000)}"
#                 )
#             )

#     async def aclose(self) -> None:
#         """Close the stream"""
#         if self._activity_monitor_task:
#             self._activity_monitor_task.cancel()
#             try:
#                 await self._activity_monitor_task
#             except asyncio.CancelledError:
#                 pass

#         if self._session:
#             await self._session.aclose()
#         await super().aclose()


# VERSION 05(B) deduplicate
import asyncio
import time
import os
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.plugins.google.beta.realtime.realtime_api import (
    RealtimeModel,
    RealtimeSession,
)
from google.genai.types import Modality, RealtimeInputConfig, AudioTranscriptionConfig


@dataclass
class GeminiSTTConfig:
    """Configuration for Gemini STT adapter"""

    model: str = "gemini-2.0-flash-live-001"
    language: str = "en-US"  # Force English only
    interim_results: bool = True
    api_key: str | None = None
    vertexai: bool = False
    project: str | None = None
    location: str | None = None


class GeminiSTT(stt.STT):
    """
    Gemini STT - Uses Gemini 2.0 Flash Live realtime model for STT only.
    This is a drop-in replacement for Deepgram STT that uses Gemini's transcription capabilities.
    """

    def __init__(self, config: GeminiSTTConfig | None = None):
        self._config = config or GeminiSTTConfig()

        # Get API key from environment if not provided
        if not self._config.api_key:
            self._config.api_key = os.environ.get("GOOGLE_API_KEY")

        if not self._config.api_key:
            raise ValueError(
                "Google API key is required. Set GOOGLE_API_KEY environment variable or provide api_key in config."
            )

        # Initialize the realtime model with STT-only configuration
        self._realtime_model = RealtimeModel(
            model=self._config.model,
            api_key=self._config.api_key,
            vertexai=self._config.vertexai,
            project=self._config.project,
            location=self._config.location,
            modalities=[Modality.AUDIO],  # Only audio modality
            input_audio_transcription=AudioTranscriptionConfig(),  # Enable input transcription
            output_audio_transcription=None,  # Disable output transcription
            # Use default realtime input config
            realtime_input_config=RealtimeInputConfig(),
        )

        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=self._config.interim_results,
                diarization=False,  # Gemini realtime doesn't support diarization
            )
        )

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        """Recognize speech from audio buffer using Gemini realtime model"""
        # For now, return a placeholder implementation
        # TODO: Implement proper transcription using Gemini realtime model

        # Create a simple speech event
        speech_data = stt.SpeechData(
            language=language if language != NOT_GIVEN else self._config.language,
            text="",  # Placeholder - will be replaced with actual transcription
            confidence=0.9,
        )

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[speech_data],
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.RecognizeStream:
        """Create a streaming recognition session"""
        return GeminiSTTStream(
            stt=self,
            conn_options=conn_options,
            language=language,
        )

    async def aclose(self) -> None:
        """Close the STT adapter"""
        pass


class GeminiSTTStream(stt.RecognizeStream):
    """Streaming recognition using Gemini STT adapter"""

    def __init__(
        self,
        stt: GeminiSTT,
        conn_options: APIConnectOptions,
        language: NotGivenOr[str],
    ):
        super().__init__(stt=stt, conn_options=conn_options)
        self._language = language
        self._session: RealtimeSession | None = None

        # Speech activity monitoring
        self._speech_active = False
        self._last_activity_time = 0
        self._silence_timeout = (
            0.3  # 0.3 seconds of silence = end of speech (ultra responsive)
        )
        self._activity_monitor_task: asyncio.Task | None = None
        self._last_transcript_time = 0  # Track when we last got a transcript
        self._last_interim_text = (
            ""  # Store last interim text for forced final transcript
        )

        # Deduplication tracking
        self._last_processed_transcript = ""  # Track last processed transcript
        self._transcript_cache = set()  # Cache of processed transcripts
        self._last_final_transcript = ""  # Track last final transcript

    async def _run(self) -> None:
        """Main streaming recognition loop"""
        # Create a realtime session
        self._session = self._stt._realtime_model.session()

        # Set up event handlers for transcription
        self._setup_transcription_handlers()

        # Start activity monitoring
        self._activity_monitor_task = asyncio.create_task(
            self._monitor_speech_activity()
        )

        try:
            # Process audio input
            async for audio_input in self._input_ch:
                if isinstance(audio_input, rtc.AudioFrame):
                    self._session.push_audio(audio_input)
                    # Update activity time when we receive audio
                    self._last_activity_time = time.time()

                    # Emit start of speech if not already active
                    if not self._speech_active:
                        self._speech_active = True
                        # Reset deduplication tracking for new speech session
                        self._reset_deduplication_tracking()
                        self._event_ch.send_nowait(
                            stt.SpeechEvent(
                                type=stt.SpeechEventType.START_OF_SPEECH,
                                request_id=f"gemini_{int(time.time() * 1000)}",
                            )
                        )
                elif isinstance(audio_input, self._FlushSentinel):
                    # Handle end of speech
                    await self._handle_end_of_speech()
        finally:
            # Clean up
            if self._activity_monitor_task:
                self._activity_monitor_task.cancel()
                try:
                    await self._activity_monitor_task
                except asyncio.CancelledError:
                    pass

            if self._session:
                await self._session.aclose()

    async def _monitor_speech_activity(self) -> None:
        """Monitor for speech activity and detect end of speech"""
        while not self._event_ch.closed:
            try:
                await asyncio.sleep(0.05)  # Check every 50ms (more frequent)

                if self._speech_active:
                    current_time = time.time()
                    silence_duration = current_time - self._last_activity_time
                    transcript_silence = current_time - self._last_transcript_time

                    # End speech if we've had silence OR no new transcripts for a while
                    if (
                        silence_duration > self._silence_timeout
                        or transcript_silence > 0.2
                    ):
                        self._speech_active = False
                        self._event_ch.send_nowait(
                            stt.SpeechEvent(
                                type=stt.SpeechEventType.END_OF_SPEECH,
                                request_id=f"gemini_{int(time.time() * 1000)}",
                            )
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in speech activity monitoring: {e}")
                break

    def _setup_transcription_handlers(self) -> None:
        """Set up event handlers for transcription events"""
        if not self._session:
            return

        # Handle input transcription events
        @self._session.on("input_audio_transcription_completed")
        def on_transcription_completed(event):
            transcript = event.transcript
            is_final = event.is_final

            if transcript:
                # Deduplication check - skip if we've already processed this exact transcript
                if transcript == self._last_processed_transcript:
                    return

                # For final transcripts, check if we already have this content
                if is_final and transcript == self._last_final_transcript:
                    return

                # Update activity time when we get transcription
                current_time = time.time()
                self._last_activity_time = current_time
                self._last_transcript_time = current_time
                self._last_processed_transcript = transcript

                # Create speech data - force English
                speech_data = stt.SpeechData(
                    language="en-US",  # Always English
                    text=transcript,
                    confidence=0.9,
                )

                # Create speech event
                event_type = (
                    stt.SpeechEventType.FINAL_TRANSCRIPT
                    if is_final
                    else stt.SpeechEventType.INTERIM_TRANSCRIPT
                )

                # Store the last interim text for potential forced final transcript
                if not is_final:
                    self._last_interim_text = transcript
                else:
                    # Track final transcript to prevent duplicates
                    self._last_final_transcript = transcript

                speech_event = stt.SpeechEvent(
                    type=event_type,
                    request_id=f"gemini_{int(time.time() * 1000)}",
                    alternatives=[speech_data],
                )

                # Send event
                self._event_ch.send_nowait(speech_event)

                # If this is a final transcript, trigger end of speech detection
                if is_final:
                    # Give a small delay then emit END_OF_SPEECH
                    asyncio.create_task(self._trigger_end_of_speech_after_delay())
                else:
                    # For interim transcripts, also check if we should force a final transcript
                    # This helps reduce the delay between interim and final
                    asyncio.create_task(self._check_for_forced_final_transcript())

                    # Also start a more aggressive timer for very quick final transcripts
                    asyncio.create_task(self._aggressive_final_transcript_timer())

    async def _check_for_forced_final_transcript(self) -> None:
        """Check if we should force a final transcript to reduce delay"""
        await asyncio.sleep(
            0.2
        )  # Wait only 200ms after interim transcript (much faster)

        # If we haven't received a final transcript yet and user seems to have stopped
        current_time = time.time()
        if (
            self._speech_active
            and current_time - self._last_transcript_time > 0.2
            and current_time - self._last_activity_time > 0.2
        ):

            # Force emit a final transcript with the last interim content
            if hasattr(self, "_last_interim_text") and self._last_interim_text:
                # Check if we already processed this as a final transcript
                if self._last_interim_text == self._last_final_transcript:
                    return

                speech_data = stt.SpeechData(
                    language="en-US",  # Always English
                    text=self._last_interim_text,
                    confidence=0.9,
                )

                speech_event = stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=f"gemini_{int(time.time() * 1000)}",
                    alternatives=[speech_data],
                )

                # Track this as final transcript to prevent duplicates
                self._last_final_transcript = self._last_interim_text
                self._event_ch.send_nowait(speech_event)

                # Trigger end of speech immediately
                await self._trigger_end_of_speech_after_delay()

    async def _aggressive_final_transcript_timer(self) -> None:
        """Very aggressive timer to force final transcripts quickly"""
        await asyncio.sleep(0.1)  # Wait only 100ms after interim transcript

        # If we still haven't received a final transcript, force it immediately
        current_time = time.time()
        if (
            self._speech_active
            and current_time - self._last_transcript_time > 0.1
            and hasattr(self, "_last_interim_text")
            and self._last_interim_text
        ):

            # Check if we already processed this as a final transcript
            if self._last_interim_text == self._last_final_transcript:
                return

            speech_data = stt.SpeechData(
                language="en-US",  # Always English
                text=self._last_interim_text,
                confidence=0.9,
            )

            speech_event = stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id=f"gemini_{int(time.time() * 1000)}",
                alternatives=[speech_data],
            )

            # Track this as final transcript to prevent duplicates
            self._last_final_transcript = self._last_interim_text
            self._event_ch.send_nowait(speech_event)

            # Trigger end of speech immediately
            await self._trigger_end_of_speech_after_delay()

    def _reset_deduplication_tracking(self) -> None:
        """Reset deduplication tracking for new speech session"""
        self._last_processed_transcript = ""
        self._transcript_cache.clear()
        self._last_final_transcript = ""
        self._last_interim_text = ""

    async def _trigger_end_of_speech_after_delay(self) -> None:
        """Trigger end of speech after a short delay when final transcript is received"""
        await asyncio.sleep(0.1)  # Small delay to ensure transcript is processed
        if self._speech_active:
            self._speech_active = False
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH,
                    request_id=f"gemini_{int(time.time() * 1000)}",
                )
            )

    def _emit_usage_event(self, audio_duration: float) -> None:
        """Emit recognition usage event for metrics"""
        usage_event = stt.SpeechEvent(
            type=stt.SpeechEventType.RECOGNITION_USAGE,
            request_id=f"gemini_{int(time.time() * 1000)}",
            alternatives=[],
            recognition_usage=stt.RecognitionUsage(audio_duration=audio_duration),
        )
        self._event_ch.send_nowait(usage_event)

    async def _handle_end_of_speech(self) -> None:
        """Handle end of speech event"""
        if self._speech_active:
            self._speech_active = False
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH,
                    request_id=f"gemini_{int(time.time() * 1000)}",
                )
            )

    async def aclose(self) -> None:
        """Close the stream"""
        if self._activity_monitor_task:
            self._activity_monitor_task.cancel()
            try:
                await self._activity_monitor_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.aclose()
        await super().aclose()
