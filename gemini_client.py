"""Small server-side Gemini JSON/vision client; no SDK or credential persistence.

Verified 2026-10-23 against Google's model pages and generateContent REST reference:
https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
https://ai.google.dev/api/generate-content
https://ai.google.dev/gemini-api/docs/image-understanding

Planning uses stable Flash-Lite; feature comparison independently uses stable Flash.
All images are EXIF-oriented, metadata-free, at most 2048px on the longest side.
Opaque images use JPEG quality90, transparent images PNG. A 1664px second pass is
used only when needed to fit Google's 20MB inline-request limit. Source files are
never modified. Requests are not automatically retried, avoiding duplicate costs.
"""

import base64
import binascii
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import time
import urllib.error
import urllib.request

from PIL import Image, ImageOps


DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_REVIEW_MODEL = "gemini-3.8-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_IMAGES = 5  # Four original references and the finished thumbnail.
MAX_REQUEST_BYTES = 20_000_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_SOURCE_BYTES = 40_000_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_TIMEOUT = 180.0
_MODEL_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
_DATA_IMAGE_PATTERN = re.compile(r"data:image/(?:jpeg|jpg|png|webp|gif);base64,([A-Za-z0-9+/=]+)\Z", re.I)


def configured():
    """Report usable key syntax only; never return, log or persist credentials."""
    key = os.environ.get("GEMINI_API_KEY", "").strip(" ")
    return bool(re.fullmatch(r"[!-~]+", key))


def model(review=False):
    """Return the independently configured planning or feature-review model."""
    name = "GEMINI_REVIEW_MODEL" if review else "GEMINI_MODEL"
    default = DEFAULT_REVIEW_MODEL if review else DEFAULT_MODEL
    return _validated_model(os.environ.get(name, "").strip() or default)


def _validated_model(value):
    if not isinstance(value, str) or not _MODEL_PATTERN.fullmatch(value):
        raise ValueError("Gemini model configuration must contain a model ID, not a URL.")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # An API redirect must never forward private images or the API-key header.
        return None


def _open_request(request, timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Gemini review timed out. Please try again.")
    return remaining


def _image_bytes(value):
    if isinstance(value, str) and value.startswith("data:"):
        match = _DATA_IMAGE_PATTERN.fullmatch(value)
        if not match or len(match.group(1)) > (MAX_SOURCE_BYTES * 4 // 3 + 4):
            raise ValueError("Gemini requires a valid inline image within the upload limit.")
        try:
            raw = base64.b64decode(match.group(1), validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Gemini received an invalid inline image.") from None
    else:
        if not isinstance(value, (str, os.PathLike)) or (isinstance(value, str) and "://" in value):
            raise ValueError("Gemini images must be local files or inline image data.")
        try:
            with Path(value).open("rb") as source:
                raw = source.read(MAX_SOURCE_BYTES + 1)
        except (OSError, ValueError):
            raise ValueError("A Gemini review image could not be read.") from None
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("A Gemini review image is empty or exceeds the upload limit.")
    return raw


def _normalized_image(raw, max_side):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions exceed the review limit.")
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            if has_alpha and image.getchannel("A").getextrema() == (255, 255):
                image = image.convert("RGB")
                has_alpha = False
            image.info.clear()
            encoded = io.BytesIO()
            if has_alpha:
                image.save(encoded, "PNG")
                mime = "image/png"
            else:
                image.save(encoded, "JPEG", quality=90)
                mime = "image/jpeg"
        return {"inlineData": {"mimeType": mime, "data": base64.b64encode(encoded.getvalue()).decode("ascii")}}
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ValueError("A Gemini review image is invalid or too large to process safely.") from None


def _ordered_inputs(user_text, image_paths, content):
    if not isinstance(user_text, str):
        raise ValueError("Gemini user text must be a string.")
    inputs = [("text", user_text)] if user_text else []
    if content is not None:
        if image_paths:
            raise ValueError("Use either Gemini content or image_paths, not both.")
        if not isinstance(content, (list, tuple)):
            raise ValueError("Gemini content must be an ordered list of text and inline images.")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("Gemini content contains an unsupported part.")
            if part.get("type") == "input_text" and isinstance(part.get("text"), str):
                inputs.append(("text", part["text"]))
            elif part.get("type") == "input_image" and isinstance(part.get("image_url"), str):
                if not part["image_url"].startswith("data:"):
                    raise ValueError("Gemini content only accepts inline image data; remote URLs are disabled.")
                inputs.append(("image", part["image_url"]))
            else:
                raise ValueError("Gemini content contains an unsupported part.")
    else:
        if isinstance(image_paths, (str, os.PathLike)):
            raise ValueError("Gemini image_paths must be an ordered sequence.")
        for index, entry in enumerate(image_paths, 1):
            label, path = entry if isinstance(entry, (tuple, list)) and len(entry) == 2 else (f"Image {index}.", entry)
            if not isinstance(label, str):
                raise ValueError("Gemini image labels must be strings.")
            inputs.extend([("text", label), ("image", path)])
    if not inputs:
        raise ValueError("Gemini requires text or an image to review.")
    if sum(kind == "image" for kind, _ in inputs) > MAX_IMAGES:
        raise ValueError("Gemini review supports at most five ordered images.")
    return [(kind, _image_bytes(value) if kind == "image" else value) for kind, value in inputs]


def _strict_json(raw):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON property.")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number.")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number.")
        return result

    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant, parse_float=finite_float)


def _matches_schema(value, schema):
    """Validate the simple JSON-schema subset used by the app's three reviews."""
    kinds = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_schema(value, {**schema, "type": kind}) for kind in expected):
            return False
    elif expected:
        if expected not in kinds or not isinstance(value, kinds[expected]):
            return False
        if expected in ("integer", "number") and isinstance(value, bool):
            return False
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        return False
    if "anyOf" in schema and not any(_matches_schema(value, option) for option in schema["anyOf"]):
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(_matches_schema(item, properties[key]) for key, item in value.items() if key in properties)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            return False
        return all(_matches_schema(item, schema.get("items", {})) for item in value)
    if type(value) in (int, float):
        return math.isfinite(value) and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
    return True


