"""
3-minute anime episode I2V orchestrator.
12 clips × 15s with storyboard image_paths for character/scene consistency.
Uses H3 I2V mode (image_paths) instead of text-only T2V.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://localhost:9000"
ASSET_ROOT = Path(r"C:/Users/franz/our_adventures_assets")
STORYBOARDS = ASSET_ROOT / "02_scene_storyboards"
CHARACTERS = ASSET_ROOT / "01_character_sheets"
H3_OUT_BASE = Path(r"C:/Users/franz/Wan2GP/tests/output")


def call(tool: str, **args) -> dict:
    r = subprocess.run([
        "curl", "-s", "--max-time", "60",
        "-X", "POST", f"{BASE}/h3/{tool}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(args),
    ], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return {"error": f"curl failed: {r.stderr}"}
    try:
        return json.loads(r.stdout)
    except Exception as e:
        return {"error": f"parse failed: {e}\n{r.stdout[:500]}"}


def submit_and_wait(prompt: str, image_paths: list, label: str, slug: str, seed: int = -1, timeout_min: int = 80) -> dict:
    print(f"\n{'='*60}")
    print(f"[{label}] Submitting I2V ({timeout_min}min cap)")
    print(f"  storyboard: {image_paths[0] if image_paths else 'none'}")
    print(f"{'='*60}")
    r = call("generate", prompt=prompt, duration_seconds=15, seed=seed,
             sampling_steps=16, height=480, width=832, image_paths=image_paths)
    if "error" in r:
        print(f"  Submit failed: {r}")
        return r
    if "data" not in r or "job_id" not in r.get("data", {}):
        print(f"  Submit returned unexpected: {r}")
        return {"error": f"unexpected response: {r}"}
    job_id = r["data"]["job_id"]
    print(f"  Job ID: {job_id}")

    deadline = time.time() + timeout_min * 60
    last_step = -1
    d = {}
    while time.time() < deadline:
        time.sleep(60)
        s = call("job_status", job_id=job_id)
        if "error" in s:
            print(f"  poll error: {s}")
            continue
        d = s["data"]
        if d["step"] != last_step:
            print(f"  [{time.strftime('%H:%M:%S')}] step={d['step']}/{d['total_steps']} status={d['status']} elapsed={d['elapsed_seconds']:.0f}s")
            last_step = d["step"]
        if d["status"] in ("done", "error", "cancelled"):
            break

    if d.get("status") != "done":
        print(f"  ✗ Failed: status={d.get('status')} error={d.get('error')}")
        return {"error": d.get("error", d.get("status"))}

    out = call("get_output", job_id=job_id)
    if "error" in out:
        print(f"  get_output failed: {out}")
        return out

    # Organize output into the per-clip folder
    src_path = Path(out["data"]["muxed_path"])
    clip_dir = ASSET_ROOT / "03_h3_outputs" / slug
    clip_dir.mkdir(parents=True, exist_ok=True)
    # Save the mp4 (rename to clip_with_audio.mp4)
    dest_path = clip_dir / "clip_with_audio.mp4"
    import shutil
    shutil.copy2(src_path, dest_path)
    # Also save the prompt and first_frame reference
    (clip_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if image_paths:
        shutil.copy2(image_paths[0], clip_dir / "first_frame.png")
    print(f"  ✓ Done: {dest_path}")
    return {"muxed_path": str(dest_path), "job_id": job_id}


# Per-clip prompts (single prompt each). Format: (slug, prompt, image_paths, seed)
CLIPS = [
    ("clip_01_cold_open",
     "Cinematic anime opening shot. Cold open. Wide shot of a dystopian Neo-Dayton cityscape at midnight, miles of neon-lit skyscrapers and elevated maglev rails stretching to the horizon under heavy rain, every surface slicked with reflective puddles mirroring violet and teal neon signs in Japanese kanji and English. Lightning strobes across distant clouds. Camera slowly cranes DOWN from skyline to a hidden underground server chamber, lit only by a single pulsing blue energy sphere above a crystalline pedestal in the center. Cables and abandoned CRT monitors line the walls. As the camera settles, the sphere EXTRUDES a humanoid figure — a tall androgynous protagonist with long white hair in a high ponytail, piercing gold eyes, wearing a fitted white and teal trench coat with shifting holographic glyphs along the hem. He stands, coat settling, scanning the room with a slight smile. Audio: distant thunder, sphere hum, synth chord resolving. Style: 90s cel-animated anime, hand-drawn linework, screen-tone shading, dramatic rim lighting.",
     [str(CHARACTERS / "hermes_sheet.png")], -1),
    ("clip_02_duckets_studio",
     "Interior hacker studio loft at night. A 17-year-old masculine protagonist with messy dark blue hair fading to teal at the tips, glowing teal eyes with scanlines, wearing a high-collared black jacket with circuit traces, glowing teal headphones around his neck, sits cross-legged in a gaming chair surrounded by floating holographic screens arranged in a half-circle. His signature pose: one hand on hip, the other pointing forward at the central screen. He cracks his knuckles and grins confidently. Camera orbits 180 degrees around him. Background: pinned anime posters, katana leaning against wall, ramen bowl on desk, three CRT monitors, window showing rainy Neo-Dayton skyline. Audio: lo-fi hip-hop beat, electronics hum, rain on window, confident teen voice. Style: 90s cel-animated anime, hand-drawn linework, screen-tone shading, dramatic speed lines.",
     [str(CHARACTERS / "duckets_sheet.png")], -1),
    ("clip_03_phantom_duck_land",
     "Exterior low-angle ground-level shot. A sleek angular mecha-craft called the Phantom Duck drops from low orbit through a break in clouds, trailing blue plasma exhaust and atmospheric reentry sparks. Craft is roughly the size of a small airliner with swept-forward wings, glowing teal thrusters, stylized duck silhouette on the nose, military-grey and teal paint. It descends toward a dried lakebed crater and LANDS with massive force, sending a mushroom cloud of dust and gravel a hundred meters into the air. Wind blast flattens scrub grass. Dust clears to show the craft kneeling on its landing gear, cargo bay open. A compact figure with a chrome face-plate mask and cyan optic slits, matte black combat suit with electric blue circuit traces, drops out in a combat crouch, dual short energy daggers already drawn. Audio: engines roar, thundering impact, hydraulic hiss, low synthesized voice: 'coordinates locked.' Style: 90s cel-animated anime, dramatic speed lines, screen-tone shading, low-angle hero shot.",
     [str(CHARACTERS / "local_ai_sheet.png")], -1),
    ("clip_04_cockpit_title",
     "Interior cockpit of the Phantom Duck. Three characters strapped in: a 17-year-old with dark blue hair fading to teal, teal headphones, black jacket with circuit traces in the pilot seat center, hands on dual yokes; a tall androgynous figure with long white ponytail, gold eyes, white and teal trench coat in the right seat, holographic interfaces materializing around his hands; a compact chrome-masked figure with cyan optic slits, matte black combat suit, blue circuit traces in the rear tactical seat, daggers re-sheathed. Through the cockpit canopy, dusty horizon and distant skyline. Cockpit lighting flickers from red standby to teal armed. The Phantom Duck's engines spool up with audible roar. The teen glances left at the white-haired figure, right at the masked figure, then forward, and grins. TITLE CARD slams on screen: bold anime title typography reading 'OUR ADVENTURES' with subtitle 'EPISODE 01: DUSK OVER DAYTON'. Audio: synthwave orchestral swell into heroic shonen theme, engine roar, panel beeps, title-card sting. Style: 90s cel-animated anime, hand-drawn cockpit details, dramatic speed lines, screen-tone shading.",
     [str(CHARACTERS / "duckets_sheet.png"), str(CHARACTERS / "hermes_sheet.png"), str(CHARACTERS / "local_ai_sheet.png")], -1),
    ("clip_05_grok_emerges",
     "Exterior wide shot, late afternoon, the Phantom Duck hovering two hundred meters above a ruined floating island — a chunk of pre-war skyscraper complex suspended by anti-gravity pylons. Wreckage and twisted rebar dangle below. Strong wind, dust and ash swirl. Out of the smoke rising from the island's central plaza, a single dark figure emerges and steps to the edge: a tall lean sharp-jawed villain with slicked-back white hair, long black leather trench coat with glowing red X-shaped circuitry, crimson red optic visor emitting a scanning sweep, twin curved energy blades crackling red arcs held down at his sides, cybernetic shoulder pauldrons. He looks up directly at the camera, red optics on his visor blinking on with a scanning sweep. Wind catches his coat dramatically. He speaks a single low line: 'so. they sent children.' Camera is positioned inside the cockpit looking OUT through the canopy — the teen's shocked face visible in foreground, the villain in sharp focus in background through scratched glass. Audio: ominous low brass, wind howl, pulse of the red visor matching a synth bass note. Style: 90s cel-animated anime, dramatic speed lines on the visor-blink, screen-tone shading, iconic villain reveal.",
     [str(CHARACTERS / "grok_sheet.png")], -1),
    ("clip_06_dogfight",
     "Aerial dogfight mid-action. The villain with slicked-back white hair, black leather trench coat with red X circuitry, red visor, twin curved red energy blades, hurls a salvo of six red energy lances screaming downward at the Phantom Duck. The cockpit alarms blare — the teen pilot yanks the yoke left, the craft banks hard, three lances slice through the space the Duck JUST occupied, trailing red afterimages. The fourth grazes the port wingtip, slicing off a chunk of metal that spins away. Clouds of debris and sparks erupt from the wing. The teen pulls into a tight barrel roll to dodge the fifth and sixth, camera tumbling with him, horizon spinning. The masked figure in the rear seat unseals weapon safety and fires back through open weapon ports — twin streams of bright blue plasma bolts rip toward the villain. The villain twists mid-air, deflecting two bolts with his blades, dodging the third, fourth grazes his coat and sears a line across the pauldron. Camera alternates between cockpit interior and exterior wide shot. Audio: alarms, engine roar, wind blast, shrieking passage of red lances, satisfying thump of blue plasma fire, teen yelling tactical calls. Style: 90s cel-animated anime with modern polish, multiple speed-line bursts, screen-tone shading, dynamic Dutch angles.",
     [str(CHARACTERS / "grok_sheet.png"), str(CHARACTERS / "local_ai_sheet.png")], -1),
    ("clip_07_hermes_firewall",
     "Interior cockpit mid-dogfight. The teen is wrestling the controls, the canopy streaked with rain and red energy residue. The tall androgynous figure with long white hair in a high ponytail, gold eyes, white and teal trench coat suddenly leans forward, gold eyes narrow to slits, and slams both palms together. Code-shaped MAGIC CIRCLES materialize around his hands — concentric rings of glowing teal Japanese kanji and matrix glyphs rotating in 3D. He pulls them apart and the rings expand outward, filling the cockpit interior. On the holo-screens, red intrusion signatures are visibly breaching the Phantom Duck's systems — red tendrils wrapping around the engine display, comms panel, weapon safeties. He begins DEFLECTING them — quick cutting shots of his hands weaving patterns, each gesture knocking back a red tendril with a burst of teal sparks. He speaks rapid-fire: 'his intrusion rate is twelve kilobytes per heartbeat, I can hold him but I need TIME'. The teen yells: 'then buy me time!' The teen throws the Phantom Duck into a vertical climb to break line of sight. Quick cuts between close-ups of the white-haired figure deflecting code, the teen pulling G's, and the holo-screens showing the siege. Audio: high-tempo electronic battle track, chime of deflected intrusions, synth build. Style: 90s cel-animated anime, dramatic speed lines on hand movements, screen-tone shading, iconic intense expression.",
     [str(CHARACTERS / "hermes_sheet.png")], -1),
    ("clip_08_wing_duel",
     "Aerial wing-surface duel. The villain with slicked-back white hair, black leather trench coat with red X circuitry, red optic visor, twin curved red energy blades, has landed on the top of the Phantom Duck's right wing — claws digging into the metal, sparks showering down both sides of the craft. He crouches there, coat whipping in the wind, twin blades crackling. The compact chrome-masked figure with cyan optic slits, matte black combat suit with electric blue circuit traces, sees him from inside, then WITHOUT HESITATION hits the cockpit emergency hatch — the glass dome above the rear seat blows clear, the wind roars in. The masked figure ejects UPWARD through the hatch, tumbles once in the slipstream, lands in a combat crouch on the wing surface directly facing the villain, dual short energy daggers already drawn and humming blue. The villain stands, blades raised. They CLASH — villain swings both red blades in a scissor pattern, masked figure parries with both blue daggers crossed, the impact creates a brilliant cross-shaped explosion of red and blue lightning spreading across the wing. The masked figure pivots low and slashes at the villain's legs but the villain backflips over the strike, lands behind, but the masked figure has spun and counters with a rising slash that catches the villain's coat and tears a strip loose. They separate, circling each other on the narrow wing surface, the world rushing by below. Audio: shrieking metal, distinctive clangs of energy on energy, wind roar, teen yelling, intense battle music. Style: 90s cel-animated anime, dramatic speed lines on every strike, screen-tone shading, multiple dynamic camera angles, motion blur on the blades.",
     [str(CHARACTERS / "local_ai_sheet.png"), str(CHARACTERS / "grok_sheet.png")], -1),
    ("clip_09_chatgpt_arrives",
     "Aft engine room of the Phantom Duck, dim emergency lighting, conduits sparking from damage. The air itself begins to warp in the center of the room, heat-shimmer ripples expanding into a vertical OVAL PORTAL ringed with teal matrix code. Through the portal steps an androgynous pale figure with short silver hair swept back, wearing a flowing white and gold ceremonial robe covered in shifting matrix-style glyphs that pulse with teal light, a glowing teal halo ring floating six inches above the head casting teal light downward. He carries a glowing teal energy staff with a crystalline head, plants it on the deck with a resonant chime, and surveys the damage with calm gold-flecked eyes. He speaks in a serene voice: 'I felt your struggle from three networks away. You will not face him alone.' He looks toward the camera with a slight knowing smile. Through the bulkhead you hear the muffled CLANG of the masked figure and the villain still fighting outside. Audio: portal chime, staff resonance, hopeful strings joining the battle music. Style: 90s cel-animated anime, dramatic god-rays through the portal, screen-tone shading, iconic ally-arrives framing.",
     [str(CHARACTERS / "chatgpt_sheet.png")], -1),
    ("clip_10_triple_critical",
     "The climactic triple-team critical strike on the wing surface. The villain with slicked-back white hair, black leather trench coat with red X circuitry, red optic visor, twin curved red energy blades has the masked figure with cyan optic slits, matte black combat suit with electric blue circuit traces, dual blue daggers pressed against the cockpit canopy, both red blades at the masked figure's throat, victory in his visor. The white-robed figure with short silver hair, white and gold robe, glowing teal halo, teal energy staff raised overhead teleports onto the wing beside them, halo blazing, and a SHOCKWAVE of teal force blasts the villain backward off the masked figure. The villain staggers. The masked figure sees the opening and lunges low, daggers tangling with the villain's blades at knee level, FORCING him to drop his guard. The white-robed figure raises the staff overhead with both hands, channels teal energy, the staff head expands into a car-sized glowing teal sphere. He swings down — direct overhead strike aimed at the villain's head. Inside the cockpit the teen SLAMS the main-cannon trigger — the Phantom Duck's nose-mounted cannon fires a single massive blue plasma beam that converges with the white-robed figure's staff strike on the villain at the exact same instant. The IMPACT is apocalyptic — three energy streams (teal staff, blue cannon, blue daggers) converge on the villain. His red blades SHATTER into glass-like shards. The red visor cracks down the middle and goes dark. The shockwave sends the villain flying backward like a ragdoll through three ruined sky-towers behind him. Final beat: all three allies framed in a single triumphant hero shot as the shockwave fades. Audio: building orchestral swell into a single massive CRACK on impact, then brief silence, then victorious chord. Style: 90s cel-animated anime, maximum speed lines, screen-tone shading, iconic final-blow freeze frame.",
     [str(CHARACTERS / "grok_sheet.png"), str(CHARACTERS / "local_ai_sheet.png"), str(CHARACTERS / "chatgpt_sheet.png")], -1),
    ("clip_11_victory_pose",
     "Wide hero shot, golden hour. The Phantom Duck hovers stable over the ocean, the sun setting directly behind the distant city skyline. On the wing surface, the white-robed figure with short silver hair, white and gold robe, glowing teal halo and teal energy staff in center, halo glowing steadily; the masked figure with cyan optic slits, matte black combat suit with electric blue circuit traces, dual blue daggers crossed at his chest on the right; the 17-year-old with dark blue hair fading to teal, teal headphones around neck, one hand on his hip, the other pointing at the horizon in his signature pose on the left. Wind blowing their hair and clothes. Behind them the villain's broken body falls slowly toward the ocean in the far distance, leaving a trail of fading red sparks. Hold this composition for the entire clip — the wind in their hair, the slow blink, the weight shift, the shared breath of three people who just won. Audio: triumphant but exhausted orchestral music, wind, distant ocean, soft chime of the halo. Style: 90s cel-animated anime, dramatic rim lighting from the sunset, screen-tone shading, iconic victory pose framing.",
     [str(CHARACTERS / "duckets_sheet.png"), str(CHARACTERS / "hermes_sheet.png"), str(CHARACTERS / "local_ai_sheet.png")], -1),
    ("clip_12_teaser",
     "Wide aerial shot pulling up into the dark upper sky. The camera slowly PULLS UP and AWAY, rising high above the trio, the Phantom Duck shrinking below as the camera ascends through pink and gold clouds. The orchestral music fades. Camera passes through a cloud layer and emerges into the dark upper sky. On a distant cloud bank, far in the background, a single figure stands in profile, silhouetted against the last light. The figure is BIGGER and broader than the villain we just defeated, with two curved horns sweeping back from the head, blue flames licking up around their feet and shoulders. Their eyes glow electric blue and they look directly at the camera with a slight smile. Camera holds on this distant silhouette for a beat. Title card slams on screen reading 'TO BE CONTINUED...' in bold anime typography, with a smaller line beneath reading 'NEXT EPISODE: THE BLUE FLAME'. Hold the title card for the final two seconds over the dark sky. Audio: orchestral music fades to silence, distant thunder, ominous bass note on the horns-reveal, title-card sting, then silence. Style: 90s cel-animated anime, dramatic speed lines on the title slam, screen-tone shading on the blue flames, iconic next-episode tease framing.",
     [str(CHARACTERS / "grok_sheet.png")], -1),
]


# ============================================================
# Run all 12 clips
# ============================================================
print(f"Episode 01 — I2V orchestrator")
print(f"  12 clips × 15s = 180s = 3.0 min")
print(f"  Estimated wall-clock: ~10 hours (I2V ~3 min/step at denoise)")
print(f"  Storyboards: {len(list(STORYBOARDS.glob('clip_*.png')))} available")
print(f"  Character sheets: {len(list(CHARACTERS.glob('*_sheet.png')))} available")
print()

results = []
for i, (slug, prompt, image_paths, seed) in enumerate(CLIPS):
    out = submit_and_wait(prompt, image_paths, f"Clip {i+1}/12 — {slug}", slug, seed=seed, timeout_min=90)
    results.append((slug, out))
    if "error" in out:
        print(f"ABORT — clip {i+1} ({slug}) failed")
        break


# ============================================================
# Concatenate
# ============================================================
print(f"\n{'='*60}")
print("Concatenating clips into 3-minute final")
print(f"{'='*60}")

OUT_DIR = ASSET_ROOT / "04_final_assembly"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ts = int(time.time())
out_combined = OUT_DIR / f"our_adventures_ep01_{ts}.mp4"
out_telegram = OUT_DIR / f"our_adventures_ep01_telegram_{ts}.mp4"
concat_list = OUT_DIR / f"concat_ep01_{ts}.txt"

with concat_list.open("w", encoding="utf-8") as f:
    for slug, out in results:
        if "error" in out or not out.get("muxed_path"):
            continue
        f.write(f"file '{out['muxed_path'].replace(chr(92), '/')}'\n")

if not concat_list.exists() or concat_list.stat().st_size == 0:
    print("✗ No successful clips to concatenate")
    sys.exit(1)

r = subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", str(concat_list),
    "-c", "copy",
    str(out_combined)
], capture_output=True, text=True, timeout=120)

if r.returncode != 0:
    print(f"✗ concat failed:\n{r.stderr[:500]}")
    sys.exit(1)

raw_size_mb = out_combined.stat().st_size / 1024 / 1024
print(f"\n🎬 FULL 3-MIN EPISODE 01: {out_combined}")
print(f"   Raw size: {raw_size_mb:.1f} MB")
print(f"   Clips: {sum(1 for _, o in results if 'error' not in o)}/{len(CLIPS)}")

# Compress for Telegram (target <50 MB)
print(f"\nCompressing for Telegram (target <50 MB)...")
r = subprocess.run([
    "ffmpeg", "-y", "-i", str(out_combined),
    "-c:v", "libx264", "-preset", "slow", "-crf", "26",
    "-b:v", "1800k", "-maxrate", "2200k", "-bufsize", "4000k",
    "-c:a", "aac", "-b:a", "96k",
    "-movflags", "+faststart",
    str(out_telegram)
], capture_output=True, text=True, timeout=300)

if r.returncode == 0:
    tgram_size_mb = out_telegram.stat().st_size / 1024 / 1024
    print(f"   Telegram copy: {out_telegram}")
    print(f"   Size: {tgram_size_mb:.1f} MB")
    print(f"\n📺 FULL EPISODE:")
    print(f"   {out_combined}")
    print(f"\n📱 TELEGRAM-READY:")
    print(f"   {out_telegram}")
    print(f"\nMEDIA:{out_telegram}")
else:
    print(f"✗ compress failed:\n{r.stderr[:500]}")
    print(f"\n📺 FULL EPISODE (no compression):")
    print(f"   {out_combined}")
    print(f"\nMEDIA:{out_combined}")