#!/usr/bin/env python3
"""
Automated Instagram Infographic Reel & 7-Slide Carousel Generator + Publisher
Styles:
  - watercolor : Cozy warm watercolor & gouache storybook illustration
  - popart     : Bold vibrant editorial pop-art magazine infographic
  - auto       : Alternates between watercolor (odd topic IDs) and popart (even topic IDs)

Formats:
  --format reel     : Slow-paced 1080x1920 (9:16) Infographic Reel with free edge-tts voiceover (6s+ per slide)
  --format carousel : 7-image swipeable 1080x1350 (4:5) Infographic Carousel
"""

import argparse
import asyncio
import base64
import io
import json
import os
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import edge_tts
except ImportError:
    edge_tts = None

import numpy as np
import requests
from moviepy import AudioFileClip, ImageClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

TOPICS_FILE = Path("finance_topics_10_to_80.json")
OUTPUT_DIR = Path("generated_reels")
OUTPUT_DIR.mkdir(exist_ok=True)

REEL_W, REEL_H = 1080, 1920
CAROUSEL_W, CAROUSEL_H = 1080, 1350
FPS = 24
MIN_SLIDE_SECONDS = 6.0
VOICE_NAME = os.getenv("VOICE_NAME", "en-IN-NeerjaNeural")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
PUBLIC_VIDEO_BASE_URL = os.getenv("PUBLIC_VIDEO_BASE_URL", "").rstrip("/")
GRAPH_API_VERSION = "v21.0"

STYLE_PROMPTS = {
    "watercolor": (
        "Cozy warm watercolor and gouache storybook illustration Instagram infographic slide, "
        "soft cream paper texture, charming hand-painted editorial style, warm sage green, "
        "honey gold, and terracotta accents, clean dark brown serif headline, crisp legible text, no watermarks."
    ),
    "popart": (
        "Bold vibrant modern editorial pop-art magazine Instagram infographic slide, "
        "sunny yellow and coral pink geometric color blocks, crisp flat vector illustration, "
        "bold navy sans-serif headline, high-contrast clean modern typography, no watermarks."
    ),
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_ai_slide(prompt: str, width: int, height: int, aspect_ratio: str = "4:5") -> Image.Image | None:
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={GEMINI_API_KEY}"
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio}}
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    b64 = resp.json()["predictions"][0]["bytesBase64Encoded"]
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    return img.resize((width, height), Image.Resampling.LANCZOS)


def render_fallback_slide(
    width: int, height: int, style: str, age_group: str, title: str,
    step_label: str, headline: str, body_text: str, slide_idx: int, total_slides: int, is_carousel: bool,
) -> Image.Image:
    if style == "watercolor":
        bg, accent, ink, muted = (253, 248, 240), (180, 105, 65), (58, 42, 32), (102, 82, 68)
    else:
        bg, accent, ink, muted = (254, 240, 138), (244, 114, 182), (15, 23, 42), (51, 65, 85)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, 24], fill=accent)
    draw.rounded_rectangle([68, 320, width - 68, height - 160], radius=40, fill=(255, 255, 255), outline=accent, width=5)

    font_badge = load_font(30, bold=True)
    draw.text((80, 110), f"AGES {age_group} • {step_label.upper()}", font=font_badge, fill=accent)

    font_title = load_font(44, bold=True)
    y = 175
    for line in textwrap.wrap(title, width=28):
        draw.text((80, y), line, font=font_title, fill=ink)
        y += 56

    font_head = load_font(50, bold=True)
    y_card = 390
    for line in textwrap.wrap(headline, width=22):
        draw.text((116, y_card), line, font=font_head, fill=ink)
        y_card += 64

    y_card += 30
    font_body = load_font(40, bold=False)
    for line in textwrap.wrap(body_text, width=30):
        draw.text((116, y_card), line, font=font_body, fill=muted)
        y_card += 56

    footer = f"{slide_idx}/{total_slides} • Swipe to read →" if (is_carousel and slide_idx < total_slides) else "Money Made Simple • Ages 10–80"
    draw.text((80, height - 95), footer, font=load_font(28, bold=True), fill=ink)
    return img


def make_slide_image(
    width: int, height: int, style: str, age_group: str, title: str,
    step_label: str, headline: str, body_text: str, slide_idx: int, total_slides: int, is_carousel: bool,
) -> Image.Image:
    aspect = "4:5" if is_carousel else "9:16"
    prompt = (
        f"{STYLE_PROMPTS[style]} "
        f"Topic: '{title}' (for Ages {age_group}, explained simply for a 10-year-old). "
        f"Slide {slide_idx} of {total_slides} ({step_label}). "
        f"Display clear readable title '{headline}' and short text '{body_text}'."
    )
    ai_img = generate_ai_slide(prompt, width, height, aspect_ratio=aspect)
    if ai_img is not None:
        return ai_img
    return render_fallback_slide(
        width, height, style, age_group, title, step_label, headline, body_text, slide_idx, total_slides, is_carousel
    )


