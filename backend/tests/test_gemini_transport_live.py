"""GeminiTransport — live smoke only (never in default CI runs, CODING.md
§3): proves text-only AND multimodal (image bytes) calls actually reach
Gemini and come back as parsed JSON. Run locally with GEMINI_API_KEY
exported to exercise it.
"""

import os

import pytest

live = pytest.mark.skipif(
    "GEMINI_API_KEY" not in os.environ,
    reason="live smoke: needs a real GEMINI_API_KEY exported in the shell",
)
pytestmark = live


async def test_text_only_call_returns_parsed_json():
    from app.llm.transport import GeminiTransport

    transport = GeminiTransport(api_key=os.environ["GEMINI_API_KEY"], timeout_seconds=60.0)
    result = await transport.generate(
        model="gemini-3.5-flash",
        prompt='Reply with exactly this JSON and nothing else: {"pong": true}',
        temperature=0.0,
    )
    assert result == {"pong": True}


async def test_multimodal_call_with_image_bytes_reaches_the_model():
    import pymupdf

    from app.llm.transport import GeminiTransport

    # A plain red square — reuses pymupdf (already a dependency, V-007's
    # own crop path) instead of adding an image library just for this test.
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 40, 40), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    png_bytes = pix.tobytes("png")

    transport = GeminiTransport(api_key=os.environ["GEMINI_API_KEY"], timeout_seconds=60.0)
    result = await transport.generate(
        model="gemini-3.5-flash",
        prompt=(
            "This image is a solid color square. Reply with exactly this JSON and "
            'nothing else: {"saw_image": true}'
        ),
        temperature=0.0,
        images=[png_bytes],
    )
    assert result == {"saw_image": True}
