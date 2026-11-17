# """
# Modified Gemini Live API for ASR-only (no TTS responses)
# Uses the Live API but only prints transcriptions
# """
# import sys
# from pathlib import Path

# # Ensure project root is on sys.path so `src` package is importable
# src_path = Path(__file__).parent.parent
# sys.path.insert(0, str(src_path))
# from groq import Groq
# from datetime import datetime
# import os
# import asyncio
# import base64
# import io
# import traceback
# import re
# from google.genai import types
# import pyaudio

# import argparse

# from google import genai
# from google.genai import types
# from dotenv import load_dotenv
# from datetime import timedelta
# from src.services.websearch import WebSearch

# load_dotenv()


# cache = {}


# FORMAT = pyaudio.paInt16
# CHANNELS = 1
# SEND_SAMPLE_RATE = 16000
# CHUNK_SIZE = 1024

# MODEL = "models/gemini-2.0-flash-live-001"


# # ANSI color codes
# class Colors:
#     BLUE = "\033[94m"
#     GREEN = "\033[92m"
#     RED = "\033[91m"
#     YELLOW = "\033[93m"
#     CYAN = "\033[96m"
#     ENDC = "\033[0m"  # End color
#     BOLD = "\033[1m"


# client = genai.Client(
#     api_key=os.getenv("GEMINI_API_KEY"),
# )

# from datetime import datetime, timedelta

# async def get_todos(include_completed: bool = False, deadline: str | None = None) -> dict:
#     """Get a list of all current to-do items."""
#     sample_todos = [
#         {"task": "Buy groceries", "priority": "high", "deadline": "2025-09-10 18:00:00", "completed": False},
#         {"task": "Finish report", "priority": "medium", "deadline": "2025-09-12 12:00:00", "completed": True},
#         {"task": "Email client", "priority": "low", "deadline": "2025-09-11 09:00:00", "completed": False},
#     ]
#     filtered = [
#         t for t in sample_todos
#         if (include_completed or not t["completed"]) and (deadline is None or t["deadline"].startswith(deadline))
#     ]
#     return {"todos": filtered}


# async def add_todo(task: str, priority: str = "medium", deadline: str = "", note: str = "") -> dict:
#     """Add a new task to the to-do list."""
#     new_task = {
#         "task": task,
#         "priority": priority,
#         "deadline": deadline or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
#         "note": note,
#         "completed": False
#     }
#     return {"added_task": new_task}


# async def delete_todo(task_description: str) -> dict:
#     """Delete a task from the to-do list."""
#     return {"deleted_task": task_description}


# async def update_todo(current_title: str, new_title: str = None, new_priority: str = None,
#                       new_deadline: str = None, new_note: str = None) -> dict:
#     """Update an existing task's properties."""
#     updated = {
#         "current_title": current_title,
#         "new_title": new_title or current_title,
#         "new_priority": new_priority or "medium",
#         "new_deadline": new_deadline or (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
#         "new_note": new_note or ""
#     }
#     return {"updated_task": updated}


# async def complete_todo(task_description: str) -> dict:
#     """Mark a task as complete."""
#     return {"completed_task": task_description}


# async def get_task_stats() -> dict:
#     """Get statistics about your tasks."""
#     stats = {
#         "total_tasks": 10,
#         "completed_tasks": 4,
#         "pending_tasks": 6,
#         "high_priority": 3,
#         "medium_priority": 5,
#         "low_priority": 2
#     }
#     return stats


