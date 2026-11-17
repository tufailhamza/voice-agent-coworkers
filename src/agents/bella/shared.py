from dataclasses import dataclass, field
from typing import Optional
from livekit.agents import Agent
from src.agents.bella.in_memory_db import InMemoryToDoClient


@dataclass
class UserData:
    personas: dict[str, Agent] = field(default_factory=dict)
    prev_agent: Agent | None = None
    current_agent: Agent | None = None
    tool_called: str = ""
    tool_status: str = ""
    modal: str = ""
    modality: str = "AUDIO"
    calendar_manager: object | None = None
    todo_client: InMemoryToDoClient | None = None
    user_id: str | None = None
    host_name: str | None = None
    key_name = "1"
    initial = True
    reconnect = False
    tool_invoked = ""
    access_token = None
    calendar_type: Optional[str] = None  # "google" | "outlook"
    write_access = False
    org_participants = []
    user_name: str = ""  # Resolved from org_participants by user_id
    environment = ""
    local_timezone = "Asia/Karachi"
    initial_msg: Optional[str] = None
    memory_manager: object | None = None
    first_time_user: bool = False
