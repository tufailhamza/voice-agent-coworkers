"""
Trigger a single LiveKit agent job via Redis (for KEDA autoscaling).
"""

import os
import json
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

try:
    import redis  # type: ignore
except Exception as _:
    raise SystemExit("redis package not installed. Run: pip install redis")

REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))
QUEUE_NAME = os.getenv("QUEUE_NAME", "")

# Defaults; override via envs if desired
DEFAULT_ROOM = os.getenv("TRIGGER_ROOM", "4")
DEFAULT_HOST_NAME = os.getenv("TRIGGER_HOST_NAME", "1388-meeting")
DEFAULT_USER_ID = os.getenv("TRIGGER_USER_ID", "1388")
DEFAULT_MODALITY = os.getenv("TRIGGER_MODALITY", "AUDIO")
DEFAULT_TIMEZONE = os.getenv("TRIGGER_TZ", "Asia/Karachi")
DEFAULT_OFFSET_MIN = int(os.getenv("TRIGGER_TZ_OFFSET_MIN", "300"))


def build_job_payload(
    room: str = DEFAULT_ROOM,
    host_name: str = DEFAULT_HOST_NAME,
    user_id: str = DEFAULT_USER_ID,
    modality: str = DEFAULT_MODALITY,
) -> dict:
    utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "job_id": str(uuid.uuid4()),
        "agent_name": "agent-bella",
        "livekit_room": room,
        "host_name": host_name,
        "user_id": user_id,
        "modality": modality,
        "cdp_url": os.getenv("CDP_URL", "http://localhost:4594"),
        "reconnect": False,
        "local_time_profile": {
            "utc_time": utc_time,
            "timezone": DEFAULT_TIMEZONE,
            "offset_minutes": DEFAULT_OFFSET_MIN,
        },
    }


def trigger_one_job(payload: dict | None = None) -> None:
    payload = payload or build_job_payload()

    print(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}...")
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        r.ping()
    except redis.exceptions.ConnectionError as e:
        raise SystemExit(f"Error connecting to Redis: {e}")

    r.lpush(QUEUE_NAME, json.dumps(payload))
    print(
        f"✅ Pushed job to '{QUEUE_NAME}' | room={payload['livekit_room']} user={payload['user_id']}"
    )


if __name__ == "__main__":
    trigger_one_job()
