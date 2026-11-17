participants_list = []
pending_context: list = []

from livekit.agents import ModelSettings, llm, FunctionTool, Agent
from typing import AsyncIterable, Optional, List
import re
from collections.abc import AsyncIterable, AsyncGenerator
import sys
from pathlib import Path
import dotenv
import json
import copy
from datetime import datetime, timedelta, date
import re
from typing import Optional
import pytz
from dataclasses import asdict
import os
import requests
from openai import AsyncOpenAI

src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))
from livekit.agents import (
    Agent,
    RunContext,
    ChatContext,
    function_tool,
    ConversationItemAddedEvent,
    AgentSession,
)
from src.agents.bella.shared import UserData
from src.agents.bella.utils import _materialize_llm_result
from typing import AsyncIterable, AsyncGenerator
from src.services.websearch import WebSearch
from utils.logger import Logger
from src.agents.bella.utils import sanitize_for_rpc

logging = Logger(__name__)


class BaseAgent(Agent):
    async def on_enter(self):
        """
        When a new agent becomes active, this method is called.
        It copies the history up to the last user message, leaving their
        request "open" for the new agent to process.
        """
        try:
            print("ENTERED")
            userdata: UserData = self.session.userdata
            userdata.current_agent = self

            print(f"Reconnect {userdata.reconnect}")

            if userdata.reconnect:
                await self.session.say("", add_to_chat_ctx=True)
                userdata.reconnect = False
                return

            if userdata.initial:
                print("Initial")
                # Check if there's an initial message to use instead of default greeting
                print("S")
                print(userdata.initial_msg)
                print("G")
                if userdata.initial_msg and userdata.initial_msg.strip():
                    print(f"Using initial message: {userdata.initial_msg}")
                    await self.session.generate_reply(
                        user_input=userdata.initial_msg,
                    ).wait_for_playout()
                    userdata.initial_msg = None
                else:
                    if getattr(userdata, "first_time_user", False):
                        await self.session.say(
                            "Hi, I am Bella your personal assistant for friendly conversations. I can also manage your tasks, meetings and search information for you.",
                            add_to_chat_ctx=True,
                        ).wait_for_playout()
                userdata.initial = False
                return
            else:
                print("Not Initial")

            if userdata.prev_agent and userdata.prev_agent.chat_ctx:
                updatable_ctx = self.chat_ctx.copy()
                history_items = userdata.prev_agent.chat_ctx.items
                last_user_message_index = -1
                for i in range(len(history_items) - 1, -1, -1):
                    item = history_items[i]
                    if hasattr(item, "role") and item.role == "user":
                        last_user_message_index = i
                        break

                if last_user_message_index != -1:
                    relevant_history = history_items[: last_user_message_index + 1]
                    truncated_relevant_history = self._truncate_chat_ctx(
                        relevant_history
                    )
                    updatable_ctx.items.extend(truncated_relevant_history)
                    await self.update_chat_ctx(updatable_ctx)

            await self.session.generate_reply()
        except Exception as e:
            print(f"❌ Error in on_enter: {e}")
            import traceback

            print(f"❌ Traceback: {traceback.format_exc()}")

    def _truncate_chat_ctx(self, items: List, keep_last_n: int = 6) -> List:
        return items[-keep_last_n:]

    async def _transfer_to_agent(self, name: str, context: RunContext) -> Agent:
        """
        Helper function to retrieve the next agent and store the current
        agent as the 'previous_agent' for the next on_enter call.
        """
        userdata: UserData = context.userdata
        userdata.prev_agent = self
        next_agent = userdata.personas[name]
        return next_agent

    # async def llm_node(
    #     self,
    #     chat_ctx: llm.ChatContext,
    #     tools: list[FunctionTool | llm.RawFunctionTool],
    #     model_settings: ModelSettings,
    # ) -> AsyncIterable[llm.ChatChunk | str]:
    #     """
    #     Override llm_node to clean duplicate assistant messages before LLM processing.
    #     This prevents the "only one message is allowed in a group" error.
    #     """
    #     # Clean the chat context by merging duplicate assistant messages
    #     cleaned_ctx = self._merge_duplicate_assistant_messages(chat_ctx)

    #     # Call the default implementation with cleaned context
    #     async for chunk in Agent.default.llm_node(
    #         self, cleaned_ctx, tools, model_settings
    #     ):
    #         yield chunk

    # def _merge_duplicate_assistant_messages(
    #     self, chat_ctx: llm.ChatContext
    # ) -> llm.ChatContext:
    #     """
    #     Merge assistant messages with the same group ID to prevent grouping errors.
    #     Keeps the most recent message per group and merges their content.
    #     This prevents the "only one message is allowed in a group" assertion error.
    #     """
    #     if not chat_ctx.items:
    #         return chat_ctx

    #     # Group items by their base ID (before "/")
    #     grouped_items: dict[str, list[llm.ChatItem]] = {}
    #     other_items: list[llm.ChatItem] = []

    #     for item in chat_ctx.items:
    #         if item.type == "message" and item.role == "assistant":
    #             # Extract base ID (part before "/")
    #             group_id = item.id.split("/")[0]
    #             if group_id not in grouped_items:
    #                 grouped_items[group_id] = []
    #             grouped_items[group_id].append(item)
    #         else:
    #             # Keep all non-assistant messages, function calls, and outputs as-is
    #             other_items.append(item)

    #     # Merge messages in each group
    #     merged_items: list[llm.ChatItem] = []

    #     for group_id, messages in grouped_items.items():
    #         if len(messages) == 1:
    #             # No duplicates, keep as-is
    #             merged_items.append(messages[0])
    #         else:
    #             # Multiple messages with same group ID - merge them
    #             # Sort by created_at to keep chronological order
    #             messages.sort(key=lambda m: m.created_at)

    #             # Use the most recent message as the base
    #             base_message = messages[-1]

    #             # Collect all text content from all messages
    #             all_content = []
    #             for msg in messages:
    #                 all_content.extend(msg.content)

    #             # Create merged message with combined content
    #             merged_message = base_message.model_copy()
    #             merged_message.content = all_content

    #             merged_items.append(merged_message)

    #     # Reconstruct chat context maintaining order
    #     # Sort all items by created_at to preserve chronological order
    #     all_items = merged_items + other_items
    #     all_items.sort(key=lambda item: item.created_at)

    #     return ChatContext(items=all_items)

    @function_tool()
    async def web_search_tool(self, queries: list[str]) -> str:
        """
        Use this tool for queries requiring latest updates.
        Accepts multiple queries at once.
        Answer in 10-12 words only unless user explicitly asks otherwise.
        """
        self.session.userdata.tool_invoked = "web_search_tool"

        results = []
        for query in queries:
            logging.info(f"[TOOL] Performing web search for query: {query}")
            result = WebSearch.search(query=query)
            if result.get("success"):
                results.append(result.get("response", "No response text received."))
            else:
                results.append(f"Error during search: {result.get('error')}")

        return " | ".join(results)

    @function_tool()
    async def document_response_tool(self, document_title: str) -> str:
        """
        Use this tool when user asks for document generation, drafting, or content creation tasks.
        Examples: draft an email, provide a job description, write a document, create content.
        This tool will generate a complete document based on the title provided.
        """
        logging.info(
            f"[DEBUG] document_response_tool called with title: '{document_title}'"
        )
        self.session.userdata.tool_invoked = "document_response_tool"

        # Resolve user name from session (from org_participants by user_id)
        user_name = (getattr(self.session.userdata, "user_name", None) or "").strip()

        # Generate complete document using OpenAI GPT-4.1 based on title only
        try:
            from src.utils.openai_env import get_openai_api_key_v2

            api_key = get_openai_api_key_v2()
            if not api_key:
                logging.error(
                    "[ERROR] OPENAI_API_KEY_2 not found in environment variables"
                )
                return "Error generating document: API key not configured"

            logging.info(
                f"[DEBUG] OpenAI API key found (length: {len(api_key)}), initializing client..."
            )
            openai_client = AsyncOpenAI(api_key=api_key)
            logging.info("[DEBUG] OpenAI client initialized successfully")

            user_name_instruction = ""
            if user_name:
                user_name_instruction = f"""
USER NAME FOR DOCUMENT: The document is for the user named **{user_name}**. When the document type requires a sender/author/signer name (e.g. email signature, cover letter sign-off, resume contact name, contract party, greeting recipient field), use this name instead of placeholders like "YOUR NAME", "[Your Name]", or "John Doe". Only substitute where a person's name is clearly required; leave other content as appropriate for the document.
"""

            generation_prompt = f"""Generate ONLY the body/content of the document requested. DO NOT include any title.

Request: {document_title}
{user_name_instruction}
CRITICAL REQUIREMENTS:
1. DO NOT include the title in your output - only generate the body/content
2. Format the content according to the appropriate structure for the document type:
   - EMAIL: Subject line (as heading), greeting, body paragraphs, closing, signature
   - JOB DESCRIPTION: Overview, Responsibilities (bulleted), Requirements (bulleted), Benefits, Location, Salary Range
   - BUSINESS PROPOSAL: Executive Summary, Problem Statement, Solution, Benefits, Implementation Plan, Budget
   - REPORT: Introduction, Findings, Analysis, Conclusions, Recommendations
   - ARTICLE/BLOG: Introduction, body paragraphs with headings, conclusion
   - RESUME/CV: Contact info, Professional Summary, Experience, Education, Skills
   - COVER LETTER: Greeting, opening paragraph, body paragraphs, closing, signature
   - CONTRACT/AGREEMENT: Parties, Terms, Conditions, Signatures section
   - And other appropriate structures based on document type
3. Use proper markdown formatting for structure
4. Make it comprehensive, detailed, and professional
5. Output ONLY the body/content - no title, no opening sentences, no closing sentences, no help text

STRICT MARKDOWN FORMATTING RULES - ONLY USE THESE:
- Headings: # for h1, ## for h2, ### for h3, etc.
- Text/Paragraphs: Regular text content
- Bullet lists: Use - (dash) for unordered lists
- Bold text: **text** for strong/bold
- Italic text: *text* for emphasis/italic
- Line breaks: Use blank lines between sections

ABSOLUTELY FORBIDDEN - DO NOT USE:
- NO tables (no |, no table rows, no table columns, no tabular data at all)
- NO code blocks (no ```code```)
- NO inline code (no `code`)
- NO blockquotes (no >)
- NO horizontal rules (no ---)
- NO title in the output
- NO introductory phrases or sentences
- NO closing phrases or sentences
- NO any other markdown elements not listed above

OUTPUT INSTRUCTIONS:
- Start directly with the document body/content in markdown format
- End directly after the document body/content
- NO title, NO "here is", NO "I've created", NO "Your document is ready", NO any other meta text
- ONLY the structured markdown content body"""

            system_content = (
                "You are a content generation assistant. Generate ONLY the body/content of documents in markdown format. "
                "NEVER include titles, opening sentences, closing sentences, or help text. "
                "Format content according to the appropriate structure for each document type (email structure for emails, JD structure for job descriptions, etc.). "
                "STRICT MARKDOWN RULES: Only use headings (#, ##, ###), text/paragraphs, bullet lists (-), bold (**text**), and italic (*text*). "
                "FORBIDDEN: NO tables, NO code blocks, NO inline code, NO blockquotes, NO horizontal rules, NO titles, NO meta text. "
                "Output starts and ends with the content body only."
            )
            if user_name:
                system_content += (
                    f" When the document requires a sender/author/signer name (e.g. email, cover letter, resume, contract), use the user's name: {user_name}. "
                    "Do not use placeholders like YOUR NAME or [Your Name] where a real name is needed."
                )

            logging.info(
                f"[DEBUG] Calling OpenAI API with model: gpt-4.1, prompt length: {len(generation_prompt)} chars"
            )
            logging.debug(
                f"[DEBUG] Generation prompt preview: {generation_prompt[:200]}..."
            )

            response = await openai_client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": generation_prompt},
                ],
                temperature=0.3,
            )

            logging.info(
                f"[DEBUG] OpenAI API response received - model: {response.model}, usage: {response.usage}"
            )

            if not response.choices or len(response.choices) == 0:
                logging.error("[ERROR] OpenAI response has no choices")
                return "Error generating document: No response from AI"

            if (
                not response.choices[0].message
                or not response.choices[0].message.content
            ):
                logging.error("[ERROR] OpenAI response message content is empty")
                return "Error generating document: Empty response from AI"

            document_text = response.choices[0].message.content.strip()
            logging.info(
                f"[DEBUG] Document generated successfully - length: {len(document_text)} chars"
            )

            print(document_text)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            import traceback

            tb = traceback.format_exc()
            logging.error(
                f"[ERROR] Failed to generate document with OpenAI - "
                f"Type: {error_type}, Message: {error_msg}\n"
                f"Traceback:\n{tb}"
            )
            return f"Error generating document: {error_msg}"

        # Print first 100 chars of document_text
        document_text_preview = (
            document_text[:100] if len(document_text) > 100 else document_text
        )
        logging.info(
            f"[DEBUG] Document text preview (first 100 chars): {document_text_preview}..."
        )

        # Create payload
        logging.info("[DEBUG] Creating data payload...")
        data_payload = {"title": document_title, "text": document_text}
        logging.info(
            f"[DEBUG] Payload dict created - title length: {len(document_title)}, text length: {len(document_text)}"
        )

        try:
            payload_json = json.dumps(data_payload)
            logging.info(
                f"[DEBUG] Payload JSON created - length: {len(payload_json)} chars"
            )
        except Exception as json_err:
            logging.error(f"[ERROR] Failed to serialize payload to JSON: {json_err}")
            return f"Error generating document response: Failed to serialize data"

        try:
            # Pre-flight checks with detailed logging
            logging.info("[DEBUG] Starting pre-flight checks...")

            # Check room availability
            if not hasattr(self, "room"):
                logging.error("[ERROR] self.room attribute does not exist")
                return "Error generating document response: Room not available"

            if self.room is None:
                logging.error("[ERROR] self.room is None")
                return "Error generating document response: Room not available"

            logging.info(
                f"[DEBUG] Room check passed - room type: {type(self.room).__name__}"
            )

            # Check local_participant
            if not hasattr(self.room, "local_participant"):
                logging.error("[ERROR] room.local_participant attribute does not exist")
                return (
                    "Error generating document response: Room participant not available"
                )

            local_participant = self.room.local_participant
            if local_participant is None:
                logging.error("[ERROR] room.local_participant is None")
                return (
                    "Error generating document response: Room participant not available"
                )

            logging.info(
                f"[DEBUG] Local participant check passed - identity: {getattr(local_participant, 'identity', 'unknown')}"
            )

            # Check publish_data method
            if not hasattr(local_participant, "publish_data"):
                logging.error(
                    "[ERROR] local_participant.publish_data method does not exist"
                )
                return "Error generating document response: publish_data method not available"

            logging.info("[DEBUG] publish_data method exists")

            # Check host_name
            if not hasattr(self.session, "userdata"):
                logging.error("[ERROR] self.session.userdata does not exist")
                return (
                    "Error generating document response: Session userdata not available"
                )

            if not hasattr(self.session.userdata, "host_name"):
                logging.error(
                    "[ERROR] self.session.userdata.host_name attribute does not exist"
                )
                return "Error generating document response: Host name attribute not available"

            host_name = self.session.userdata.host_name
            if not host_name:
                logging.error(f"[ERROR] host_name is empty/None - value: {host_name}")
                return "Error generating document response: Host not identified"

            logging.info(f"[DEBUG] Host name check passed - host_name: '{host_name}'")

            # Check room connection
            is_connected = False
            try:
                if hasattr(self.room, "isconnected"):
                    is_connected = self.room.isconnected()
                    logging.info(
                        f"[DEBUG] Room connection check - isconnected(): {is_connected}"
                    )
                else:
                    logging.warning("[DEBUG] Room does not have isconnected() method")
            except Exception as conn_check_err:
                logging.warning(
                    f"[DEBUG] Error checking room connection: {conn_check_err}"
                )

            # Convert payload to bytes (UTF-8 encoding)
            logging.info("[DEBUG] Converting payload to bytes...")
            try:
                payload_bytes = payload_json.encode("utf-8")
                logging.info(
                    f"[DEBUG] Payload converted to bytes - size: {len(payload_bytes)} bytes"
                )
            except Exception as encode_err:
                logging.error(
                    f"[ERROR] Failed to encode payload to bytes: {encode_err}"
                )
                return f"Error generating document response: Encoding failed"

            # Check payload size (15KiB limit for reliable delivery)
            max_size = 15 * 1024
            if len(payload_bytes) > max_size:
                logging.error(
                    f"[ERROR] Payload too large: {len(payload_bytes)} bytes (max {max_size} bytes / 15KiB). "
                    f"Document text length: {len(document_text)} chars"
                )
                return "Error generating document response: Document too large"

            logging.info(
                f"[DEBUG] Payload size check passed: {len(payload_bytes)} bytes <= {max_size} bytes"
            )

            # Prepare destination identities list
            destination_identities = [host_name]
            logging.info(
                f"[DEBUG] Publishing data packet - "
                f"destination_identities: {destination_identities}, "
                f"topic: document_response_agent, "
                f"size: {len(payload_bytes)} bytes, "
                f"reliable: True, "
                f"room connected: {is_connected}"
            )

            # Publish data packet instead of RPC
            logging.info("[DEBUG] Calling publish_data()...")
            await local_participant.publish_data(
                payload_bytes,
                reliable=True,  # Use reliable delivery for guaranteed delivery
                destination_identities=destination_identities,  # List of identities
                topic="document_response_agent",  # Topic to identify the data type
            )
            logging.info("[DEBUG] publish_data() call completed successfully")

            # Print success message with title and first 100 chars
            logging.info(
                f"[SUCCESS] Data packet successfully published - "
                f"title: '{document_title}', "
                f"text preview: {document_text_preview}..., "
                f"payload size: {len(payload_bytes)} bytes"
            )

            return "Only inform the user that document response generated successfully, do not output the document text itself"

        except AttributeError as e:
            error_msg = str(e)
            error_type = type(e).__name__
            import traceback

            tb = traceback.format_exc()
            logging.error(
                f"[ERROR] AttributeError in document_response_agent data packet - "
                f"Type: {error_type}, Message: {error_msg}\n"
                f"Traceback:\n{tb}"
            )
            if "room" in error_msg.lower() or "local_participant" in error_msg.lower():
                return "Error generating document response: Room connection issue"
            return f"Error generating document response: {error_msg}"

        except ValueError as e:
            error_msg = str(e)
            error_type = type(e).__name__
            import traceback

            tb = traceback.format_exc()
            logging.error(
                f"[ERROR] ValueError in document_response_agent data packet - "
                f"Type: {error_type}, Message: {error_msg}\n"
                f"Traceback:\n{tb}"
            )
            return f"Error generating document response: {error_msg}"

        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            import traceback

            tb = traceback.format_exc()

            # Get additional context
            room_connected = "unknown"
            try:
                if hasattr(self, "room") and self.room:
                    if hasattr(self.room, "isconnected"):
                        room_connected = self.room.isconnected()
            except:
                pass

            host_name_debug = "unknown"
            try:
                if hasattr(self.session, "userdata") and hasattr(
                    self.session.userdata, "host_name"
                ):
                    host_name_debug = self.session.userdata.host_name or "None/empty"
            except:
                pass

            logging.error(
                f"[ERROR] Failed to publish document_response_agent data packet - "
                f"Type: {error_type}, Message: {error_msg}, "
                f"Room connected: {room_connected}, "
                f"Host: {host_name_debug}, "
                f"Payload size: {len(payload_bytes) if 'payload_bytes' in locals() else 'N/A'} bytes\n"
                f"Traceback:\n{tb}"
            )
            return f"Error generating document response: {error_msg}"

    # @function_tool()
    # async def get_daily_overview(
    #     self, date: str, include_completed_todos: bool = True
    # ) -> str:
    #     """
    #     Get a comprehensive overview of a specific day including both calendar events and todos.
    #     Perfect for questions like "How does my Tuesday look?" or "What's planned for tomorrow?"

    #     Args:
    #         date: Date in YYYY-MM-DD format (e.g., '2025-09-05')
    #         include_completed_todos: Whether to include completed tasks in the overview

    #     Returns:
    #         A formatted overview combining calendar events and todos for the specified day
    #     """
    #     self.session.userdata.tool_invoked = "get_daily_overview"
    #     logging.info(f"[TOOL] Getting daily overview for date: {date}")

    #     # Validate date format
    #     try:
    #         target_date = datetime.strptime(date, "%Y-%m-%d")
    #     except ValueError:
    #         return f"❌ Invalid date format: {date}. Please use YYYY-MM-DD format (e.g., '2025-09-05')."

    #     # Create time range for the entire day
    #     start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    #     end_of_day = start_of_day + timedelta(days=1) - timedelta(microseconds=1)

    #     # Format for calendar API (RFC 3339 with Z suffix)
    #     start_time_rfc = start_of_day.isoformat() + "Z"
    #     end_time_rfc = end_of_day.isoformat() + "Z"

    #     overview_parts = []
    #     overview_parts.append(
    #         f"📅 **Daily Overview for {target_date.strftime('%A, %B %d, %Y')}**\n"
    #     )

    #     # speech_handle = self.session.say(text="Just give me a minute", add_to_chat_ctx=False)
    #     # Get calendar events

    #     calendar_rpc_payload = None
    #     calendar_formatted_result = None
    #     todos_rpc_payload = None
    #     todos_formatted_result = None

    #     if self.session.userdata.access_token:
    #         calendar_formatted_result, calendar_rpc_payload = (
    #             await self._get_calendar_events_for_day(start_time_rfc, end_time_rfc)
    #         )

    #         print(f"Calendar RPC Payload")
    #         print(json.dumps(calendar_rpc_payload))

    #         response = await self.room.local_participant.perform_rpc(
    #             destination_identity=self.session.userdata.host_name,
    #             method="fetch_agendas_agent",
    #             payload=json.dumps(calendar_rpc_payload),
    #         )

    #     else:
    #         calendar_formatted_result = (
    #             "Cannot find any meetings or events as your calendar is not integrated"
    #         )

    #     overview_parts.append("🗓️ **Calendar Events:**")

    #     calendar_speech_handle = self.session.generate_reply(
    #         user_input=calendar_formatted_result,
    #         instructions="The user asked their meetings and tasks this is just telling them their meetings tasks will be told in a while",
    #     )
    #     await calendar_speech_handle.wait_for_playout()

    #     overview_parts.append(calendar_formatted_result)

    #     overview_parts.append("")  # Empty line separator

    #     # Get todos for the day
    #     todos_formatted_result, todos_rpc_payload = await self._get_todos_for_day(
    #         date, include_completed_todos
    #     )

    #     print(f"Todos RPC Payload")
    #     print(json.dumps(todos_rpc_payload))

    #     response = await self.room.local_participant.perform_rpc(
    #         destination_identity=self.session.userdata.host_name,
    #         method="fetch_todos_agent",
    #         payload=json.dumps(todos_rpc_payload),
    #     )

    #     overview_parts.append("📋 **Tasks & Todos:**")

    #     todos_speech_handle = self.session.generate_reply(
    #         user_input=todos_formatted_result,
    #         instructions="The user asked their meetings and tasks, user have been already told their meetings now this is only their tasks",
    #     )
    #     await todos_speech_handle.wait_for_playout()

    #     overview_parts.append(todos_formatted_result)

    #     # await speech_handle.wait_for_playout()
    #     # return "\n".join(overview_parts)
    #     return "The function executed successfully and the user already know their tasks and meetings for today so you dont need to mention it, don`t even mention to user that they have been told the tasks you only reply this to user = Have a good day"

    @function_tool()
    async def get_daily_overview(
        self, date: str, include_completed_todos: bool = True
    ) -> str:

        try:
            # If full ISO string
            if "T" in date:
                # Replace Z for Python parsing
                cleaned = date.replace("Z", "+00:00")
                parsed_dt = datetime.fromisoformat(cleaned)
                normalized_date = parsed_dt.date().strftime("%Y-%m-%d")
            else:
                # Already YYYY-MM-DD or equivalent
                normalized_date = datetime.strptime(date, "%Y-%m-%d").strftime(
                    "%Y-%m-%d"
                )

            date = normalized_date  # overwrite input variable
        except Exception:
            return (
                f"❌ Invalid date format: {date}. Please use YYYY-MM-DD format "
                "(for example 2025-09-05)."
            )
        """
        Get a comprehensive overview of a specific day including both calendar events and todos.
        Perfect for questions like "How does my Tuesday look?" or "What's planned for tomorrow?"

        Args:
            date: Date in YYYY-MM-DD format (e.g., '2025-09-05')
            include_completed_todos: Whether to include completed tasks in the overview

        Returns:
            A formatted overview combining calendar events and todos for the specified day
        """
        try:
            self.session.userdata.tool_invoked = "get_daily_overview"
            include_completed_todos = True
            logging.info(f"[TOOL] Getting daily overview for date: {date}")

            # ---- Validate date format ----
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return f"❌ Invalid date format: {date}. Please use YYYY-MM-DD (e.g., 2025-09-05)."

            # ---- Build time window in RFC format ----
            start_of_day = target_date.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_of_day = start_of_day + timedelta(days=1) - timedelta(microseconds=1)
            start_time_rfc = start_of_day.isoformat() + "Z"
            end_time_rfc = end_of_day.isoformat() + "Z"

            # ---- Fetch agendas (calendar events) — NO RPC YET ----
            agenda_str, agenda_payload = await self._get_calendar_events_for_day(
                start_time_rfc, end_time_rfc
            )

            # ---- Fetch todos — NO RPC YET ----
            todo_str, todo_payload = await self._get_todos_for_day(
                date, include_completed_todos
            )

            # ---- Build unified RPC payload (old behavior) ----
            # Handle NOT_CONNECTED marker for agendas
            if agenda_payload == "NOT_CONNECTED":
                agendas_value = "NOT_CONNECTED"
            else:
                agendas_value = agenda_payload if agenda_payload else []

            rpc_payload = {
                "agendas": agendas_value,
                "todos": todo_payload.get("tasks", []) if todo_payload else [],
            }

            print("JSON of overview")
            print(json.dumps(rpc_payload))

            # with open("daily_overview_payload.json", "w", encoding="utf-8") as f:
            #     json.dump(rpc_payload, f, ensure_ascii=False, indent=2)

            # ---- ALWAYS perform RPC ----
            try:
                _ = await self.room.local_participant.perform_rpc(
                    destination_identity=self.session.userdata.host_name,
                    method="fetch_daily_overview_agent",
                    payload=json.dumps(rpc_payload),
                )
                pass
            except Exception as rpc_err:
                # don't break UI formatting just because RPC failed
                logging.error(f"[RPC ERROR] fetch_daily_overview_agent: {rpc_err}")

            # ---- FINAL OUTPUT TO USER (formatted) ----
            final_result = (
                f"📅 **Daily overview for {date}**\n\n"
                f"--- 📌 Agendas ---\n"
                f"{agenda_str}\n\n"
                f"--- 📝 Todos ---\n"
                f"{todo_str}"
            )

            return final_result

        except Exception as err:
            logging.error(f"[get_daily_overview ERROR] {err}")
            return "❌ Something went wrong while fetching your overview. Please try again."

    async def _try_fetch_and_verify_token(self) -> str | None:
        """
        Try to fetch and verify calendar access token once.
        Parses API response as token string or { "token", "type" }; verifies with Google or Outlook based on userdata.calendar_type.
        Returns token if successful, None otherwise.
        """
        try:
            userdata: UserData = self.session.userdata
            user_id = getattr(userdata, "user_id", None)
            if not user_id:
                return None

            M2M_TOKEN = os.getenv("M2M_TOKEN")
            if not M2M_TOKEN:
                return None

            payload = {"userIds": [user_id]}
            headers = {
                "Authorization": f"Bearer {M2M_TOKEN}",
                "Content-Type": "application/json",
            }
            environment = getattr(userdata, "environment", None)
            api_url = (
                os.getenv("QLU_M2M_CALENDAR_API_URL_STAGING")
                if environment == "STAGING"
                else os.getenv("QLU_M2M_CALENDAR_API_URL")
            )
            if not api_url:
                return None

            response = requests.post(api_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            payload_list = data.get("data") or []
            if not isinstance(payload_list, list):
                return None
            raw = None
            for item in payload_list:
                if str(item.get("userId")) == str(user_id):
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

            org_participants = getattr(userdata, "org_participants", [])
            user = next(
                (u for u in org_participants if str(u.get("id")) == str(user_id)), None
            )
            if not user or not user.get("email"):
                return None
            expected_email = user["email"].strip().lower()

            calendar_type = api_type

            if calendar_type == "outlook":
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
                try:
                    userdata.calendar_type = calendar_type
                except Exception:
                    pass
                return calendar_token
            return None
        except Exception as e:
            logging.error(f"⚠️ Failed to fetch and verify token: {e}")
            return None

    async def _get_calendar_events_for_day(self, start_time: str, end_time: str):
        """Helper method to get calendar events for a specific day."""
        try:
            userdata: UserData = self.session.userdata

            # Check access_token, try fetching once if missing
            if not userdata.access_token:
                new_token = await self._try_fetch_and_verify_token()
                if new_token:
                    userdata.access_token = new_token
                else:
                    # Still no token - return NOT_CONNECTED marker
                    return "   ⚠️ Calendar is not connected.", "NOT_CONNECTED"

            # Get calendar manager. Use userdata.calendar_type (current context) so Outlook/Google choice works
            # when called from TodoAgent (calendar_agent.session may be unset when not active).
            calendar = None
            ct = (getattr(userdata, "calendar_type", None) or "google").lower()
            if hasattr(userdata, "calendar_manager") and userdata.calendar_manager:
                obj = userdata.calendar_manager
                if hasattr(obj, "outlook_calendar") and hasattr(obj, "google_calendar"):
                    calendar = (
                        obj.outlook_calendar if ct == "outlook" else obj.google_calendar
                    )
                else:
                    calendar = obj.calendar if hasattr(obj, "calendar") else obj
            elif hasattr(userdata, "personas") and "calendar" in userdata.personas:
                calendar_agent = userdata.personas["calendar"]
                if hasattr(calendar_agent, "outlook_calendar") and hasattr(
                    calendar_agent, "google_calendar"
                ):
                    calendar = (
                        calendar_agent.outlook_calendar
                        if ct == "outlook"
                        else calendar_agent.google_calendar
                    )
                else:
                    calendar = (
                        calendar_agent.calendar
                        if hasattr(calendar_agent, "calendar")
                        else calendar_agent
                    )
                userdata.calendar_manager = calendar_agent
            if not calendar:
                return "   ⚠️ Calendar is not connected", "NOT_CONNECTED"

            events = await calendar.list_events(
                user_calendar_access_token=userdata.access_token,
                time_min=start_time,
                time_max=end_time,
            )

            items = events.get("items", [])
            if not items:
                return "   🟢 No events scheduled for this day.", []

            # Build email → name lookup (try to import participants_list)
            participant_lookup = {}
            try:
                participant_lookup = {p["email"]: p["name"] for p in participants_list}
            except ImportError:
                pass

            formatted = []
            filtered_events = []

            for i, e in enumerate(items, 1):
                if e.get("eventType") == "workingLocation":
                    continue  # skip all-day office / working location events

                summary = e.get("summary", "Untitled")
                start = e.get("start", {}).get("dateTime") or e.get("start", {}).get(
                    "date", "N/A"
                )
                end = e.get("end", {}).get("dateTime") or e.get("end", {}).get(
                    "date", "N/A"
                )

                # Format time display (remove date part if it's the same day)
                if "T" in str(start) and start != "N/A":
                    start_time_only = start.split("T")[1][:5]  # HH:MM
                    end_time_only = (
                        end.split("T")[1][:5]
                        if "T" in str(end) and end != "N/A"
                        else end
                    )
                    time_range = f"{start_time_only} → {end_time_only}"
                else:
                    time_range = "All day"

                # Extract attendees
                attendees = e.get("attendees", [])
                if attendees:
                    participants = []
                    for a in attendees:
                        email = a.get("email", "")
                        name = participant_lookup.get(email, email)
                        participants.append(name)
                    participants_str = f" | 👥 {', '.join(participants)}"
                else:
                    participants = []
                    participants_str = ""

                # Determine platform name (match list_events)
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

                # Append formatted text
                formatted.append(
                    f"   {i}. 🕒 {time_range} - {summary}{participants_str} | 💻 {platform}"
                )

                # Append structured event (match list_events RPC payload)
                filtered_events.append(
                    {
                        "id": e.get("id"),
                        "title": summary,
                        "startTime": start,
                        "endTime": end,
                        "participants": attendees,
                        "platform": platform,
                        "colorId": e.get("colorId", "1"),
                    }
                )

            # Sanitize RPC payload

            rpc_payload = sanitize_for_rpc(filtered_events)

            return "\n".join(formatted), rpc_payload

        except Exception as e:
            logging.error(f"[ERROR] Failed to get calendar events: {e}")
            return f"   ❌ Error fetching calendar events: {str(e)}", []

    def _parse_deadline_to_date(self, deadline_val) -> Optional[date]:
        """Convert different deadline formats into a date object safely with timezone handling."""
        if not deadline_val:
            return None

        # Already a datetime object
        if isinstance(deadline_val, datetime):
            # If timezone-aware, convert to local timezone for date comparison
            if deadline_val.tzinfo is not None:
                local_tz = pytz.timezone("Asia/Karachi")
                return deadline_val.astimezone(local_tz).date()
            else:
                # If naive, assume it's already in local timezone
                return deadline_val.date()

        # String formats
        if isinstance(deadline_val, str):
            try:
                # Handle ISO with Zulu (Z)
                if deadline_val.endswith("Z"):
                    dt = datetime.fromisoformat(deadline_val.replace("Z", "+00:00"))
                    local_tz = pytz.timezone("Asia/Karachi")
                    return dt.astimezone(local_tz).date()
                # Handle ISO with timezone offset (e.g. +00:00)
                if "T" in deadline_val:
                    dt = datetime.fromisoformat(deadline_val)
                    if dt.tzinfo is not None:
                        local_tz = pytz.timezone("Asia/Karachi")
                        return dt.astimezone(local_tz).date()
                    else:
                        return dt.date()
                # Handle plain YYYY-MM-DD
                return datetime.strptime(deadline_val, "%Y-%m-%d").date()
            except Exception as e:
                logging.warning(f"Could not parse deadline '{deadline_val}': {e}")
                return None

        return None

    async def _get_todos_for_day(self, date: str, include_completed: bool):
        """Helper method to get todos for a specific day, returning both formatted text and sanitized RPC payload."""
        try:
            userdata = self.session.userdata

            # Get todo client
            todo_client = getattr(userdata, "todo_client", None)
            if (
                not todo_client
                and hasattr(userdata, "personas")
                and "todo" in userdata.personas
            ):
                todo_agent = userdata.personas["todo"]
                if hasattr(todo_agent, "in_memory_todo_client"):
                    todo_client = todo_agent.in_memory_todo_client
                    userdata.todo_client = todo_client

            if not todo_client:
                return "   ⚠️ Todo functionality not available.", {"tasks": []}

            # Get all todos
            all_tasks = await todo_client.get_todos(include_completed=include_completed)
            if not all_tasks:
                return "   🟢 No tasks for this day.", {"tasks": []}

            # Parse input date
            filter_date = datetime.strptime(date, "%Y-%m-%d").date()

            # Filter tasks by deadline
            day_tasks = []
            for task in all_tasks:
                deadline_date = self._parse_deadline_to_date(task.deadline)
                if deadline_date == filter_date:
                    day_tasks.append(task)

            if not day_tasks:
                return "   🟢 No tasks scheduled for this day.", {"tasks": []}

            # Separate completed and pending tasks
            pending_tasks = [task for task in day_tasks if not task.completed]
            completed_tasks = [task for task in day_tasks if task.completed]

            # --- Build formatted string ---
            result = []

            if pending_tasks:
                result.append("   📌 **Pending:**")
                for i, task in enumerate(pending_tasks, 1):
                    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
                    emoji = priority_emoji.get(task.priority, "🟡")
                    result.append(f"     {i}. {emoji} {task.title}")
                    if task.note:
                        result.append(f"        📝 {task.note}")

            if include_completed and completed_tasks:
                result.append("   ✅ **Completed:**")
                for task in completed_tasks:
                    result.append(f"     • ✓ {task.title}")

            formatted_output = "\n".join(result)

            # --- Build RPC payload using shared sanitizer ---
            rpc_payload = {"tasks": [sanitize_for_rpc(asdict(t)) for t in day_tasks]}

            return formatted_output, rpc_payload

        except Exception as e:
            logging.error(f"[ERROR] Failed to get todos: {e}")
            return f"   ❌ Error fetching todos: {str(e)}", {"tasks": []}
