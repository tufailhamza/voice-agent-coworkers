from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Transcript:
    """Transcript model"""

    id: Optional[int] = None
    room_id: str = ""
    session_id: str = ""
    speaker_name: str = ""
    participant_sid: Optional[str] = None
    text_content: str = ""
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "room_id": self.room_id,
            "session_id": self.session_id,
            "speaker_name": self.speaker_name,
            "participant_sid": self.participant_sid,
            "text_content": self.text_content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class MeetingSummary:
    """Meeting summary model"""

    id: Optional[int] = None
    room_id: str = ""
    session_id: str = ""
    summary_content: str = ""
    participants: List[str] = None
    timestamp: Optional[datetime] = None

    def __post_init__(self):
        if self.participants is None:
            self.participants = []
