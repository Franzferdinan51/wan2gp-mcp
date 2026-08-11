# Our Adventures — Anime Episode Asset Library

Local-only project folder for generating a 3-minute anime episode using
MiniMax H3 (local Wan2GP) with image-to-video continuity, fed by
externally-rendered character reference sheets.

## Layout

```
our_adventures_assets/
├── README.md                        # this file
├── 01_character_sheets/             # locked designs for every named character
│   ├── duckets_sheet.png
│   ├── hermes_sheet.png
│   ├── local_ai_sheet.png
│   ├── grok_sheet.png
│   └── chatgpt_sheet.png
├── 02_scene_storyboards/            # per-clip storyboards (optional, helps continuity)
│   └── clip_NN_*.png
├── 03_h3_outputs/                   # per-clip folder for H3 I2V outputs
│   └── clip_NN_<slug>/
│       ├── prompt.txt               # exact prompt used
│       ├── first_frame.png          # I2V first frame (image-to-video)
│       ├── clip.mp4                 # raw 15s video
│       └── clip_with_audio.mp4      # with native H3 audio
├── 04_final_assembly/               # ffmpeg concat result + master
│   ├── our_adventures_ep01.mp4      # final 3-min master
│   └── concat_list.txt
└── docs/
    ├── character_bible.md           # canonical character descriptions
    └── style_bible.md               # canonical visual style notes
```

## Workflow

1. **Generate character sheets** (one image per character) via the active
   `image_generate` tool (xAI Grok) — these define the locked visual identity.
2. **Optionally generate storyboards** (one image per clip) so H3 has a
   first-frame reference to anchor motion/composition via I2V mode.
3. **For each clip**:
   - Submit H3 I2V via the Wan2GP MCP server, passing the storyboard as
     first_frame and the prompt as motion/composition guide.
   - H3 generates 15 s of native audio+video using the image as anchor.
4. **Concat** all 12 clips with `ffmpeg -c copy` to a single 3-min file.
5. **Validate** via the upstream `seam_probe.py` / `level_step.py` /
   `freeze_detect.py` from `NikoDemon80/ComfyUI-H3-Motion-Context` (GPL-3.0).

## Why this approach

- **Pattern A** (text-only H3) gave us a 3-min episode but character
  consistency drifted between clips (clips 1 and 6 looked like different
  shows).
- **I2V with locked first-frame** anchors each clip's composition to a
  known-good image. The character sheets make every clip start with the
  same character design, so motion continuity is automatic.
- **No stitching / ffmpeg drama** — each clip is a complete 15-s asset
  saved to its own folder; concat is a single `ffmpeg -c copy` command.

## MCP tools used

- `image_generate` (active xAI Grok backend in this Hermes session) →
  character sheets and storyboards
- `h3_generate` (Wan2GP MCP, http://localhost:9000) → 15-s clips via I2V
  (pass `image_paths: [<storyboard>]` instead of pure text)
- `h3_job_status`, `h3_get_output` → poll and retrieve per-clip outputs