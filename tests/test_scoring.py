"""Completion score gate (spec 0007)."""

from __future__ import annotations

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring  # noqa: E402
from scoring import AssessmentScore, ScoreConfig, decide  # noqa: E402


def _s(score, gaps=None):
    return AssessmentScore(score=score, reason="r", gaps=gaps or [])


def test_decide_finalize_when_above_threshold():
    d = decide(_s(85), threshold=70)
    assert d.action == "finalize" and d.score == 85


def test_decide_ask_when_low_and_interactive():
    d = decide(_s(40, ["fix cap"]), threshold=70, on_low_score="ask")
    assert d.action == "ask"
    assert d.extra_steps == scoring.DEFAULT_EXTRA_STEPS
    assert d.gaps == ["fix cap"]


def test_decide_auto_continues():
    d = decide(_s(40), threshold=70, on_low_score="auto", extra_steps=100)
    assert d.action == "continue" and d.extra_steps == 100


def test_decide_ignore_finalizes():
    d = decide(_s(10), threshold=70, on_low_score="ignore")
    assert d.action == "finalize"


def test_decide_stops_after_max_rounds():
    d = decide(_s(30), threshold=70, on_low_score="auto", rounds_done=2, max_rounds=2)
    assert d.action == "finalize"


def test_scoreconfig_from_request_interactive_vs_headless():
    class Req:
        pass

    assert ScoreConfig.from_request(Req(), interactive=True).on_low_score == "ask"
    assert ScoreConfig.from_request(Req(), interactive=False).on_low_score == "auto"


def test_scoreconfig_respects_explicit_fields():
    class Req:
        score = True
        score_threshold = 55
        on_low_score = "ignore"
        extra_steps = 50
        max_score_rounds = 3
        score_model = "gpt-5.4-mini"

    c = ScoreConfig.from_request(Req())
    assert (c.threshold, c.on_low_score, c.extra_steps, c.max_rounds, c.model) == (
        55, "ignore", 50, 3, "gpt-5.4-mini",
    )


def test_gaps_to_instruction():
    assert "cap" in scoring.gaps_to_instruction(["cap too round"])
    assert "already works" in scoring.gaps_to_instruction([])


def test_assess_parses_model_output(monkeypatch):
    class FakeStructured:
        def invoke(self, messages):
            return AssessmentScore(score=250, reason="ok", gaps=["a", "", "b"])

    class FakeLLM:
        def with_structured_output(self, schema):
            return FakeStructured()

    import agent

    monkeypatch.setattr(agent, "_get_llm", lambda *a, **k: FakeLLM())
    monkeypatch.setattr(agent, "_is_vision_model", lambda m: True)

    out = scoring.assess(
        goal="a red mushroom", sprite_type="icon",
        final_img=Image.new("RGBA", (16, 16), (255, 0, 0, 255)),
        model_name="fake",
    )
    assert out.score == 100          # clamped
    assert out.gaps == ["a", "b"]    # falsy dropped


def test_run_gate_disabled_returns_none():
    cfg = ScoreConfig(enabled=False)
    assert scoring.run_gate(
        goal="x", sprite_type="icon",
        final_img=Image.new("RGBA", (8, 8)), gen_model="m", cfg=cfg,
    ) is None


def test_run_gate_swallows_model_error(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no creds")

    monkeypatch.setattr(scoring, "assess", boom)
    cfg = ScoreConfig(enabled=True)
    assert scoring.run_gate(
        goal="x", sprite_type="icon",
        final_img=Image.new("RGBA", (8, 8)), gen_model="m", cfg=cfg,
    ) is None