async def _synthesize_tts(text: str, out_mp3: Path) -> None:
    if edge_tts is None:
        raise ImportError("edge-tts is not installed. Run: pip install -r requirements.txt")
    await edge_tts.Communicate(text, VOICE_NAME).save(str(out_mp3))


def generate_audio_segment(text: str, out_mp3: Path) -> AudioFileClip:
    asyncio.run(_synthesize_tts(text, out_mp3))
    return AudioFileClip(str(out_mp3))


def build_reel_video(topic: dict, style: str) -> Path:
    topic_id, age_group, title = topic["id"], topic["age_group"], topic["title"]
    segments = [
        ("Start", "Quick Question", topic["hook"]),
        ("Tip 1", "First Big Idea", topic["points"][0]),
        ("Tip 2", "Second Big Idea", topic["points"][1]),
        ("Tip 3", "Third Big Idea", topic["points"][2]),
        ("Save!", "You Got This!", topic["cta"]),
    ]

    clips, audio_clips, temp_files = [], [], []
    total = len(segments)
    for idx, (step_label, headline, narration) in enumerate(segments, start=1):
        mp3_path = OUTPUT_DIR / f"_tmp_{topic_id}_{idx}.mp3"
        temp_files.append(mp3_path)
        audio_clip = generate_audio_segment(narration, mp3_path)
        audio_clips.append(audio_clip)
        duration = max(MIN_SLIDE_SECONDS, audio_clip.duration + 2.0)

        img = make_slide_image(REEL_W, REEL_H, style, age_group, title, step_label, headline, narration, idx, total, is_carousel=False)
        clips.append(ImageClip(np.array(img)).with_duration(duration).with_audio(audio_clip))

    final_video = concatenate_videoclips(clips, method="compose")
    out_mp4 = OUTPUT_DIR / f"reel_{topic_id:02d}_age_{age_group}.mp4"
    final_video.write_videofile(str(out_mp4), fps=FPS, codec="libx264", audio_codec="aac", preset="medium", threads=4)

    final_video.close()
    for a in audio_clips:
        a.close()
    for f in temp_files:
        if f.exists():
            f.unlink()
    return out_mp4


def build_carousel_images(topic: dict, style: str) -> list[Path]:
    topic_id, age_group, title = topic["id"], topic["age_group"], topic["title"]
    slides_spec = [
        ("Cover", title, topic["hook"]),
        ("Step 1", "First Big Idea", topic["points"][0]),
        ("Step 2", "Second Big Idea", topic["points"][1]),
        ("Step 3", "Third Big Idea", topic["points"][2]),
        ("Why It Matters", "Think About It!", f"Even at age {age_group}, small daily habits turn tiny coins into big freedom."),
        ("Recap", "Remember This", f"{title}: Keep it simple, stay steady, and start today."),
        ("Your Turn!", "Save & Share", topic["cta"]),
    ]

    out_paths = []
    total = len(slides_spec)
    for idx, (step_label, headline, body_text) in enumerate(slides_spec, start=1):
        img = make_slide_image(CAROUSEL_W, CAROUSEL_H, style, age_group, title, step_label, headline, body_text, idx, total, is_carousel=True)
        out_jpg = OUTPUT_DIR / f"carousel_{topic_id:02d}_slide_{idx}.jpg"
        img.save(out_jpg, quality=95)
        out_paths.append(out_jpg)
    return out_paths


def _poll_container(container_id: str) -> None:
    base_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    for attempt in range(24):
        resp = requests.get(f"{base_url}/{container_id}", params={"fields": "status_code,status", "access_token": IG_ACCESS_TOKEN}, timeout=15)
        resp.raise_for_status()
        status_code = resp.json().get("status_code")
        print(f"[IG API] Poll {attempt + 1}: {status_code}")
        if status_code == "FINISHED":
            return
        if status_code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Container failed: {resp.json()}")
        time.sleep(5)
    raise TimeoutError("Timed out waiting for Instagram container processing.")


