"""Server-only Higgsfield image generation using stdlib HTTP and Pillow.

Verified production contracts (2026-10-23):
https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference
https://docs.higgsfield.ai/docs/authentication
https://docs.higgsfield.ai/docs/concepts/file-uploads
https://docs.higgsfield.ai/docs/concepts/requests
https://docs.higgsfield.ai/docs/concepts/idempotency
https://docs.higgsfield.ai/docs/concepts/billing-and-retention

Direct Sunburst accepts up to 16 references; this app deliberately allows four.
No models are invoked at import time. Generation POSTs are never retried here,
including ambiguous failures. GET status/download requests may be retried.
"""

import io
import ipaddress
import json
import math
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, ImageOps


API_BASE = "https://api.higgsfield.ai"
DEFAULT_MODEL = "marketing-studio/image/sunburst"
MAX_REFERENCES = 4
MAX_JSON_BYTES = 1024 * 1024
MAX_MEDIA_BYTES = 32 * 1024 * 1024


class HiggsfieldError(RuntimeError):
    def __init__(self, message, *, retryable=False, ambiguous_submission=False):
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous_submission = ambiguous_submission
        self.request_id = None
        self.idempotency_key = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Neither credentials nor presigned uploads follow unvalidated redirects.
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def configured():
    values = [os.environ.get(name, "").strip() for name in ("HF_API_KEY_ID", "HF_API_KEY_SECRET")]
    return all(values) and all(not any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values)


def _credentials():
    if not configured():
        raise HiggsfieldError("Set HF_API_KEY_ID and HF_API_KEY_SECRET on the server before using Higgsfield.")
    return os.environ["HF_API_KEY_ID"].strip() + ":" + os.environ["HF_API_KEY_SECRET"].strip()


def image_model(has_references=False):
    model = os.environ.get("HF_IMAGE_MODEL", "").strip() or DEFAULT_MODEL
    if model != DEFAULT_MODEL:
        raise HiggsfieldError("HF_IMAGE_MODEL is not supported by this client. Use marketing-studio/image/sunburst.")
    return model


def image_quality(has_references=False):
    return "high"


def image_size(has_references=False):
    return "9:16 / 2K"


def _deadline(timeout):
    try:
        seconds = float(timeout)
    except (TypeError, ValueError):
        raise HiggsfieldError("The Higgsfield timeout must be a positive number.") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise HiggsfieldError("The Higgsfield timeout must be a positive number.")
    return time.monotonic() + min(seconds, 3600)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HiggsfieldError("Timed out waiting for Higgsfield. A submitted generation may still finish; do not resubmit automatically.")
    return remaining


def _validated_url(url, *, api=False):
    if not isinstance(url, str) or not url or any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise HiggsfieldError("Higgsfield returned an invalid media or status URL.")
    try:
        parsed = urllib.parse.urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise HiggsfieldError("Higgsfield returned an invalid media or status URL.") from None
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None or port not in (None, 443) or parsed.fragment:
        raise HiggsfieldError("Higgsfield returned an unsafe media or status URL.")
    if api:
        if host != "api.higgsfield.ai" or parsed.query:
            raise HiggsfieldError("Higgsfield returned an unsafe authenticated API URL.")
    else:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise ValueError("Non-public address")
        except (OSError, ValueError):
            raise HiggsfieldError("Higgsfield returned a media URL that is not publicly reachable.") from None
    return url


def _http_message(status):
    if status == 401:
        return "Higgsfield could not authenticate the server API credentials."
    if status in (402, 403):
        return "Higgsfield denied the request. Check the API dollar balance and account permissions."
    if status in (404, 423, 503):
        return "The Higgsfield model or request is unavailable for this account."
    if status == 429:
        return "Higgsfield reached a rate limit. Please try again later."
    if status >= 500:
        return "Higgsfield is temporarily unavailable."
    return "Higgsfield could not process the request. Check the prompt and reference photos."


