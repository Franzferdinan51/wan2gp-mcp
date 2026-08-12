"""
Standalone H3 generation worker (subprocess-friendly).
Reads job params from JSON via stdin or argv, runs generation, writes
output paths + status to a JSON status file, prints progress lines.

Use as: subprocess generation worker. No MCP imports, no threading.
"""

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(r"C:/Users/franz/Wan2GP")
sys.path.insert(0, str(REPO_ROOT))
for k in ("PYTHONPATH", "PYTHONHOME", "UV_INTERNAL__PYTHONHOME"):
    os.environ.pop(k, None)
os.environ["HF_HOME"] = r"D:/Wan2GP-Models/.hf"

OUT_DIR = REPO_ROOT / "tests" / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# CRITICAL: argparse in wgp.py parses argv on import. Clear custom args BEFORE importing wgp.
# We move them into env vars (WAN2GP_* namespace) and clear them from sys.argv.
def _extract_worker_args() -> tuple:
    """Pull our custom args out of sys.argv before wgp's argparse sees them.
    Returns (status_file, params dict)."""
    import re
    status_path = None
    params = {}

    # Keep only python.exe / script.py args; filter the rest
    keep = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg.startswith("--status-file="):
            status_path = Path(arg.split("=", 1)[1])
            i += 1
            continue
        if arg.startswith("--wan2gp-"):
            # --wan2gp-key=value  →  params[key] = value
            kv = arg[len("--wan2gp-"):]
            if "=" in kv:
                k, v = kv.split("=", 1)
                v = v.strip('"').strip("'")
                # Repeatable list keys (image_path) accumulate into a list.
                if k == "image_path":
                    params.setdefault("image_paths", []).append(v)
                else:
                    try:
                        v = int(v)
                    except ValueError:
                        pass
                    params[k] = v
            i += 1
            continue
        # Anything else: pass through (wgp argparse will see it)
        keep.append(arg)
        i += 1

    # Replace argv so wgp only sees its own flags
    sys.argv = [sys.argv[0]] + keep
    return status_path, params


# Extract custom args FIRST
_status_path, _params = _extract_worker_args()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def update_status(status_path: Path, **kwargs):
    """Atomic JSON status update."""
    try:
        if status_path.exists():
            cur = json.loads(status_path.read_text(encoding="utf-8"))
        else:
            cur = {}
        cur.update(kwargs)
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        os.replace(tmp, status_path)
    except Exception as e:
        log(f"  status update failed: {e}")


