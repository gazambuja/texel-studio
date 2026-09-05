# Spec 0004 — Persistent Reference Context

- **Branch:** `spec/0004-persistent-reference-context` (from `main`, after 0003)
- **Status:** Implemented — merged to local `main`
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

## 5. Tasks

1. [x] `view_reference()` tool — reference | current side by side (multimodal
   for Gemini-class; text for OpenAI/non-vision). Added to the toolset only when
   a reference exists. `tests/test_context.py`.
2. [x] Re-anchor: `_make_context_hook` appends an **ephemeral text** reminder
   (target subject + "call view_reference()") every `REANCHOR_EVERY` model
   calls, skipped when an image appeared in the last 2 messages.
3. [x] Image pruning: same hook keeps the newest `MAX_IMAGE_PARTS` (3)
   image-bearing messages; older image blocks collapse to
   `[preview image removed to save context]` (text kept); message 0 exempt.
   Done via `llm_input_messages` — the persisted thread keeps the full history,
   the LLM just sees a trimmed view each call.
4. [x] Continuations load the reference: `server._run_agent_sse` (dropped the
   `not is_continuation` guard), `worker.handle_generate`, `jobs/sprite_chat`
   (new `reference_id` param, `/api/chat` forwards `gen["reference_id"]`), and
   `run_agent_stream`'s continuation follow-up message now attaches the
   reference image.
5. [x] Config: `REANCHOR_EVERY` (8), `MAX_IMAGE_PARTS` (3) — env-overridable in
   `agent.py`.
6. [x] Verification — Section 6.

### Design note — why not the spec's mechanism

The drafted R3 ("push a `HumanMessage` into the next `agent.stream` input") does
not work: `agent.stream()` runs the whole ReAct loop in one call, so there is no
"next input" to push into mid-run. And 0003 already proved that injecting an
**image** `HumanMessage` mid-conversation makes Gemini re-call view tools in a
loop. So the re-anchor is a **text-only** nudge inside a `pre_model_hook`, and
the reference image itself stays available through 0003's `view_canvas` triptych
+ the new `view_reference()` tool.

## 6. Verification

- **Unit (`tests/test_context.py`, 7 tests; 61 total green):**
  - 6 preview images in history → the hook's `llm_input_messages` carries
    ≤ `MAX_IMAGE_PARTS` + the exempt message 0; message 0's reference image is
    untouched; a collapsed message still carries its text.
  - Re-anchor `HumanMessage` present when `ai_count % REANCHOR_EVERY == 0` and no
    recent image; absent when the last message is an image.
  - `view_reference` returns two image blocks (reference + current) for a
    vision model, is omitted entirely with no reference, text-only for OpenAI.
- **Live:** Gemini agent run (`run_agent_stream`, `REANCHOR_EVERY=4`, 16px) with
  the hook active → 13 tool calls, drew the shape, completed, **no loop**
  (confirming `llm_input_messages` pruning + ephemeral nudge don't wedge the
  ReAct loop the way 0003's image-hook did).
- **Continuation:** `view_reference` is present in the toolset on a continuation
  because `reference_b64` now flows through; `_run_agent_sse` /
  `worker.handle_generate` / `sprite_chat` load it from the row.

### Token note

Before 0004 preview images accumulated unbounded across a run. The hook caps the
LLM-visible set at 3 (+message 0), so a long session's per-call image payload is
now **bounded regardless of step count** — strictly fewer tokens on any run past
~4 `view_canvas` calls. The re-anchor adds ~30 tokens once per 8 calls.

## 7. Risks

- Token growth from repeated images → R5 cap + "recently shown" skip; measure.
- Pruning the wrong message (e.g. the seed) → never prune the first user message
  or the most recent 3 image parts; unit-test.

## 8. Definition of done

- [x] `view_reference()` returns real image parts (reference + current) for
  vision providers; present only when a reference exists.
- [x] Text re-anchor every `REANCHOR_EVERY` model calls with the "recently
  shown an image" skip rule.
- [x] Continuations (`sprite.chat`, `is_continuation`, `/continue_drawing`) load
  the reference and pass it through.
- [x] LLM-visible context capped at `MAX_IMAGE_PARTS` images + message 0; older
  image blocks collapse to a text stub; unit-tested.
- [x] Token behaviour characterised (bounded per-call image payload; see note).
  61 tests green; live run confirms no ReAct-loop regression.
