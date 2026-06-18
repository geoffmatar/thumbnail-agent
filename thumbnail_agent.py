#!/usr/bin/env python3
import argparse
import base64
import hmac
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import urllib.error
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

DESIGN_PATH = ASSETS_DIR / "zoomex-design.png"
TITLE_FONT_PATH = ASSETS_DIR / "Blinker-Bold.ttf"
OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
CANVAS_SIZE = (1080, 1920)

TOP_BAND = {"x": 160, "y": 1243, "w": 760, "h": 116, "radius": 62}
BOTTOM_BAND = {"x": 160, "y": 1366, "w": 760, "h": 126, "radius": 64}
FOCUS_ZONE = {"x": 210, "y": 560, "w": 660, "h": 660}


def focus_zone_prompt():
    x1 = FOCUS_ZONE["x"]
    y1 = FOCUS_ZONE["y"]
    x2 = FOCUS_ZONE["x"] + FOCUS_ZONE["w"]
    y2 = FOCUS_ZONE["y"] + FOCUS_ZONE["h"]
    return (
        f"Composition: place the important subject details inside the central focus square "
        f"from x={x1} to {x2}, y={y1} to {y2} on a 1080x1920 canvas. "
        "This square is between the ZOOMEX logo and the title bars. "
        "Faces, eyes, heads, products, vehicles, or the most important action must be centered in that square. "
        "The subject may extend beyond the square, but no important face or key object should be hidden by the top-left logo "
        "or by the title bars at the bottom."
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


def app_password():
    return os.environ.get("APP_PASSWORD", "").strip()


def browser_key_setup_allowed():
    if os.environ.get("ALLOW_BROWSER_API_KEY_SETUP", "").strip() == "1":
        return True
    return not app_password()


def safe_filename(value, fallback="thumbnail"):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:70] or fallback


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


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
                "description": "Prompt for the full-frame image behind the fixed ZOOMEX design. No text/logos.",
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