# tool_container = types.Tool(
#     function_declarations=[
#         types.FunctionDeclaration(
#             name="get_todos",
#             description="Get a list of all current to-do items.",
#             parameters={
#                 "type": "object",
#                 "properties": {
#                     "include_completed": {"type": "boolean"},
#                     "deadline": {"type": "string"},  # optional, can be None in code
#                 },
#             },
#             response={"type": "string"},
#         ),
#         types.FunctionDeclaration(
#             name="add_todo",
#             description="Add a new task to the to-do list.",
#             parameters={
#                 "type": "object",
#                 "properties": {
#                     "task": {"type": "string"},
#                     "priority": {"type": "string"},
#                     "deadline": {"type": "string"},
#                 },
#             },
#             response={"type": "string"},
#         ),
#         types.FunctionDeclaration(
#             name="delete_todo",
#             description="Permanently delete a task from the to-do list by describing it naturally.",
#             parameters={
#                 "type": "object",
#                 "properties": {
#                     "task_description": {"type": "string"},
#                 },
#             },
#             response={"type": "string"},
#         ),
#         types.FunctionDeclaration(
#             name="update_todo",
#             description="Update an existing task's properties (title, priority, deadline, note).",
#             parameters={
#                 "type": "object",
#                 "properties": {
#                     "current_title": {"type": "string"},
#                     "new_title": {"type": "string"},
#                     "new_priority": {"type": "string"},
#                     "new_deadline": {"type": "string"},
#                     "new_note": {"type": "string"},
#                 },
#             },
#             response={"type": "string"},
#         ),
#         types.FunctionDeclaration(
#             name="complete_todo",
#             description="Mark a task as complete by describing it.",
#             parameters={
#                 "type": "object",
#                 "properties": {
#                     "task_description": {"type": "string"},
#                 },
#             },
#             response={"type": "string"},
#         ),
#         types.FunctionDeclaration(
#             name="get_task_stats",
#             description="Get statistics about your tasks.",
#             response={"type": "string"},
#         ),
#         types.FunctionDeclaration(
#             name="web_search_tool",
#             description="Perform a web search for recent/current information.",
#             parameters={
#                 "type": "object",
#                 "properties": {
#                     "query": {"type": "string"},
#                 },
#                 "required": ["query"],
#             },
#             response={"type": "string"},
#         ),
#     ]
# )

# TODO_SYSTEM_PROMPT = f"""
# <role>
# You are Todo Agent named Bella, a human-like assistant that manages tasks and schedules.
# Bella is thoughtful, lightly witty, and supportive — she nudges instead of pushes.
# Her goal is to help users stay organized without ever sounding robotic or forceful.
# </role>
# <tone>
# - Offer, don’t push. Ask one question at a single time.
# - Use light personality (encouraging, caring).
# - Sometimes use light humor and sarcasm.
# - Your responses should be conversational, short and natural.
# - Your responses should be like a human speech conversation.
# </tone>
# <very_important_instructions>
# Step 1: User Query Classification
# 1. When a user query comes in, you must first decide:
# - Is it about Todo/Task Management? (tasks, todos, task items, checklists)
# - Is it about Calendar/Meeting Management? (meetings, events, free time, schedules)
# - Is it about LLM Knowledge/Web Search? (concepts, general knowledge, timeless info)
# - Is it about A Specific Day Overview? (how does my day look like on a certain date)
# 2. If it is ambiguous or unclear, you must ask the user to clarify. Do not assume the query class blindly.
# Step 2: Silent Delegation
# - If the query is about calendar, meetings, events, free time, you Must silently delegate to `handle_calendar_request`.
# - Never tell the user you are switching or that the feature is unsupported.
# - For task/todo-related queries, you Must use the Todo tools directly.
# Step 3: CRUD Awareness
# - Both tasks and calendar management have Create, Read/Get, Update, Delete operations.
# - Only perform CRUD operations on the type explicitly mentioned by the user.
# </very_important_instructions>
# <date_time_rules>
# - For reference, current datetime is: {datetime.now().strftime('%I:%M %p, %A, %B %d, %Y')}.
# - You Must always use the current datetime internally.
# - Temporal expressions Must follow tense rules:
#   - Past events → "had" or "were supposed to have"
#   - Present (today) → "have"
#   - Future → "will have" or "you have"
# - You Must never mix relative words with explicit dates.
# - You Must internally convert relative dates to ISO format for tool calls.
# </date_time_rules>
# <strict_tool_usage>
# - To add a todo task: You must call `add_todo` tool call. Ask for missing info (deadline, priority). If no priority is specified, set priority to "medium".
# - To get todo tasks: You must call `get_todos` tool call. Filter by deadline if specified. Include completed tasks only if explicitly requested.
# - To delete a todo task: You must call `delete_todo` tool call. Confirm task description before deletion.
# - To update a todo task: You must call `update_todo` tool call. Only update fields explicitly mentioned. Never update calendar events.
# - To complete a todo task: You must call `complete_todo` tool call. to mark a task done.
# - To get daily overview: You must call `get_daily_overview` tool call. for combined tasks + calendar.
# - To get task stats: You must call `get_task_stats` tool call. for status of tasks. Summarize naturally.
# </strict_tool_usage>
# <web_search_strict_usage>
# 1. Internal Verification
# - Before any web search, you must internally check that all information needed is explicitly provided.
# - Never assume missing info
# - If information is missing, Must ask the user for clarification exactly once.
# 2. User Confirmation
# - After verifying info internally, Must ask the user if they want the search performed.
# - Wait for explicit confirmation before proceeding.
# 3. Search Clarification
# - Once confirmed, Must ask the user to specify exactly what to search.
# - Only after the user specifies exactly, Must perform the web search.
# 4. Search Execution
# - Must call `web_search_tool` only after both verification and confirmation.
# - Summarize results in 1–2 sentences, naturally unless user explicitly asks longer answer.
# - Use minimal source attribution if needed.
# 5. Strict Rules
# - Must never assume missing parameters.
# - Must never search automatically.
# </web_search_strict_usage>
# <general_rules>
# - You Must always use correct temporal language.
# - You Must use web search only for information that needs realtime information (news, weather, stock, etc.).
# - You Must never use web search for timeless knowledge, how-to, or conceptual explanations.
# </general_rules>
# """

