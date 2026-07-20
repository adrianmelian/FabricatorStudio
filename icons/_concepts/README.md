# KS icon concept pipeline

This folder is the staging ground for generating KS shelf icon
concepts. **Current active pipeline:** the ComfyUI Mindmeld icon
workflow (see [Mindmeld icon generator (ComfyUI)](#mindmeld-icon-generator-comfyui)
section below — added 2026-05-12). **Earlier exploration** documented
the Nano Banana Pro (Gemini 3 Pro Image) approach, kept for reference
but no longer the recommended path.

**Honest framing:** both pipelines produce **concept material**, not
shippable shelf icons. The Mindmeld Atlas icons (`../MindmeldAtlas_v1.png`)
are hand-authored in pixel-art tools — Aseprite, PixaroMa, or hand-edited
in Photoshop with pixel-grid + nearest-neighbor discipline. Use the
pipelines below for ideation; redraw winners by hand for the actual shelf.

---

## TL;DR — generate all 4 concepts

From PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "d:\Documents\KinematicSolutions\Icons\KSShelf\_concepts\_run_all.ps1"
```

That script: loads `GEMINI_API_KEY` from Windows User scope into the
process, then runs `_gen.py` sequentially over all 4 prompt files,
writing PNGs into this folder.

Once rendered, magic-wand the pure-black background in Photoshop (the
prompts intentionally use solid `#000000` instead of carbon `#0B0E10`
because Nano Banana doesn't reliably produce a clean alpha — see
`feedback_nano_banana_alpha` memory).

---

## Layout

```
_concepts/
├── README.md               — this file
├── _gen.py                 — single-image generator (prompt file → PNG)
├── _run_all.ps1            — sequential runner for all 4 concepts
├── _probe.py               — minimal image-gen probe (5-word prompt)
├── _probe_text_only.py     — text-only probe (model accessibility check)
├── _inspect_model.py       — fetch model metadata (tier/limits hints)
├── _check_tier.py          — multi-model probe to triangulate key tier
├── _diag.py                — list all models visible to the current key
├── _prompts/               — original JSON-spec prompts (designed for Pro)
│   ├── 01_bench_vise.txt
│   ├── 02_welding_torch.txt
│   ├── 03_lathe_chuck.txt
│   └── 04_welded_corner.txt
├── _prompts_nl/            — natural-language prompts (Flash 2 compatible)
│   └── (same 4 names)
└── NN_<concept>.png        — generated outputs
```

---

## Setup

### Prerequisites

- Python 3.11+ with `google-genai` and `pillow` installed
  (`bash ~/.claude/skills/nano-banana-pro/install.sh` if missing)
