#!/usr/bin/env python3
"""Debug script to test youtube_v2 prompt and see LLM response."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from openai import OpenAI

from obsidian_ai_tools.config import get_settings
from obsidian_ai_tools.llm import build_prompt, load_prompt_template, parse_llm_response
from obsidian_ai_tools.youtube import get_video_metadata

# Get settings
settings = get_settings()

# Test video
url = "https://www.youtube.com/watch?v=VHHT6W-N0ak"

print("Fetching video metadata...")
metadata = get_video_metadata(url)
print(f"✓ Fetched transcript ({len(metadata.transcript)} chars)")
print(f"✓ Language: {metadata.source_language}")
print()

# Load youtube_v2 prompt
print("Loading youtube_v2 prompt...")
template = load_prompt_template("youtube_v2")
prompt = build_prompt(metadata, template, None)
print(f"✓ Prompt built ({len(prompt)} chars)")
print()

# Call LLM
print("Calling LLM...")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.openrouter_api_key,
)

response = client.chat.completions.create(
    model=settings.llm_model,
    messages=[{"role": "user", "content": prompt}],
    temperature=0.7,
)

response_text = response.choices[0].message.content
print("=" * 80)
print("LLM RESPONSE:")
print("=" * 80)
print(response_text)
print("=" * 80)
print()

# Try to parse
print("Attempting to parse...")

# Ensure response_text is not None for type checker
assert response_text is not None, "LLM returned empty response"

try:
    result = parse_llm_response(response_text)
    print("✓ Parse successful!")
    print(f"  Title: {result.get('title')}")
    print(f"  Tags: {result.get('tags')}")
except Exception as e:
    print(f"✗ Parse failed: {e}")
    print()
    print("Debugging extraction...")
    if "```json" in response_text:
        start = response_text.find("```json") + 7
        end = response_text.find("```", start)
        json_str = response_text[start:end].strip()
        print("Extracted JSON (from ```json block):")
        print(repr(json_str[:200]))
    elif "```" in response_text:
        start = response_text.find("```") + 3
        end = response_text.find("```", start)
        json_str = response_text[start:end].strip()
        print("Extracted JSON (from ``` block):")
        print(repr(json_str[:200]))