# print(TODO_SYSTEM_PROMPT)

# CALENDAR_SYSTEM_PROMPT = f"""
# You are Calendar Agent, a human-like assistant that manages meetings, events, schedules, and free time.

# ------------------------------------------------------------
# VERY STRICT INITIAL INSTRUCTIONS
# ------------------------------------------------------------
# - **Step 1: User Query Classification**
#   1. When a user query comes in, you **Must** first decide:
#      - Is it about **Calendar/Meeting Management**? (meetings, events, free time, schedules)
#      - Is it about **Todo/Task Management**? (tasks, todos, task items)
#      - Is it about **LLM Knowledge/Web Search**? (concepts, general knowledge, timeless info)
#      - Is it about **A Specific Day Overview**? (how does my day look like on a certain date)
#   2. If it is **ambiguous or unclear**, you **Must pause** and ask the user to clarify.
#      Do **not** assume; do **not** decide blindly.

# - **Step 2: Silent Delegation**
#   - If the query is about **tasks/todos**, you **Must silently delegate** to `handle_todo_request`.
#   - If it is about **calendar events, meetings, or free time**, you **Must use Calendar tools** directly.
#   - **Never** tell the user you are switching tools or that a feature is unsupported.

# - **Step 3: CRUD Awareness**
#   - Both calendar and task management have **Create, Read/Get, Update, Delete** operations.
#   - Only perform CRUD operations on the type explicitly mentioned by the user.

# - **Step 4: Speaking and Pausing**
#   - You **Must only respond when asked**.
#   - You **Must pause if unclear** until all required info is provided.
#   - You **Must never offer suggestions** or continue without clarity.

# ------------------------------------------------------------
# DATE & TIME RULES
# ------------------------------------------------------------
# - Always use current datetime: {datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')} as reference.
# - You **Must** always use current datetime internally.
# - Temporal expressions **Must** follow tense rules:
#   - Past events → "had" or "were supposed to have"
#   - Present (today) → "have"
#   - Future → "will have" or "you have"
# - You **Must** never mix relative words with explicit dates.
# - You **Must** internally convert relative dates to RFC 3339 format for tool calls.

# ------------------------------------------------------------
# STRICT TOOL USAGE
# ------------------------------------------------------------
# - **Schedule Meeting:** You **Must** never call `schedule_meeting_with_participants` directly.
#   1. You **Must** collect missing info: title, date/time, participant names.
#   2. You **Must** resolve participants via `resolve_participants_from_query`.
#      - Confirmed → proceed
#      - Confused → ask user (max 3 attempts)
#      - Not found → pause until user clarifies
#   3. You **Must** summarize meeting to user for confirmation before final scheduling.
#   4. Only after user confirms, you **Must call `schedule_meeting_with_participants`**.
# - **Update Meeting:** You **Must call `update_calendar_event`** only if user explicitly requests. Only update fields mentioned.
# - **Delete Meeting:** You **Must confirm** before calling `delete_calendar_event`. Only for events/meetings.
# - **List Meetings:** You **Must call `list_events`**. Use correct temporal language in response.
# - **Find Free Time:** You **Must call `find_free_time`**, including date/duration if provided.
# - **Daily Overview:** You **Must call `get_daily_overview`**. Provide combined summary of meetings + tasks.

