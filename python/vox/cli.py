"""Vox CLI — management interface for the Vox voice-to-text system."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import click
import requests

from vox.config import load_config
from vox.ledger import Ledger

_SOCKET_PATH = "/tmp/vox.sock"
_DB_PATH = Path.home() / ".vox" / "corrections.db"
_SOCKET_TIMEOUT = 2


def _open_ledger() -> Ledger | None:
    """Open the correction ledger, returning ``None`` if unavailable."""
    if not _DB_PATH.exists():
        return None
    try:
        return Ledger(_DB_PATH, encryption_key=None)
    except Exception:
        return None


def _format_corrections_table(records: list) -> str:
    """Format a list of :class:`CorrectionRecord` as a table string."""
    header = (
        f"{'ID':<4}| {'Confidence':<11}| {'Seen':<5}| "
        f"{'Original':<16}| {'Corrected':<16}| App"
    )
    separator = (
        "────┼────────────┼──────┼"
        "─────────────────┼─────────────────┼──────────"
    )
    lines = [header, separator]
    for rec in records:
        app = rec.app_bundle_id if rec.app_bundle_id else "(any)"
        lines.append(
            f"{rec.id:<4}| {rec.confidence:<11.2f}| {rec.times_seen:<5}| "
            f"{rec.injected_text:<16}| {rec.corrected_text:<16}| {app}"
        )
    return "\n".join(lines)


def _query_daemon_status() -> dict | None:
    """Connect to the daemon socket and request status.

    Returns the parsed JSON response, or ``None`` when the daemon is
    unreachable.
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_SOCKET_TIMEOUT)
        sock.connect(_SOCKET_PATH)
        payload = json.dumps({"type": "control", "action": "status"}) + "\n"
        sock.sendall(payload.encode("utf-8"))
        data = sock.recv(4096)
        sock.close()
        if data:
            return json.loads(data.decode("utf-8").strip())
        return None
    except (OSError, json.JSONDecodeError, ConnectionRefusedError):
        return None


def _get_correction_counts() -> tuple[int, int]:
    """Return ``(active, disabled)`` correction counts from the ledger."""
    if not _DB_PATH.exists():
        return 0, 0
    try:
        ledger = Ledger(_DB_PATH, encryption_key=None)
        conn = ledger.connection
        active = conn.execute(
            "SELECT COUNT(*) FROM corrections WHERE active = 1",
        ).fetchone()[0]
        disabled = conn.execute(
            "SELECT COUNT(*) FROM corrections WHERE active = 0",
        ).fetchone()[0]
        ledger.close()
        return active, disabled
    except Exception:
        return 0, 0


@click.group()
def main() -> None:
    """Vox — self-improving local voice-to-text agent for macOS."""


# ------------------------------------------------------------------
# Top-level commands
# ------------------------------------------------------------------


@main.command()
def status() -> None:
    """Show daemon status, model info, and correction statistics."""
    config = load_config()

    click.echo("Vox Status")
    click.echo("──────────────────────────")

    daemon_info = _query_daemon_status()
    if daemon_info is not None:
        pid = daemon_info.get("pid", "?")
        click.echo(f"Daemon:       running (pid {pid})")
        click.echo(f"Uptime:       {daemon_info.get('uptime', '?')}")
        whisper = daemon_info.get(
            "whisper_model", config.dictation.whisper_model,
        )
        click.echo(f"Whisper:      {whisper} (loaded)")
        ollama_status = daemon_info.get("ollama_status", "unknown")
        pp = config.post_processing
        click.echo(
            f"Ollama:       {pp.ollama_model}"
            f" ({ollama_status}, {pp.ollama_host}:{pp.ollama_port})",
        )
    else:
        click.echo("Daemon:       not running")

    active, disabled = _get_correction_counts()
    suffix = " (from ledger)" if daemon_info is None else ""
    click.echo(f"Corrections:  {active} active, {disabled} disabled{suffix}")

    if daemon_info is not None:
        last = daemon_info.get("last_dictation")
        if last:
            click.echo(f"Last dictation: {last}")


@main.command()
@click.option("--full", is_flag=True, help="Pause everything (dictation + observation)."
              )
def pause(*, full: bool) -> None:
    """Pause correction observer (or everything with --full)."""
    raise NotImplementedError


@main.command()
def resume() -> None:
    """Resume from paused state."""
    raise NotImplementedError


@main.command("test-dictation")
def test_dictation() -> None:
    """Interactive diagnostic: record, transcribe, and display results."""
    raise NotImplementedError


@main.command("test-correction")
def test_correction() -> None:
    """Interactive diagnostic: inject test text and capture correction."""
    raise NotImplementedError


