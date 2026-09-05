# Texel Studio — Spec-Driven Development

## Why this exists

Sprite quality from the current "draw-from-scratch, primitive-by-primitive"
agent is poor even with strong LLMs. The agent is forced to mentally rasterize
geometry it cannot see well, against a reference image it is shown only once.

Instead of one big rewrite, we ship a sequence of **small, independently
verifiable specs**, each on its own branch, each moving the product toward one
north star.

## North star

> The app can turn **any reference image** into clean, palette-consistent pixel
> art with minimal user effort — and later, produce an **animation sprite sheet**
> of that same pixel art subject.

Every spec is judged by whether it moves us toward that sentence.

## SDD process

Each spec is one Markdown file in this directory with a fixed structure:

1. **Context** — what exists today, why it falls short (cite files/lines).
2. **Goal** — one paragraph. What "done" looks like for the user.
3. **Requirements** — numbered user stories, each with EARS-style acceptance
   criteria (`WHEN <trigger> THE SYSTEM SHALL <behavior>`).
4. **Design** — architecture, files touched, data model, API changes.
5. **Tasks** — ordered checklist an implementer follows.
6. **Verification** — how we prove it works (manual + automated).
7. **Risks / open questions**.
8. **Definition of done**.

Rules:

- A spec is frozen once its branch is cut. Changes go in a follow-up spec.
- No implementation before the spec's Requirements + Design sections are filled.
- Each spec must keep the app runnable and all existing job kinds working.

## Branch strategy

Specs advance in a chain — each branches from the previous spec's branch so
later work builds on earlier work without waiting for `main` merges.

| Spec | Branch | Branches from | Theme |
|------|--------|---------------|-------|
| — | `spec/sdd-scaffold` | `feature/extract-reference-palette` | These docs |
| 0001 | `spec/0001-image-first-pipeline` | `spec/sdd-scaffold` | Image model + quantize is the primary path |
| 0002 | `spec/0002-reference-seeded-canvas` | `spec/0001-...` | Agent starts from the quantized reference, not blank |
| 0003 | `spec/0003-visual-preview-overhaul` | `spec/0002-...` | Give the agent a preview it can actually read |
| 0004 | `spec/0004-persistent-reference-context` | `spec/0003-...` | Keep the reference in front of the agent |
| 0005 | `spec/0005-silhouette-first-workflow` | `spec/0004-...` | Structured phases: silhouette → fill → detail |
| 0006 | `spec/0006-deterministic-drawing-config` | `spec/0005-...` | Sampling/temperature tuned per phase |

When a spec lands on `main`, rebase the downstream chain.

## Execution order

**0001 first.** It is the highest-leverage change: it replaces the failing
generation paradigm with one that already half-exists in the codebase
(`jobs/sprite_from_photo.py`). Specs 0002–0006 then progressively improve the
optional agent refinement pass that runs on top of the 0001 output.

## Future (post-0006, not yet specced)

- `0007` — animation sprite sheet: given a finished pixel-art subject, generate
  N-frame walk/idle/attack cycles that stay palette- and silhouette-consistent.
- `0008` — tileset/autotile generation from a single reference-derived tile.
