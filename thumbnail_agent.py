#!/usr/bin/env python3
import argparse
import base64
import concurrent.futures
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

warnings.filterwarnings("ignore", "'cgi' is deprecated", DeprecationWarning)
import cgi

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


APP_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = APP_DIR / "public"
ASSETS_DIR = APP_DIR / "assets"
STATE_DIR = APP_DIR / "state"
GENERATED_DIR = APP_DIR / "generated"
WORK_DIR = APP_DIR / "work"

OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
PUBLIC_ASSET_PATHS = {
    "/styles.css",
    "/app.js",
    "/1000media-logo.png",
    "/zoomex-logo.png",
    "/zoomex-card-logo.png",
    "/alliance-latin-logo.png",
    "/alliance-latin-card-logo.png",
    "/alliance-black-logo.png",
    "/alliance-black-card-logo.png",
    "/alliance-lgbtq-logo.png",
    "/alliance-lgbtq-card-logo.png",
    "/leverage-logo.png",
    "/leverage-card-logo.png",
    "/abh-logo.png",
    "/abh-card-logo.png",
    "/interfaith-logo.png",
    "/interfaith-card-logo.png",
    "/dentiste-logo.png",
    "/dentiste-card-logo.png",
    "/siyata-logo.jpg",
    "/siyata-card-logo.png",
    "/builders-middle-east-card-logo.png",
    "/favicon.ico",
    "/favicon-16x16.png",
    "/favicon-32x32.png",
    "/favicon-48x48.png",
    "/apple-touch-icon.png",
    "/android-chrome-192x192.png",
    "/android-chrome-512x512.png",
    "/site.webmanifest",
}
CANVAS_SIZE = (1080, 1920)

TOP_BAND = {"x": 160, "y": 1243, "w": 760, "h": 116, "radius": 62}
BOTTOM_BAND = {"x": 160, "y": 1366, "w": 760, "h": 126, "radius": 64}
ZOOMEX_DESIGN_PATH = ASSETS_DIR / "zoomex-design.png"
ZOOMEX_FONT_PATH = ASSETS_DIR / "Blinker-Bold.ttf"
ALLIANCE_DESIGN_PATH = ASSETS_DIR / "alliance-latin-design.png"
ALLIANCE_BLACK_DESIGN_PATH = ASSETS_DIR / "alliance-black-design.png"
ALLIANCE_LGBTQ_DESIGN_PATH = ASSETS_DIR / "alliance-lgbtq-design.png"
ALLIANCE_FONT_PATH = ASSETS_DIR / "Poppins-Bold.ttf"
FOCUS_ZONE = {"x": 210, "y": 560, "w": 660, "h": 660}
CLIENTS = {
    "zoomex": {
        "slug": "zoomex",
        "display_name": "Zoomex",
        "prompt_name": "ZOOMEX",
        "design_path": ZOOMEX_DESIGN_PATH,
        "font_path": ZOOMEX_FONT_PATH,
        "font_label": "Blinker ready",
        "design_label": "ZOOMEX design ready",
        "logo_area": "top-left ZOOMEX logo area",
        "template_context": "fixed transparent ZOOMEX design layer",
        "title_bands": [
            {**TOP_BAND, "fill": (0, 0, 0, 245), "text_fill": "#12d8c3", "font_max": 84, "font_min": 38},
            {**BOTTOM_BAND, "fill": (18, 216, 195, 255), "text_fill": "#030303", "font_max": 92, "font_min": 38},
        ],
    },
    "alliance-latin": {
        "slug": "alliance-latin",
        "display_name": "Alliance Latin",
        "prompt_name": "Alliance Latin Community",
        "design_path": ALLIANCE_DESIGN_PATH,
        "font_path": ALLIANCE_FONT_PATH,
        "font_label": "Poppins ready",
        "design_label": "Alliance design ready",
        "logo_area": "top-center Alliance Latin Community logo area",
        "template_context": "fixed Alliance Latin Community design layer with blue and orange title bars",
        "title_bands": [
            {"x": 38, "y": 1256, "w": 1042, "h": 108, "radius": 58, "fill": (0, 124, 190, 255), "text_fill": "#ffffff", "font_max": 74, "font_min": 34, "text_center_x": 540},
            {"x": -10, "y": 1376, "w": 1052, "h": 108, "radius": 58, "fill": (247, 87, 30, 255), "text_fill": "#ffffff", "font_max": 74, "font_min": 34, "text_center_x": 540},
        ],
    },
    "alliance-black": {
        "slug": "alliance-black",
        "display_name": "Alliance Black",
        "prompt_name": "Alliance Black Community",
        "design_path": ALLIANCE_BLACK_DESIGN_PATH,
        "font_path": ALLIANCE_FONT_PATH,
        "font_label": "Poppins ready",
        "design_label": "Alliance Black design ready",
        "logo_area": "top-center Alliance Black Community logo area",
        "template_context": "fixed Alliance Black Community design layer with espresso and copper title bars",
        "title_bands": [
            {"x": 38, "y": 1256, "w": 1042, "h": 108, "radius": 58, "fill": (43, 35, 34, 255), "text_fill": "#ffffff", "font_max": 74, "font_min": 34, "text_center_x": 540},
            {"x": -10, "y": 1376, "w": 1052, "h": 108, "radius": 58, "fill": (177, 98, 54, 255), "text_fill": "#ffffff", "font_max": 74, "font_min": 34, "text_center_x": 540},
        ],
    },
    "alliance-lgbtq": {
        "slug": "alliance-lgbtq",
        "display_name": "Alliance LGBTQ",
        "prompt_name": "Alliance LGBTQ+",
        "design_path": ALLIANCE_LGBTQ_DESIGN_PATH,
        "font_path": ALLIANCE_FONT_PATH,
        "font_label": "Poppins ready",
        "design_label": "Alliance LGBTQ design ready",
        "logo_area": "top-center Alliance LGBTQ+ logo area",
        "template_context": "fixed Alliance LGBTQ+ design layer with yellow and pink title bars",
        "title_bands": [
            {"x": 38, "y": 1256, "w": 1042, "h": 108, "radius": 58, "fill": (250, 202, 48, 255), "text_fill": "#ffffff", "font_max": 74, "font_min": 34, "text_center_x": 540},
            {"x": -10, "y": 1376, "w": 1052, "h": 108, "radius": 58, "fill": (237, 49, 134, 255), "text_fill": "#ffffff", "font_max": 74, "font_min": 34, "text_center_x": 540},
        ],
    },
}
DEFAULT_CLIENT = "zoomex"
TWO_THUMBNAIL_CLIENTS = {"zoomex", "alliance-latin", "alliance-black", "alliance-lgbtq"}
JOB_TTL_SECONDS = 60 * 60 * 3
JOBS = {}
JOBS_LOCK = threading.Lock()


