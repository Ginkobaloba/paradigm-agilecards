# Gantry Brand Handoff Packet

Source: Gemini Pro session, 2026-06-18. This is the finalized brand identity for the agile-cards/agile-cards-board product, customer-facing brand: **Gantry**.

Aesthetic: "tech casual," industrial-grade. The brand is built around the metaphor of a structural frame (the gantry crane) that lifts and routes work, just as the product is the structural frame on which intelligent agents do work.

---

## 1. Brand Foundation and Assets

### Logotype and Visual Identity

The logotype is a persistent system asset, rendered in **Geist Mono (Bold)**, entirely in lowercase, and heavily tracked at **-0.04em**.

- **Primary Lockup**: `gantry` in Dark Gunmetal (#2a3439)
- **Active Lockup**: `gantry` in Dark Gunmetal (#2a3439), with the structural `[ ▮ ]` framework accented in Paradigm Forest Green (#1f5a44)
- **Monochrome Variant**: A strict engineering schematic, two fine vertical lines framing a heavy, horizontal block `[ ▮ ]`. Optimized for favicons and system trays.

Reference image files in this directory:
- `gantry-logotype-1.png`
- `gantry-logotype-2.png`

### Color Palette Matrix

Industrial-grade neutrals. Useful, not decorative.

| Token | Hex Code | Application |
|---|---|---|
| Primary Accent | `#1f5a44` | Paradigm Forest Green (active states, connections) |
| Technical Base | `#2a3439` | Dark Gunmetal (text, structural boundaries) |
| Surface Light | `#f4f5f6` | Light Industrial Grey (primary canvas) |
| System Success | `#2a6b4e` | Deep Sage (completed and verified tasks) |
| System Info | `#4a7abc` | Slate Blue (passive data nodes) |

### Typography

- **Display / Hero**: Fraunces
- **Headings / UI**: Geist Sans
- **Logotype / Monospace**: Geist Mono

---

## 2. Media Deliverables Checklist

| Asset | Description and Specs |
|---|---|
| Hero Graphic | Overhead, high-contrast UI view on Light Industrial Grey (`#f4f5f6`). Shows grid lines, a highlighted task card, fine line paths connecting to the orchestration engine. |
| Context Imagery | Macro, tactile photography: dark anodized aluminum, network chassis cabling, geometric architecture. Lighting must be natural and directional. No studio gloss. |
| Card Drop Animation | 2-second loop. Physics must mimic the weight of a heavy card landing on a solid surface with instantaneous, snappy velocity. See `gantry-motion-1.mp4` or `gantry-motion-2.mp4`. |
| Technical Blueprints | Line-based, geometric, isometric vector diagrams. 1px lines, no gradients. Cards as nodes connecting via structural paths. |
| Social Templates | 1200x630px. Horizontal split: left side off-white (objective card), right side dark gunmetal (tracking logs). |

Reference motion files in this directory:
- `gantry-motion-1.mp4` (card drop or blueprint motion)
- `gantry-motion-2.mp4` (the other)

---

## 3. Landing Page Structural Hierarchy

Apply this structure to the marketing landing build at `apps/board/marketing/` or wherever the public face lives.

1. **Hero Block**: headline "Manage the outcome. Leave the process to us." Accompanied by Hero Graphic.
2. **The Direct Shift**: visual comparison of chaotic chat vs. structured board using the Card Drop Animation.
3. **Pipeline Lifecycle**: "Write, Drop, and Review." Use Technical Blueprint illustrations.
4. **Functional Scenarios**: high-contrast interface panels (UI mockups).
5. **Final Action**: centered email input, "Clear out your administrative logjam."

Typography lockup: Fraunces for hero/display, Geist Sans for headings, Geist Mono for logotype and any inline code or technical reference.

---

## 4. Open Iteration Points (for next session)

Things Gemini left as iteration room or that surfaced as questions during the brand pass:

- Specific engineering grid line spacing for the canvas pattern
- Card-drop motion physics tuning (curve, mass, snap)
- Whether the BYO-LLM "works with" footer strip uses vendor logos or a neutral text treatment
- Sample copy beyond the hero (secondary blocks, social card variants)
- Final social template export specs for Twitter, LinkedIn, etc.

---

## 5. Provenance

- Brand brief sent to Gemini Pro: `outputs/GEMINI_BRAND_BRIEF_agile-cards_2026-06-18.md`
- Brand brief addendum (BYO-LLM tenet) sent to Gemini Pro: `outputs/GEMINI_BRIEF_ADDENDUM_byo-model_2026-06-18.md`
- Returned brand handoff (this document) committed: 2026-06-18
- Product status: agile-cards monorepo at C:\dev\agile-cards, customer-facing brand "Gantry," not yet applied to UI or marketing landing
