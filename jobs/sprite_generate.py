"""Handler for `sprite.generate` — initial AI sprite painting from a prompt."""

from __future__ import annotations

from typing import Iterator, Optional

from pydantic import BaseModel, Field

from . import Event, JobContext, JobHandler, log, progress, result, register_job
from ._runtime import EventBridge, run_in_thread


class SpriteGenerateParams(BaseModel):
    prompt: str
    colors: list[str] = Field(default_factory=lambda: ["#c8a44e"])
    size: int = 16
    model: Optional[str] = None
    sprite_type: str = "block"
    system_prompt: Optional[str] = None
    reference_id: Optional[str] = None
    seed_mode: str = "soft"       # spec 0002 — "soft" | "locked" | "off"
    # spec 0007 — completion score gate (headless: auto-continue on low score)
    score: bool = True
    score_threshold: Optional[int] = None
    score_model: Optional[str] = None
    on_low_score: Optional[str] = None
    extra_steps: Optional[int] = None
    max_score_rounds: Optional[int] = None


@register_job("sprite.generate")
class SpriteGenerateHandler(JobHandler):
    Params = SpriteGenerateParams

    def run(self, params: SpriteGenerateParams, ctx: JobContext) -> Iterator[Event]:
        # Local imports keep import-time cheap.
        from agent import run_agent_stream
        from server import (
            DEFAULT_MODEL,
            DEFAULT_SYSTEM_PROMPT,
            load_reference_b64,
            upscale_image,
        )
        import storage

        size = params.size
        model = params.model or DEFAULT_MODEL
        system_prompt = params.system_prompt or DEFAULT_SYSTEM_PROMPT
        ref_b64 = load_reference_b64(params.reference_id)
        external_id = ctx.external_id or ctx.job_id

        bridge = EventBridge()
        bridge.emit(log(f"Agent painting {size}x{size} with {model}...", step="start"))

        step_count = [0]
        last_pixel_step = [0]

        def on_step(canvas, step_type, msg):
            step_count[0] += 1
            bridge.emit(log(msg, step=f"{step_type}_{step_count[0]}"))
            if step_type == "tool_result" and (step_count[0] - last_pixel_step[0] >= 1):
                last_pixel_step[0] = step_count[0]
                bridge.emit(progress(
                    pixel_data=[row[:] for row in canvas.pixels],
                    iteration=step_count[0],
                    notes=f"Step {step_count[0]}",
                ))

        def worker():
            import scoring

            # Headless: default to auto-continue (no user to ask).
            cfg = scoring.ScoreConfig.from_request(params, interactive=False)
            filename = f"gen_{external_id}_{size}x{size}.png"
            msg = params.prompt
            existing = None
            rounds = 0
            decision = None

            while True:
                canvas = run_agent_stream(
                    gen_id=external_id,
                    message=msg,
                    palette=params.colors,
                    size=size,
                    model_name=model,
                    style_prompt=system_prompt,
                    sprite_type=params.sprite_type,
                    reference_b64=ref_b64,
                    on_step=on_step,
                    existing_pixels=existing,
                    cancel_check=ctx.cancel_check,
                    seed_mode=(params.seed_mode if rounds == 0 else "off"),
                    **({"max_steps": cfg.extra_steps} if rounds else {}),
                )

                final_pixels = [row[:] for row in canvas.pixels]
                existing = final_pixels
                bridge.emit(progress(pixel_data=final_pixels, iteration=step_count[0], notes="Agent finished"))

                final_img = canvas.to_image()
                storage.save_image(final_img, f"output/{filename}")
                storage.save_image(upscale_image(final_img, 512), f"output/gen_{external_id}_preview.png")

                decision = None
                if cfg.enabled:
                    bridge.emit(log("Scoring result against the prompt...", step="scoring"))
                    decision = scoring.run_gate(
                        goal=params.prompt, sprite_type=params.sprite_type, final_img=final_img,
                        gen_model=model, cfg=cfg, reference_b64=ref_b64, rounds_done=rounds,
                    )
                if decision is not None and decision.action == "continue":
                    rounds += 1
                    msg = scoring.gaps_to_instruction(decision.gaps)
                    bridge.emit(log(f"Score {decision.score} < {cfg.threshold} — +{cfg.extra_steps} steps", step="continue"))
                    continue
                break

            bridge.emit(log(f"Done in {step_count[0]} steps", step="complete"))
            res: dict = dict(
                id=external_id, image_path=filename, iterations=step_count[0],
                pixel_data=final_pixels, status="completed",
            )
            if decision is not None:
                res.update(score=decision.score, score_reason=decision.reason, gaps=decision.gaps)
            bridge.emit(result(**res))

        run_in_thread(worker, bridge)
        yield from bridge.iter_events()