def _request_bytes(request, deadline, *, limit=MAX_JSON_BYTES):
    try:
        with _OPENER.open(request, timeout=min(30, _remaining(deadline))) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                raise HiggsfieldError("Higgsfield returned a response larger than the allowed size.")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise HiggsfieldError("Higgsfield returned a response larger than the allowed size.")
            _remaining(deadline)
            return data
    except urllib.error.HTTPError as error:
        raise HiggsfieldError(
            f"{_http_message(error.code)} (HTTP {error.code})",
            retryable=error.code == 429 or error.code >= 500,
            ambiguous_submission=request.get_method() == "POST" and (300 <= error.code < 400 or error.code >= 500),
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise HiggsfieldError(
            "Could not reach Higgsfield. A submitted generation may still finish; do not resubmit automatically.",
            retryable=True, ambiguous_submission=request.get_method() == "POST",
        ) from None
    except (TypeError, ValueError):
        raise HiggsfieldError("Higgsfield returned an unreadable response.") from None


def _api_json(url, deadline, credential, *, payload=None, idempotency_key=None):
    url = API_BASE + url if url.startswith("/") else url
    _validated_url(url, api=True)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET")
    request.add_unredirected_header("Authorization", "Key " + credential)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if idempotency_key:
        request.add_header("Idempotency-Key", idempotency_key)
    try:
        result = json.loads(_request_bytes(request, deadline))
    except (ValueError, TypeError):
        raise HiggsfieldError("Higgsfield returned an unreadable response.", ambiguous_submission=bool(idempotency_key)) from None
    if not isinstance(result, dict):
        raise HiggsfieldError("Higgsfield returned an invalid response.", ambiguous_submission=bool(idempotency_key))
    return result


def _reference_bytes(paths):
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = list(paths)
    if len(paths) > MAX_REFERENCES:
        raise HiggsfieldError("Upload at most four person reference photos.")
    references = []
    for index, path in enumerate(paths, 1):
        try:
            path = Path(path)
            if not path.is_file() or not 0 < path.stat().st_size <= 20 * 1024 * 1024:
                raise ValueError("Invalid size")
            with Image.open(path) as loaded:
                if loaded.width * loaded.height > 40_000_000:
                    raise ValueError("Oversized image")
                photo = ImageOps.exif_transpose(loaded).convert("RGBA")
            if photo.getchannel("A").getextrema()[1] == 0:
                raise ValueError("Transparent image")
            photo.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            photo.save(output, format="PNG")
            references.append(output.getvalue())
        except Exception:
            raise HiggsfieldError(f"Could not read reference photo {index}. Use a valid image under 20 MB.") from None
    return references


def _upload_reference(data, deadline, credential):
    result = _api_json("/files/generate-upload-url", deadline, credential, payload={"content_type": "image/png"})
    upload_url = _validated_url(result.get("upload_url"))
    public_url = _validated_url(result.get("public_url"))
    headers = result.get("upload_headers")
    if not isinstance(headers, dict):
        raise HiggsfieldError("Higgsfield returned invalid upload instructions.")
    for name, value in headers.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", name)
                or name.lower() in {"authorization", "proxy-authorization", "cookie", "host"}
                or not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or credential in value):
            raise HiggsfieldError("Higgsfield returned unsafe upload instructions.")
    request = urllib.request.Request(upload_url, data=data, headers=headers, method="PUT")
    _request_bytes(request, deadline)
    return public_url


def _payload(prompt, urls=()):
    if not isinstance(prompt, str) or not prompt.strip():
        raise HiggsfieldError("Add a visual prompt before generating an image.")
    payload = {"prompt": prompt, "quality": "high", "resolution": "2k", "aspect_ratio": "9:16", "enhance_prompt": False}
    if urls:
        payload["image_urls"] = list(urls)
    return payload


def _wait(delay, deadline):
    time.sleep(min(delay + random.uniform(0, 0.25), _remaining(deadline)))


