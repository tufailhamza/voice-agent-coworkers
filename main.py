import os
import sys

# Check if we're running download-files FIRST, before importing agent code
# This avoids importing modules that require API keys during Docker build
if len(sys.argv) > 1 and sys.argv[1] == "download-files":
    # For download-files, we don't need to import agent code
    # Just import plugins and run download
    from livekit.agents.plugin import Plugin
    from livekit.agents.log import logger
    from livekit.plugins import silero  # noqa: F401 - Register plugin

    logger.info("Downloading files for all registered plugins...")
    for plugin in Plugin.registered_plugins:
        logger.info(f"Downloading files for {plugin}")
        try:
            plugin.download_files()
            logger.info(f"Finished downloading files for {plugin}")
        except Exception as e:
            logger.warning(f"Error downloading files for {plugin}: {e}")
    logger.info("All plugin files downloaded successfully!")
    sys.exit(0)

# Only import agent code if we're NOT running download-files
import json
from livekit.agents import JobRequest, cli, WorkerOptions, WorkerType
from src.agents.bella.main import entrypoint as bella_entrypoint
from src.agents.ryan.main import entrypoint as ryan_entrypoint
from src.agents.meeting.entrypoint import entrypoint as transcriber_entrypoint
from src.utils.logger import Logger
from dotenv import load_dotenv
from livekit.agents import JobContext

# Import plugins to ensure they're registered
from livekit.plugins import silero  # noqa: F401

load_dotenv()
logging = Logger("main")
mode = os.getenv("MODE")


async def get_agent(
    name: str,
    ctx: JobContext,
    host_name: str,
    user_id: str,
    modality: str,
    cdp_url: str | None = None,
    reconnect: bool = False,
    local_time_profile: dict | None = None,
    environment: str | None = None,
    recording_id: str | None = None,
    initial_message: str | None = None,
    organization_id: int | None = None,
):
    if name == "agent-bella":
        if organization_id is None:
            raise ValueError("organization_id is required for agent-bella")
        return await bella_entrypoint(
            ctx,
            host_name,
            user_id,
            modality,
            reconnect,
            local_time_profile,
            environment,
            initial_message,
            organization_id,
        )
    elif name == "agent-ryan":
        return await ryan_entrypoint(ctx, host_name, user_id, modality, cdp_url)
    elif name == "agent-meeting-bot":
        return await transcriber_entrypoint(
            ctx, local_time_profile, environment, recording_id, organization_id
        )
    else:
        raise ValueError(f"Unknown agent: {name}")


async def entrypoint(ctx: JobContext):
    if mode == "development":
        agent_type = "agent-bella"
        host_name = "1388-active"
        user_id = "1388"
        modality = "AUDIO"
        cdp_url = "wss://8f14d48ef8ff.ngrok-free.app/devtools/browser/1aeb9bb9-dc0c-4fa1-bd64-8038b66447a7"
        reconnect = False
        local_time_profile = {
            "utc_time": "2025-09-19T12:50:30.000Z",
            "timezone": "Asia/Karachi",
            "offset_minutes": 300,
        }
        environment = "PRODUCTION"
        recording_id = None  # Will generate UUID if not provided
        initial_message = None
        organization_id = 1  # Hardcoded for development only

        return await get_agent(
            agent_type,
            ctx,
            host_name,
            user_id,
            modality,
            cdp_url,
            reconnect,
            local_time_profile,
            environment,
            recording_id,
            initial_message,
            organization_id,
        )

    logging.info(f"😎 {ctx.job.dispatch_id}")
    Logger.set_global_prefix(f"😎 [{ctx.job.dispatch_id}]")
    metadata = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    logging.info(f"📦 Job metadata (full): {metadata}")

    agent_type = metadata.get("agent_name", "default-agent-mode")
    host_name = metadata.get("host_name", "default-host-name")
    user_id = metadata.get("user_id", "default-user-id")
    modality = metadata.get("modality", "AUDIO")
    cdp_url = metadata.get("cdp_url", "http://localhost:9222")
    reconnect = metadata.get("reconnect", False)
    local_time_profile = metadata.get("local_time_profile", None)
    environment = metadata.get("environment", "PRODUCTION")
    recording_id = metadata.get("recording_id", None)
    initial_message = metadata.get("initial_message", None)
    # organizationId from request (camelCase) or organization_id
    organization_id = metadata.get("organizationId") or metadata.get("organization_id")
    if organization_id is not None:
        organization_id = int(organization_id)

    # For meeting bot, these are required and must not be None
    if agent_type == "agent-meeting-bot":
        missing = []
        if local_time_profile is None:
            missing.append("localTimeProfile")
        if recording_id is None or (
            isinstance(recording_id, str) and recording_id.strip() == ""
        ):
            missing.append("recording_id")
        if organization_id is None:
            missing.append("organizationId")
        if missing:
            raise ValueError(
                f"agent-meeting-bot requires non-empty values; missing or empty: {', '.join(missing)}"
            )

    # For Bella, all of these are required and must not be None
    if agent_type == "agent-bella":
        missing = []
        if user_id is None or (
            isinstance(user_id, str)
            and (user_id.strip() == "" or user_id == "default-user-id")
        ):
            missing.append("user_id")
        if host_name is None or (
            isinstance(host_name, str)
            and (host_name.strip() == "" or host_name == "default-host-name")
        ):
            missing.append("host_name")
        if modality is None or (isinstance(modality, str) and modality.strip() == ""):
            missing.append("modality")
        if environment is None or (
            isinstance(environment, str) and environment.strip() == ""
        ):
            missing.append("environment")
        if organization_id is None:
            missing.append("organization_id")
        if local_time_profile is None:
            missing.append("local_time_profile")
        if missing:
            raise ValueError(
                f"agent-bella requires non-empty values; missing or empty: {', '.join(missing)}"
            )

    logging.info(f"Agent mode: {agent_type}")

    return await get_agent(
        agent_type,
        ctx,
        host_name,
        user_id,
        modality,
        cdp_url,
        reconnect,
        local_time_profile,
        environment,
        recording_id,
        initial_message,
        organization_id,
    )


if __name__ == "__main__":
    # download-files is already handled at the top of the file
    # For other commands, set up WorkerOptions
    opts = WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="agent-worker",
        worker_type=WorkerType.ROOM,
        num_idle_processes=2,
    )
    cli.run_app(opts)
