"""Completion score gate (spec 0007).

Before a drawing job is reported complete, an LLM is shown the final rendered
sprite plus the user's goal (and the reference, if any) and returns a closeness
score 0-100, a one-line reason, and a short list of concrete gaps.

`assess()` does the LLM call. `decide()` turns a score + config into an action
(`finalize` / `ask` / `continue`). Keeping them separate makes the policy
unit-testable without a model.
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

# Env defaults (request/params can override).
DEFAULT_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))
DEFAULT_EXTRA_STEPS = int(os.getenv("SCORE_EXTRA_STEPS", "100"))
DEFAULT_MAX_ROUNDS = int(os.getenv("SCORE_MAX_ROUNDS", "2"))


class AssessmentScore(BaseModel):
    score: int = Field(description="0-100, how close the sprite is to the user's goal")
    reason: str = Field(description="One sentence explaining the score")
    gaps: list[str] = Field(
        default_factory=list,
        description="Up to 5 concrete, fixable issues (empty if the sprite is good)",
    )


class ScoreConfig(BaseModel):
    """Resolved scoring settings for one generation."""
    enabled: bool = True
    threshold: int = DEFAULT_THRESHOLD
    on_low_score: str = "ask"          # "ask" | "auto" | "ignore"
    extra_steps: int = DEFAULT_EXTRA_STEPS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    model: str | None = None           # scoring model; None => generation model

    @classmethod
    def from_request(cls, data: Any, *, interactive: bool = True) -> "ScoreConfig":
        """Build from a GenerateRequest-like object; safe on missing attrs."""
        g = lambda k, d=None: getattr(data, k, d)
        on_low = g("on_low_score") or ("ask" if interactive else "auto")
        return cls(
            enabled=g("score", True) is not False,
            threshold=int(g("score_threshold") or DEFAULT_THRESHOLD),
            on_low_score=on_low,
            extra_steps=int(g("extra_steps") or DEFAULT_EXTRA_STEPS),
            max_rounds=int(g("max_score_rounds") or DEFAULT_MAX_ROUNDS),
            model=g("score_model"),
        )


Action = Literal["finalize", "ask", "continue"]


class Decision(BaseModel):
    action: Action
    score: int
    reason: str
    gaps: list[str] = Field(default_factory=list)
    extra_steps: int = 0
    round: int = 0


_SYS = (
    "You are a strict pixel-art art director. You are shown a finished sprite and "
    "the brief it was made for. Judge only how well the sprite matches the brief "
    "and reads as clean pixel art at its native size: correct subject and "
    "silhouette, sensible colors, no stray pixels or broken edges. Be honest and "
    "harsh. Return a score from 0 (unrecognizable / wrong) to 100 (ship it), a "
    "one-sentence reason, and up to 5 specific fixable gaps."
)


def _png_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def assess(
    *,
    goal: str,
    sprite_type: str,
    final_img,
    reference_b64: str | None = None,
    model_name: str = "",
    temperature: float = 0.2,
    upscale: int = 256,
) -> AssessmentScore:
    """Score `final_img` (a PIL image) against `goal`. Raises on model failure."""
    from PIL import Image
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent import _get_llm, _is_vision_model

    big = final_img.convert("RGBA").resize((upscale, upscale), Image.NEAREST)

    parts: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"BRIEF: {goal}\n"
            f"SPRITE TYPE: {sprite_type}\n\n"
            "The FINISHED sprite is attached"
            + (" (second image is the reference it should match)." if reference_b64 else ".")
        ),
    }, {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{_png_b64(big)}"},
    }]
    if reference_b64 and _is_vision_model(model_name):
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{reference_b64}"},
        })

    llm = _get_llm(model_name, temperature=temperature).with_structured_output(AssessmentScore)
    result = llm.invoke([SystemMessage(content=_SYS), HumanMessage(content=parts)])
    if isinstance(result, dict):
        result = AssessmentScore(**result)
    result.score = max(0, min(100, int(result.score)))
    result.gaps = [g for g in (result.gaps or []) if g][:5]
    return result


def decide(
    score: AssessmentScore,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    on_low_score: str = "ask",
    extra_steps: int = DEFAULT_EXTRA_STEPS,
    rounds_done: int = 0,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> Decision:
    """Pure policy: what to do given a score and the config."""
    base = dict(score=score.score, reason=score.reason, gaps=score.gaps, round=rounds_done)

    if score.score >= threshold or on_low_score == "ignore" or rounds_done >= max_rounds:
        return Decision(action="finalize", **base)
    if on_low_score == "auto":
        return Decision(action="continue", extra_steps=extra_steps, **base)
    return Decision(action="ask", extra_steps=extra_steps, **base)


def run_gate(
    *,
    goal: str,
    sprite_type: str,
    final_img,
    gen_model: str,
    cfg: ScoreConfig,
    reference_b64: str | None = None,
    rounds_done: int = 0,
) -> Decision | None:
    """assess + decide in one call. Returns None if scoring is disabled or the
    model call fails (caller should then just finalize)."""
    if not cfg.enabled:
        return None
    try:
        score = assess(
            goal=goal,
            sprite_type=sprite_type,
            final_img=final_img,
            reference_b64=reference_b64,
            model_name=cfg.model or gen_model,
        )
    except Exception:
        return None
    return decide(
        score,
        threshold=cfg.threshold,
        on_low_score=cfg.on_low_score,
        extra_steps=cfg.extra_steps,
        rounds_done=rounds_done,
        max_rounds=cfg.max_rounds,
    )


def gaps_to_instruction(gaps: list[str]) -> str:
    if not gaps:
        return (
            "Improve the sprite so it matches the brief more closely. Fix the "
            "silhouette, colors, and any stray or broken pixels. Do not restyle "
            "what already works. Call finish when done."
        )
    bullets = "\n".join(f"- {g}" for g in gaps)
    return (
        "Improve the sprite. Fix ONLY these specific gaps, and do not restyle "
        f"anything that already works:\n{bullets}\nCall finish when done."
    )
