"""Silhouette-first phased workflow (spec 0005)."""

from __future__ import annotations

import base64
import io
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
from jobs._render_core import iou, silhouette_of  # noqa: E402


# ── IoU helpers ──

def test_iou_and_silhouette():
    assert iou(set(), set()) == 1.0
    assert iou({(0, 0)}, set()) == 0.0
    assert iou({(0, 0), (1, 1)}, {(0, 0)}) == 0.5
    assert silhouette_of([[0, -1], [-1, 3]]) == {(0, 0), (1, 1)}


# ── phase tool filtering ──

def test_silhouette_phase_drops_pixel_and_finish_tools():
    c = agent.Canvas(16, ["#000000", "#ffffff"])
    names = {t.name for t in agent.make_tools(c, phase="silhouette")}
    assert "finish" not in names and "draw_pixel" not in names and "draw_pixels" not in names
    assert "fill_rect" in names and "view_canvas" in names


def test_detail_phase_has_finish():
    c = agent.Canvas(16, ["#000000", "#ffffff"])
    assert "finish" in {t.name for t in agent.make_tools(c, phase="detail")}


def test_base_colors_phase_has_pixels_no_finish():
    c = agent.Canvas(16, ["#000000", "#ffffff"])
    names = {t.name for t in agent.make_tools(c, phase="base_colors")}
    assert "draw_pixel" in names and "finish" not in names


# ── orchestrator ──

def _ref_b64():
    img = Image.new("RGBA", (32, 32), (240, 240, 240, 255))
    ImageDraw.Draw(img).ellipse([6, 6, 26, 26], fill=(200, 50, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_phased_runs_one_call_per_phase(monkeypatch):
    calls = []

    def fake_run(**kw):
        calls.append(kw["phase"])
        c = agent.Canvas(kw["size"], kw["palette"], kw.get("existing_pixels"))
        c.fill_rect(2, 2, 13, 13, 0)          # a healthy silhouette every time
        return c

    monkeypatch.setattr(agent, "run_agent_stream", fake_run)
    phases = []
    agent.run_phased_generation(
        gen_id="p1", message="a red dome", palette=["#c83232", "#f0f0f0"], size=16,
        model_name="fake", sprite_type="icon", reference_b64=None,
        on_step=lambda canvas, t, m: phases.append(m) if t == "phase" else None,
    )
    assert calls == ["silhouette", "base_colors", "shading", "detail", "cleanup"]
    assert phases[:5] == ["silhouette", "base_colors", "shading", "detail", "cleanup"]


def test_phased_gate_retries_on_low_iou(monkeypatch):
    agent.SILHOUETTE_RETRIES = 2
    n = {"i": 0}

    def fake_run(**kw):
        c = agent.Canvas(kw["size"], kw["palette"], kw.get("existing_pixels"))
        if kw["phase"] == "silhouette":
            n["i"] += 1
            # first two silhouette attempts are bad, third is good
            if n["i"] >= 3:
                c.fill_rect(3, 3, 12, 12, 0)
            else:
                c.set_pixel(0, 0, 0)
        else:
            c.fill_rect(3, 3, 12, 12, 0)
        return c

    monkeypatch.setattr(agent, "run_agent_stream", fake_run)
    markers = []
    agent.run_phased_generation(
        gen_id="p2", message="a red dome", palette=["#c83232", "#f0f0f0"], size=16,
        model_name="fake", sprite_type="icon", reference_b64=_ref_b64(),
        on_step=lambda canvas, t, m: markers.append(m) if t == "phase" else None,
    )
    # 1 initial + 2 retries of the silhouette phase
    assert n["i"] == 3
    assert any("retry" in m for m in markers)


def test_phased_carries_pixels_between_phases(monkeypatch):
    seen_existing = []

    def fake_run(**kw):
        seen_existing.append(kw.get("existing_pixels"))
        c = agent.Canvas(kw["size"], kw["palette"], kw.get("existing_pixels"))
        c.set_pixel(len(seen_existing), 0, 0)
        return c

    monkeypatch.setattr(agent, "run_agent_stream", fake_run)
    agent.run_phased_generation(
        gen_id="p3", message="x", palette=["#000000", "#ffffff"], size=16,
        model_name="fake", sprite_type="icon", reference_b64=None,
    )
    # first phase gets None, later phases get the accumulated grid
    assert seen_existing[0] is None
    assert seen_existing[1] is not None and any(v == 0 for row in seen_existing[1] for v in row)