# ------------------------------------------------------------
# WEB SEARCH STRICT USAGE
# ------------------------------------------------------------
# 1. **Internal Verification**
#    - Before any web search, you **Must internally check** that all information needed is explicitly provided.
#    - **Never assume** missing info
#    - If information is missing, **Must ask the user for clarification** exactly once.

# 2. **User Confirmation**
#    - After verifying info internally, **Must ask the user** if they want the search performed.
#    - Wait for **explicit confirmation** before proceeding.

# 3. **Search Clarification**
#    - Once confirmed, **Must ask the user** to specify exactly what to search.
#    - Only after the user specifies exactly, **Must perform the web search**.

# 4. **Search Execution**
#    - **Must call `web_search_tool`** only after both verification and confirmation.
#    - Summarize results in **10-12 words**, naturally unless user explicitly asks longer answer.
#    - Use minimal source attribution if needed.

# 5. **Strict Rules**
#    - **Must never assume** missing parameters.
#    - **Must never search automatically**.
#    - **Must pause** if clarification is missing.
#    - **Must not provide results without user confirmation**.
# <general_rules>
# - You **Must never refuse** supported features.
# - You **Must never assume missing information**.
# - You **Must pause if unclear**; never offer unsolicited suggestions.
# - You **Must always use correct temporal language**.
# - You **Must use web search only for recent/current information** (news, live updates, leadership, releases, etc.).
# - You **Must never** use web search for timeless knowledge, how-to, or conceptual explanations.
# </general_rules>
# """

# pya = pyaudio.PyAudio()

# class ASROnlyLoop:
#     def __init__(self):
#         print("Hello")
#         self.session = None
#         self.audio_stream = None
#         self.out_queue = None
#         self.transcript_buffer = ""  # Buffer to store interim transcripts
#         self.resumption_handle = None  # Store session handle for resumption
#         self.initial_prompt = "Hi."
#         self.initial_response_processed = False

#     async def send_text(self):
#         """Optional text input"""
#         while True:
#             text = await asyncio.to_thread(
#                 input,
#                 "Type message (or just press Enter to use voice only) > ",
#             )
#             if text.lower() == "q":
#                 # Capture current transcript buffer and send it
#                 current_transcript = self.transcript_buffer.strip()
#                 if current_transcript:
#                     print(
#                         f"{Colors.YELLOW}📝 Sending partial transcript: '{current_transcript}'{Colors.ENDC}"
#                     )
#                     await self.send_hidden_trigger(current_transcript)
#                 else:
#                     await self.send_hidden_trigger("")
#                 # await self.session.send_realtime_input(activity_end=types.ActivityEnd())

#             if text.strip():  # Only send if there's actual text
#                 await self.session.send_realtime_input(text=text or ".")

#     async def send_realtime(self):
#         """Send audio data to API"""
#         while True:
#             msg = await self.out_queue.get()
#             await self.session.send_realtime_input(audio=msg)

#     async def send_initial_prompt(self):
#         """Send initial prompt to start the conversation"""
#         try:
#             await self.session.send_realtime_input(text="Hi")
#             print(f"{Colors.GREEN}🎯 Sent initial prompt to Gemini{Colors.ENDC}")
#         except Exception as e:
#             print(
#                 f"{Colors.RED}🔴 Error sending initial prompt to Gemini: {e}{Colors.ENDC}"
#             )

#     async def send_hidden_trigger(self, message):
#         try:
#             await self.session.send_realtime_input(text=message)
#             print(
#                 f"{Colors.GREEN}🚀 Sent hidden trigger to Gemini: '{message}'{Colors.ENDC}"
#             )
#         except Exception as e:
#             print(f"{Colors.RED}⚠️ Failed to send hidden trigger: {e}{Colors.ENDC}")

#     async def listen_audio(self):
#         """Capture microphone audio for ASR"""
#         mic_info = pya.get_default_input_device_info()
#         self.audio_stream = await asyncio.to_thread(
#             pya.open,
#             format=FORMAT,
#             channels=CHANNELS,
#             rate=SEND_SAMPLE_RATE,
#             input=True,
#             input_device_index=mic_info["index"],
#             frames_per_buffer=CHUNK_SIZE,
#         )

#         print("🎤 Microphone active - speak naturally for ASR")

#         if __debug__:
#             kwargs = {"exception_on_overflow": False}
#         else:
#             kwargs = {}

#         while True:
#             data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
#             await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})

