"""Tests for the VoxIPCClient message dispatcher."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from vox.ipc import VoxIPCClient

# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture()
def mock_ledger():
    """Create a mock Ledger with spec-like interface."""
    ledger = MagicMock()
    ledger.insert_correction.return_value = 1
    return ledger


@pytest.fixture()
def mock_config():
    """Create a mock VoxConfig."""
    return MagicMock()


@pytest.fixture()
def client(mock_ledger, mock_config):
    """Create a VoxIPCClient with mocked dependencies and a fake socket."""
    c = VoxIPCClient(mock_ledger, mock_config, socket_path="/tmp/test_vox.sock")
    # Inject a mock socket so tests don't touch real sockets.
    c._sock = MagicMock()
    return c


# --------------------------------------------------------------------
# Transcription dispatch
# --------------------------------------------------------------------


class TestTranscriptionDispatch:
    """Transcription messages trigger post_process and return inject response."""

    @patch("vox.ipc.post_process")
    def test_transcription_calls_post_process_and_sends_inject(
        self, mock_pp, client, mock_ledger, mock_config
    ):
        """Transcription triggers post_process and sends inject."""
        mock_pp.return_value = "cleaned text"
        msg = {
            "type": "transcription",
            "raw": "raw whisper output",
            "app_bundle_id": "com.example.App",
            "timestamp": "2026-02-26T10:00:00Z",
        }

        client.dispatch(msg)

        mock_pp.assert_called_once_with(
            raw_transcript="raw whisper output",
            app_bundle_id="com.example.App",
            ledger=mock_ledger,
            config=mock_config,
        )
        # Verify inject message was sent via the socket.
        sent_bytes = client._sock.sendall.call_args[0][0]
        sent_msg = json.loads(sent_bytes.decode("utf-8").strip())
        assert sent_msg == {"type": "inject", "text": "cleaned text"}

    @patch("vox.ipc.post_process")
    def test_transcription_with_missing_app_bundle_id(self, mock_pp, client):
        """Transcription message without app_bundle_id passes None to post_process."""
        mock_pp.return_value = "output"
        msg = {"type": "transcription", "raw": "hello"}

        client.dispatch(msg)

        assert mock_pp.call_args[1]["app_bundle_id"] is None

    @patch("vox.ipc.post_process")
    def test_transcription_with_empty_raw(self, mock_pp, client):
        """Transcription with missing 'raw' field defaults to empty string."""
        mock_pp.return_value = ""
        msg = {"type": "transcription"}

        client.dispatch(msg)

        assert mock_pp.call_args[1]["raw_transcript"] == ""


# --------------------------------------------------------------------
# Correction dispatch
# --------------------------------------------------------------------


class TestCorrectionDispatch:
    """Correction messages trigger diff extraction and ledger insert."""

    @patch("vox.ipc.extract_diff_pairs")
    def test_correction_extracts_diffs_and_inserts(
        self, mock_extract, client, mock_ledger
    ):
        """A correction message extracts diff pairs and inserts into the ledger."""
        mock_extract.return_value = [("teh", "the")]
        msg = {
            "type": "correction",
            "injected": "teh quick fox",
            "corrected": "the quick fox",
            "app_bundle_id": "com.apple.TextEdit",
        }

        client.dispatch(msg)

        mock_extract.assert_called_once_with("teh quick fox", "the quick fox")
        mock_ledger.insert_correction.assert_called_once_with(
            injected_text="teh quick fox",
            corrected_text="the quick fox",
            diff_pairs=[("teh", "the")],
            app_bundle_id="com.apple.TextEdit",
        )

    @patch("vox.ipc.extract_diff_pairs")
    def test_correction_with_no_diffs_skips_insert(
        self, mock_extract, client, mock_ledger
    ):
        """When extract_diff_pairs returns empty list, no ledger insert occurs."""
        mock_extract.return_value = []
        msg = {
            "type": "correction",
            "injected": "hello world",
            "corrected": "hello world",
        }

        client.dispatch(msg)

        mock_ledger.insert_correction.assert_not_called()

    @patch("vox.ipc.extract_diff_pairs")
    def test_correction_with_missing_fields_defaults_to_empty(
        self, mock_extract, client
    ):
        """Correction message with missing fields uses empty string defaults."""
        mock_extract.return_value = []
        msg = {"type": "correction"}

        client.dispatch(msg)

        mock_extract.assert_called_once_with("", "")


# --------------------------------------------------------------------
# Dispatch routing
# --------------------------------------------------------------------


class TestDispatchRouting:
    """dispatch() routes messages to the correct handler."""

    def test_unknown_message_type_is_ignored(self, client):
        """Unknown message type logs warning and does not raise."""
        msg = {"type": "unknown_type", "data": "something"}
        # Should not raise.
        client.dispatch(msg)

    def test_missing_type_field_is_ignored(self, client):
        """Message with no 'type' field logs warning and does not raise."""
        msg = {"data": "something"}
        client.dispatch(msg)


# --------------------------------------------------------------------
# Disconnection handling
# --------------------------------------------------------------------


class TestDisconnectionHandling:
    """Tests for graceful disconnection and reconnection."""

    def test_disconnect_closes_socket(self, client):
        """disconnect() closes the socket and clears internal state."""
        sock = client._sock
        client.disconnect()

        sock.close.assert_called_once()
        assert client._sock is None
        assert client._buffer == ""

    def test_disconnect_when_already_disconnected(self, client):
        """disconnect() is safe to call when already disconnected."""
        client._sock = None
        client.disconnect()  # Should not raise.

    def test_read_messages_on_empty_data_disconnects(self, client):
        """When recv returns empty bytes (peer closed), client disconnects."""
        client._sock.recv.return_value = b""

        messages = client._read_messages()

        assert messages == []
        assert client._sock is None

    def test_read_messages_on_socket_error_disconnects(self, client):
        """When recv raises OSError, client disconnects gracefully."""
        client._sock.recv.side_effect = OSError("Connection reset")

        messages = client._read_messages()

        assert messages == []
        assert client._sock is None

    @patch("vox.ipc.socket.socket")
    def test_ensure_connected_reconnects_when_disconnected(
        self, mock_socket_cls, mock_ledger, mock_config
    ):
        """_ensure_connected() reconnects when socket is None."""
        mock_sock_instance = MagicMock()
        mock_socket_cls.return_value = mock_sock_instance

        c = VoxIPCClient(mock_ledger, mock_config, socket_path="/tmp/test.sock")
        assert c._sock is None

        c._ensure_connected()

        mock_sock_instance.connect.assert_called_once_with("/tmp/test.sock")
        assert c._sock is mock_sock_instance


# --------------------------------------------------------------------
# Malformed JSON handling
# --------------------------------------------------------------------


class TestMalformedJsonHandling:
    """Malformed JSON messages are skipped without crashing."""

    def test_malformed_json_is_skipped(self, client):
        """Malformed JSON line is skipped; valid messages around it are returned."""
        data = 'not json\n{"type": "transcription", "raw": "hi"}\n'
        client._sock.recv.return_value = data.encode("utf-8")

        messages = client._read_messages()

        assert len(messages) == 1
        assert messages[0]["type"] == "transcription"

    def test_empty_lines_are_skipped(self, client):
        """Empty lines between messages are ignored."""
        data = '\n\n{"type": "correction"}\n\n'
        client._sock.recv.return_value = data.encode("utf-8")

        messages = client._read_messages()

        assert len(messages) == 1
        assert messages[0]["type"] == "correction"

    def test_partial_message_buffered(self, client):
        """A partial message (no trailing newline) is buffered, not returned."""
        data = '{"type": "transcri'
        client._sock.recv.return_value = data.encode("utf-8")

        messages = client._read_messages()

        assert messages == []
        assert client._buffer == data

    def test_partial_message_completed_on_next_read(self, client):
        """A partial message is completed when the rest arrives."""
        client._buffer = '{"type": "transcri'
        data = 'ption", "raw": "test"}\n'
        client._sock.recv.return_value = data.encode("utf-8")

        messages = client._read_messages()

        assert len(messages) == 1
        assert messages[0]["type"] == "transcription"
        assert messages[0]["raw"] == "test"


# --------------------------------------------------------------------
# Content-free logging
# --------------------------------------------------------------------


class TestContentFreeLogging:
    """Verify that no transcribed or corrected text appears in log output."""

    @patch("vox.ipc.post_process")
    def test_transcription_logs_contain_no_text_content(
        self, mock_pp, client, caplog
    ):
        """Transcription handler logs app context but never the raw/cleaned text."""
        mock_pp.return_value = "the cleaned output"
        msg = {
            "type": "transcription",
            "raw": "secret dictation content",
            "app_bundle_id": "com.example.App",
        }

        with caplog.at_level(logging.DEBUG, logger="vox.ipc"):
            client.dispatch(msg)

        log_text = caplog.text
        assert "secret dictation content" not in log_text
        assert "the cleaned output" not in log_text
        # App bundle ID is metadata and IS allowed in logs.
        assert "com.example.App" in log_text

    @patch("vox.ipc.extract_diff_pairs")
    def test_correction_logs_contain_no_text_content(
        self, mock_extract, client, mock_ledger, caplog
    ):
        """Correction handler logs app context but never the injected/corrected text."""
        mock_extract.return_value = [("teh", "the")]
        msg = {
            "type": "correction",
            "injected": "teh private sentence",
            "corrected": "the private sentence",
            "app_bundle_id": "com.apple.TextEdit",
        }

        with caplog.at_level(logging.DEBUG, logger="vox.ipc"):
            client.dispatch(msg)

        log_text = caplog.text
        assert "teh private sentence" not in log_text
        assert "the private sentence" not in log_text
        # Metadata is allowed.
        assert "com.apple.TextEdit" in log_text

    def test_malformed_json_log_contains_no_raw_content(self, client, caplog):
        """Malformed JSON warning does not echo the raw line content."""
        raw_line = '{"broken: "sensitive data here"}'
        data = raw_line + "\n"
        client._sock.recv.return_value = data.encode("utf-8")

        with caplog.at_level(logging.DEBUG, logger="vox.ipc"):
            client._read_messages()

        log_text = caplog.text
        assert "sensitive data here" not in log_text
        assert "Malformed JSON" in log_text

    def test_disconnect_log_contains_no_text_content(self, client, caplog):
        """Disconnect warning does not echo buffered data."""
        client._buffer = "some private buffered text"
        client._sock.recv.return_value = b""

        with caplog.at_level(logging.DEBUG, logger="vox.ipc"):
            client._read_messages()

        log_text = caplog.text
        assert "some private buffered text" not in log_text


# --------------------------------------------------------------------
# Send
# --------------------------------------------------------------------


class TestSend:
    """Verify _send serializes JSON with newline terminator."""

    def test_send_serializes_json_newline(self, client):
        """_send() writes JSON terminated by newline to the socket."""
        client._send({"type": "inject", "text": "hello"})

        sent = client._sock.sendall.call_args[0][0]
        assert sent.endswith(b"\n")
        parsed = json.loads(sent.decode("utf-8").strip())
        assert parsed == {"type": "inject", "text": "hello"}


# --------------------------------------------------------------------
# Run loop
# --------------------------------------------------------------------


class TestRunLoop:
    """Verify the run() main loop processes messages until disconnection."""

    @patch("vox.ipc.post_process")
    def test_run_processes_messages_until_disconnect(self, mock_pp, client):
        """run() reads and dispatches messages, stopping when disconnected."""
        mock_pp.return_value = "cleaned"
        msg_data = json.dumps({"type": "transcription", "raw": "hi"}) + "\n"

        # First recv returns a message, second returns empty (disconnect).
        client._sock.recv.side_effect = [
            msg_data.encode("utf-8"),
            b"",
        ]

        client.run()

        mock_pp.assert_called_once()
        # Socket should be None after disconnect.
        assert client._sock is None
