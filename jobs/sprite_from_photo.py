"""Handler for `sprite.from_photo` — quantize a user-uploaded photo to the
selected palette to produce a pixel-art sprite.

Since spec 0001 this is a thin wrapper over `_render_core` (the same pipeline
`sprite.render` uses): download the image, then run
resize → quantize → background-removal → despeckle. Kept as its own kind for
API back-compat (the cloud calls POST /api/jobs with kind="sprite.from_photo"
and an `image_url`).
"""

from __future__ import annotations

import io
import urllib.request
from typing import Iterator

from PIL import Image
from pydantic import BaseModel, Field

from . import Event, JobContext, JobHandler, log, progress, register_job, result
from ._render_core import render, render_png
from ._runtime import EventBridge, run_in_thread


class SpriteFromPhotoParams(BaseModel):
    image_url: str
    colors: list[str] = Field(default_factory=lambda: ["#000000", "#ffffff"])
    size: int = 32
    sprite_type: str = "block"


@register_job("sprite.from_photo")
class SpriteFromPhotoHandler(JobHandler):
    Params = SpriteFromPhotoParams

    def run(self, params: SpriteFromPhotoParams, ctx: JobContext) -> Iterator[Event]:
        from server import upscale_image
        import storage

        external_id = ctx.external_id or ctx.job_id
        bridge = EventBridge()

        def worker() -> None:
            bridge.emit(log("Downloading image...", step="download"))
            img_bytes = _download_image(params.image_url)
            src = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

            bridge.emit(log(
                f"Rendering {params.size}×{params.size} in {len(params.colors)} colors...",
                step="render",
            ))
            grid, palette = render(
                src,
                size=params.size,
                sprite_type=params.sprite_type,
                colors=params.colors,
            )

            bridge.emit(progress(pixel_data=grid, iteration=1, notes="Quantized"))

            out = render_png(grid, palette, params.size)
            filename = f"gen_{external_id}_{params.size}x{params.size}.png"
            storage.save_image(out, f"output/{filename}")
            storage.save_image(upscale_image(out, 512), f"output/gen_{external_id}_preview.png")

            bridge.emit(log("Done", step="complete"))
            bridge.emit(result(
                id=external_id,
                image_path=filename,
                iterations=1,
                pixel_data=grid,
                colors=palette,
                status="completed",
            ))

        run_in_thread(worker, bridge)
        yield from bridge.iter_events()


def _download_image(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "texel-studio/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()