@main.command("test-ollama")
def test_ollama() -> None:
    """Verify Ollama endpoint, model, and latency."""
    config = load_config()
    pp = config.post_processing
    host = pp.ollama_host
    port = pp.ollama_port
    model = pp.ollama_model

    click.echo("Ollama Status")
    click.echo("──────────────────────────")

    # 1. Check endpoint reachability.
    try:
        resp = requests.get(f"http://{host}:{port}/", timeout=5)
        resp.raise_for_status()
        click.echo(f"Endpoint:   {host}:{port} ✓")
    except requests.ConnectionError:
        click.echo(f"Endpoint:   {host}:{port} ✗ (not reachable)")
        return
    except requests.Timeout:
        click.echo(f"Endpoint:   {host}:{port} ✗ (timeout)")
        return
    except requests.RequestException:
        click.echo(f"Endpoint:   {host}:{port} ✗ (error)")
        return

    # 2. Check model availability.
    try:
        resp = requests.get(f"http://{host}:{port}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        found = any(
            model == name or name == f"{model}:latest"
            for name in model_names
        )
        if found:
            click.echo(f"Model:      {model} ✓ (loaded)")
        else:
            click.echo(f"Model:      {model} ✗ (not found)")
            return
    except requests.RequestException:
        click.echo(f"Model:      {model} ✗ (could not query models)")
        return

    # 3. Send a test prompt and report latency.
    try:
        start = time.monotonic()
        resp = requests.post(
            f"http://{host}:{port}/api/generate",
            json={
                "model": model,
                "prompt": "Hello",
                "stream": False,
                "options": {"temperature": 0, "num_predict": 10},
            },
            timeout=30,
        )
        resp.raise_for_status()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result = resp.json().get("response", "").strip()
        click.echo(f'Test:       "Hello" → "{result}" ({elapsed_ms}ms)')
    except requests.RequestException as exc:
        click.echo(f"Test:       ✗ ({exc})")


# ------------------------------------------------------------------
# corrections subgroup
# ------------------------------------------------------------------


@main.group()
def corrections() -> None:
    """Manage the correction ledger."""


@corrections.command("list")
@click.option("--app", "app_bundle_id", default=None, help="Filter by app bundle ID.")
def corrections_list(*, app_bundle_id: str | None) -> None:
    """List active corrections with confidence scores."""
    ledger = _open_ledger()
    if ledger is None:
        click.echo("No corrections recorded yet.")
        return
    try:
        records = ledger.list_corrections(app_bundle_id=app_bundle_id)
        if not records:
            click.echo("No corrections recorded yet.")
            return
        click.echo(_format_corrections_table(records))
    finally:
        ledger.close()


@corrections.command()
@click.argument("term")
def search(*, term: str) -> None:
    """Search corrections by original or corrected text."""
    ledger = _open_ledger()
    if ledger is None:
        click.echo("No corrections recorded yet.")
        return
    try:
        records = ledger.search_corrections(term)
        if not records:
            click.echo("No corrections recorded yet.")
            return
        click.echo(_format_corrections_table(records))
    finally:
        ledger.close()


@corrections.command()
@click.argument("id", type=int)
def delete(*, id: int) -> None:
    """Permanently delete a correction."""
    raise NotImplementedError


@corrections.command()
@click.argument("id", type=int)
def disable(*, id: int) -> None:
    """Disable a correction (excluded from queries, preserved in DB)."""
    raise NotImplementedError


@corrections.command()
@click.argument("id", type=int)
def enable(*, id: int) -> None:
    """Re-enable a disabled correction."""
    raise NotImplementedError


@corrections.command("export")
def corrections_export() -> None:
    """Export all corrections as JSON to stdout."""
    raise NotImplementedError


@corrections.command("import")
@click.argument("file", type=click.Path(exists=True))
def corrections_import(*, file: str) -> None:
    """Import corrections from a JSON file."""
    raise NotImplementedError


@corrections.command()
@click.option("--confirm", is_flag=True, help="Required to actually reset.")
def reset(*, confirm: bool) -> None:
    """Delete all corrections (requires --confirm)."""
    raise NotImplementedError


# ------------------------------------------------------------------
# config subgroup
# ------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.pass_context
def config(ctx: click.Context) -> None:
    """View or modify Vox configuration."""
    if ctx.invoked_subcommand is None:
        raise NotImplementedError


@config.command("get")
@click.argument("key")
def config_get(*, key: str) -> None:
    """Print the value of a config key (dotted notation)."""
    raise NotImplementedError


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(*, key: str, value: str) -> None:
    """Set a config value (validates type before writing)."""
    raise NotImplementedError