def main():
    status_path = _status_path
    params = _params
    log(f"=== worker started: {params} ===")
    if status_path:
        update_status(status_path, status="loading", started_at=time.time(),
                      step=0, total_steps=params.get("sampling_steps", 16))

    import torch
    free_gb = torch.cuda.mem_get_info()[0] / 1024**3 if torch.cuda.is_available() else 0
    log(f"  GPU: {torch.cuda.get_device_name(0)} ({free_gb:.1f} GB free)")
    if status_path:
        update_status(status_path, gpu=torch.cuda.get_device_name(0), vram_free_gb=free_gb)

    import wgp
    from shared.utils import files_locator as fl
    fl.set_checkpoints_paths([r"D:\Wan2GP-Models", "checkpoints", "."])

    # Architecture selection: the MCP server picks based on whether
    # image_paths were provided. Ref2VA is required for character
    # consistency; FL2VA is fine for text-only generations.
    arch = params.get("architecture") or "minimax_h3_fl2va_pruned"
    if "ref2va" in arch:
        model_short = "MiniMax-H3-Ref2VA-pruned_int8_convrot.safetensors"
        config_short = "minimax_h3_ref2va_pruned.json"
    else:
        model_short = "MiniMax-H3-FL2VA-pruned_int8_convrot.safetensors"
        config_short = "minimax_h3_fl2va_pruned.json"
    log(f"  architecture: {arch}")
    model_filename = fl.locate_file(model_short)
    _text_dir = fl.locate_folder("Qwen3-VL-32B-Instruct")
    text_filename = os.path.join(_text_dir, "Qwen3-VL-32B-Instruct-layer50_quanto_bf16_int8.safetensors")

    log(f"  model: {model_filename} ({os.path.getsize(model_filename)/1e9:.1f} GB)")
    log(f"  text:  {text_filename} ({os.path.getsize(text_filename)/1e9:.1f} GB)")

    log("  loading handler...")
    model_def = json.loads((REPO_ROOT / "defaults" / config_short).read_text(encoding="utf-8"))
    from models.minimax_h3.minimax_h3_handler import family_handler
    handler = family_handler()

    log("  loading model + text encoder...")
    t0 = time.time()
    pipe_obj, modules = handler.load_model(
        model_filename=model_filename,
        model_type=arch,
        base_model_type=arch,
        model_def=model_def["model"],
        quantizeTransformer=False,
        VAE_dtype=torch.float32,
        text_encoder_filename=text_filename,
    )
    log(f"  loaded in {time.time()-t0:.1f}s")
    if status_path:
        update_status(status_path, model_loaded=True)

    log("  setting up mmgp offload profile...")
    from mmgp import offload, profile_type
    profile = getattr(profile_type, "VerylowRAM_LowVRAM", None) or 3
    offload.profile(modules, profile_no=profile, quantizeTransformer=False,
                    convertWeightsFloatTo=torch.bfloat16)
    from shared.attention import get_default_attention_mode
    offload.shared_state["_attention"] = get_default_attention_mode()
    log(f"  offload ready, GPU mem: {torch.cuda.memory_allocated()/1e9:.2f} GB")
    if status_path:
        update_status(status_path, offload_ready=True)

    frame_num = 121 if params["duration_seconds"] <= 5 else 362
    log(f"  generating: frame_num={frame_num}, prompt='{params['prompt'][:80]}...'")

    last_log = [0.0]
    def cb(*args, **kwargs):
        if args and isinstance(args[0], int):
            step = args[0] + 1
            now = time.time()
            if status_path:
                update_status(status_path, step=step, status="denoising")
            if now - last_log[0] >= 30:
                log(f"    step {step}/{params.get('sampling_steps', 16)}")
                last_log[0] = now

    if status_path:
        update_status(status_path, status="denoising")

    t1 = time.time()
    # Optional image-to-video: pass image_start as a single reference frame.
    # The MCP server passes image_paths as a list of absolute paths to character
    # sheets or storyboards; we hand the first one as image_start (H3 supports
    # up to 9 image refs in FL2VA mode).
    image_paths = params.get("image_paths") or []
    gen_kwargs = dict(
        input_prompt=params["prompt"],
        height=params.get("height", 480),
        width=params.get("width", 832),
        frame_num=frame_num,
        sampling_steps=params.get("sampling_steps", 16),
        shift=12.0,
        seed=params.get("seed", -1),
        fps=24,
        callback=cb,
    )
    if image_paths:
        from PIL import Image
        import torch as _torch
        # H3 pipeline expects CTHW (4D) float tensors in [-1, 1].
        def _pil_to_tensor(path):
            img = Image.open(path).convert("RGB").resize(
                (params.get("width", 832), params.get("height", 480)),
                Image.LANCZOS,
            )
            arr = _torch.from_numpy(__import__("numpy").asarray(img)).float()
            # (H, W, 3) -> (3, 1, H, W) CTHW with T=1.
            arr = arr.permute(2, 0, 1).unsqueeze(1)
            arr = (arr / 127.5) - 1.0
            return arr

        gen_kwargs["image_start"] = _pil_to_tensor(image_paths[0])
        log(f"  I2V mode: using {image_paths[0]} as first frame (shape={tuple(gen_kwargs['image_start'].shape)})")
        if len(image_paths) > 1:
            gen_kwargs["image_refs"] = [_pil_to_tensor(p) for p in image_paths[1:10]]
            log(f"  +{len(gen_kwargs['image_refs'])} additional reference images")

    result = pipe_obj.generate(**gen_kwargs)
    log(f"  generation done in {time.time()-t1:.1f}s")

    if status_path:
        update_status(status_path, status="saving")

    log("  encoding video + audio...")
    import imageio.v2 as imageio, soundfile as sf, subprocess
    vid = (result["x"].detach().clone().clamp_(-1, 1).add_(1).mul_(127.5)
           .clamp_(0, 255).to(torch.uint8).permute(1, 2, 3, 0).cpu().numpy())
    ts = int(time.time())
    out_mp4 = OUT_DIR / f"h3_worker_{ts}.mp4"
    writer = imageio.get_writer(str(out_mp4), fps=24, codec="libx264", quality=8, macro_block_size=1)
    for t in range(vid.shape[0]):
        writer.append_data(vid[t])
    writer.close()

    audio_path = OUT_DIR / f"h3_worker_{ts}_audio.wav"
    sf.write(str(audio_path), result["audio"], result["audio_sampling_rate"])

    muxed = OUT_DIR / f"h3_worker_{ts}_with_audio.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(out_mp4), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(muxed),
    ], capture_output=True, text=True, timeout=120)

    log(f"  OK: {muxed} ({muxed.stat().st_size//1024//1024} MB)")

    if status_path:
        update_status(status_path,
                      status="done",
                      finished_at=time.time(),
                      video_path=str(out_mp4),
                      audio_path=str(audio_path),
                      muxed_path=str(muxed))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"FATAL: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        # If we have a status file, mark as errored
        if _status_path:
            update_status(_status_path, status="error",
                          error=f"{type(e).__name__}: {e}",
                          finished_at=time.time())
        sys.exit(1)
