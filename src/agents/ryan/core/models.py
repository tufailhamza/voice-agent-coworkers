import copy
from google.genai import types
from groq import Groq
from livekit.agents import (
    ModelSettings,
    llm,
    FunctionTool,
    Agent,
    JobContext,
    AgentSession,
    function_tool,
    RunContext,
    BackgroundAudioPlayer,
    stt,
    ConversationItemAddedEvent,
)
from typing import AsyncIterable, Optional
import asyncio
import sys
from pathlib import Path
import dotenv
from collections import defaultdict
from datetime import datetime
import json
from livekit import rtc
from typing import TypeVar, Generic
from livekit.plugins import silero
from agents.ryan.networking.socket import RyanClient

src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))
from aiohttp import ClientSession
from src.agents.ryan.core.prompts import RYAN_SYSTEM_PROMPT
from src.agents.ryan.core.session_storage import UserData
from src.utils.logger import Logger
from src.agents.ryan.core.utils import is_english, translate
from src.agents.ryan.core.prompts import SUGGESTIONS_PROMPT
import time
from src.agents.ryan.core.memory import RyanRecallManager
import os
import src.agents.ryan.core.tools as ryan_tools

logging = Logger(__name__)
import re


def clean_markdown_formatting(text: str) -> str:
    if not text:
        return text

    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = text.strip()
    return text


