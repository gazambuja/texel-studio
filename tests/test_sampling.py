"""Deterministic drawing config (spec 0006)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
from agent import SamplingConfig, _get_llm, _seed_from, resolve_sampling  # noqa: E402


def test_sampling_config_default_temperature():
    assert SamplingConfig().temperature == agent.AGENT_TEMPERATURE == 0.2
    assert SamplingConfig().seed is None


def test_resolution_order_request_beats_phase_beats_env():
    assert resolve_sampling(request_temperature=0.9, phase="silhouette").temperature == 0.9
    assert resolve_sampling(phase="silhouette").temperature == agent.PHASE_TEMPERATURE["silhouette"]
    assert resolve_sampling(phase="detail").temperature == agent.PHASE_TEMPERATURE["detail"]
    assert resolve_sampling(phase="unknown-phase").temperature == agent.AGENT_TEMPERATURE
    assert resolve_sampling().temperature == agent.AGENT_TEMPERATURE


def test_seed_derivation_is_deterministic_and_per_gen():
    assert _seed_from("job_5") == _seed_from("job_5")
    assert _seed_from("job_5") != _seed_from("job_6")
    assert 0 <= _seed_from("anything") <= 0x7FFFFFFF


def test_seed_resolution_request_beats_gen_id():
    assert resolve_sampling(gen_id="job_5").seed == _seed_from("job_5")
    assert resolve_sampling(request_seed=42, gen_id="job_5").seed == 42
    assert resolve_sampling().seed is None


def test_phase_temperatures_cover_all_phases():
    phase_names = [p[0] for p in agent.AGENT_PHASES]
    assert set(phase_names) == set(agent.PHASE_TEMPERATURE)
    # precision phases colder than detail
    assert agent.PHASE_TEMPERATURE["silhouette"] < agent.PHASE_TEMPERATURE["detail"]
    assert agent.PHASE_TEMPERATURE["cleanup"] < agent.PHASE_TEMPERATURE["shading"]


def test_get_llm_threads_seed_to_gemini():
    llm = _get_llm("gemini-3-flash-preview", SamplingConfig(temperature=0.0, seed=12345))
    assert type(llm).__name__ == "ChatGoogleGenerativeAI"
    assert llm.seed == 12345
    assert llm.temperature == 0.0


def test_get_llm_threads_seed_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    llm = _get_llm("gpt-5.4-mini", SamplingConfig(temperature=0.1, seed=999))
    assert type(llm).__name__ == "ChatOpenAI"
    assert llm.model_dump()["seed"] == 999


def test_get_llm_back_compat_temperature_arg():
    llm = _get_llm("gemini-3-flash-preview", temperature=0.4)
    assert llm.temperature == 0.4
    assert llm.seed is None


def test_no_stray_temperature_literals_in_get_llm():
    import inspect
    src = inspect.getsource(_get_llm)
    # the only numeric temperature literal allowed is via AGENT_TEMPERATURE / sampling
    assert "temperature=0.7" not in src
    assert "temperature=0.5" not in src
