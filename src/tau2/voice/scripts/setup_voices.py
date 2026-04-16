#!/usr/bin/env python3
# Copyright Sierra
"""Create ElevenLabs voices for all τ-bench personas via the Voice Design API.

This script automates the voice setup described in docs/voice-personas.md.
It generates a voice for each persona using the ElevenLabs Voice Design API,
saves it to your ElevenLabs account, and prints the environment variables
you need to add to your .env file.

Usage:
    # Create all 7 voices
    python -m tau2.voice.scripts.setup_voices

    # Create only control personas (for quick testing)
    python -m tau2.voice.scripts.setup_voices --control-only

    # Dry run — show what would be created without calling the API
    python -m tau2.voice.scripts.setup_voices --dry-run

    # Preview voices before saving (plays audio)
    python -m tau2.voice.scripts.setup_voices --preview

Requirements:
    - ELEVENLABS_API_KEY set in your environment or .env file
    - uv sync --extra voice
"""

import argparse
import sys
import time

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

SAMPLE_TEXT = (
    "Hi, I'm calling about an issue with my account. "
    "I tried logging in earlier today but it keeps saying my password is wrong. "
    "I'm pretty sure I haven't changed it recently, "
    "so I'm not sure what's going on. "
    "Could you help me figure this out?"
)

# Voice Design parameters matching the docs/voice-personas.md recommendations.
# UI percentage → API value mapping:
#   loudness: UI 0–100% maps to API -1.0–1.0  (75% → 0.5)
#   guidance_scale: UI 0–100% maps to API 0–100  (38% → 38)
VOICE_DESIGN_LOUDNESS = 0.5  # 75% in the ElevenLabs UI
VOICE_DESIGN_GUIDANCE_SCALE = 38  # 38% in the ElevenLabs UI
VOICE_DESIGN_MODEL = "eleven_multilingual_ttv_v2"

# ElevenLabs voice_description has a 1000-char limit. The full persona prompts
# include punctuation/prosody guidelines meant for the LLM, not voice generation.
# We truncate to the voice-relevant content (character description).
VOICE_DESCRIPTION_MAX_CHARS = 1000


def _truncate_voice_description(prompt: str) -> str:
    """Truncate a persona prompt to fit the Voice Design API's 1000-char limit.

    Splits on double-newlines (paragraph breaks) and keeps as many complete
    paragraphs as fit. The punctuation/prosody guideline sections (bullet
    lists at the end) are trimmed since they don't affect voice generation.
    """
    if len(prompt) <= VOICE_DESCRIPTION_MAX_CHARS:
        return prompt

    paragraphs = prompt.split("\n\n")
    result = ""
    for para in paragraphs:
        candidate = (result + "\n\n" + para).strip() if result else para.strip()
        if len(candidate) <= VOICE_DESCRIPTION_MAX_CHARS:
            result = candidate
        else:
            break
    return result or prompt[:VOICE_DESCRIPTION_MAX_CHARS]