class BaseRyanAgent(Agent):

    Modality = types.Modality.AUDIO

    def __init__(
        self,
        instructions: str,
        ctx: JobContext,
        host_name: str,
        user_id: str,
        socket_client: RyanClient,
        cdp_url: str,
    ):
        super().__init__(
            instructions=instructions,
        )
        self.ctx = ctx
        self.host_name = host_name
        self.user_id = user_id
        self.search_history = RyanRecallManager(user_id)
        self.socket_client = socket_client
        self.socket_client.on("operator.say", self.operator_say)
        self.socket_client.on("operator.converse", self.operator_converse)
        self.cdp_url = cdp_url
        self.room = ctx.room

    async def on_enter(self):
        # self.search_history.start_background_db_fetch()
        await self.session.generate_reply(instructions="Greet the user")
        await self.socket_client.connect(cdp_url=self.cdp_url)
        logging.info(f"connected to socket")
        logging.info(f"sending CDP url {self.cdp_url}")
        await self.socket_client.emit(
            "operator.execute/aisearch",
            {
                "query": "please run a search for ai engineer working at amazon and select the top 2 "
            },
        )
        #

    async def operator_say(self, response):
        """Handle operator say requests via socket; parses dict payloads with 'message' key"""
        print(response)
        try:
            if isinstance(response, dict):
                message = (
                    response.get("response") or response.get("message") or str(response)
                )
            else:
                message = str(response)

            if not message:
                logging.warning("❌ No message received in operator.say payload")
                return
            message = clean_markdown_formatting(message)
            self.session.say(message, allow_interruptions=True)

            logging.info(f"🔶 Operator said: {message}")

            # Reset tool_called flag when tool completes (operator_say indicates completion)
            if hasattr(self.session, "set_tool_running"):
                self.session.set_tool_running(False)
                logging.info("🔄 Tool execution completed, reset tool_called flag")

            try:
                rpc_response = await self.ctx.room.local_participant.perform_rpc(
                    destination_identity=self.host_name,
                    method="operator_complete",
                    payload="operator complete",
                )
                logging.info(f"🔗 RPC call successful - operator ended: {rpc_response}")
            except Exception as e:
                logging.error(f"❌ RPC call failed for operator said: {e}")
        except Exception as e:
            logging.error(f"❌ Error in operator_say: {e}")

    async def operator_converse(self, payload):
        """Handle operator.converse events for real-time reasoning updates"""
        try:
            if isinstance(payload, dict):
                message = (
                    payload.get("message")
                    or payload.get("response")
                    or json.dumps(payload)
                )
            else:
                message = str(payload)
            if message:
                message = clean_markdown_formatting(message)
                # self.session.say(
                #     message,
                #     allow_interruptions=True
                # )

                # await self.socket_client.emit("operator.execute/cancel_agent", {"query": "aefeje"})
                logging.info(f"🟠 Operator converse: {message}")
        except Exception as e:
            logging.error(f"❌ Error in operator_converse: {e}")

    async def on_exit(self):
        logging.info(f"🔄 Starting cleanup for user {self.user_id}")

        try:
            # 1. Cancel background database save task
            if (
                hasattr(self.search_history, "_db_save_task")
                and self.search_history._db_save_task
            ):
                logging.info("🔄 Cancelling background database save task")
                self.search_history._db_save_task.cancel()

            # 2. Wait for database save to complete with timeout
            if hasattr(self.search_history, "wait_for_save_complete"):
                try:
                    await asyncio.wait_for(
                        self.search_history.wait_for_save_complete(timeout=5.0),
                        timeout=6.0,
                    )
                    logging.info("✅ Database save completed")
                except asyncio.TimeoutError:
                    logging.warning(
                        "⚠️ Database save timed out, continuing with cleanup"
                    )
                except Exception as e:
                    logging.error(f"❌ Error waiting for database save: {e}")

            # 3. Cleanup socket client (includes proper aiohttp session cleanup)
            if self.socket_client:
                try:
                    logging.info("🔄 Cleaning up socket client")
                    await self.socket_client.cleanup()
                    logging.info("✅ Socket client cleaned up")
                except Exception as e:
                    logging.error(f"❌ Error cleaning up socket: {e}")

            # 4. Cleanup search history
            if hasattr(self.search_history, "cleanup"):
                try:
                    await self.search_history.cleanup()
                    logging.info("✅ Search history cleaned up")
                except Exception as e:
                    logging.error(f"❌ Error cleaning up search history: {e}")

            # 5. Say goodbye
            try:

                logging.info("✅ Goodbye message sent")
            except Exception as e:
                logging.error(f"❌ Error sending goodbye: {e}")

            logging.info(f"✅ Cleanup completed for user {self.user_id}")

        except Exception as e:
            logging.error(f"❌ Error during cleanup: {e}")
            # Continue with cleanup even if some parts fail

    # https://docs.livekit.io/agents/build/nodes/#transcription-node
    async def transcription_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[str]:
        _text_accumulator = ""
        async for delta in text:
            _text_accumulator += delta
            yield delta

        if self.session.input.audio_enabled is True:
            pass
        else:
            suggestions = await self._generate_suggestions(_text_accumulator)
            if suggestions:
                yield f"{suggestions}"

    async def start_listening(self):
        self.session.input.set_audio_enabled(True)
        self.session.output.set_audio_enabled(True)
        response = await self.session.room.local_participant.perform_rpc(
            destination_identity=self.host_name,
            method="start_listening",
            payload="",
        )
        logging.info(f"🔄 audio enabled: {self.session.input.audio_enabled} {response}")

    async def stop_listening(self):
        self.session.input.set_audio_enabled(False)
        self.session.output.set_audio_enabled(False)
        response = await self.session.room.local_participant.perform_rpc(
            destination_identity=self.host_name,
            method="stop_listening",
            payload="",
        )
        logging.info(f"🔄 audio enabled: {self.session.input.audio_enabled} {response}")

    async def _generate_suggestions(self, text: str):
        try:
            client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            prompt = (
                f"{SUGGESTIONS_PROMPT}\n\nSentence to generate suggestions for: {text}"
            )

            response = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.3,
                seed=4594,
            )
            return response.choices[0].message.content

        except Exception as e:
            logging.error(f"Error generating suggestions: {e}")
            return []


Userdata_T = TypeVar("Userdata_T")


class RyanAgentSession(AgentSession[Userdata_T], Generic[Userdata_T]):
    def __init__(self, *args, **kwargs):
        # Initialize userdata before calling super().__init__
        if "userdata" not in kwargs:
            kwargs["userdata"] = UserData()
        super().__init__(*args, **kwargs)

    def can_execute_tool(self) -> bool:
        """Check if a tool can be executed (not already running)"""
        return not self.userdata.tool_called

    def set_tool_running(self, running: bool = True) -> None:
        """Set the tool_called flag to indicate if a tool is running"""
        self.userdata.tool_called = running

    def _conversation_item_added(self, message: llm.ChatMessage) -> None:
        self._chat_ctx.insert(message)
        text = message.text_content

        self.emit("conversation_item_added", ConversationItemAddedEvent(item=message))