def _poll(submission, deadline, credential):
    request_id = submission.get("request_id")
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        raise HiggsfieldError("Higgsfield returned an incomplete generation receipt. Do not resubmit automatically.", ambiguous_submission=True) from None
    status_url = _validated_url(submission.get("status_url"), api=True)
    if urllib.parse.urlsplit(status_url).path != f"/requests/{request_id}/status":
        raise HiggsfieldError("Higgsfield returned an unsafe request status URL.", ambiguous_submission=True)
    result, delay = submission, 2.0
    while True:
        _remaining(deadline)
        if result.get("request_id", request_id) != request_id:
            raise HiggsfieldError("Higgsfield returned an inconsistent request status.")
        status = result.get("status")
        if status == "completed":
            return result
        if status in {"failed", "nsfw", "canceled"}:
            messages = {"failed": "Higgsfield could not finish this generation.", "nsfw": "Higgsfield declined the image request during moderation.", "canceled": "The Higgsfield generation was canceled."}
            raise HiggsfieldError(messages[status])
        if status not in {"queued", "in_progress"}:
            raise HiggsfieldError("Higgsfield returned an unknown request status.")
        _wait(delay, deadline)
        try:
            result = _api_json(status_url, deadline, credential)
        except HiggsfieldError as error:
            if not error.retryable:
                raise
        delay = min(delay * 1.5, 10)


def _download(result, deadline):
    images = result.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise HiggsfieldError("Higgsfield completed without an image.")
    url = _validated_url(images[0].get("url"))
    delay = 2.0
    while True:
        try:
            data = _request_bytes(urllib.request.Request(url, method="GET"), deadline, limit=MAX_MEDIA_BYTES)
            break
        except HiggsfieldError as error:
            if not error.retryable:
                raise
            _wait(delay, deadline)
            delay = min(delay * 1.5, 10)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_777_216:
                raise ValueError("Oversized result")
            if abs((image.width / image.height) / (9 / 16) - 1) > 0.02:
                raise ValueError("Unexpected result aspect ratio")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            photo = ImageOps.exif_transpose(image).convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            photo.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        raise HiggsfieldError("Higgsfield did not return a valid image.") from None


def generate_image(prompt, reference_paths=(), timeout=600):
    """Submit exactly one generation, poll it, and return validated PNG bytes."""
    model = image_model(bool(reference_paths))
    payload = _payload(prompt)
    credential, deadline = _credentials(), _deadline(timeout)
    references = _reference_bytes(reference_paths)
    if references:
        payload["image_urls"] = [_upload_reference(data, deadline, credential) for data in references]
    idempotency_key = str(uuid.uuid4())
    submission = None
    try:
        submission = _api_json("/" + model, deadline, credential, payload=payload, idempotency_key=idempotency_key)
        return _download(_poll(submission, deadline, credential), deadline)
    except HiggsfieldError as error:
        error.idempotency_key = idempotency_key
        if submission and isinstance(submission.get("request_id"), str):
            error.request_id = submission["request_id"]
        raise


def estimate_image(prompt, reference_urls=(), timeout=60):
    """Estimate using existing public URLs; never upload or generate in a dry run.

    Estimates exclude separate upload/storage fees, if any; the documented
    endpoint estimates generation only. Local files must be uploaded separately
    before an accurate reference-dependent estimate can be requested.
    """
    credential, deadline = _credentials(), _deadline(timeout)
    if isinstance(reference_urls, str):
        reference_urls = [reference_urls]
    urls = list(reference_urls)
    if len(urls) > MAX_REFERENCES:
        raise HiggsfieldError("Estimate at most four reference photos.")
    urls = [_validated_url(url) for url in urls]
    model = image_model(bool(urls))
    result = _api_json("/estimate/" + model, deadline, credential, payload=_payload(prompt, urls))
    estimate = {}
    for name in ("credits", "usd"):
        try:
            value = Decimal(str(result[name]))
            if value.is_finite() and 0 <= value <= 1_000_000_000 and value.as_tuple().exponent >= -12:
                estimate[name] = format(value, "f")
        except (KeyError, ValueError, InvalidOperation):
            pass
    if not estimate:
        raise HiggsfieldError("Higgsfield did not return a valid cost estimate.")
    return estimate
