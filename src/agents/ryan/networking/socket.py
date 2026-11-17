import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional
import socketio
from src.utils.logger import Logger
import os
from urllib.parse import urlencode

logging = Logger(__name__)


class RyanClient:
    _instance = None
    _initialized = False

    # singleton pattern
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        *,
        reconnection: bool = True,
        reconnection_attempts: Optional[int] = 3,
        reconnection_delay: float = 1.0,
        reconnection_delay_max: float = 5.0,
    ) -> None:
        # Only initialize once for singleton
        if self._initialized:
            return

        self.url = os.getenv("OPERATOR_URL")
        self._connected_event = asyncio.Event()
        self._create_client(
            reconnection=reconnection,
            reconnection_attempts=reconnection_attempts,
            reconnection_delay=reconnection_delay,
            reconnection_delay_max=reconnection_delay_max,
        )
        self._initialized = True

    def _create_client(
        self,
        *,
        reconnection: bool,
        reconnection_attempts: Optional[int],
        reconnection_delay: float,
        reconnection_delay_max: float,
    ) -> None:
        self._client = socketio.AsyncClient(
            reconnection=reconnection,
            reconnection_attempts=reconnection_attempts,
            reconnection_delay=reconnection_delay,
            reconnection_delay_max=reconnection_delay_max,
        )

        @self._client.event
        async def connect() -> None:
            logging.info(f"🔌 Socket connected to {self.url}")
            self._connected_event.set()

        @self._client.event
        async def disconnect() -> None:
            logging.info("🔌 Socket disconnected")
            self._connected_event.clear()

        @self._client.event
        async def connect_error(data: Any) -> None:
            logging.error(f"🔌 Socket connect_error: {data}")

    async def connect(
        self,
        *,
        headers: Optional[Dict[str, str]] = None,
        auth: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        cdp_url: Optional[str] = None,
        transports: Optional[list[str]] = None,
        wait: bool = True,
        wait_timeout: float = 400.0,
        socketio_path: str = "socket.io",
    ) -> None:
        if self._client is None:
            raise RuntimeError("Socket client not initialized")

        params = query or {}
        if cdp_url:
            params["cdp_url"] = cdp_url
        used_transports = transports or ["websocket"]

        logging.info(
            f"🔌 Connecting to {self.url} (path={socketio_path}, transports={used_transports})"
        )
        qs = f"?{urlencode(params)}" if params else ""
        await self._client.connect(
            f"{self.url}{qs}",
            headers=headers,
            auth=auth,
            transports=used_transports,
            socketio_path=socketio_path,
            wait=wait,
            wait_timeout=wait_timeout,
        )

        if wait:
            try:
                await asyncio.wait_for(
                    self._connected_event.wait(), timeout=wait_timeout
                )
                logging.info("✅ Socket connection confirmed via event")
            except asyncio.TimeoutError:
                logging.error("Timed out waiting for socket connect event")
                raise

    async def disconnect(self) -> None:
        if self._client is None:
            return
        await self._client.disconnect()

    @classmethod
    async def emit(cls, event: str, data: Any, namespace: Optional[str] = None):
        instance = cls()

        if instance._client is None:

            raise RuntimeError("Socket client not initialized")

        # Better connection check - use both the event and client state
        client_connected = (
            instance._client.connected
            if hasattr(instance._client, "connected")
            else False
        )
        event_connected = instance._connected_event.is_set()

        logging.info(
            f"🔍 Connection status - Client connected: {client_connected}, Event set: {event_connected}"
        )

        # If not connected, wait a bit for connection to establish
        if not client_connected or not event_connected:
            logging.info("🔄 Client not fully connected, waiting...")
            try:
                await asyncio.wait_for(
                    instance._connected_event.wait(), timeout=50000.0
                )
                logging.info("✅ Connection established, proceeding with emit")
            except asyncio.TimeoutError:
                raise RuntimeError(
                    "Socket not connected and connection timeout exceeded"
                )

        logging.info(f"🔄 Emitting event: {event} with data: {data}")
        await instance._client.emit(event, data, namespace=namespace)

    def on(self, event: str, handler: Callable[..., Awaitable[None]]) -> None:
        if self._client is None:
            raise RuntimeError("Socket client not initialized")
        self._client.on(event, handler)

    @property
    def is_connected(self) -> bool:
        client_connected = (
            self._client.connected
            if (self._client and hasattr(self._client, "connected"))
            else False
        )
        event_connected = self._connected_event.is_set()
        return client_connected and event_connected

    async def cleanup(self):
        """Proper socket client cleanup with aiohttp session cleanup"""
        try:
            logging.info("🧹 Starting socket client cleanup")

            if self._client:
                try:
                    # Disconnect if connected
                    if self._client.connected:
                        logging.info("🔄 Disconnecting socket client")
                        await self._client.disconnect()
                        logging.info("✅ Socket client disconnected")

                    # Force close the underlying aiohttp session
                    if hasattr(self._client, "_http") and self._client._http:
                        logging.info("🔄 Closing underlying aiohttp session")
                        await self._client._http.close()
                        logging.info("✅ aiohttp session closed")

                    # Clear the client reference
                    self._client = None

                except Exception as e:
                    logging.error(f"❌ Error during socket client cleanup: {e}")

            # Clear connection event
            self._connected_event.clear()

            # Reset singleton state
            self._initialized = False
            RyanClient._instance = None

            logging.info("✅ Socket client cleanup completed")

        except Exception as e:
            logging.error(f"❌ Error in socket cleanup: {e}")
            # Reset singleton even if cleanup fails
            self._initialized = False
            RyanClient._instance = None