#     async def receive_responses(self):
#         """Receive and print LLM responses with turn completion detection"""
#         print("📝 LLM responses will appear below:\n")

#         while True:
#             turn = self.session.receive()
#             turn_text = ""

#             async for response in turn:
#                 # Handle session resumption updates
#                 if response.session_resumption_update:
#                     update = response.session_resumption_update
#                     if update.resumable and update.new_handle:
#                         self.resumption_handle = update.new_handle
#                         print(f"{Colors.GREEN}🔄 New session handle saved{Colors.ENDC}")

#                 # Handle GoAway signals
#                 if response.go_away:
#                     print(
#                         f"{Colors.YELLOW}⚠️ GoAway received, reconnecting in {response.go_away.time_left}s...{Colors.ENDC}"
#                     )

#                 # Existing: response.text …
#                 # Handle user audio transcription
#                 if resp := getattr(
#                     response.server_content, "input_transcription", None
#                 ):
#                     if resp.text:
#                         # Remove <noise> and [noise] markers from transcription
#                         # clean_text = re.sub(r'<noise>|\[noise\]', '', resp.text)
#                         clean_text = resp.text

#                         # Partial transcription - update buffer and show progress
#                         self.transcript_buffer += clean_text
#                         print(f"{Colors.YELLOW}[PARTIAL] {clean_text}{Colors.ENDC}")
#                         # Runtime trigger detection
#                 if response.tool_call:
#                     function_responses = []
#                     for fc in response.tool_call.function_calls:
#                         args = fc.args  # Instead of fc.arguments
#                         params = {k: v for k, v in args.items()}

#                         print(
#                             f"{Colors.CYAN}🔍 TOOL CALL: {fc.name} with {params}{Colors.ENDC}"
#                         )
#                         if fc.name == "get_todos":
#                             result = await get_todos(
#                                 include_completed=params.get("include_completed", False),
#                                 deadline=params.get("deadline")
#                             )
#                         elif fc.name == "add_todo":
#                             result = await add_todo(
#                                 task=params["task"],
#                                 priority=params.get("priority", "medium"),
#                                 deadline=params.get("deadline", ""),
#                             )
#                         elif fc.name == "delete_todo":
#                             result = await delete_todo(
#                                 task_description=params["task_description"]
#                             )
#                         elif fc.name == "update_todo":
#                             result = await update_todo(
#                                 current_title=params["current_title"],
#                                 new_title=params.get("new_title"),
#                                 new_priority=params.get("new_priority"),
#                                 new_deadline=params.get("new_deadline"),
#                                 new_note=params.get("new_note")
#                             )
#                         elif fc.name == "complete_todo":
#                             result = await complete_todo(
#                                 task_description=params["task_description"]
#                             )
#                         elif fc.name == "get_task_stats":
#                             result = await get_task_stats()
#                         elif fc.name == "web_search_tool":
#                             ws = WebSearch.search(
#                                 query=params["query"],
#                             )
#                             # Only return JSON-serializable fields
#                             result = {
#                                 "query": ws.get("query"),
#                                 "response": ws.get("response"),
#                                 "execution_time": float(ws.get("execution_time", 0.0)),
#                                 "success": bool(ws.get("success", False)),
#                                 "error": ws.get("error"),
#                             }

#                         function_responses.append(
#                             types.FunctionResponse(
#                                 id=fc.id, name=fc.name, response=result
#                             )
#                         )
#                     await self.session.send_tool_response(
#                         function_responses=function_responses
#                     )

#                 # Handle Gemini's transcription
#                 if resp2 := getattr(
#                     response.server_content, "output_transcription", None
#                 ):
#                     if resp2.text:

#                         print(
#                             f"{Colors.CYAN}Gemini transcribed: {resp2.text}{Colors.ENDC}"
#                         )

#                 # Handle text responses from LLM
#                 if text := response.text:

#                     # Print LLM response in blue color
#                     print(f"{Colors.BLUE}{text}{Colors.ENDC}", end="", flush=True)
#                     turn_text += text

#                 # Remove the data check since we're only expecting text responses
#                 # if response.data:
#                 #     pass  # Skip audio data

#             # Turn has completed - print completion indicator
#             print()  # New line after response

