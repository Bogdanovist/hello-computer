"""Vox CLI — management interface for the Vox voice-to-text system."""

from __future__ import annotations

import click


@click.group()
def main() -> None:
    """Vox — self-improving local voice-to-text agent for macOS."""


# ------------------------------------------------------------------
# Top-level commands
# ------------------------------------------------------------------


@main.command()
def status() -> None:
    """Show daemon status, model info, and correction statistics."""
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


@corrections.command()
@click.argument("term")
def search(*, term: str) -> None:
    """Search corrections by original or corrected text."""
    raise NotImplementedError


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
