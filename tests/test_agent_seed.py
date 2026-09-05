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
