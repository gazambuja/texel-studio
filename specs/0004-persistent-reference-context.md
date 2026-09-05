# Spec 0004 — Persistent Reference Context

- **Branch:** `spec/0004-persistent-reference-context` (from `spec/0003-visual-preview-overhaul`)
- **Status:** Planned
- **Depends on:** 0003 (preview image plumbing, shared preview module).
- **North-star link:** The agent keeps comparing its work to the reference for
  the whole session, not just at step 1.

---

## 1. Context

The reference image is attached exactly once, in the first `HumanMessage`
(`agent.py:765-769`). After 20–60 tool calls it is buried; models lose track of
the target and drift. There is no tool to re-look at the reference.

## 2. Goal

The reference stays continuously available: it is included in every preview
(from 0003) and there is an explicit `view_reference()` tool plus periodic
re-injection so the model can re-anchor. Continuations (`sprite.chat`) also get
the reference, which they currently do not (`server.py:561` passes
`ref_b64=None` when `is_continuation`).

## 3. Requirements

### R1 — Reference in every preview
- WHEN a reference exists, the 0003 triptych SHALL always include it (already
  R2 of 0003; this spec makes it non-optional for reference-driven jobs).

### R2 — `view_reference()` tool
- THE SYSTEM SHALL expose `view_reference()` returning the reference image (real
  image part) at a legible upscale, optionally alongside the current canvas for
  direct comparison.

### R3 — Periodic re-anchor
- Every `N` agent steps (default `N=8`, configurable), THE SYSTEM SHALL inject a
  short `HumanMessage` reminding the model of the SUBJECT text and re-attaching
  the reference image.
- The re-anchor SHALL be skipped if a `view_reference`/`view_canvas` image was
  already delivered in the last 2 steps (avoid redundancy/token waste).

### R4 — Continuations get the reference
- WHEN `sprite.chat` / `is_continuation` runs and the generation row has a
  `reference_id`, THE SYSTEM SHALL load and include the reference.

### R5 — Token guardrail
- Total image parts kept in the live context SHALL be capped (e.g. last 3);
  older image parts SHALL be pruned from the message list before each LLM call
  (text stays).

## 4. Design sketch

- `make_tools` already receives `reference_img` after 0003 → add `view_reference`.
- Re-anchor: a counter in the `run_agent_stream` stream loop; when
  `step_count % N == 0`, push a `HumanMessage` into the next `agent.stream`
  input. LangGraph checkpointer persists it.
- Pruning: before/after each step, walk `state["messages"]`, and for image
  content blocks beyond the newest 3, replace with a text stub
  `"[previous preview image removed]"`. Implement via a `pre_model_hook` on
  `create_react_agent` if available, else a manual trim on the persisted state.
- `sprite_chat.py` + `server._run_agent_sse` + `worker.py`: stop nulling
  `ref_b64` on continuation; load from the row.

## 5. Tasks (outline)

1. [ ] `view_reference` tool + test.
2. [ ] Re-anchor injection with the "recently shown" skip rule.
3. [ ] Image-part pruning (cap = 3) + test that text is preserved.
4. [ ] Continuation reference loading in all 3 call sites.
5. [ ] Config: `REANCHOR_EVERY`, `MAX_IMAGE_PARTS`.
6. [ ] Verification.

## 6. Verification

- Unit: after 20 simulated steps, message list has ≤ 3 image parts; re-anchor
  present at steps 8 and 16 (unless recently shown).
- Manual: long refine session — dump messages, confirm reference re-appears
  periodically; sprite stays on-subject vs 0003 baseline.
- Continuation: "make the hat red" on a reference-backed sprite → agent
  references the original correctly.

## 7. Risks

- Token growth from repeated images → R5 cap + "recently shown" skip; measure.
- Pruning the wrong message (e.g. the seed) → never prune the first user message
  or the most recent 3 image parts; unit-test.

## 8. Definition of done

- [ ] `view_reference()` available and returns a real image part.
- [ ] Reference re-anchored every N steps with the skip rule.
- [ ] Continuations load the reference.
- [ ] Live context capped at 3 image parts, text intact, unit-tested.
- [ ] Token delta measured in the PR.
