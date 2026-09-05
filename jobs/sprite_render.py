"""Handler for `sprite.render` — image-first pixel-art generation.

This is the primary path when a reference image is available (see
specs/0001-image-first-pipeline.md). Instead of an LLM drawing on a blank
canvas primitive by primitive, we:

    1. get a source image (an uploaded/AI reference, a photo URL, or an
       auto-generated concept image),
    2. run the deterministic `_render_core` pipeline
       (resize → quantize → background removal → despeckle),
    3. optionally run a short LLM "refine" pass seeded from that grid to fix
       localized defects only.
"""

from __future__ import annotations

import io
from typing import Iterator, Optional

from PIL import Image
from pydantic import BaseModel, Field

from . import Event, JobContext, JobHandler, log, progress, register_job, result
from ._render_core import render, render_png
from ._runtime import EventBridge, run_in_thread

# The standalone UI sends ["#c8a44e"] when the user picked no palette. A
# single-colour palette is never a deliberate choice, so treat len<=1 as
# "derive from the reference".


class SpriteRenderParams(BaseModel):
    reference_id: Optional[str] = None
    image_url: Optional[str] = None
    prompt: Optional[str] = None
    colors: list[str] = Field(default_factory=list)
    size: int = 32
    sprite_type: str = "block"
    model: Optional[str] = None            # image model for auto-reference
    max_colors: int = 16
    cleanup: bool = True
    remove_bg: Optional[bool] = None
    auto_reference: bool = True
    refine: bool = False
    refine_model: Optional[str] = None
    refine_steps: int = 20
    system_prompt: Optional[str] = None


@register_job("sprite.render")
class SpriteRenderHandler(JobHandler):
    Params = SpriteRenderParams

    def run(self, params: SpriteRenderParams, ctx: JobContext) -> Iterator[Event]:
        from server import upscale_image
        import storage

        external_id = ctx.external_id or ctx.job_id
        bridge = EventBridge()

        def worker() -> None:
            src = _load_source(params, storage, bridge)

            supplied = _explicit_palette(params.colors)
            bridge.emit(log(
                f"Rendering {params.size}×{params.size} pixel art"
                + ("" if supplied else " (deriving palette)"),
                step="render",
            ))
            grid, palette = render(
                src,
                size=params.size,
                sprite_type=params.sprite_type,
                colors=supplied,
                max_colors=params.max_colors,
                cleanup=params.cleanup,
                remove_bg=params.remove_bg,
            )
            bridge.emit(progress(pixel_data=grid, iteration=1, notes="Quantized", colors=palette))

            iterations = 1
            if params.refine:
                grid, iterations = _refine(params, external_id, grid, palette, bridge, ctx)

            out = render_png(grid, palette, params.size)
            filename = f"gen_{external_id}_{params.size}x{params.size}.png"
            storage.save_image(out, f"output/{filename}")
            storage.save_image(upscale_image(out, 512), f"output/gen_{external_id}_preview.png")

            bridge.emit(log("Done", step="complete"))
            bridge.emit(result(
                id=external_id,
                image_path=filename,
                iterations=iterations,
                pixel_data=grid,
                colors=palette,
                status="completed",
            ))

        run_in_thread(worker, bridge)
        yield from bridge.iter_events()


# ── source resolution ──

def _load_source(params: SpriteRenderParams, storage, bridge: EventBridge) -> Image.Image:
    if params.reference_id:
        bridge.emit(log("Loading reference image...", step="source"))
        data = storage.read_file(f"references/{params.reference_id}")
        if not data:
            raise FileNotFoundError(f"Reference {params.reference_id!r} not found")
        return Image.open(io.BytesIO(data)).convert("RGBA")

    if params.image_url:
        bridge.emit(log("Downloading image...", step="source"))
        import urllib.request

        req = urllib.request.Request(params.image_url, headers={"User-Agent": "texel-studio/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return Image.open(io.BytesIO(resp.read())).convert("RGBA")

    if params.prompt and params.auto_reference:
        bridge.emit(log("No reference — generating a concept image...", step="source"))
        from ._reference import generate_reference_png

        png = generate_reference_png(params.prompt, params.sprite_type, model=params.model)
        # Persist it so the UI / chat-continue can show the same reference.
        rid = f"ref_{params.reference_id or ''}_{abs(hash(params.prompt)) & 0xFFFFFF:06x}.png"
        storage.save_file(f"references/{rid}", png)
        params.reference_id = rid
        bridge.emit(log("Concept image ready", step="source", reference_id=rid))
        return Image.open(io.BytesIO(png)).convert("RGBA")

    raise ValueError(
        "sprite.render needs a reference_id, an image_url, or a prompt with "
        "auto_reference enabled."
    )


def _explicit_palette(colors: list[str]) -> Optional[list[str]]:
    """Return the caller's palette, or None when we should derive one."""
    if len(colors) <= 1:
        return None
    return colors


# ── optional refine pass ──

def _refine(params, external_id, grid, palette, bridge: EventBridge, ctx):
    from agent import run_agent_stream
    from server import DEFAULT_MODEL

    bridge.emit(log("Refining: fixing localized defects only...", step="refine"))
    steps = [0]

    def on_step(canvas, step_type, msg):
        steps[0] += 1
        bridge.emit(log(msg, step=f"refine_{step_type}_{steps[0]}"))
        if step_type == "tool_result":
            bridge.emit(progress(
                pixel_data=[row[:] for row in canvas.pixels],
                iteration=1 + steps[0],
                notes=f"Refine step {steps[0]}",
            ))

    canvas = run_agent_stream(
        gen_id=external_id,
        message=(
            (params.prompt or "")
            + "\n\nThe canvas already contains a faithful first-pass conversion "
            "of the reference. Fix ONLY obvious defects: stray pixels, broken "
            "edges, a handful of wrong-colored spots. Do not redraw or restyle "
            "anything else. Call finish when done."
        ),
        palette=palette,
        size=params.size,
        model_name=params.refine_model or DEFAULT_MODEL,
        style_prompt=params.system_prompt or "",
        sprite_type=params.sprite_type,
        on_step=on_step,
        max_steps=params.refine_steps,
        existing_pixels=grid,
        cancel_check=ctx.cancel_check,
    )
    return [row[:] for row in canvas.pixels], 1 + steps[0]