def setup_voices(
    control_only: bool = False,
    dry_run: bool = False,
    preview: bool = False,
) -> dict[str, str]:
    """Create ElevenLabs voices for τ-bench personas.

    Returns:
        Mapping of persona_name → voice_id for all created voices.
    """
    from elevenlabs import ElevenLabs

    from tau2.data_model.voice_personas import (
        ALL_PERSONAS,
        CONTROL_PERSONAS,
    )

    personas = CONTROL_PERSONAS if control_only else list(ALL_PERSONAS.values())

    if dry_run:
        print("\n=== DRY RUN — no API calls will be made ===\n")
        print("Would create voices for the following personas:\n")
        for p in personas:
            env_key = f"TAU2_VOICE_ID_{p.name.upper()}"
            print(f"  {p.display_name} ({p.name})")
            print(f"    Complexity: {p.complexity}")
            print(f"    Env var: {env_key}")
            print(f"    Prompt: {p.prompt[:80]}...")
            print()
        print(f"Voice Design settings:")
        print(f"  Model: {VOICE_DESIGN_MODEL}")
        print(f"  Loudness: {VOICE_DESIGN_LOUDNESS} (75% in UI)")
        print(f"  Guidance scale: {VOICE_DESIGN_GUIDANCE_SCALE} (38% in UI)")
        return {}

    client = ElevenLabs()
    created_voices: dict[str, str] = {}

    print(f"\nCreating {len(personas)} voice(s) via ElevenLabs Voice Design API...\n")
    print(f"  Model: {VOICE_DESIGN_MODEL}")
    print(f"  Loudness: {VOICE_DESIGN_LOUDNESS} (75% in UI)")
    print(f"  Guidance scale: {VOICE_DESIGN_GUIDANCE_SCALE} (38% in UI)")
    print()

    for i, persona in enumerate(personas, 1):
        print(f"[{i}/{len(personas)}] Creating voice: {persona.display_name}")
        print(f"  Description: {persona.short_description}")

        try:
            voice_description = _truncate_voice_description(persona.prompt)
            if len(voice_description) < len(persona.prompt):
                print(
                    f"  (prompt truncated from {len(persona.prompt)} to "
                    f"{len(voice_description)} chars for API limit)"
                )

            # Step 1: Generate previews
            result = client.text_to_voice.design(
                voice_description=voice_description,
                text=SAMPLE_TEXT,
                model_id=VOICE_DESIGN_MODEL,
                loudness=VOICE_DESIGN_LOUDNESS,
                guidance_scale=VOICE_DESIGN_GUIDANCE_SCALE,
                auto_generate_text=False,
            )

            if not result.previews:
                print(f"  ERROR: No previews returned for {persona.display_name}")
                continue

            selected_preview = result.previews[0]
            print(
                f"  Generated {len(result.previews)} preview(s), "
                f"using first: {selected_preview.generated_voice_id}"
            )

            if preview:
                _play_preview(selected_preview)

            # Step 2: Save the voice to the account
            voice = client.text_to_voice.create(
                voice_name=f"tau2_{persona.name}",
                voice_description=persona.short_description,
                generated_voice_id=selected_preview.generated_voice_id,
            )

            created_voices[persona.name] = voice.voice_id
            print(f"  Saved as voice_id: {voice.voice_id}")
            print()

            # Brief pause to be polite to the API
            if i < len(personas):
                time.sleep(1)

        except Exception as e:
            print(f"  ERROR: Failed to create voice for {persona.display_name}: {e}")
            print()
            continue

    return created_voices


def _play_preview(preview) -> None:
    """Play a voice preview through speakers."""
    import base64

    try:
        from elevenlabs.play import play

        audio_bytes = base64.b64decode(preview.audio_base_64)
        print("  Playing preview...")
        play(audio_bytes)
    except ImportError:
        print("  (skipping playback — elevenlabs.play not available)")
    except Exception as e:
        print(f"  (playback failed: {e})")


def print_env_block(created_voices: dict[str, str]) -> None:
    """Print the env var block to paste into .env."""
    if not created_voices:
        return

    print("=" * 60)
    print("Add the following to your .env file:")
    print("=" * 60)
    print()
    for name, voice_id in created_voices.items():
        env_key = f"TAU2_VOICE_ID_{name.upper()}"
        print(f"{env_key}={voice_id}")
    print()
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Create ElevenLabs voices for τ-bench personas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create all 7 voices
  python -m tau2.voice.scripts.setup_voices

  # Create only the 2 control personas (for quick testing)
  python -m tau2.voice.scripts.setup_voices --control-only

  # See what would be created without calling the API
  python -m tau2.voice.scripts.setup_voices --dry-run

  # Preview each voice before saving
  python -m tau2.voice.scripts.setup_voices --preview

See docs/voice-personas.md for the full setup guide.
""",
    )
    parser.add_argument(
        "--control-only",
        action="store_true",
        help="Only create the 2 control personas (Matt Delaney, Lisa Brenner). "
        "Sufficient for running with --speech-complexity control.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without calling the API.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Play each voice preview before saving.",
    )

    args = parser.parse_args()
    logger.configure(handlers=[{"sink": sys.stderr, "level": "WARNING"}])

    created_voices = setup_voices(
        control_only=args.control_only,
        dry_run=args.dry_run,
        preview=args.preview,
    )

    if created_voices:
        print_env_block(created_voices)
        print(
            f"Done! Created {len(created_voices)} voice(s). "
            "Copy the lines above into your .env file."
        )
    elif not args.dry_run:
        print("\nNo voices were created. Check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
