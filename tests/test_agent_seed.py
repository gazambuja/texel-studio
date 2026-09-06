"""Seed-awareness of the agent (spec 0001 task 7)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402


def test_has_content():
    assert agent._has_content([[0, -1], [-1, -1]]) is True
    assert agent._has_content([[-1, -1], [-1, -1]]) is False
    assert agent._has_content(None) is False
    assert agent._has_content([]) is False


def test_canvas_grid_string_roundtrips_seed():
    c = agent.Canvas(3, ["#000000", "#ffffff"], [[0, 1, -1], [-1, 0, 1], [1, -1, 0]])
    grid = c.to_grid_string()
    assert "0" in grid and "1" in grid
    # the seeded system-prompt branch embeds exactly this string
    assert grid.count("\n") >= 3


def test_build_refine_prompt_says_do_not_redraw():
    p = agent.build_refine_prompt(
        "a red mushroom", ["#c83232", "#e6d2aa"], 16, "", "icon",
        "................\n....0000....\n", has_reference=True, locked=False,
    )
    assert "REFINING an existing" in p
    assert "do NOT redraw" in p or "not to draw a new one" in p
    assert "CURRENT CANVAS STATE:" in p
    assert "0000" in p                       # grid embedded
    assert "reference image is attached" in p


def test_build_refine_prompt_locked_variant():
    p = agent.build_refine_prompt("x", ["#000"], 8, "", "icon", "", has_reference=False, locked=True)
    assert "LOCKED" in p
    assert "reference image is attached" not in p


def test_run_agent_stream_seeded_uses_refine_prompt(monkeypatch):
    captured = {}

    class _Noop:
        def stream(self, inp, **kw):
            captured["messages"] = inp["messages"]
            return iter(())

    monkeypatch.setattr(agent, "thread_exists", lambda gid: False)
    monkeypatch.setattr(agent, "_get_llm", lambda *a, **k: object())
    monkeypatch.setattr(agent, "get_checkpointer", lambda: None)
    monkeypatch.setattr(agent, "create_react_agent", lambda *a, **k: _Noop())

    grid = [[0 if 2 <= x <= 13 and 2 <= y <= 13 else -1 for x in range(16)] for y in range(16)]
    agent.run_agent_stream(
        gen_id="refine_seed_1", message="a red mushroom", palette=["#c83232", "#e6d2aa"],
        size=16, model_name="fake", sprite_type="icon", existing_pixels=grid,
    )
    text = captured["messages"][0].content[0]["text"]
    assert "REFINING an existing" in text
    assert "You are a pixel artist" not in text     # NOT the from-scratch prompt