def create_visual_brief(script_text, title):
    if not openai_key():
        return {
            "visual_prompt": (
                "Create a vertical 9:16 full-frame editorial thumbnail image based on the script. "
                f"No text, no captions, no logos, no black empty poster space. {focus_zone_prompt()}"
            ),
            "negative_prompt": "text, captions, watermarks, logos, blank black background, empty poster space",
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
                    "The title is supplied by the user and will be rendered later in fixed ZOOMEX title bars. "
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
                            "The final image must be full-frame 9:16 and fill the whole thumbnail behind a fixed transparent ZOOMEX design layer. "
                            "Avoid black voids, blank poster space, title-card layouts, or large empty areas. "
                            f"{focus_zone_prompt()} "
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


def generate_subject_image(brief):
    prompt = (
        f"{brief['visual_prompt']}\n\n"
        "Hard requirements: vertical 9:16, full-bleed image, no text, no readable letters, no logos, no watermarks. "
        "The scene must fill the entire canvas from top to bottom. Do not make a mostly black image. "
        "Do not leave giant blank areas for text. Use real environment, texture, action, and depth behind the whole frame. "
        f"{focus_zone_prompt()} "
        "Keep the area behind the title bars lower contrast but still visually present."
    )
    payload = {
        "model": openai_model(),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "tools": [{"type": "image_generation", "action": "generate"}],
    }
    image_bytes = base64.b64decode(extract_image_base64(openai_responses_create(payload)))
    image_path = WORK_DIR / f"subject-{uuid.uuid4().hex}.png"
    image_path.write_bytes(image_bytes)
    return image_path


def resolve_font(size):
    candidates = [
        TITLE_FONT_PATH,
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


def fit_font(text, max_width, max_height, max_size, min_size):
    probe = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    for size in range(max_size, min_size - 1, -2):
        font = resolve_font(size)
        width, height = text_size(draw, text, font)
        if width <= max_width and height <= max_height:
            return font
    return resolve_font(min_size)


def draw_centered_text(draw, box, text, font, fill):
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tx = x + (w - (right - left)) / 2 - left
    ty = y + (h - (bottom - top)) / 2 - top
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


def draw_title(canvas, title):
    top_text, bottom_text = split_title(title)
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle(
        (TOP_BAND["x"], TOP_BAND["y"], TOP_BAND["x"] + TOP_BAND["w"], TOP_BAND["y"] + TOP_BAND["h"]),
        radius=TOP_BAND["radius"],
        fill=(0, 0, 0, 245),
    )
    top_font = fit_font(top_text, TOP_BAND["w"] - 88, TOP_BAND["h"] * 0.72, 84, 38)
    draw_centered_text(draw, TOP_BAND, top_text, top_font, "#12d8c3")

    if bottom_text:
        draw.rounded_rectangle(
            (
                BOTTOM_BAND["x"],
                BOTTOM_BAND["y"],
                BOTTOM_BAND["x"] + BOTTOM_BAND["w"],
                BOTTOM_BAND["y"] + BOTTOM_BAND["h"],
            ),
            radius=BOTTOM_BAND["radius"],
            fill=(18, 216, 195, 255),
        )
        bottom_font = fit_font(bottom_text, BOTTOM_BAND["w"] - 88, BOTTOM_BAND["h"] * 0.72, 92, 38)
        draw_centered_text(draw, BOTTOM_BAND, bottom_text, bottom_font, "#030303")
    return canvas


def render_thumbnail(title, subject_image_path, output_path):
    if not DESIGN_PATH.exists():
        raise RuntimeError(f"Missing fixed design asset: {DESIGN_PATH}")
    if not TITLE_FONT_PATH.exists():
        raise RuntimeError(f"Missing title font asset: {TITLE_FONT_PATH}")
    subject = adjust_subject(fit_cover(Image.open(subject_image_path), CANVAS_SIZE))
    design = Image.open(DESIGN_PATH).convert("RGBA").resize(CANVAS_SIZE, Image.Resampling.LANCZOS)
    canvas = Image.alpha_composite(subject, design)
    canvas = draw_title(canvas, title)
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


def handle_create(script_text, title, subject_path=None):
    ensure_dirs()
    title = title.strip()
    if not title:
        raise RuntimeError("Add the thumbnail title first.")
    if not script_text.strip():
        raise RuntimeError("Paste the script first.")

    brief = create_visual_brief(script_text, title)
    used_ai = bool(openai_key())
    if not subject_path:
        subject_path = generate_subject_image(brief)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = GENERATED_DIR / f"{timestamp}-{safe_filename(title)}.png"
    render_thumbnail(title, subject_path, output_path)

    meta = {
        "title": title,
        "visual_prompt": brief["visual_prompt"],
        "negative_prompt": brief.get("negative_prompt", ""),
        "rationale": brief.get("rationale", ""),
        "thumbnail": str(output_path),
        "design_asset": str(DESIGN_PATH),
        "title_font": str(TITLE_FONT_PATH),
        "used_ai": used_ai,
    }
    save_json(output_path.with_suffix(".json"), meta)
    return meta


class ThumbnailHandler(BaseHTTPRequestHandler):
    server_version = "ZoomexThumbnailAgent/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def is_authorized(self):
        password = app_password()
        if not password:
            return True
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        except Exception:
            return False
        provided_password = decoded.split(":", 1)[1] if ":" in decoded else ""
        return hmac.compare_digest(provided_password, password)

    def require_authorized(self):
        if self.is_authorized():
            return True
        body = b"Authentication required."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ZOOMEX Thumbnail Agent"')
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        path = Path(path)
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.send_json({"ok": True})
        if not self.require_authorized():
            return
        if self.path == "/":
            return self.send_file(PUBLIC_DIR / "index.html")
        if self.path == "/api/status":
            return self.send_json(
                {
                    "openai_configured": bool(openai_key()),
                    "model": openai_model(),
                    "design_ready": DESIGN_PATH.exists(),
                    "font_ready": TITLE_FONT_PATH.exists(),
                    "auth_enabled": bool(app_password()),
                    "allow_browser_key_setup": browser_key_setup_allowed(),
                }
            )
        if self.path.startswith("/generated/"):
            return self.send_file(GENERATED_DIR / self.path.removeprefix("/generated/"))
        if self.path in ["/styles.css", "/app.js"]:
            return self.send_file(PUBLIC_DIR / self.path.lstrip("/"))
        self.send_error(404)

    def do_POST(self):
        if not self.require_authorized():
            return
        if self.path == "/api/settings":
            if not browser_key_setup_allowed():
                return self.send_json({"error": "API key setup is disabled on this hosted app. Set OPENAI_API_KEY on the server."}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                api_key = str(payload.get("openai_api_key", "")).strip()
                if not api_key:
                    return self.send_json({"error": "Paste your OpenAI API key first."}, 400)
                save_env_value("OPENAI_API_KEY", api_key)
                return self.send_json({"ok": True, "openai_configured": True})
            except Exception as error:
                return self.send_json({"error": str(error)}, 500)

        if self.path != "/api/create":
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
            if not title:
                return self.send_json({"error": "Add the thumbnail title first."}, 400)
            if not script_text:
                return self.send_json({"error": "Paste the script first."}, 400)

            meta = handle_create(script_text=script_text, title=title)
            thumbnail_name = Path(meta["thumbnail"]).name
            return self.send_json(
                {
                    "title": meta["title"],
                    "visual_prompt": meta["visual_prompt"],
                    "thumbnail_url": f"/generated/{thumbnail_name}",
                    "used_ai": meta["used_ai"],
                }
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
    )
    print(json.dumps(meta, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create ZOOMEX vertical thumbnails from scripts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the local web app.")
    serve_parser.add_argument("--host", default=os.environ.get("THUMBNAIL_AGENT_HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(os.environ.get("THUMBNAIL_AGENT_PORT", "8787")))

    render_parser = subparsers.add_parser("render", help="Render a thumbnail from the command line.")
    render_parser.add_argument("--script", required=True)
    render_parser.add_argument("--title", required=True)
    render_parser.add_argument("--subject-image")

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.host, args.port)
    elif args.command == "render":
        command_render(args)


if __name__ == "__main__":
    main()
