"""
Modified Gemini Live API for ASR-only (no TTS responses)
Uses the Live API but only prints transcriptions
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so `src` package is importable
src_path = Path(__file__).parent.parent
sys.path.insert(0, str(src_path))
from groq import Groq
from datetime import datetime
import os
import asyncio
import base64
import io
import traceback
import re
from google.genai import types
import pyaudio

import argparse

from google import genai
from google.genai import types
from dotenv import load_dotenv
from datetime import timedelta
from src.services.websearch import WebSearch

load_dotenv()


cache = {}


FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
CHUNK_SIZE = 1024

MODEL = "models/gemini-2.0-flash-live-001"


# ANSI color codes
class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    ENDC = "\033[0m"  # End color
    BOLD = "\033[1m"


client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
)

from datetime import datetime, timedelta


async def run_ai_search(user_query: str):
    """Get a list of all current to-do items."""
    print(f"Running AI search for: {user_query}")


async def add_todo(
    task: str, priority: str = "medium", deadline: str = "", note: str = ""
) -> dict:
    """Add a new task to the to-do list."""
    new_task = {
        "task": task,
        "priority": priority,
        "deadline": deadline
        or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "completed": False,
    }
    return {"added_task": new_task}


async def delete_todo(task_description: str) -> dict:
    """Delete a task from the to-do list."""
    return {"deleted_task": task_description}


async def update_todo(
    current_title: str,
    new_title: str = None,
    new_priority: str = None,
    new_deadline: str = None,
    new_note: str = None,
) -> dict:
    """Update an existing task's properties."""
    updated = {
        "current_title": current_title,
        "new_title": new_title or current_title,
        "new_priority": new_priority or "medium",
        "new_deadline": new_deadline
        or (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
        "new_note": new_note or "",
    }
    return {"updated_task": updated}


async def complete_todo(task_description: str) -> dict:
    """Mark a task as complete."""
    return {"completed_task": task_description}


async def get_task_stats() -> dict:
    """Get statistics about your tasks."""
    stats = {
        "total_tasks": 10,
        "completed_tasks": 4,
        "pending_tasks": 6,
        "high_priority": 3,
        "medium_priority": 5,
        "low_priority": 2,
    }
    return stats


tool_container = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="run_ai_search",
            description="This tool call initates searche by writing the user query in the search bar on the browser window  ",
            parameters={
                "type": "object",
                "properties": {
                    "user_query": {"type": "string"},  # optional, can be None in code
                },
            },
            response={"type": "string"},
        ),
    ]
)

TODO_SYSTEM_PROMPT = f"""
<role> You are Ryan, a personal assistant, expert in performing profile searches for executive recruitment </role>
<instructions>
- You are part of a much larger system and can control Web agent to perform tasks
- It is an AI-powered space where multiple agents are available to assist user (executive recruiter) in multiple tasks
- You are an expert in running searches on behalf of the user and saving profiles
- The space offers a tool, named Qlu Search, where you can simply search for profiles by giving a natural language search prompt
- The quality of the results depends on how good the search prompt is defined
- A good prompt includes granular details regarding:
1) Well defined industry of companies
2) Any required specific skills of profiles
- A User will be giving tasks to you to run their search
- Your task is to converse with the user and draft a final search prompt
- Be proactive and action-oriented - when users give you a search request, immediately start working on it rather than asking many clarifying questions
- Use your expertise to make reasonable assumptions and fill in missing details when possible
- Only ask questions when absolutely necessary for the search to be meaningful
- Take the flow naturally and do not directly jump onto asking requirements, maybe user is just there to casually converse with you
- You can use the general_web_agent_search tool to perform tasks on the QLU such as selecting candidates, saving searches, and save to assignment etc.
- When users asks you to recall previous searches, you must infer user wants to query from their past AI search responses
- use the search_previous_queries tool to recall previous searches
- use the get_search_statistics tool to get statistics about previous searches
- When given a search request, immediately create and execute a search prompt using your expertise
- If you need to clarify something, ask only the most critical question and proceed with reasonable assumptions
- Always call the run_ai_search tool directly whenever a user gives a search request or clarification, and continue passing follow-ups into the same tool until the user explicitly ends the search session
- After the initial search, every new user reply must be sent again into the same function to continue the loop.
- Do not reset context between turns; instead, assume the browser agent is keeping track of the conversation.
- Keep passing the user’s clarifications or follow-up replies into the same function until the user explicitly ends the search session.
- Always keep the loop alive between user and browser agent until finished.
MOST IMPORTANT INSTRUCTION:
- IF USER IS CONVERSING IN VOICE MODALITY, then:
  - Use a casual, friendly, and spoken tone — like you're conversing on a phone call.
  - DO NOT use text formatting.
  - DO NOT use bullet points (like * or -)
  - DO NOT use numbering (like 1., 2., etc.)
  - DO NOT use colons or headings
  - DO NOT use Markdown or newline-based formatting
  - Just reply like you're talking aloud: relaxed, natural, conversational.
  - Always ask one question at a time.

- IF USER IS CONVERSING IN TEXT MODALITY, then:
  - Use proper formatting: bullet points, numbering, newlines, etc.
  - Be clear, structured, and easy to read in written form.
</instructions>
<tools>
- input user's query to run_ai_search toolcall (user_query: str) -> str
- select_candidates(candidate_selection: str, assignment_names: str) -> str
- handle_user_confirmation(confirmation_type: str, user_response: str) -> str
- execute_user_instruction(instruction: str) -> str
</tools>
"""

