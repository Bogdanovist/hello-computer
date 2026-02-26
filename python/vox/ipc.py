"""IPC client — connects to the VoxDaemon Unix domain socket and dispatches messages."""

from __future__ import annotations

import json
import logging
import socket
from typing import TYPE_CHECKING

from vox.diff_engine import extract_diff_pairs
from vox.post_processor import post_process

if TYPE_CHECKING:
    from vox.config import VoxConfig
    from vox.ledger import Ledger

logger = logging.getLogger(__name__)

_DEFAULT_SOCKET_PATH = "/tmp/vox.sock"
_RECV_BUFFER_SIZE = 4096


class VoxIPCClient:
    """Client that connects to the VoxDaemon socket and dispatches messages.

    Handles two inbound message types from the Swift daemon:

    * ``transcription`` — runs :func:`post_process` and responds with an
      ``inject`` message containing the cleaned text.
    * ``correction`` — runs :func:`extract_diff_pairs` and stores the result
      via :meth:`Ledger.insert_correction`.

    Parameters
    ----------
    ledger:
        The correction ledger for storing corrections.
    config:
        Application configuration.
    socket_path:
        Path to the Unix domain socket (default ``/tmp/vox.sock``).
    """

    def __init__(
        self,
        ledger: Ledger,
        config: VoxConfig,
        socket_path: str = _DEFAULT_SOCKET_PATH,
    ) -> None:
        self._ledger = ledger
        self._config = config
        self._socket_path = socket_path
        self._sock: socket.socket | None = None
        self._buffer = ""

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to the daemon's Unix domain socket."""
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self._socket_path)
        logger.info("Connected to daemon at %s", self._socket_path)

    def disconnect(self) -> None:
        """Close the socket connection."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buffer = ""

    def _ensure_connected(self) -> None:
        """Ensure the socket is connected, reconnecting if necessary."""
        if self._sock is None:
            logger.warning("Connection lost — reconnecting to %s", self._socket_path)
            self.connect()

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def _send(self, message: dict) -> None:  # type: ignore[type-arg]
        """Send a JSON message terminated by newline."""
        self._ensure_connected()
        assert self._sock is not None
        payload = json.dumps(message, ensure_ascii=False) + "\n"
        self._sock.sendall(payload.encode("utf-8"))
        logger.debug("Sent message type=%s", message.get("type", "unknown"))

    def _read_messages(self) -> list[dict]:  # type: ignore[type-arg]
        """Read available data and return complete JSON messages.

        Accumulates partial reads in an internal buffer and splits on
        newline boundaries.  Returns an empty list when the connection
        is closed by the peer.
        """
        self._ensure_connected()
        assert self._sock is not None
        try:
            data = self._sock.recv(_RECV_BUFFER_SIZE)
        except OSError as exc:
            logger.warning("Socket read error: %s", exc)
            self.disconnect()
            return []

        if not data:
            logger.warning("Daemon closed connection")
            self.disconnect()
            return []

        self._buffer += data.decode("utf-8")
        messages: list[dict] = []  # type: ignore[type-arg]
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Malformed JSON message — skipping")
                continue
            messages.append(msg)
        return messages

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _handle_transcription(self, msg: dict) -> None:  # type: ignore[type-arg]
        """Process a transcription message and respond with an inject message."""
        raw = msg.get("raw", "")
        app_bundle_id = msg.get("app_bundle_id")
        logger.info(
            "Received transcription message — app=%s",
            app_bundle_id or "unknown",
        )

        cleaned = post_process(
            raw_transcript=raw,
            app_bundle_id=app_bundle_id,
            ledger=self._ledger,
            config=self._config,
        )
        self._send({"type": "inject", "text": cleaned})

    def _handle_correction(self, msg: dict) -> None:  # type: ignore[type-arg]
        """Process a correction message: extract diff pairs and store."""
        injected = msg.get("injected", "")
        corrected = msg.get("corrected", "")
        app_bundle_id = msg.get("app_bundle_id")
        logger.info(
            "Received correction message — app=%s",
            app_bundle_id or "unknown",
        )

        diff_pairs = extract_diff_pairs(injected, corrected)
        if diff_pairs:
            self._ledger.insert_correction(
                injected_text=injected,
                corrected_text=corrected,
                diff_pairs=diff_pairs,
                app_bundle_id=app_bundle_id,
            )
            logger.info("Stored correction — %d diff pair(s)", len(diff_pairs))
        else:
            logger.info("No diff pairs extracted — skipping ledger insert")

    def dispatch(self, msg: dict) -> None:  # type: ignore[type-arg]
        """Route a single message to the appropriate handler.

        Unknown message types are logged and ignored.
        """
        msg_type = msg.get("type")
        if msg_type == "transcription":
            self._handle_transcription(msg)
        elif msg_type == "correction":
            self._handle_correction(msg)
        else:
            logger.warning("Unknown message type=%s — ignoring", msg_type)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Connect and process messages until the connection is closed.

        Reconnects automatically on disconnection and continues processing.
        """
        self._ensure_connected()
        while True:
            messages = self._read_messages()
            if not messages and self._sock is None:
                # Connection was lost — _read_messages already disconnected.
                # Break to let the caller decide whether to restart.
                break
            for msg in messages:
                self.dispatch(msg)