def _parse_response(response, schema):
    try:
        envelope = _strict_json(response)
        if not isinstance(envelope, dict):
            raise ValueError()
        feedback = envelope.get("promptFeedback", {})
        if not isinstance(feedback, dict):
            raise ValueError()
        if feedback.get("blockReason") not in (None, "", "BLOCK_REASON_UNSPECIFIED"):
            raise RuntimeError("Gemini could not review this content because the request was blocked.")
        prompt_ratings = feedback.get("safetyRatings", [])
        if not isinstance(prompt_ratings, list) or any(not isinstance(rating, dict) or rating.get("blocked") is True for rating in prompt_ratings):
            raise RuntimeError("Gemini could not complete this content review safely.")
        candidates = envelope.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
            raise ValueError()
        candidate = candidates[0]
        if candidate.get("finishReason") != "STOP":
            raise RuntimeError("Gemini did not complete the review. No unchecked result was accepted.")
        ratings = candidate.get("safetyRatings", [])
        if not isinstance(ratings, list) or any(not isinstance(rating, dict) or rating.get("blocked") is True for rating in ratings):
            raise RuntimeError("Gemini could not complete this content review safely.")
        parts = candidate["content"]["parts"]
        if not isinstance(parts, list) or not parts:
            raise ValueError()
        text = []
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError()
            if part.get("thought") is True:
                continue
            if not isinstance(part.get("text"), str) or "functionCall" in part or "inlineData" in part:
                raise ValueError()
            text.append(part["text"])
        result = _strict_json("".join(text))
        if not isinstance(result, dict) or (schema is not None and not _matches_schema(result, schema)):
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise RuntimeError("Gemini returned an invalid JSON review. No unchecked result was accepted.") from None


def _http_error(status):
    if status in (401, 403):
        message = "Gemini could not authorize the server API key. Check its project, API restrictions and model permissions."
    elif status == 429:
        message = "Gemini quota or rate limit was reached. Check the project's usage, billing and limits."
    elif status == 404:
        message = "The configured Gemini model is unavailable. Check the model ID and project access."
    elif status >= 500:
        message = "Gemini is temporarily unavailable. Please try again."
    else:
        message = "Gemini could not process this review request. Check the model and request settings."
    return RuntimeError(f"{message} (HTTP {status})")


def generate_json(system_prompt, user_text="", image_paths=(), schema=None, timeout=180, content=None, model_name=None):
    """Return a strict JSON object from one bounded generateContent POST.

    ``content`` preserves OpenAI-style input_text/input_image(data URL) parts in
    their original order. Alternatively image_paths accepts paths or (label,path)
    pairs appended after user_text. At most five images are allowed. No remote
    images, tools, source-file writes, retries or provider-body errors are used.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip(" ")
    if not key:
        raise RuntimeError("Gemini is not configured. Set GEMINI_API_KEY on the server.")
    if not re.fullmatch(r"[!-~]+", key):
        raise RuntimeError("Gemini server API key configuration is invalid.")
    if not isinstance(system_prompt, str) or (schema is not None and not isinstance(schema, dict)):
        raise ValueError("Gemini requires a text system prompt and an object JSON schema.")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Gemini timeout must be a positive number of seconds.")
    deadline = time.monotonic() + min(float(timeout), MAX_TIMEOUT)
    selected_model = model() if model_name is None else _validated_model(model_name.strip() if isinstance(model_name, str) else model_name)
    inputs = _ordered_inputs(user_text, image_paths, content)
    generation = {"responseMimeType": "application/json", "candidateCount": 1}
    if schema is not None:
        generation["responseJsonSchema"] = schema
    for max_side in (2048, 1664):
        _remaining(deadline)
        parts = [{"text": value} if kind == "text" else _normalized_image(value, max_side) for kind, value in inputs]
        payload = {"systemInstruction": {"parts": [{"text": system_prompt}]}, "contents": [{"role": "user", "parts": parts}], "generationConfig": generation}
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            raise ValueError("Gemini received an invalid JSON schema or prompt.") from None
        if len(body) <= MAX_REQUEST_BYTES:
            break
    else:
        raise ValueError("Gemini review exceeds the 20MB request limit. Use smaller reference images or shorter text.")
    request = urllib.request.Request(f"{API_BASE}/{selected_model}:generateContent", data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    try:
        with _open_request(request, _remaining(deadline)) as response:
            chunks = []
            total = 0
            while True:
                _remaining(deadline)
                chunk = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Gemini returned an oversized review response.")
        _remaining(deadline)
    except urllib.error.HTTPError as error:
        raise _http_error(error.code) from None
    except (TimeoutError, socket.timeout):
        raise RuntimeError("Gemini review timed out. Please try again.") from None
    except (urllib.error.URLError, OSError):
        raise RuntimeError("Gemini could not be reached. Please try again.") from None
    return _parse_response(b"".join(chunks), schema)