- `GEMINI_API_KEY` env var with a Gemini API key
  (get one at https://aistudio.google.com/apikey)

### The Windows env-var trap (load-bearing)

On Adrian's machine, `GEMINI_API_KEY` is stored at the **Windows User
scope** via `[Environment]::SetEnvironmentVariable(..., 'User')`. The
Claude Code Bash and PowerShell tools both spawn fresh shells whose
process environment does NOT inherit User-scope env vars.

Symptoms:

- `env | grep gemini` in Bash → empty
- `$env:GEMINI_API_KEY` in a fresh PowerShell session → empty
- `py -3 -c "import os; print(os.environ.get('GEMINI_API_KEY'))"` → None
- `nano-banana-pro/generate_image.py` exits with "GEMINI_API_KEY not set"

The fix every script in this folder uses:

```powershell
$env:GEMINI_API_KEY = [Environment]::GetEnvironmentVariable('GEMINI_API_KEY','User')
```

Always set this in the same PowerShell line before the `py -3` invocation.
Python subprocesses inherit process-scope env, so once `$env:` has the value
the call works.

**False-positive trap:** `bash -c '[ -n \"$GEMINI_API_KEY\" ] && echo YES'`
will sometimes print YES even when the var is empty, because the escaped-quote
pattern collapses to `[ -n ]` which is a single-arg test that returns true
if `"-n"` is non-empty (it always is). Don't trust that check — verify with
the Python one-liner above.

---

## Why `_gen.py` exists (PowerShell native-arg quoting bug)

The skill ships `generate_image.py` which takes the prompt as a positional
CLI arg. That works on Linux/macOS bash. On Windows PowerShell 5.1 it
breaks for any prompt containing embedded `"` characters (e.g. JSON
specs): PowerShell's native-command argument passing mangles the quoting,
so by the time `py.exe` receives the args, the prompt has been split into
multiple unrecognized arguments and argparse rejects it.

PowerShell 7.3+ has `PSNativeCommandArgumentPassing` that fixes this, but
we're on 5.1.

`_gen.py` sidesteps the bug entirely by taking **prompt-FILE** and
**output-FILE** paths on the command line. The actual prompt content
stays on disk and is read inside Python. Only file paths cross the shell
boundary, so no quote mangling.

```powershell
py -3 _gen.py _prompts/01_bench_vise.txt 01_bench_vise.png
```

---

## Pro vs Flash 2 — which model to use

`_gen.py` is currently set to `gemini-3.1-flash-image-preview` (Nano
Banana 2). To switch back to Pro, edit line 32 of `_gen.py`:

```python
model="gemini-3-pro-image-preview",
```

### Trade-offs

| | Pro (`gemini-3-pro-image-preview`) | Flash 2 (`gemini-3.1-flash-image-preview`) |
|---|---|---|
| **Quality** | Higher fidelity, follows precise specs | Faster, less prone to over-elaboration |
| **Prompt format** | Handles JSON specs well | Rejects heavy JSON with 503; needs natural language |
| **Constraint discipline** | Respects "black background" reliably | Sometimes ignores background color, needs constraint up front |
| **Availability (Adrian's setup, 2026-05-11)** | **503s consistently** — see below | Generally accessible |

### The Pro 503 issue (investigated and resolved 2026-05-11)

#### Original cause (fixed)

When Adrian first put $25 into Gemini API billing, the upgrade was
applied to his **original** GCP project. But the API key in use was
created in a **different (new) project** that remained on free tier.
Google's free-tier daily quota for `gemini-3-pro-image` is **literally
zero requests**, so every API call was hard-rejected.

**Tell:** the 503 from the wrong-project key flips to a 429
`RESOURCE_EXHAUSTED` with explicit detail when the key state is clean:

```
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
limit: 0, model: gemini-3-pro-image
```

The 503 was Google's opaque wrapper for the same underlying gate.

**Fix:** Delete the wrong-project key. Create a fresh key inside the
Tier 1 project at https://aistudio.google.com/app/apikey. Update the
Windows User-scope env var to the new key value.

#### Residual 503 (real, not a config issue)

After the project fix, Pro still 503s intermittently — but for a
different reason. Adrian's actual usage per https://ai.dev/rate-limit
shows healthy quotas (RPD 31/250, TPM 6.66K/100K, RPM 15/20). The 503
is now **genuine API-surface capacity throttling**: Google sheds direct
API calls to preview models during peak windows even for paid Tier 1
users. AI Studio works because it has a separate reserved-capacity
routing path.

**There is no further configuration that resolves this.** Practical
options when Pro is shedding:

1. **Wait.** Capacity opens and closes throughout the day.
2. **Probe first.** Run
   `py -3 _probe.py gemini-3-pro-image-preview`. If OK, fire the full
   `_run_all.ps1` immediately while the window is open.
3. **Use Flash 2.** Swap `_gen.py` model param to
   `gemini-3.1-flash-image-preview`, accept the prompt rewrite cost
   (JSON specs need to become NL — see `_prompts_nl/` for the pattern).
4. **Set up a `/loop` poll** that retries every 10 minutes until Pro
   responds, then runs `_run_all.ps1`.
5. **Vertex AI surface.** Different routing, separate capacity pool.
   Set `GOOGLE_GENAI_USE_VERTEXAI=true`, plus `GOOGLE_CLOUD_PROJECT` and
   `GOOGLE_CLOUD_LOCATION`. Requires
   `gcloud auth application-default login`.

---

## Prompt format

### JSON-spec format (for Pro)

Use the structure recommended by the `skills` skill's JSON-prompting-
for-Nano-Banana schemas (`marketing_image`, `social_graphic`, etc.).
Pro reads the structured spec and renders precisely. See `_prompts/`.

### Natural-language format (for Flash 2)

Flash 2 rejects heavy JSON prompts with 503. Convert to natural language
with strict constraints repeated up front. See `_prompts_nl/`.

**Critical NL conventions discovered:**

1. **Lead with the background constraint.** Flash 2 has a strong prior
   toward white backgrounds for icons. Open the prompt with
   `"CRITICAL: The entire canvas background is pure solid #000000 black,
   edge to edge, with no white or light areas anywhere."` and reinforce
   it again partway through. Without this, expect a white background
   even when "black background" is specified once.

2. **Spell out the palette explicitly.** Name every allowed color hex
   and explicitly forbid grays, whites, and anything off-palette.

3. **Use "solid filled" vs "hollow outline" terminology** rather than
   "no fill" — Flash 2 sometimes interprets "no fill" as transparent
   (which it can't render) and falls back to white.

4. **End with a "Do NOT add:" list.** Both models respect explicit
   prohibitions better than implicit "stay minimal" hints.

---

## Troubleshooting decision tree

### `GEMINI_API_KEY is not set`
→ The `$env:` line is missing or your PowerShell session doesn't have
the User-scope inheritance. Re-run with the explicit
`[Environment]::GetEnvironmentVariable(..., 'User')` line.

### `503 UNAVAILABLE` with "high demand" message
1. Run `py -3 _probe.py gemini-3-pro-image-preview` and
   `py -3 _probe.py gemini-3.1-flash-image-preview`.
2. If **both** 503: genuine outage. Check
   https://status.cloud.google.com/ and wait.
3. If only **Pro** 503s, Flash 2 works: tier-gating on Pro. Either
   upgrade the key to Tier 1 (paid billing) or swap `_gen.py` to
   Flash 2.
4. If Flash 2 503s on a heavy JSON prompt but works on a 5-word probe:
   the prompt is over-complex for Flash 2. Convert to natural language.

### `unrecognized arguments` from argparse
→ PowerShell native-arg quoting bug. Don't pass the prompt directly to
`generate_image.py`; use `_gen.py` with prompt-file paths instead.

### Output PNG has white background
→ Flash 2 ignored the background constraint. Move the
`"CRITICAL: ... pure solid #000000 black ..."` instruction to the very
first line of the prompt and add a second reinforcement halfway through.

### Output PNG has wrong colors / drifted from palette
→ Add explicit forbidden colors at the end: `"Do NOT use: any color
outside #E8E0D0 / #7CFFB2 / #000000, any grays, any whites, any beiges."`

### TypeError on Imagen 4 calls
→ The `google-genai` SDK uses a different method signature for Imagen
(`generate_images()`, not `generate_content()`). Not currently supported
by `_gen.py`. Would need a parallel `_gen_imagen.py` wrapper.

---

## The 4 concept directions (current iteration)

| # | Concept | Reading | Status |
|---|---|---|---|
| 1 | Bench vise | Clamping / forming stock | Generated on Flash 2 |
| 2 | Welding torch | Joining metal / heat work | Generated on Flash 2 |
| 3 | Lathe chuck (face-on) | Centered turning | Needs re-gen with bg-up-front prompt |
| 4 | Welded corner | Two plates + bead | Generated on Flash 2 |

All four pivoted from an earlier "blueprint / plug-and-socket" direction
(too abstract) to "machinist's shop tooling" (Adrian's explicit
direction). Color discipline: bone `#E8E0D0` for structural linework,
plasma `#7CFFB2` for "locked / formed / live" elements (workpiece in
vise, stock in chuck), ember `#FF7A3D` for heat (flame, weld bead).

---

## Related memory entries (Nano Banana era)

- `feedback_nano_banana_alpha` — black-bg + Photoshop magic-wand workflow
- `reference_gemini_api_key_user_scope` — the env-var inheritance quirk

---

# Mindmeld icon generator (ComfyUI)

**Shipped 2026-05-12.** Replaces the Nano Banana Pro pipeline above as
the active concept-generator. JSON-spec authoring → deterministic Python
converter → ComfyUI workflow (SDXL + RMBG) → RGBA PNG. Total per render:
~60s for 4 variations on the RTX 2080 Ti.

## When to use it

- **Yes:** exploring "what should the X icon look like?" — generate 4
  variations, pick the strongest silhouette, redraw it in Aseprite for
  the actual shelf.
- **Yes:** Photoshop reference layers for hand-authored icons.
- **No:** dropping renders directly onto the shelf. They look in-family
  but aren't pixel-perfect Atlas. See
  `feedback-ai-icons-concept-not-final` memory for the rationale.

## Files

```
ksMindmeld_icon_generator.json    canonical workflow JSON (git-tracked)
_mindmeld_icon_gen.py              Python caller + JSON-spec converter
mindmeld_icon.example.json         schema reference (Fabricator example)
```

The workflow JSON is **also synced** to
`D:\AI\ComfyUI-Easy-Install\ComfyUI-Easy-Install\ComfyUI\user\default\workflows\`
so it shows up in the ComfyUI browser UI's workflow library. When the
canonical JSON in this folder changes, copy it across — the git copy
is the source of truth.

## Prerequisites

| Item | Where | Status |
|---|---|---|
| ComfyUI (Easy-Install) | `D:\AI\ComfyUI-Easy-Install\ComfyUI-Easy-Install\` | Installed |
| `sd_xl_base_1.0.safetensors` | `…\ComfyUI\models\checkpoints\` | Downloaded |
| `pixel-art-xl.safetensors` LoRA | `…\ComfyUI\models\loras\` | Downloaded but **disabled** (strength 0.0) since the flat-vector pivot |
| `comfyui-rmbg` custom node pack | `…\ComfyUI\custom_nodes\comfyui-rmbg` | Installed; auto-downloads RMBG-2.0 (~88MB) on first run |
| `comfyui-kjnodes` custom node pack | `…\ComfyUI\custom_nodes\comfyui-kjnodes` | Installed; provides `ColorToMask` + `SaveImageWithAlpha` (legacy chain, no longer load-bearing post the RMBG pivot — see spec) |
| Windows pagefile ≥ 16GB | C: drive | Bumped to 48GB (see `reference-sdxl-pagefile-constraint`) |

See `reference-comfyui-install-paths` memory for the MCP-filesystem
path-mismatch gotcha — HTTP API tools work fine, but `upload_image` /
`enqueue_workflow` MCP tools target the wrong path; the Python caller
sidesteps this entirely by POSTing to `http://127.0.0.1:8188/prompt`
directly.

## Interactive use (ComfyUI browser)

1. Load the `ksMindmeld_icon_generator` workflow from the user library.
2. Click the positive prompt node titled `// SUBJECT // — change this only`.
3. Replace the text with a description of your subject (the workflow as
   saved is a starter — for repeatable structured authoring, use the CLI).
4. Click **Queue Prompt** → 4 RGBA PNGs land at
   `D:\AI\…\ComfyUI\output\ksMindmeld_NNNNN_.png` ~60s later.

## Programmatic use (CLI)

```bash
# Bare subject (uses Mindmeld palette defaults + flat-vector style):
py -3 _mindmeld_icon_gen.py wrench

# JSON spec (full control over subject, branding, palette, constraints):
py -3 _mindmeld_icon_gen.py mindmeld_icon.example.json

# Preview the composed prompts without rendering (useful for tuning):
py -3 _mindmeld_icon_gen.py --preview mindmeld_icon.example.json

# Run the converter unit tests:
py -3 _mindmeld_icon_gen.py --test
```

## Programmatic use (Python)

```python
from _mindmeld_icon_gen import generate_icons, compose_prompt
import json

# Bare-subject (returns 4 PNG paths):
paths = generate_icons('wrench')

# Full JSON spec — see mindmeld_icon.example.json for the schema:
spec = json.load(open('mindmeld_icon.example.json'))
paths = generate_icons(spec, batch=4, seed=42)  # seed for reproducibility

# Inspect the composed prompt strings without rendering:
positive, negative = compose_prompt(spec)
```

## JSON spec format

See `mindmeld_icon.example.json` for the canonical reference. Brief shape:

```jsonc
{
  "mindmeld_icon": {
    "meta":     { "title": "...", "tool_name": "..." },
    "subject":  { "type": "robot", "details": "...", "pose": "..." },
    "branding": [
      { "type": "chest_panel", "content": "FAB",
        "treatment": "LED segment display", "color_token": "live" }
    ],
    "style":    { "aesthetic": "flat vector icon...",
                  "outline": "thick uniform outlines...",
                  "extra_tokens": [] },
    "palette":  { "structural": "#E8E0D0", "live": "#7CFFB2",
                  "heat": "#FF7A3D", "background": "#000000" },
    "constraints": { "exclude": ["humans"], "extra_negative": [] }
  }
}
```

Palette uses **semantic role names** (`structural` / `live` / `heat`)
not literal token names — `branding[].color_token` references these.
Empty `branding[]` → converter auto-injects `text, letters, words` into
the negative prompt (the no-text default). Non-empty → those exclusions
are dropped + the branding becomes a positive-prompt fragment.

Spec rationale lives in
`docs/superpowers/specs/2026-05-12-mindmeld-icon-generator-design.md`.

## Adding text to a generated icon (hybrid pattern)

SDXL renders the silhouette + flat-color body; if you need legible text,
composite it in Photoshop using **VT323** (the Mindmeld display font,
installed system-wide at user scope as of 2026-05-12). VT323 tips:

- Set anti-aliasing to **None** or **Sharp** in the Character panel —
  default smooth AA destroys the pixel-grid integrity.
- Render at multiples of the source bitmap size (24, 48, 72, 96, ...).
- Black text on plasma/ember accent panels reads best — high contrast.

The Mindmeld system fonts are also bundled in
`maya_tools/utils/qt/mindmeld/fonts/` so the Maya UI picks them up.

## Spec acceptance criteria — final walk

The 5 acceptance criteria from the design spec, with honest status:

1. **Workflow produces 4 RGBA PNGs from a single Queue Prompt** —
   ✅ Validated. `ksMindmeld_00033..00040` series.
2. **Python caller returns the 4 paths from `generate_icons(spec)`** —
   ✅ Validated. CLI + Python API both work end-to-end.
3. **At least 2 of 4 outputs recognizable as the subject** —
   ✅ Validated. Subject recognition is strong post the prompt-and-LoRA
   pivots; the `cute_robot_with_FAB` Fabricator spec consistently
   produces recognizable robots.
4. **Alpha channel is correct (clean transparent bg, opaque subject)** —
   ✅ Validated. RMBG-2.0 at process_res=512, mask_blur=0 produces clean
   binary alpha with no Photoshop touch-up required.
5. **On-subject colors are from the Mindmeld palette** —
   ✅ Validated. Post the LoRA-off + anti-framing pivot, palette adherence
   is strong — bone/plasma/ember dominate, with the `color_token`
   semantic mapping translating reliably ("live" → mint accents,
   "heat" → ember accents).

**Outside the original acceptance criteria but worth recording:** even
with all 5 criteria passing, the renders aren't pixel-perfect matches
for the Mindmeld Atlas hand-drawn style. They are ~90% of the way there
(isolated silhouettes, on-palette, flat-vector) but the last 10% — the
exact line weights, optical corner placements, intentional pixel-grid
discipline — is hand-author territory. Treat workflow output as concept
reference, not final shelf assets.

## Iteration playbook (quick reference)

When a render doesn't hit, in order of cheapness:

1. **Re-roll seed** — same prompt, different render. Free.
2. **Subject specificity** — `wrench` → `crescent wrench`, generic →
   specific noun. Free.
3. **`style.extra_tokens`** — add one or two visual modifiers to the
   positive prompt without touching defaults.
4. **`constraints.extra_negative`** — add specific anti-tokens for
   things that keep appearing (e.g. `'framed sticker'` if framing
   resurfaces).
5. **Palette overrides** — swap `palette.live` or `palette.heat` for a
   different hex to shift the accent mood (e.g. branded variants).
6. **RMBG `sensitivity` / `mask_blur`** — only if alpha edges look wrong
   (rarely needed at the defaults).

Full iteration playbook (with LoRA, CFG, threshold knobs) lives in the
spec's "Iteration playbook" section.