def get_client_config(client_slug):
    slug = (client_slug or DEFAULT_CLIENT).strip().lower()
    if slug not in CLIENTS:
        raise RuntimeError(f"Unknown thumbnail client: {client_slug}")
    return CLIENTS[slug]


def public_client_status(config):
    return {
        "slug": config["slug"],
        "display_name": config["display_name"],
        "design_ready": config["design_path"].exists(),
        "font_ready": config["font_path"].exists(),
        "design_label": config["design_label"],
        "font_label": config["font_label"],
    }


def focus_zone_prompt(config=None):
    config = config or get_client_config(DEFAULT_CLIENT)
    x1 = FOCUS_ZONE["x"]
    y1 = FOCUS_ZONE["y"]
    x2 = FOCUS_ZONE["x"] + FOCUS_ZONE["w"]
    y2 = FOCUS_ZONE["y"] + FOCUS_ZONE["h"]
    return (
        f"SAFE ZONE RULE, NON-NEGOTIABLE: the hero of the script must live inside the central safe square "
        f"from x={x1} to {x2}, y={y1} to {y2} on a 1080x1920 canvas. "
        f"This safe square is below the {config['prompt_name']} logo area and above the title bars. "
        "Treat the top area above this square as background/logo space only, never hero-subject space. "
        "The hero can be a person, face, athlete, vehicle, robot, product, object, or key action. "
        "The hero's important details, visual center, and main readable features must be centered inside this safe square. "
        f"Nothing important may sit above y={y1}. "
        "MANDATORY LIGHTING RULE: any main subject in this central square, including vehicles, robots, products, tools, or other key objects, "
        "must be well-lit, cinematic, sharp, and clearly readable with visible surface detail, strong key light, and subtle rim or edge light. "
        "Do not make the central subject dark, muddy, silhouetted, or low-contrast. "
        "MANDATORY HUMAN RULE: if any human appears, the face, eyes, full head, shoulders, and upper half-body must be centered "
        f"inside this safe square, below the {config['prompt_name']} logo area. The top of the head, hair, eyes, and face must never appear above "
        f"y={y1}, behind the logo, overlapping the logo, near the top edge, or higher than the logo. "
        f"Place the face center around x=540 and between y={y1 + 90} and y={y1 + 260}. "
        "The face should appear lower than the logo, with clear breathing room between the logo and the head. "
        "Do not use tight close-up portraits or headshots that push the face into the top half of the image; zoom out and lower the subject "
        "so the person reads as a centered half-body or full-body hero inside the safe square. "
        f"The subject may extend beyond the square, but no important face, eyes, head, hero object, vehicle, robot, product, or key action should be hidden by the {config['logo_area']} "
        "or by the title bars at the bottom. If there is any conflict, prioritize safe-zone placement over dramatic cropping."
    )


