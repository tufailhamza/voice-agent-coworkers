import copy
from google.genai import types
from agents import ryan
from agents.ryan.core.models import BaseRyanAgent

from agents.ryan.core.utils import is_english, translate
from agents.ryan.networking.socket import RyanClient
from src.agents.ryan.core.prompts import SUGGESTIONS_PROMPT
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
import re

src_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_path))
from aiohttp import ClientSession
from src.agents.ryan.core.prompts import RYAN_SYSTEM_PROMPT
from src.agents.bella.shared import UserData
from src.utils.logger import Logger
import time
from src.agents.ryan.core.memory import RyanRecallManager
import os

logging = Logger("ryan.tools")


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


class RyanAgent(BaseRyanAgent):

    @function_tool()
    async def run_ai_search(
        self, context: RunContext, user_query: str, title: str
    ) -> str:
        """
        This tool call initates search by writing the user query in the search bar on the browser window
        Args:
            user_query (str): Full Query user has instructed to search for.
            title (str): designation of people we are searching for.
        """
        # Check if a tool is already running
        if not self.session.can_execute_tool():
            logging.info("🔄 Tool already running, skipping AI search")

            return "SINCE I AM ALREADY WORKING CANT PROCESS THIS REQUEST AT THE MOMENT"

        # Set tool as running
        self.session.set_tool_running(True)
        logging.info(f"\033[93mRunning AI search for: {user_query}\033[0m")

        # Make RPC call to notify that AI search is starting
        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="operator_started",
                payload=f"operator started : {user_query}",
            )
            logging.info(f"RPC call successful - operator started : {user_query}")
        except Exception as e:
            logging.error(f"❌ RPC call failed for operator started: {e}")
        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="ai_search_started",
                payload=f"{title}",
            )
            logging.info(f"🔗 RPC call successful - AI search started: {user_query}")
        except Exception as e:
            logging.error(f"❌ RPC call failed for ai_search_started: {e}")

        try:
            response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="webagent_status",
                payload="Initiating Search",
            )
            logging.info(f"Web agent STATUS: Initiating AI search")
        except Exception as e:
            logging.error(f"❌ RPC call failed for webagent_status: {e}")

        await self.socket_client.emit(
            "operator.execute/aisearch", {"query": user_query}
        )

    @function_tool()
    async def handle_general_web_query(self, context: RunContext, command: str) -> str:
        """
        Handles normmal Task execution from the the user on the Qlu AI search window
         Args:
            command (str): Exact command user want to execute on the web browser

        Returns:
            str: Formatted query for the socket client
        """
        # Check if a tool is already running
        if not self.session.can_execute_tool():
            logging.info("🔄 Tool already running, skipping general web query")

            return "SINCE I AM ALREADY WORKING CANT PROCESS THIS REQUEST AT THE MOMENT"

        # Set tool as running
        self.session.set_tool_running(True)
        logging.info(f"\033[93mHandling user command for: {command} \033[0m")
        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="operator_started",
                payload=f"operator started",
            )
            logging.info(f"RPC call successful - operator started ")
        except Exception as e:
            logging.error(f"RPC call failed for operator started: {e}")

        try:
            response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="webagent_status",
                payload="Applying Filters",
            )
            logging.info(f"Web agent STATUS: Applying Filters")
        except Exception as e:
            logging.error(f"❌ RPC call failed for webagent_status: {e}")
        await self.socket_client.emit(
            "operator.execute/general_queries", {"query": command}
        )

    @function_tool()
    async def select_candidates(
        self, context: RunContext, candidate_selection_query: str
    ) -> str:
        """
        This tool initiates the process of selection of candidates and assigns them to an assignment on the QLU AI search UI.
        Supports multiple dynamic selection formats for flexible candidate selection.
        please write the entire query in the candidate_selection_query Like Select the top 5 profiles with full sentence

        Args:
            candidate_selection_query (str): Selection criteria in various formats:
                - Specific names: "John Doe, Jane Smith"
                - Top selection: "top 5 profiles", "top 3 candidates"
                - Ordinal selection: "5th profile", "3rd candidate", "1st profile"
                - Simple count: "5 profiles", "3 candidates"
                - Range selection: "profiles 1-5", "candidates 1 to 3"
                - Position-based: "first 3 profiles", "last 2 candidates"
                - assignment_names : Assignment names to save the selected candidates to optional if the user doesnt specify THEN PASS NONE
        """
        logging.info(
            f"\033[93mRunning candidate selection for: {candidate_selection_query}\033[0m"
        )

        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="operator_started",
                payload=f"operator started",
            )
            logging.info(f"RPC call successful - operator started ")
        except Exception as e:
            logging.error(f"RPC call failed for operator started: {e}")
        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="candidate_selection",
                payload="cadidate selection started",
            )
            logging.info(f"🔗 RPC call successful - candidate selection started:")

        except Exception as e:
            logging.error(f"❌ RPC call failed for candidate selection started: {e}")

        try:
            response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="webagent_status",
                payload="Selecting Candidates",
            )
            logging.info(f"Web agent STATUS: Selecting Candidates")
        except Exception as e:
            logging.error(f"❌ RPC call failed for webagent_status: {e}")
        #     # Use existing connected socket client instead of creating new one
        await self.socket_client.emit(
            "operator.select/candidates", {"query": candidate_selection_query}
        )

    @function_tool()
    async def clarification_query(
        self, context: RunContext, clarification_query: str
    ) -> str:
        """
        This tool initiates the process of selection of candidates and assigns them to an assignment on the QLU AI search UI.
        Supports multiple dynamic selection formats for flexible candidate selection.

        Args:
            clarification_query (str): Clarification instruction from the previous ai search query
        """
        # Check if a tool is already running
        if not self.session.can_execute_tool():
            logging.info("🔄 Tool already running, skipping candidate selection")
            await self.session.say(
                "I'm already working on something. Please wait for me to finish."
            )
            return "SINCE I AM ALREADY WORKING CANT PROCESS THIS REQUEST AT THE MOMENT"

        # Set tool as running
        self.session.set_tool_running(True)
        logging.info(
            f"\033[93mRunning candidate selection for: {clarification_query}\033[0m"
        )
        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="operator_started",
                payload=f"operator started",
            )
            logging.info(f"RPC call successful - operator started ")
        except Exception as e:
            logging.error(f"RPC call failed for operator started: {e}")
        try:
            rpc_response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="candidate_selection",
                payload="cadidate selection started",
            )
            logging.info(f"🔗 RPC call successful - candidate selection started:")

        except Exception as e:
            logging.error(f"❌ RPC call failed for candidate selection started: {e}")

        try:
            response = await self.ctx.room.local_participant.perform_rpc(
                destination_identity=self.host_name,
                method="webagent_status",
                payload="Selecting Candidates",
            )
            logging.info(f"Web agent STATUS: Selecting Candidates")
        except Exception as e:
            logging.error(f"❌ RPC call failed for webagent_status: {e}")
        #     # Use existing connected socket client instead of creating new one
        await self.socket_client.emit(
            "operator.select/general_queries", {"query": clarification_query}
        )

    def _build_candidate_selection_query(
        self, candidate_selection: str, assignment_names: str
    ) -> str:
        """
        Build the appropriate query based on the candidate selection format.

        Args:
            candidate_selection (str): Can be:
                - Specific names (e.g., "John Doe, Jane Smith")
                - Top selection format (e.g., "top 5 profiles", "top 3 candidates")
                - Ordinal selection (e.g., "5th profile", "3rd candidate", "1st profile")
                - Mixed formats
            assignment_names (str): Assignment names

        Returns:
            str: Formatted query for the socket client
        """
        import re

        # Normalize the input
        selection_lower = candidate_selection.lower().strip()

        # Check for "top x profiles/candidates" format
        top_match = re.search(r"top\s+(\d+)\s+(profiles?|candidates?)", selection_lower)
        if top_match:
            number = top_match.group(1)
            entity_type = top_match.group(2)
            return f"Select top {number} {entity_type}  {assignment_names}"

        # Check for ordinal format (1st, 2nd, 3rd, 4th, 5th, etc.)
        ordinal_match = re.search(
            r"(\d+)(?:st|nd|rd|th)\s+(profiles?|candidates?)", selection_lower
        )
        if ordinal_match:
            number = ordinal_match.group(1)
            entity_type = ordinal_match.group(2)
            return f"Select {number}th {entity_type} {assignment_names}"

        # Check for simple number + entity format (e.g., "5 profiles", "3 candidates")
        simple_match = re.search(r"^(\d+)\s+(profiles?|candidates?)$", selection_lower)
        if simple_match:
            number = simple_match.group(1)
            entity_type = simple_match.group(2)
            return f"Select {number} {entity_type} {assignment_names}"

        # Check for range format (e.g., "profiles 1-5", "candidates 1 to 3")
        range_match = re.search(
            r"(profiles?|candidates?)\s+(\d+)\s*(?:-|to)\s*(\d+)", selection_lower
        )
        if range_match:
            entity_type = range_match.group(1)
            start_num = range_match.group(2)
            end_num = range_match.group(3)
            return f"Select {entity_type} {start_num} to {end_num} {assignment_names}"

        # Check for "first x" or "last x" format
        first_last_match = re.search(
            r"(first|last)\s+(\d+)\s+(profiles?|candidates?)", selection_lower
        )
        if first_last_match:
            position = first_last_match.group(1)
            number = first_last_match.group(2)
            entity_type = first_last_match.group(3)
            return f"Select {position} {number} {entity_type}  {assignment_names}"

        # If none of the above patterns match, treat as specific candidate names
        return f"Select {candidate_selection}  {assignment_names} Assignment"