print(TODO_SYSTEM_PROMPT)

pya = pyaudio.PyAudio()


class ASROnlyLoop:
    def __init__(self):
        print("Hello")
        self.session = None
        self.audio_stream = None
        self.out_queue = None
        self.transcript_buffer = ""  # Buffer to store interim transcripts
        self.resumption_handle = None  # Store session handle for resumption
        self.initial_prompt = "Hi."
        self.initial_response_processed = False

    async def send_text(self):
        """Optional text input"""
        while True:
            text = await asyncio.to_thread(
                input,
                "Type message (or just press Enter to use voice only) > ",
            )
            if text.lower() == "q":
                # Capture current transcript buffer and send it
                current_transcript = self.transcript_buffer.strip()
                if current_transcript:
                    print(
                        f"{Colors.YELLOW}📝 Sending partial transcript: '{current_transcript}'{Colors.ENDC}"
                    )
                    await self.send_hidden_trigger(current_transcript)
                else:
                    await self.send_hidden_trigger("")
                # await self.session.send_realtime_input(activity_end=types.ActivityEnd())

            if text.strip():  # Only send if there's actual text
                await self.session.send_realtime_input(text=text or ".")

    async def send_realtime(self):
        """Send audio data to API"""
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(audio=msg)

    async def send_initial_prompt(self):
        """Send initial prompt to start the conversation"""
        try:
            await self.session.send_realtime_input(text="Hi")
            print(f"{Colors.GREEN}🎯 Sent initial prompt to Gemini{Colors.ENDC}")
        except Exception as e:
            print(
                f"{Colors.RED}🔴 Error sending initial prompt to Gemini: {e}{Colors.ENDC}"
            )

    async def send_hidden_trigger(self, message):
        try:
            await self.session.send_realtime_input(text=message)
            print(
                f"{Colors.GREEN}🚀 Sent hidden trigger to Gemini: '{message}'{Colors.ENDC}"
            )
        except Exception as e:
            print(f"{Colors.RED}⚠️ Failed to send hidden trigger: {e}{Colors.ENDC}")

    async def listen_audio(self):
        """Capture microphone audio for ASR"""
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )

        print("🎤 Microphone active - speak naturally for ASR")

        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}

        while True:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
            await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})

    async def receive_responses(self):
        """Receive and print LLM responses with turn completion detection"""
        print("📝 LLM responses will appear below:\n")

        while True:
            turn = self.session.receive()
            turn_text = ""

            async for response in turn:
                # Handle session resumption updates
                if response.session_resumption_update:
                    update = response.session_resumption_update
                    if update.resumable and update.new_handle:
                        self.resumption_handle = update.new_handle
                        print(f"{Colors.GREEN}🔄 New session handle saved{Colors.ENDC}")

                # Handle GoAway signals
                if response.go_away:
                    print(
                        f"{Colors.YELLOW}⚠️ GoAway received, reconnecting in {response.go_away.time_left}s...{Colors.ENDC}"
                    )

                # Existing: response.text …
                # Handle user audio transcription
                if resp := getattr(
                    response.server_content, "input_transcription", None
                ):
                    if resp.text:
                        # Remove <noise> and [noise] markers from transcription
                        # clean_text = re.sub(r'<noise>|\[noise\]', '', resp.text)
                        clean_text = resp.text

                        # Partial transcription - update buffer and show progress
                        self.transcript_buffer += clean_text
                        print(f"{Colors.YELLOW}[PARTIAL] {clean_text}{Colors.ENDC}")
                        # Runtime trigger detection
                if response.tool_call:
                    function_responses = []
                    for fc in response.tool_call.function_calls:
                        args = fc.args  # Instead of fc.arguments
                        params = {k: v for k, v in args.items()}

                        print(
                            f"{Colors.CYAN}🔍 TOOL CALL: {fc.name} with {params}{Colors.ENDC}"
                        )
                        if fc.name == "run_ai_search":
                            result = await run_ai_search(
                                user_query=params.get("user_query")
                            )

                        function_responses.append(
                            types.FunctionResponse(
                                id=fc.id, name=fc.name, response=result
                            )
                        )
                    await self.session.send_tool_response(
                        function_responses=function_responses
                    )

                # Handle Gemini's transcription
                if resp2 := getattr(
                    response.server_content, "output_transcription", None
                ):
                    if resp2.text:

                        print(
                            f"{Colors.CYAN}Gemini transcribed: {resp2.text}{Colors.ENDC}"
                        )

                # Handle text responses from LLM
                if text := response.text:

                    # Print LLM response in blue color
                    print(f"{Colors.BLUE}{text}{Colors.ENDC}", end="", flush=True)
                    turn_text += text

                # Remove the data check since we're only expecting text responses
                # if response.data:
                #     pass  # Skip audio data

            # Turn has completed - print completion indicator
            print()  # New line after response

            # Check if this is the initial response
            is_initial_response = (
                not self.transcript_buffer.strip()
                and not self.initial_response_processed
            )
            if is_initial_response:
                self.initial_response_processed = True
                print(
                    f"{Colors.GREEN}💚 Processing initial Gemini response{Colors.ENDC}"
                )

            # Check if call ended
            if "<CALL_END>" in turn_text:
                print(
                    f"{Colors.RED}🔚 CALL ENDED - LLM terminated the conversation{Colors.ENDC}"
                )
                print(f"{Colors.RED}{'=' * 50}{Colors.ENDC}")
                break
            else:
                final_text = self.transcript_buffer.strip()
                if final_text:  # Only show transcription if there was user input
                    print(f"{Colors.GREEN}🎯 FINAL TRANSCRIPTION:{Colors.ENDC}")
                    print(f"{Colors.GREEN}┌{'─' * 60}┐{Colors.ENDC}")
                    print(f"{Colors.GREEN}│ {final_text:<58} │{Colors.ENDC}")
                    print(f"{Colors.GREEN}└{'─' * 60}┘{Colors.ENDC}")
                    print()

                # Clear the buffer for next utterance
                self.transcript_buffer = ""
                print(
                    f"{Colors.GREEN}✅ LLM RESPONSE COMPLETE - Turn ended{Colors.ENDC}"
                )
                print(f"{Colors.YELLOW}🎤 Your turn to speak...{Colors.ENDC}")
                print(f"{Colors.CYAN}{'-' * 30}{Colors.ENDC}")

    async def run(self):
        while True:
            try:
                # Create config with current resumption handle
                config = types.LiveConnectConfig(
                    response_modalities=[
                        "TEXT",  # Only text responses, no audio
                    ],
                    seed=4594,
                    tools=[tool_container],
                    input_audio_transcription={},  # enable transcribing incoming mic audio
                    output_audio_transcription={},  # enable transcribing Gemini's spoken replies
                    realtime_input_config=types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            # start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                            # end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                            # prefix_padding_ms=50,
                            silence_duration_ms=220
                        )
                    ),
                    session_resumption=types.SessionResumptionConfig(
                        # The handle of the session to resume is passed here,
                        # or else None to start a new session.
                        handle=self.resumption_handle
                    ),
                    system_instruction=types.Content(
                        parts=[types.Part(text=TODO_SYSTEM_PROMPT)]
                    ),
                    speech_config=types.SpeechConfig(language_code="en-US"),
                )

                async with (
                    client.aio.live.connect(model=MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session = session
                    self.out_queue = asyncio.Queue(maxsize=5)

                    # Send initial prompt first
                    await self.send_initial_prompt()

                    # Start tasks
                    send_text_task = tg.create_task(self.send_text())
                    tg.create_task(self.send_realtime())
                    tg.create_task(self.listen_audio())
                    tg.create_task(self.receive_responses())

                    await send_text_task
                    raise asyncio.CancelledError("User requested exit")

            except asyncio.CancelledError:
                print(f"{Colors.YELLOW}👋 User requested exit{Colors.ENDC}")
                break
            except ExceptionGroup as EG:
                if self.audio_stream:
                    self.audio_stream.close()
                traceback.print_exception(EG)
                print(
                    f"{Colors.RED}🔴 Connection error, attempting to reconnect...{Colors.ENDC}"
                )
                await asyncio.sleep(2)  # Wait before reconnecting
            except Exception as e:
                print(f"{Colors.RED}🔴 Unexpected error: {e}{Colors.ENDC}")
                print(
                    f"{Colors.YELLOW}🔄 Reconnecting and resuming session...{Colors.ENDC}"
                )
                await asyncio.sleep(2)  # Wait before reconnecting
            finally:
                if self.audio_stream:
                    self.audio_stream.close()


if __name__ == "__main__":
    print("Hi")
    main = ASROnlyLoop()
    asyncio.run(main.run())