def safe_zone_negative_prompt(config=None):
    config = config or get_client_config(DEFAULT_CLIENT)
    return (
        f"face above {config['prompt_name']} logo, head above logo, eyes above logo, face above y={FOCUS_ZONE['y']}, "
        f"head above y={FOCUS_ZONE['y']}, hero above y={FOCUS_ZONE['y']}, important subject above logo, "
        "tight close-up headshot, cropped forehead near top edge, hero outside safe zone, hero hidden behind logo, "
        "important object behind logo, dark central subject, underlit main object"
    )


def ensure_dirs():
    for path in [ASSETS_DIR, STATE_DIR, GENERATED_DIR, WORK_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def load_dotenv(path):
    path = Path(path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def save_env_value(key, value):
    env_path = APP_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output = []
    found = False
    for raw_line in lines:
        if raw_line.strip().startswith(f"{key}="):
            output.append(f'{key}="{value}"')
            found = True
        else:
            output.append(raw_line)
    if not found:
        output.append(f'{key}="{value}"')
    env_path.write_text("\n".join(output).strip() + "\n", encoding="utf-8")
    os.environ[key] = value


load_dotenv(APP_DIR / ".env")


def openai_key():
    return os.environ.get("OPENAI_API_KEY", "").strip()


def openai_model():
    return os.environ.get("OPENAI_MODEL", "gpt-5.5")


def browser_key_setup_allowed():
    return False


def safe_filename(value, fallback="thumbnail"):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:70] or fallback


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def generated_file_path(filename):
    return GENERATED_DIR / Path(filename).name


def openai_responses_create(payload, timeout=240):
    api_key = openai_key()
    if not api_key:
        raise RuntimeError("Add your OpenAI API key before generating thumbnails.")

    request = urllib.request.Request(
        OPENAI_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("error", {}).get("message") or body
        except json.JSONDecodeError:
            message = body
        raise RuntimeError(f"OpenAI request failed: {message}") from error


def extract_response_text(response):
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks = []
    for item in response.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            text = content.get("text") or content.get("content")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def extract_image_base64(response):
    for item in response.get("output", []):
        if item.get("type") == "image_generation_call" and item.get("result"):
            return item["result"]
    raise RuntimeError("The model did not return generated image data.")


def visual_brief_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["visual_prompt", "negative_prompt", "rationale"],
        "properties": {
            "visual_prompt": {
                "type": "string",
                "description": "Prompt for the full-frame image behind the fixed client design. No text/logos.",
            },
            "negative_prompt": {
                "type": "string",
                "description": "Things to avoid in image generation.",
            },
            "rationale": {
                "type": "string",
                "description": "One sentence explaining the image choice.",
            },
        },
    }


def create_visual_brief(script_text, title, config, has_person_reference=False, variant_index=1, variant_count=1):
    reference_note = ""
    if has_person_reference:
        reference_note = (
            "A person reference photo will be supplied to the image generator. "
            "The generated thumbnail must feature that exact person as the main human subject, preserving their recognizable face, "
            "hair, age, gender presentation, and overall appearance while placing them into the scene required by the script. "
        )
    variant_note = ""
    if variant_count > 1:
        variant_note = (
            f"This is thumbnail option {variant_index} of {variant_count}. "
            "Make this option visually distinct in composition, setting, camera angle, color mood, and subject action from the other option, "
            "while still following the same title, script, template, and subject-placement rules. "
        )
    if not openai_key():
        return {
            "visual_prompt": (
                "Create a vertical 9:16 full-frame editorial thumbnail image based on the script. "
                f"{reference_note}"
                f"{variant_note}"
                f"No text, no captions, no logos, no black empty poster space. {focus_zone_prompt(config)}"
            ),
            "negative_prompt": (
                "text, captions, watermarks, logos, blank black background, empty poster space, "
                f"{safe_zone_negative_prompt(config)}"
            ),
            "rationale": "Local fallback brief because OPENAI_API_KEY is not set.",
        }

    payload = {
        "model": openai_model(),
        "reasoning": {"effort": "low"},
        "input": [
            {
                "role": "developer",
                "content": (
                    "You create visual concepts for vertical social thumbnails. "
                    f"The title is supplied by the user and will be rendered later in fixed {config['prompt_name']} title bars. "
                    f"Safe-zone composition is mandatory: the hero subject must be centered in x={FOCUS_ZONE['x']}..{FOCUS_ZONE['x'] + FOCUS_ZONE['w']} "
                    f"and y={FOCUS_ZONE['y']}..{FOCUS_ZONE['y'] + FOCUS_ZONE['h']}. "
                    f"Human composition is strict: faces, eyes, full heads, shoulders, and upper half-bodies must be centered below the {config['prompt_name']} logo area, never above it. "
                    f"For any human, the entire face/head/eyes must stay below y={FOCUS_ZONE['y']}; never above or overlapping the logo. "
                    "Avoid tight closeups; zoom out and lower the hero subject when needed. "
                    "Central-subject lighting is strict: any main person, vehicle, robot, product, or key object must be well-lit, cinematic, and clearly readable. "
                    f"Add safe-zone failures to the negative prompt, including: {safe_zone_negative_prompt(config)}. "
                    "Return only JSON matching the schema. Never include text, captions, or logos in the image prompt."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"TITLE: {title}\n\n"
                            "Create only the subject/background image prompt for this script. "
                            f"The final image must be full-frame 9:16 and fill the whole thumbnail behind a {config['template_context']}. "
                            "Avoid black voids, blank poster space, title-card layouts, or large empty areas. "
                            f"{reference_note}"
                            f"{variant_note}"
                            f"{focus_zone_prompt(config)} "
                            "Do not propose a close-up face crop above the logo; the hero face, object, vehicle, robot, product, or key action must be inside the safe square. "
                            "Keep the lower title area visually calmer but still image-filled.\n\n"
                            f"SCRIPT:\n{script_text[:14000]}"
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "visual_brief",
                "strict": True,
                "schema": visual_brief_schema(),
            }
        },
    }
    return json.loads(extract_response_text(openai_responses_create(payload)))


def generate_subject_image(brief, config, person_reference_path=None):
    negative_prompt = " ".join(
        part.strip()
        for part in [brief.get("negative_prompt", ""), safe_zone_negative_prompt(config)]
        if part and part.strip()
    )
    prompt = (
        f"{brief['visual_prompt']}\n\n"
        "Hard requirements: vertical 9:16, full-bleed image, no text, no readable letters, no logos, no watermarks. "
        "The scene must fill the entire canvas from top to bottom. Do not make a mostly black image. "
        "Do not leave giant blank areas for text. Use real environment, texture, action, and depth behind the whole frame. "
        "Any main central subject, including vehicles, robots, products, tools, or other key objects, must be well-lit, cinematic, sharp, and clearly readable; "
        "avoid dark, muddy, silhouetted, or low-contrast central subjects. "
        f"SAFE-ZONE COMPOSITION IS MORE IMPORTANT THAN DRAMA: the hero subject's face, object, vehicle, robot, product, or key action must be centered in "
        f"x={FOCUS_ZONE['x']}..{FOCUS_ZONE['x'] + FOCUS_ZONE['w']}, y={FOCUS_ZONE['y']}..{FOCUS_ZONE['y'] + FOCUS_ZONE['h']}. "
        f"If humans are present, the main face and upper half-body must be centered below the {config['prompt_name']} logo area; "
        f"the full face, head, and eyes must stay below y={FOCUS_ZONE['y']}; do not place any face above the logo height or near the top of the frame. "
        "Avoid close-up headshots; zoom out and lower the person so the face and half-body sit inside the central safe square. "
        f"{focus_zone_prompt(config)} "
        "Keep the area behind the title bars lower contrast but still visually present. "
        f"Avoid: {negative_prompt}."
    )
    content = [{"type": "input_text", "text": prompt}]
    if person_reference_path:
        content.append({"type": "input_image", "image_url": image_data_url(person_reference_path)})
        content[0]["text"] += (
            "\n\nUse the attached person reference photo as the identity reference for the main human subject. "
            "Preserve the person's recognizable facial identity and upper-body appearance, but place them naturally into the generated scene. "
            "The uploaded photo is a reference only; do not recreate it as a flat pasted photo."
        )
    payload = {
        "model": openai_model(),
        "input": [{"role": "user", "content": content}],
        "tools": [{"type": "image_generation", "action": "generate"}],
    }
    image_bytes = base64.b64decode(extract_image_base64(openai_responses_create(payload)))
    image_path = WORK_DIR / f"subject-{uuid.uuid4().hex}.png"
    image_path.write_bytes(image_bytes)
    return image_path


def resolve_font(size, config=None):
    config = config or get_client_config(DEFAULT_CLIENT)
    candidates = [
        config["font_path"],
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def text_size(draw, text, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def split_title(title):
    title = " ".join(title.upper().split())
    for separator in ["|", "\n", "/"]:
        if separator in title:
            parts = [part.strip() for part in title.split(separator, 1)]
            if parts[0] and parts[1]:
                return parts[0], parts[1]
    words = title.split()
    if len(words) <= 1:
        return title, ""
    if len(words) <= 3:
        return " ".join(words[:-1]), words[-1]
    split_at = max(1, min(len(words) - 1, len(words) // 2))
    return " ".join(words[:split_at]), " ".join(words[split_at:])


def fit_font(text, max_width, max_height, max_size, min_size, config=None):
    probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    for size in range(max_size, min_size - 1, -2):
        font = resolve_font(size, config)
        width, height = text_size(draw, text, font)
        if width <= max_width and height <= max_height:
            return font
    return resolve_font(min_size, config)


def draw_centered_text(draw, box, text, font, fill):
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    center_x = box.get("text_center_x", x + w / 2)
    center_y = box.get("text_center_y", y + h / 2)
    tx = center_x - (right + left) / 2
    ty = center_y - (bottom + top) / 2
    draw.text((tx, ty), text, font=font, fill=fill)


def fit_cover(image, size):
    return ImageOps.fit(image.convert("RGBA"), size, method=Image.Resampling.LANCZOS)


def adjust_subject(image):
    image = image.convert("RGB")
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Brightness(image).enhance(1.08)
    image = ImageEnhance.Contrast(image).enhance(1.04)
    image = ImageEnhance.Color(image).enhance(1.02)
    return image.convert("RGBA")


def draw_title(canvas, title, config):
    top_text, bottom_text = split_title(title)
    title_lines = [top_text, bottom_text]
    draw = ImageDraw.Draw(canvas)

    for index, band in enumerate(config["title_bands"]):
        draw.rounded_rectangle(
            (band["x"], band["y"], band["x"] + band["w"], band["y"] + band["h"]),
            radius=band["radius"],
            fill=band["fill"],
        )
        text = title_lines[index] if index < len(title_lines) else ""
        if text:
            font = fit_font(
                text,
                band["w"] - 88,
                band["h"] * 0.72,
                band.get("font_max", 88),
                band.get("font_min", 38),
                config,
            )
            draw_centered_text(draw, band, text, font, band["text_fill"])
    return canvas


def render_thumbnail(title, subject_image_path, output_path, config=None):
    config = config or get_client_config(DEFAULT_CLIENT)
    design_path = config["design_path"]
    font_path = config["font_path"]
    if not design_path.exists():
        raise RuntimeError(f"Missing fixed design asset: {design_path}")
    if not font_path.exists():
        raise RuntimeError(f"Missing title font asset: {font_path}")
    subject = adjust_subject(fit_cover(Image.open(subject_image_path), CANVAS_SIZE))
    design = Image.open(design_path).convert("RGBA").resize(CANVAS_SIZE, Image.Resampling.LANCZOS)
    canvas = Image.alpha_composite(subject, design)
    canvas = draw_title(canvas, title, config)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)
    return output_path


def save_upload(field, destination_dir, preferred_name):
    if field is None or not getattr(field, "filename", ""):
        return None
    destination_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(field.filename).suffix or Path(preferred_name).suffix
    path = destination_dir / f"{Path(preferred_name).stem}-{uuid.uuid4().hex}{suffix}"
    with open(path, "wb") as handle:
        shutil.copyfileobj(field.file, handle)
    return path


def field_value(form, name, default=""):
    field = form[name] if name in form else None
    if field is None or getattr(field, "filename", ""):
        return default
    value = field.value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value


def image_data_url(path):
    image = Image.open(path).convert("RGB")
    image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def handle_create(script_text, title, subject_path=None, person_reference_path=None, progress_callback=None, client_slug=DEFAULT_CLIENT):
    ensure_dirs()
    config = get_client_config(client_slug)
    title = title.strip()
    if not title:
        raise RuntimeError("Add the thumbnail title first.")
    if not script_text.strip():
        raise RuntimeError("Paste the script first.")

    thumbnail_count = 2 if config["slug"] in TWO_THUMBNAIL_CLIENTS else 1
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    def build_variant(index, show_detailed_progress=False):
        variant_index = index + 1
        option_label = f"Option {variant_index}" if thumbnail_count > 1 else ""

        if progress_callback and show_detailed_progress:
            label = f" {variant_index}/{thumbnail_count}" if thumbnail_count > 1 else ""
            progress_callback(12, f"Building visual brief{label}...")
        brief = create_visual_brief(
            script_text,
            title,
            config,
            has_person_reference=bool(person_reference_path),
            variant_index=variant_index,
            variant_count=thumbnail_count,
        )

        if progress_callback and show_detailed_progress:
            label = f" {variant_index}/{thumbnail_count}" if thumbnail_count > 1 else ""
            message = f"Visual direction ready. Generating subject image{label}..."
            if person_reference_path:
                message = f"Visual direction ready. Generating subject image{label} with the person reference..."
            progress_callback(34, message)

        used_ai = bool(openai_key())
        current_subject_path = subject_path
        if not current_subject_path:
            if not used_ai and person_reference_path:
                current_subject_path = person_reference_path
            else:
                try:
                    current_subject_path = generate_subject_image(brief, config, person_reference_path=person_reference_path)
                except Exception as error:
                    if not person_reference_path:
                        raise
                    current_subject_path = person_reference_path
                    used_ai = False
                    brief["rationale"] = (
                        f"{brief.get('rationale', '')} Used the person reference photo directly because AI image generation failed: {error}"
                    ).strip()

        if progress_callback and show_detailed_progress:
            label = f" {variant_index}/{thumbnail_count}" if thumbnail_count > 1 else ""
            progress_callback(
                82,
                f"Image generated. Applying the {config['display_name']} template and title{label}...",
            )

        filename_suffix = f"-option-{variant_index}" if thumbnail_count > 1 else ""
        output_path = GENERATED_DIR / f"{timestamp}-{config['slug']}-{safe_filename(title)}{filename_suffix}.png"
        render_thumbnail(title, current_subject_path, output_path, config)

        if progress_callback and show_detailed_progress:
            label = f" {variant_index}/{thumbnail_count}" if thumbnail_count > 1 else ""
            progress_callback(96, f"Saving thumbnail{label}...")

        meta = {
            "title": title,
            "visual_prompt": brief["visual_prompt"],
            "negative_prompt": brief.get("negative_prompt", ""),
            "rationale": brief.get("rationale", ""),
            "thumbnail": str(output_path),
            "client": config["slug"],
            "client_name": config["display_name"],
            "design_asset": str(config["design_path"]),
            "title_font": str(config["font_path"]),
            "used_ai": used_ai,
            "person_reference_used": bool(person_reference_path),
            "option_index": variant_index,
            "option_count": thumbnail_count,
            "option_label": option_label,
        }
        save_json(output_path.with_suffix(".json"), meta)
        return meta

    if thumbnail_count == 1:
        metas = [build_variant(0, show_detailed_progress=True)]
    else:
        if progress_callback:
            message = "Building two visual directions..."
            if person_reference_path:
                message = "Building two visual directions with the person reference..."
            progress_callback(12, message)
            progress_callback(34, "Generating both subject images at the same time...")

        metas_by_index = [None] * thumbnail_count
        completed_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=thumbnail_count) as executor:
            futures = {
                executor.submit(build_variant, index): index
                for index in range(thumbnail_count)
            }
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                metas_by_index[index] = future.result()
                completed_count += 1
                if progress_callback and completed_count < thumbnail_count:
                    progress_callback(62, "First thumbnail ready. Finishing the second...")

        metas = metas_by_index
        if progress_callback:
            progress_callback(96, "Both thumbnails are ready. Saving results...")

    primary = metas[0]
    if thumbnail_count == 1:
        return primary

    return {
        **primary,
        "thumbnail": primary["thumbnail"],
        "used_ai": any(item["used_ai"] for item in metas),
        "thumbnails": metas,
    }


def cleanup_jobs():
    cutoff = time.time() - JOB_TTL_SECONDS
    with JOBS_LOCK:
        expired = [
            job_id
            for job_id, job in JOBS.items()
            if job.get("finished_at") and job["finished_at"] < cutoff
        ]
        for job_id in expired:
            JOBS.pop(job_id, None)


def update_job(job_id, **updates):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def public_job(job):
    now = time.time()
    started_at = job.get("started_at") or now
    payload = {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "elapsed_seconds": max(0, round(now - started_at)),
        "result": job.get("result"),
        "error": job.get("error", ""),
    }
    if job.get("finished_at"):
        payload["elapsed_seconds"] = max(0, round(job["finished_at"] - started_at))
    return payload


def get_job(job_id):
    cleanup_jobs()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return public_job(job) if job else None


def run_create_job(job_id, script_text, title, person_reference_path=None, client_slug=DEFAULT_CLIENT):
    def progress(progress_value, message):
        update_job(job_id, status="running", progress=progress_value, message=message)

    try:
        progress(6, "Starting thumbnail generation...")
        meta = handle_create(
            script_text=script_text,
            title=title,
            person_reference_path=person_reference_path,
            progress_callback=progress,
            client_slug=client_slug,
        )
        source_thumbnails = meta.get("thumbnails") or [meta]
        result_thumbnails = []
        for item in source_thumbnails:
            item_thumbnail_name = Path(item["thumbnail"]).name
            result_thumbnails.append(
                {
                    "title": item["title"],
                    "client": item["client"],
                    "client_name": item["client_name"],
                    "visual_prompt": item["visual_prompt"],
                    "thumbnail_url": f"/generated/{item_thumbnail_name}",
                    "download_url": f"/api/download/{item_thumbnail_name}",
                    "filename": item_thumbnail_name,
                    "used_ai": item["used_ai"],
                    "person_reference_used": item.get("person_reference_used", False),
                    "option_index": item.get("option_index", 1),
                    "option_count": item.get("option_count", len(source_thumbnails)),
                    "option_label": item.get("option_label", ""),
                }
            )
        primary_result = result_thumbnails[0]
        result = {
            "title": meta["title"],
            "client": meta["client"],
            "client_name": meta["client_name"],
            "visual_prompt": meta["visual_prompt"],
            "thumbnail_url": primary_result["thumbnail_url"],
            "download_url": primary_result["download_url"],
            "filename": primary_result["filename"],
            "used_ai": meta["used_ai"],
            "person_reference_used": meta.get("person_reference_used", False),
            "thumbnails": result_thumbnails,
        }
        update_job(
            job_id,
            status="done",
            progress=100,
            message="Done. Your thumbnails are ready." if len(result_thumbnails) > 1 else "Done. Your thumbnail is ready.",
            result=result,
            finished_at=time.time(),
        )
    except Exception as error:
        update_job(
            job_id,
            status="error",
            progress=100,
            message="Thumbnail creation failed.",
            error=str(error),
            finished_at=time.time(),
        )


def start_create_job(script_text, title, person_reference_path=None, client_slug=DEFAULT_CLIENT):
    cleanup_jobs()
    job_id = uuid.uuid4().hex
    now = time.time()
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued...",
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "result": None,
            "error": "",
        }
    thread = threading.Thread(
        target=run_create_job,
        args=(job_id, script_text, title, person_reference_path, client_slug),
        daemon=True,
    )
    thread.start()
    return job_id


class ThumbnailHandler(BaseHTTPRequestHandler):
    server_version = "ThumbnailAgent/3.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def request_path(self):
        return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)

    def request_query(self):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def send_json(self, payload, status=200, include_body=True):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def send_file(self, path, include_body=True, attachment_name=None):
        path = Path(path)
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        if attachment_name:
            safe_name = Path(attachment_name).name
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.end_headers()
        if include_body:
            self.wfile.write(path.read_bytes())

    def do_HEAD(self):
        path = self.request_path()
        if path == "/healthz":
            return self.send_json({"ok": True}, include_body=False)
        if path == "/":
            return self.send_file(PUBLIC_DIR / "index.html", include_body=False)
        if path == "/api/status":
            return self.send_json({"ok": True}, include_body=False)
        if path.startswith("/api/jobs/"):
            return self.send_json({"ok": True}, include_body=False)
        if path.startswith("/api/download/"):
            filename = path.removeprefix("/api/download/").strip("/")
            return self.send_file(generated_file_path(filename), include_body=False, attachment_name=filename)
        if path.startswith("/generated/"):
            return self.send_file(generated_file_path(path.removeprefix("/generated/")), include_body=False)
        if path in PUBLIC_ASSET_PATHS:
            return self.send_file(PUBLIC_DIR / path.lstrip("/"), include_body=False)
        self.send_error(404)

    def do_GET(self):
        path = self.request_path()
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path == "/":
            return self.send_file(PUBLIC_DIR / "index.html")
        if path == "/api/status":
            query = self.request_query()
            client_slug = query.get("client", [DEFAULT_CLIENT])[0]
            try:
                config = get_client_config(client_slug)
            except RuntimeError as error:
                return self.send_json({"error": str(error)}, 404)
            return self.send_json(
                {
                    "openai_configured": bool(openai_key()),
                    "model": openai_model(),
                    "client": public_client_status(config),
                    "clients": {slug: public_client_status(client) for slug, client in CLIENTS.items()},
                    "design_ready": config["design_path"].exists(),
                    "font_ready": config["font_path"].exists(),
                    "auth_enabled": False,
                    "allow_browser_key_setup": browser_key_setup_allowed(),
                }
            )
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/").strip("/")
            job = get_job(job_id)
            if not job:
                return self.send_json({"error": "Job not found."}, 404)
            return self.send_json(job)
        if path.startswith("/api/download/"):
            filename = path.removeprefix("/api/download/").strip("/")
            return self.send_file(generated_file_path(filename), attachment_name=filename)
        if path.startswith("/generated/"):
            return self.send_file(generated_file_path(path.removeprefix("/generated/")))
        if path in PUBLIC_ASSET_PATHS:
            return self.send_file(PUBLIC_DIR / path.lstrip("/"))
        self.send_error(404)

    def do_POST(self):
        path = self.request_path()
        if path == "/api/settings":
            return self.send_json({"error": "API key setup is disabled. Set OPENAI_API_KEY on the server."}, 403)

        if path != "/api/create":
            self.send_error(404)
            return

        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", "")},
            )
            request_dir = WORK_DIR / f"upload-{uuid.uuid4().hex}"
            request_dir.mkdir(parents=True, exist_ok=True)

            title = field_value(form, "title").strip()
            script_text = field_value(form, "script").strip()
            client_slug = field_value(form, "client", DEFAULT_CLIENT).strip() or DEFAULT_CLIENT
            try:
                get_client_config(client_slug)
            except RuntimeError as error:
                return self.send_json({"error": str(error)}, 400)
            if not title:
                return self.send_json({"error": "Add the thumbnail title first."}, 400)
            if not script_text:
                return self.send_json({"error": "Paste the script first."}, 400)

            person_reference_path = save_upload(
                form["person_reference"] if "person_reference" in form else None,
                request_dir,
                "person-reference.png",
            )

            job_id = start_create_job(
                script_text=script_text,
                title=title,
                person_reference_path=person_reference_path,
                client_slug=client_slug,
            )
            return self.send_json(
                {
                    "job_id": job_id,
                    "status_url": f"/api/jobs/{job_id}",
                },
                202,
            )
        except Exception as error:
            return self.send_json({"error": str(error)}, 500)


def serve(host, port):
    ensure_dirs()
    server = ThreadingHTTPServer((host, port), ThumbnailHandler)
    print(f"Thumbnail Agent running at http://{host}:{port}", flush=True)
    server.serve_forever()


def command_render(args):
    script_text = Path(args.script).read_text(encoding="utf-8")
    meta = handle_create(
        script_text=script_text,
        title=args.title,
        subject_path=Path(args.subject_image) if args.subject_image else None,
        client_slug=args.client,
    )
    print(json.dumps(meta, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create vertical thumbnails from scripts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the local web app.")
    serve_parser.add_argument("--host", default=os.environ.get("THUMBNAIL_AGENT_HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(os.environ.get("THUMBNAIL_AGENT_PORT", "8787")))

    render_parser = subparsers.add_parser("render", help="Render a thumbnail from the command line.")
    render_parser.add_argument("--script", required=True)
    render_parser.add_argument("--title", required=True)
    render_parser.add_argument("--subject-image")
    render_parser.add_argument("--client", default=DEFAULT_CLIENT, choices=sorted(CLIENTS))

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.host, args.port)
    elif args.command == "render":
        command_render(args)


if __name__ == "__main__":
    main()
