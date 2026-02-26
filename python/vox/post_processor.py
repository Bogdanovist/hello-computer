"""Post-processor — LLM-based transcript correction using the correction ledger."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

from vox.utils import levenshtein_distance

if TYPE_CHECKING:
    from vox.config import PostProcessingConfig, VoxConfig
    from vox.ledger import CorrectionRecord, Ledger

logger = logging.getLogger(__name__)


def construct_prompt(
    raw_transcript: str, corrections: list[CorrectionRecord],
) -> str:
    """Build the Ollama prompt from raw transcript and correction records.

    Corrections are ordered by confidence (highest first).
    Each correction record may contain multiple diff_pairs.
    """
    sorted_corrections = sorted(corrections, key=lambda c: c.confidence, reverse=True)

    correction_lines: list[str] = []
    for c in sorted_corrections:
        for original, replacement in c.diff_pairs:
            line = (
                f'- "{original}" \u2192 "{replacement}"'
                f" (confidence: {c.confidence:.2f})"
            )
            correction_lines.append(line)

    corrections_block = "\n".join(correction_lines)

    preamble = (
        "You are a transcription post-processor."
        " Fix the raw speech-to-text output using the user's"
        " known correction patterns listed below."
    )

    return f"""{preamble}

Rules:
- ONLY apply corrections you are confident about based on the patterns
- Do NOT rephrase, summarise, reformat, or add content
- Do NOT change words that are not in the correction patterns
- Preserve the user's exact phrasing \u2014 just fix transcription errors
- If unsure, leave the original text unchanged

## Known correction patterns (most confident first)
{corrections_block}

## Raw transcript
{raw_transcript}

## Output the corrected text only, no explanation:"""


def call_ollama(
    prompt: str, raw_transcript: str, config: PostProcessingConfig,
) -> str | None:
    """POST to Ollama /api/generate and return the response text.

    Returns None on connection error, timeout, or any other failure.
    """
    word_count = len(raw_transcript.split())
    try:
        response = requests.post(
            f"http://{config.ollama_host}:{config.ollama_port}/api/generate",
            json={
                "model": config.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": config.temperature,
                    "num_predict": word_count * 3,
                },
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "").strip()
    except requests.ConnectionError:
        return None
    except requests.Timeout:
        return None
    except Exception:
        return None


def validate_output(
    raw_transcript: str,
    llm_output: str | None,
    config: PostProcessingConfig,
) -> str:
    """Validate LLM output. Return cleaned text or fall back to raw transcript.

    Discards the LLM result when:
    - It is ``None`` or empty (Ollama failure)
    - The edit distance ratio exceeds the hallucination threshold (default 0.5)
    """
    if not llm_output:
        return raw_transcript

    if not llm_output.strip():
        return raw_transcript

    edit_dist = levenshtein_distance(raw_transcript, llm_output)
    max_len = max(len(raw_transcript), len(llm_output))
    if max_len > 0 and edit_dist / max_len > config.hallucination_threshold:
        return raw_transcript

    return llm_output


def post_process(
    raw_transcript: str,
    app_bundle_id: str | None,
    ledger: Ledger,
    config: VoxConfig,
) -> str:
    """Post-process a raw Whisper transcript using correction history.

    Returns the cleaned text, or *raw_transcript* on any failure or skip.
    """
    pp = config.post_processing

    if not pp.enabled:
        return raw_transcript

    corrections = ledger.query_relevant_corrections(
        raw_transcript=raw_transcript,
        app_bundle_id=app_bundle_id,
        limit=pp.max_correction_pairs_in_prompt,
        min_confidence=pp.confidence_threshold,
    )

    if not corrections:
        return raw_transcript

    prompt = construct_prompt(raw_transcript, corrections)
    llm_output = call_ollama(prompt, raw_transcript, pp)

    return validate_output(raw_transcript, llm_output, pp)
