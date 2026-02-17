"""
TLS Keep-Alive Manager for maintaining persistent TLS connections to GitLab instances.

Some enterprise network environments (particularly AWS-to-corporate connections) require
a persistent TLS connection to keep firewall/NAT state tables alive. Without this,
HTTPS mirror connections may fail intermittently.

This module runs background tasks that maintain persistent ``openssl s_client`` connections
to configured GitLab instance hosts. When the connection drops (typically after 20-30s),
it automatically reconnects after a configurable interval.

This is an optional feature, disabled by default. Enable globally via
``TLS_KEEPALIVE_ENABLED=true`` and per-instance via the ``tls_keepalive_enabled``
field on GitLabInstance.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class KeepAliveStatus:
    """Status of a single TLS keep-alive connection."""
    instance_id: int
    instance_name: str
    host: str
    port: int
    running: bool = False
    last_connect_at: Optional[float] = None
    last_disconnect_at: Optional[float] = None
    connect_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None


class TLSKeepAliveManager:
    """
    Manages background ``openssl s_client`` processes for configured instances.

    Each enabled instance gets a dedicated asyncio task that:
    1. Spawns ``openssl s_client -connect host:port``
    2. Waits for the process to exit (server closes connection after idle timeout)
    3. Waits a configurable interval
    4. Reconnects

    This keeps the TLS session / network path alive for other connections.
    """

    # Mapping from user-facing version string to openssl s_client flag
    _TLS_VERSION_FLAGS = {
        "1.2": "-tls1_2",
        "1.3": "-tls1_3",
        "1.1": "-tls1_1",
        "1.0": "-tls1",
    }

    def __init__(self, reconnect_interval: int = 5, tls_version: str = ""):
        self._reconnect_interval = reconnect_interval
        self._tls_version = tls_version.strip()
        self._tasks: dict[int, asyncio.Task] = {}  # instance_id -> task
        self._statuses: dict[int, KeepAliveStatus] = {}  # instance_id -> status
        self._stop_event = asyncio.Event()
        self._started = False

    async def start(self, instances: list[dict]) -> None:
        """
        Start keep-alive connections for the given instances.

        Args:
            instances: List of dicts with keys: id, name, url
        """
        if self._started:
            logger.warning("TLS keep-alive manager already started")
            return

        self._stop_event.clear()
        self._started = True

        for inst in instances:
            await self._start_instance(inst["id"], inst["name"], inst["url"])

        if self._tasks:
            logger.info(
                f"TLS keep-alive manager started for {len(self._tasks)} instance(s)"
            )
        else:
            logger.info("TLS keep-alive manager started (no instances enabled)")

    async def stop(self) -> None:
        """Stop all keep-alive connections."""
        if not self._started:
            return

        self._stop_event.set()

        # Cancel all tasks
        for instance_id, task in self._tasks.items():
            task.cancel()

        # Wait for tasks to finish
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            logger.info("TLS keep-alive manager stopped")

        self._tasks.clear()
        self._statuses.clear()
        self._started = False

    async def refresh(self, instances: list[dict]) -> None:
        """
        Refresh the set of active keep-alive connections.

        Starts connections for new instances and stops connections for removed ones.

        Args:
            instances: Current list of enabled instances (id, name, url).
        """
        desired_ids = {inst["id"] for inst in instances}
        current_ids = set(self._tasks.keys())

        # Stop removed instances
        for instance_id in current_ids - desired_ids:
            await self._stop_instance(instance_id)

        # Start new instances
        inst_map = {inst["id"]: inst for inst in instances}
        for instance_id in desired_ids - current_ids:
            inst = inst_map[instance_id]
            await self._start_instance(inst["id"], inst["name"], inst["url"])

    def get_status(self) -> list[dict]:
        """Return status of all keep-alive connections."""
        result = []
        for status in self._statuses.values():
            entry = {
                "instance_id": status.instance_id,
                "instance_name": status.instance_name,
                "host": status.host,
                "port": status.port,
                "running": status.running,
                "connect_count": status.connect_count,
                "error_count": status.error_count,
                "last_error": status.last_error,
            }
            if status.last_connect_at:
                entry["last_connect_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(status.last_connect_at)
                )
            if status.last_disconnect_at:
                entry["last_disconnect_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(status.last_disconnect_at)
                )
            result.append(entry)
        return result

    def get_instance_status(self, instance_id: int) -> Optional[dict]:
        """Return status of a specific instance's keep-alive connection."""
        statuses = self.get_status()
        for s in statuses:
            if s["instance_id"] == instance_id:
                return s
        return None

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    async def _start_instance(self, instance_id: int, name: str, url: str) -> None:
        """Start a keep-alive task for an instance."""
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if not host:
            logger.error(f"TLS keep-alive: cannot parse host from URL '{url}' for instance '{name}'")
            return

        status = KeepAliveStatus(
            instance_id=instance_id,
            instance_name=name,
            host=host,
            port=port,
        )
        self._statuses[instance_id] = status

        task = asyncio.create_task(
            self._keepalive_loop(instance_id, host, port, status),
            name=f"tls-keepalive-{instance_id}",
        )
        self._tasks[instance_id] = task
        logger.info(f"TLS keep-alive started for '{name}' ({host}:{port})")

    async def _stop_instance(self, instance_id: int) -> None:
        """Stop a keep-alive task for an instance."""
        task = self._tasks.pop(instance_id, None)
        status = self._statuses.pop(instance_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            name = status.instance_name if status else f"id={instance_id}"
            logger.info(f"TLS keep-alive stopped for '{name}'")

    async def _keepalive_loop(
        self, instance_id: int, host: str, port: int, status: KeepAliveStatus
    ) -> None:
        """Main loop: connect, wait for disconnect, reconnect."""
        status.running = True
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_openssl_session(host, port, status)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    status.error_count += 1
                    status.last_error = str(e)
                    logger.warning(
                        f"TLS keep-alive error for '{status.instance_name}' "
                        f"({host}:{port}): {e}"
                    )

                # Wait before reconnecting (unless we're stopping)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._reconnect_interval,
                    )
                    # If wait_for completes without timeout, stop_event was set
                    break
                except asyncio.TimeoutError:
                    # Normal case: timeout expired, reconnect
                    pass

        except asyncio.CancelledError:
            pass
        finally:
            status.running = False

    async def _run_openssl_session(
        self, host: str, port: int, status: KeepAliveStatus
    ) -> None:
        """
        Run a single ``openssl s_client`` session.

        The process connects and stays open until the server closes the connection
        (typically after 20-30 seconds of idle time). stdin is closed immediately
        so we don't send any data — we just maintain the TLS connection.
        """
        connect_arg = f"{host}:{port}"
        logger.debug(f"TLS keep-alive connecting to {connect_arg}")

        cmd = ["openssl", "s_client", "-connect", connect_arg]
        if self._tls_version:
            flag = self._TLS_VERSION_FLAGS.get(self._tls_version)
            if flag:
                cmd.append(flag)
            else:
                logger.warning(
                    f"Unknown TLS version '{self._tls_version}', "
                    f"valid options: {', '.join(sorted(self._TLS_VERSION_FLAGS))}. "
                    f"Falling back to auto-negotiate."
                )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        status.last_connect_at = time.time()
        status.connect_count += 1

        try:
            # Close stdin — we don't want to send anything, just hold the connection
            if proc.stdin:
                proc.stdin.close()
                await proc.stdin.wait_closed()

            # Wait for the process to exit (server will close after idle timeout)
            await proc.wait()
        except asyncio.CancelledError:
            # Graceful shutdown: terminate the process
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            raise
        finally:
            status.last_disconnect_at = time.time()

        logger.debug(
            f"TLS keep-alive session ended for {connect_arg} "
            f"(exit code: {proc.returncode})"
        )


# Singleton instance — created lazily via get_tls_keepalive_manager()
_manager: Optional[TLSKeepAliveManager] = None


def get_tls_keepalive_manager() -> TLSKeepAliveManager:
    """Get or create the global TLS keep-alive manager."""
    global _manager
    if _manager is None:
        from app.config import settings
        _manager = TLSKeepAliveManager(
            reconnect_interval=settings.tls_keepalive_interval,
            tls_version=settings.tls_keepalive_tls_version,
        )
    return _manager
