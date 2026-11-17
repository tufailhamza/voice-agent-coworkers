from dataclasses import dataclass, field
from typing import Optional
from livekit.agents import Agent


@dataclass
class UserData:
    access_token: Optional[str] = None
    tool_called: bool = False
