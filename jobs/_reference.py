"""Concept/reference image generation (Gemini image models).

Extracted so both `sprite.reference` (returns a saved reference id) and
`sprite.render` (consumes the bytes directly for auto-reference) share one
implementation.
"""

from __future__ import annotations

import base64


class ReferenceGenError(RuntimeError):
    """Raised when the image model returns no usable image."""


def generate_reference_png(
    prompt: str,
    sprite_type: str = "block",
    feedback: str | None = None,
    model: str | None = None,
) -> bytes:
    """Generate a concept image and return its PNG bytes. Raises on failure."""
    from google import genai
    from server import (
        DEFAULT_IMAGE_MODEL,
        IMAGE_GEN_MODELS,
        SPRITE_TYPES,
        gemini,
    )

    type_config = SPRITE_TYPES.get(sprite_type, SPRITE_TYPES["block"])
    ref_prompt = f"{prompt}\n\n{type_config['ref_prompt']}"
    if feedback:
        ref_prompt += f"\n\nRevision feedback: {feedback}"

    img_model = model if model in IMAGE_GEN_MODELS else DEFAULT_IMAGE_MODEL

    try:
        response = gemini().models.generate_content(
            model=img_model,
            contents=[ref_prompt],
            config=genai.types.GenerateContentConfig(
                response_modalities=["Image", "Text"],
            ),
        )
    except Exception:
        response = gemini().models.generate_content(model=img_model, contents=[ref_prompt])

    png = _extract_png(response)
    if png is None:
        raise ReferenceGenError(
            "Image model returned no image. It may not support image generation, "
            "or billing is not enabled for the image model."
        )
    return png


def _extract_png(response) -> bytes | None:
    parts = list(getattr(response, "parts", None) or [])
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if content and getattr(content, "parts", None):
            parts.extend(content.parts)

    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        # Prefer PIL round-trip (normalizes to PNG); fall back to raw bytes.
        try:
            import io

            from PIL import Image

            buf = io.BytesIO()
            part.as_image().save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            data = inline.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            return data
    return None