def publish_reel_to_instagram(video_url: str, caption: str) -> str:
    if not IG_USER_ID or not IG_ACCESS_TOKEN:
        raise ValueError("Missing IG_USER_ID or IG_ACCESS_TOKEN.")
    base_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    resp = requests.post(
        f"{base_url}/{IG_USER_ID}/media",
        data={"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true", "access_token": IG_ACCESS_TOKEN},
        timeout=30,
    )
    if not resp.ok: print("META API ERROR:", resp.status_code, resp.text)
    resp.raise_for_status()
    cid = resp.json()["id"]
    _poll_container(cid)
    pub = requests.post(f"{base_url}/{IG_USER_ID}/media_publish", data={"creation_id": cid, "access_token": IG_ACCESS_TOKEN}, timeout=30)
    if not pub.ok: print("META API ERROR:", pub.status_code, pub.text)
    pub.raise_for_status()
    return pub.json()["id"]


def publish_carousel_to_instagram(image_urls: list[str], caption: str) -> str:
    if not IG_USER_ID or not IG_ACCESS_TOKEN:
        raise ValueError("Missing IG_USER_ID or IG_ACCESS_TOKEN.")
    base_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    child_ids = []
    for url in image_urls:
        c = requests.post(f"{base_url}/{IG_USER_ID}/media", data={"image_url": url, "is_carousel_item": "true", "access_token": IG_ACCESS_TOKEN}, timeout=30)
        if not c.ok: print("META API ERROR:", c.status_code, c.text)
        c.raise_for_status()   
        child_ids.append(c.json()["id"])

    parent = requests.post(f"{base_url}/{IG_USER_ID}/media", data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption, "access_token": IG_ACCESS_TOKEN}, timeout=30)
    if not parent.ok: print("META API ERROR:", parent.status_code, parent.text)
    parent.raise_for_status()
    pid = parent.json()["id"]
    _poll_container(pid)
    pub = requests.post(f"{base_url}/{IG_USER_ID}/media_publish", data={"creation_id": pid, "access_token": IG_ACCESS_TOKEN}, timeout=30)
    if not pub.ok: print("META API ERROR:", pub.status_code, pub.text)
    pub.raise_for_status()
    return pub.json()["id"]


def build_caption(topic: dict) -> str:
    bullets = "\n".join(f"✨ {p}" for p in topic["points"])
    return f"Age {topic['age_group']}: {topic['title']}\n\n{topic['hook']}\n\n{bullets}\n\n{topic['cta']}\n\n{topic['hashtags']}"


def main():
    parser = argparse.ArgumentParser(description="Automate Watercolor & Pop-Art Finance Reels & 7-Slide Carousels")
    parser.add_argument("--format", choices=["reel", "carousel"], default="carousel", help="Post format")
    parser.add_argument("--style", choices=["auto", "watercolor", "popart"], default="auto", help="Visual theme")
    parser.add_argument("--dry-run", action="store_true", help="Generate locally without publishing")
    parser.add_argument("--age-group", type=str, help="Filter by age group (e.g. 10-15)")
    parser.add_argument("--topic-id", type=int, help="Run a specific topic ID")
    args = parser.parse_args()

    topics = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    selected = next(
        (
            t for t in topics
            if (args.topic_id and t["id"] == args.topic_id)
            or (not args.topic_id and not t.get("posted") and (not args.age_group or t["age_group"] == args.age_group))
        ),
        None,
    )
    if not selected:
        print("No matching unposted topics found!")
        return

    style = args.style
    if style == "auto":
        style = "watercolor" if selected["id"] % 2 == 1 else "popart"

    caption = build_caption(selected)
    print(f"Topic #{selected['id']} [{selected['age_group']}] | Format: {args.format} | Style: {style}")

    if args.format == "carousel":
        files = build_carousel_images(selected, style)
        print(f"Generated {len(files)} carousel slides in {OUTPUT_DIR}")
        if args.dry_run:
            return
        if not PUBLIC_VIDEO_BASE_URL:
            raise ValueError("PUBLIC_VIDEO_BASE_URL is required to publish.")
        media_id = publish_carousel_to_instagram([f"{PUBLIC_VIDEO_BASE_URL}/{f.name}" for f in files], caption)
    else:
        mp4_path = build_reel_video(selected, style)
        print(f"Generated slow-paced Reel: {mp4_path}")
        if args.dry_run:
            return
        if not PUBLIC_VIDEO_BASE_URL:
            raise ValueError("PUBLIC_VIDEO_BASE_URL is required to publish.")
        media_id = publish_reel_to_instagram(f"{PUBLIC_VIDEO_BASE_URL}/{mp4_path.name}", caption)

    selected["posted"] = True
    selected["posted_at"] = datetime.now(timezone.utc).isoformat()
    selected["ig_media_id"] = media_id
    TOPICS_FILE.write_text(json.dumps(topics, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