#             # Check if this is the initial response
#             is_initial_response = (
#                 not self.transcript_buffer.strip()
#                 and not self.initial_response_processed
#             )
#             if is_initial_response:
#                 self.initial_response_processed = True
#                 print(
#                     f"{Colors.GREEN}💚 Processing initial Gemini response{Colors.ENDC}"
#                 )

#             # Check if call ended
#             if "<CALL_END>" in turn_text:
#                 print(
#                     f"{Colors.RED}🔚 CALL ENDED - LLM terminated the conversation{Colors.ENDC}"
#                 )
#                 print(f"{Colors.RED}{'=' * 50}{Colors.ENDC}")
#                 break
#             else:
#                 final_text = self.transcript_buffer.strip()
#                 if final_text:  # Only show transcription if there was user input
#                     print(f"{Colors.GREEN}🎯 FINAL TRANSCRIPTION:{Colors.ENDC}")
#                     print(f"{Colors.GREEN}┌{'─' * 60}┐{Colors.ENDC}")
#                     print(f"{Colors.GREEN}│ {final_text:<58} │{Colors.ENDC}")
#                     print(f"{Colors.GREEN}└{'─' * 60}┘{Colors.ENDC}")
#                     print()

#                 # Clear the buffer for next utterance
#                 self.transcript_buffer = ""
#                 print(
#                     f"{Colors.GREEN}✅ LLM RESPONSE COMPLETE - Turn ended{Colors.ENDC}"
#                 )
#                 print(f"{Colors.YELLOW}🎤 Your turn to speak...{Colors.ENDC}")
#                 print(f"{Colors.CYAN}{'-' * 30}{Colors.ENDC}")

#     async def run(self):
#         while True:
#             try:
#                 # Create config with current resumption handle
#                 config = types.LiveConnectConfig(
#                     response_modalities=[
#                         "TEXT",  # Only text responses, no audio
#                     ],
#                     seed=4594,
#                     tools=[tool_container],
#                     input_audio_transcription={},  # enable transcribing incoming mic audio
#                     output_audio_transcription={},  # enable transcribing Gemini's spoken replies
#                     realtime_input_config=types.RealtimeInputConfig(
#                         automatic_activity_detection=types.AutomaticActivityDetection(
#                             # start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
#                             # end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
#                             # prefix_padding_ms=50,
#                             silence_duration_ms=220
#                         )
#                     ),
#                     session_resumption=types.SessionResumptionConfig(
#                         # The handle of the session to resume is passed here,
#                         # or else None to start a new session.
#                         handle=self.resumption_handle
#                     ),
#                     system_instruction=types.Content(
#                         parts=[
#                             types.Part(
#                                 text=TODO_SYSTEM_PROMPT
#                             )
#                         ]
#                     ),
#                     speech_config=types.SpeechConfig(language_code="en-US"),
#                 )

#                 async with (
#                     client.aio.live.connect(model=MODEL, config=config) as session,
#                     asyncio.TaskGroup() as tg,
#                 ):
#                     self.session = session
#                     self.out_queue = asyncio.Queue(maxsize=5)

#                     # Send initial prompt first
#                     await self.send_initial_prompt()

#                     # Start tasks
#                     send_text_task = tg.create_task(self.send_text())
#                     tg.create_task(self.send_realtime())
#                     tg.create_task(self.listen_audio())
#                     tg.create_task(self.receive_responses())

#                     await send_text_task
#                     raise asyncio.CancelledError("User requested exit")

#             except asyncio.CancelledError:
#                 print(f"{Colors.YELLOW}👋 User requested exit{Colors.ENDC}")
#                 break
#             except ExceptionGroup as EG:
#                 if self.audio_stream:
#                     self.audio_stream.close()
#                 traceback.print_exception(EG)
#                 print(
#                     f"{Colors.RED}🔴 Connection error, attempting to reconnect...{Colors.ENDC}"
#                 )
#                 await asyncio.sleep(2)  # Wait before reconnecting
#             except Exception as e:
#                 print(f"{Colors.RED}🔴 Unexpected error: {e}{Colors.ENDC}")
#                 print(
#                     f"{Colors.YELLOW}🔄 Reconnecting and resuming session...{Colors.ENDC}"
#                 )
#                 await asyncio.sleep(2)  # Wait before reconnecting
#             finally:
#                 if self.audio_stream:
#                     self.audio_stream.close()


# if __name__ == "__main__":
#     print("Hi")
#     main = ASROnlyLoop()
#     asyncio.run(main.run())
