"""
script connects to the given room and creates an explicit dispatch for the given agent by using GKE deployed AI backend.
script dispatches the agent to the given room
agent waits until the participant joins the room
"""

import asyncio
from livekit import api
from dotenv import load_dotenv
from livekit.agents import JobContext

load_dotenv()

room_name = "1"

agent_name = "agent-worker"


async def create_explicit_dispatch():
    lkapi = api.LiveKitAPI()
    dispatch = await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=agent_name,
            room=room_name,
            metadata="""
            {
                "agent_name": "agent-ryan",
                "host_name": "1600-meeting",
                "user_id": "1600",
                "modality": "AUDIO",
                "cdp_url": "http://localhost:4594",
                "reconnect" : false,
                "local_time_profile": { "utc_time": "2025-09-19T12:50:30.000Z",   "timezone": "Asia/Karachi",   "offset_minutes": 300 },
                "environment" : "STAGING"
            }
            """,
        )
    )
    print("created dispatch", dispatch)

    dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    print(f"there are {len(dispatches)} dispatches in {room_name}")
    await lkapi.aclose()


asyncio.run(create_explicit_dispatch())
