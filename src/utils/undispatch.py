import asyncio
from livekit import api
from dotenv import load_dotenv

load_dotenv()

room_name = "4"
agent_name = "agent-worker"


async def undispatch_agent():
    lkapi = api.LiveKitAPI()

    dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    print(f"There are {len(dispatches)} dispatches in room {room_name}")

    dispatch_to_delete = None
    for d in dispatches:
        # Try 'agent_name' or check d.metadata if stored as JSON string
        if getattr(d, "agent_name", None) == agent_name:
            dispatch_to_delete = d
            break
        # If agent_name is embedded in metadata, parse it:
        # import json
        # metadata = json.loads(d.metadata)
        # if metadata.get("agent_name") == agent_name:
        #     dispatch_to_delete = d
        #     break

    if dispatch_to_delete:
        # Use 'sid' or 'id' for the dispatch identifier
        dispatch_id = getattr(dispatch_to_delete, "sid", None) or getattr(
            dispatch_to_delete, "id", None
        )
        if dispatch_id:
            print(f"Deleting dispatch with id {dispatch_id} for agent {agent_name}")
            deleted_dispatch = await lkapi.agent_dispatch.delete_dispatch(
                dispatch_id=dispatch_id, room_name=room_name
            )
            print("Deleted dispatch:", deleted_dispatch)
        else:
            print("Dispatch ID not found.")
    else:
        print(f"No dispatch found for agent {agent_name} in room {room_name}")

    await lkapi.aclose()


asyncio.run(undispatch_agent())
