import asyncio
import logging
import uuid
from livekit.agents import JobContext, AutoSubscribe
from livekit import rtc
from .config import Config
from .transcriber import MultiUserTranscriber, SUMMARY_WAIT_TIMEOUT
from src.utils.exceptions import TranscriberServiceError

logger = logging.getLogger(__name__)


async def entrypoint(
    ctx: JobContext,
    local_time_profile: dict | None = None,
    environment: str | None = None,
    recording_id: str | None = None,
    organization_id: int | None = None,
):
    """Main entrypoint for the transcriber service"""
    try:
        # Load config again in this process
        config = Config.from_env()

        # Setup logging again (optional but useful for subprocess logs)
        import logging as log_module

        logger.setLevel(getattr(log_module, config.log_level))

        # Set defaults
        if local_time_profile is None:
            local_time_profile = {
                "timezone": "Asia/Karachi",
                "offset_minutes": 300,
            }
        if environment is None:
            environment = "PRODUCTION"
        if recording_id is None:
            recording_id = str(uuid.uuid4())
            logger.info(f"📝 Generated recording_id: {recording_id}")

        # Create transcriber
        transcriber = MultiUserTranscriber(
            ctx, config, local_time_profile, environment, recording_id, organization_id
        )
        transcriber.start()

        # Connect to room IMMEDIATELY - this must happen quickly to avoid timeout
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

        # Log agent identity for debugging RPC calls
        agent_identity = ctx.room.local_participant.identity
        agent_sid = ctx.room.local_participant.sid
        logger.info(f"🤖 Agent Identity: {agent_identity}")
        logger.info(f"🤖 Agent SID: {agent_sid}")
        logger.info(f"🤖 Agent Kind: {ctx.room.local_participant.kind}")
        logger.info(f"📋 Room Name: {ctx.room.name}")
        logger.info(f"👥 Total Participants: {ctx.room.num_participants}")
        logger.info(f"🔌 Connection State: {ctx.room.connection_state}")
        logger.info(f"🔌 Is Connected: {ctx.room.isconnected()}")

        # Register RPC method IMMEDIATELY after connect (like old working version)
        # This matches the old working pattern - register before handling participants
        @ctx.room.local_participant.register_rpc_method("stop-recording-agent")
        async def stop_recording_agent_rpc(data: rtc.RpcInvocationData):
            """RPC method to trigger summary generation from cached transcripts"""
            logger.info(f"🛑 Stop recording RPC called by: {data.caller_identity}")
            logger.info(f"📞 RPC method: stop-recording-agent")
            logger.info(f"📦 RPC payload: {data.payload}")
            try:
                # Trigger the same logic as room update detection
                # This will check the flag internally to prevent duplicates
                transcriber.on_recording_stopped()
                logger.info("✅ Stop recording RPC processed successfully")
                return "Stop recording triggered successfully"
            except Exception as e:
                logger.error(f"❌ Error processing stop recording RPC: {e}")
                import traceback

                logger.error(f"❌ Traceback: {traceback.format_exc()}")
                return f"Error: {str(e)}"

        logger.info("✅ RPC method registered: stop-recording-agent")
        logger.info(f"✅ RPC registered on agent participant: {agent_identity}")

        # Alternative 1: Listen for data channel messages (if RPC doesn't work)
        def on_data_received(data_packet: rtc.DataPacket):
            """Handle data channel messages from frontend"""
            try:
                # Check if it's a stop recording signal
                # Topic can be "stop-recording-agent" or "stop-recording" for flexibility
                if data_packet.topic in ("stop-recording-agent", "stop-recording"):
                    payload = (
                        data_packet.data.decode("utf-8")
                        if isinstance(data_packet.data, bytes)
                        else data_packet.data
                    )
                    logger.info(
                        f"📨 Data channel message received: topic={data_packet.topic}, payload={payload}"
                    )
                    logger.info(
                        f"📨 From participant: {data_packet.participant.identity if data_packet.participant else 'unknown'}"
                    )

                    # Trigger stop recording (will check flag internally to prevent duplicates)
                    # This uses the same flag as RPC and room update - first trigger wins
                    transcriber.on_recording_stopped()
                    logger.info("✅ Stop recording triggered via data channel")
                else:
                    logger.debug(
                        f"📨 Data channel message received (ignored): topic={data_packet.topic}"
                    )
            except Exception as e:
                logger.error(f"❌ Error handling data channel message: {e}")

        ctx.room.on("data_received", on_data_received)
        logger.info(
            "✅ Data channel listener registered for stop-recording-agent topic"
        )

        # Alternative 2: Listen for room metadata changes (if RPC doesn't work)
        def on_room_metadata_changed(old_metadata: str, new_metadata: str):
            """Handle room metadata changes from frontend"""
            try:
                import json

                # Check if metadata contains stop recording signal
                if new_metadata:
                    try:
                        metadata = (
                            json.loads(new_metadata)
                            if isinstance(new_metadata, str)
                            else new_metadata
                        )
                        if (
                            isinstance(metadata, dict)
                            and metadata.get("stop_recording") == True
                        ):
                            logger.info(
                                f"📋 Room metadata changed - stop_recording signal detected"
                            )
                            logger.info(f"📋 Old metadata: {old_metadata}")
                            logger.info(f"📋 New metadata: {new_metadata}")

                            # Trigger stop recording (will check flag internally to prevent duplicates)
                            # This uses the same flag as RPC and data channel - first trigger wins
                            transcriber.on_recording_stopped()
                            logger.info("✅ Stop recording triggered via room metadata")
                    except (json.JSONDecodeError, AttributeError):
                        # Metadata is not JSON, check for simple string signal
                        if "stop-recording" in str(new_metadata).lower():
                            logger.info(
                                f"📋 Room metadata contains stop-recording signal"
                            )
                            # Trigger stop recording (will check flag internally to prevent duplicates)
                            transcriber.on_recording_stopped()
                            logger.info(
                                "✅ Stop recording triggered via room metadata (string match)"
                            )
            except Exception as e:
                logger.error(f"❌ Error handling room metadata change: {e}")

        ctx.room.on("room_metadata_changed", on_room_metadata_changed)
        logger.info("✅ Room metadata change listener registered")

        # Handle existing participants (after RPC registration, like old version)
        for participant in ctx.room.remote_participants.values():
            transcriber.on_participant_connected(participant)

        # Force data channel establishment by sending a dummy data packet
        # This ensures the RPC data channel is ready before the first RPC call
        # Without this, the first RPC fails with "Method not supported" when there's no media activity
        # because WebRTC data channels are lazy-loaded
        try:
            await ctx.room.local_participant.publish_data(
                b"__rpc_channel_init__",  # Dummy payload to establish data channel
                reliable=True,
                topic="__rpc_init__",
            )
            logger.info("✅ Data channel established for RPC")
        except Exception as e:
            logger.warning(f"⚠️ Failed to establish data channel (non-critical): {e}")
            # Continue anyway - the channel might still work

        # Shutdown callback: wait for transcript processing and both API submissions
        async def cleanup(shutdown_reason: str = ""):
            logger.info("🧹 Starting cleanup...")
            await transcriber.wait_for_completion_task(timeout=SUMMARY_WAIT_TIMEOUT)
            await transcriber.aclose()
            logger.info("✅ Cleanup completed")

        ctx.add_shutdown_callback(cleanup)

        logger.info("🚀 Transcriber service is running")

    except Exception as e:
        logger.error(f"❌ Error in entrypoint: {e}")
        raise
