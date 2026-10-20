"""Build original-pixel reference layers; predicted cutouts require a separate audit."""

import hashlib
import threading
import uuid
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps


MODEL_PATH = Path(__file__).resolve().parent / "assets" / "u2netp.onnx"
MODEL_SHA256 = "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"
_MODEL_LOCK = threading.Lock()
_SESSION = None


def _clamp_geometry(canvas_size, focus_zone):
    if len(canvas_size) != 2:
        raise ValueError("Canvas size must contain width and height.")
    width, height = (max(1, min(8192, int(value))) for value in canvas_size)
    zone = focus_zone or {"x": 0, "y": 0, "w": width, "h": height}
    x, y = int(zone.get("x", 0)), int(zone.get("y", 0))
    right = x + max(1, int(zone.get("w", width)))
    bottom = y + max(1, int(zone.get("h", height)))
    x, y = max(0, min(width - 1, x)), max(0, min(height - 1, y))
    right, bottom = max(x + 1, min(width, right)), max(y + 1, min(height, bottom))
    return (width, height), {"x": x, "y": y, "w": right - x, "h": bottom - y}


def _get_session_locked():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort

        if not MODEL_PATH.is_file():
            raise RuntimeError("The bundled reference cutout model is unavailable.")
        digest = hashlib.sha256()
        with MODEL_PATH.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError("The bundled reference cutout model failed its checksum.")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        _SESSION = ort.InferenceSession(str(MODEL_PATH), sess_options=options, providers=["CPUExecutionProvider"])
    return _SESSION


def _predict_mask(image):
    """Predict alpha only, without generating or changing source RGB channels."""
    import numpy as np

    with _MODEL_LOCK:
        session = _get_session_locked()
        pixels = np.asarray(image.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
        pixels /= max(float(pixels.max()), 1.0)
        pixels = (pixels - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        inputs = np.transpose(pixels, (2, 0, 1))[None, ...]
        raw = session.run(None, {session.get_inputs()[0].name: inputs})[0][0, 0]
        if not np.isfinite(raw).all() or float(raw.max() - raw.min()) < 1e-8:
            raise RuntimeError("The reference cutout model returned an empty or invalid mask.")
        raw = (raw - raw.min()) / float(raw.max() - raw.min())
        mask = Image.fromarray(np.uint8(np.clip(raw * 255, 0, 255))).resize(image.size, Image.Resampling.LANCZOS)
    # This is only a candidate: numeric mask tests cannot prove every person or
    # strand of hair is retained. Keep generous edges and opaque interiors.
    return mask.point(lambda value: max(0, min(255, round((value - 8) * 255 / 120)))).filter(ImageFilter.MaxFilter(11))


def _separator_runs(image, axis):
    """Find narrow neutral gutters, allowing small interruptions such as hands."""
    import numpy as np

    rgb = np.asarray(image.convert("RGB"))
    if axis == "columns":
        rgb = np.transpose(rgb, (1, 0, 2))
    rgb = rgb[:, ::max(1, rgb.shape[1] // 384)]
    low, high = rgb.min(axis=2), rgb.max(axis=2)
    coverage = ((low >= 160) & ((high.astype(np.int16) - low) <= 35)).mean(axis=1)
    deviation = rgb.std(axis=1).mean(axis=1)
    candidates = (coverage >= 0.80) & (deviation < 23)
    length = len(candidates)
    runs, start = [], None
    for index, matches in enumerate([*candidates, False]):
        if matches and start is None:
            start = index
        elif not matches and start is not None:
            if index - start >= 3 and start >= length * 0.15 and index <= length * 0.85:
                left, right = start, index
                while left > 0 and coverage[left - 1] >= 0.60 and deviation[left - 1] < 45:
                    left -= 1
                while right < length and coverage[right] >= 0.60 and deviation[right] < 45:
                    right += 1
                if right - left <= length * 0.12 and (not runs or left > runs[-1][1]):
                    runs.append((left, right))
            start = None
    return runs


def _partition_source(image):
    # Assign EVERY gutter pixel to its preceding panel. A divider can also
    # contain fingers; partitioning must not discard source rows or columns.
    axis = "rows" if image.height >= image.width * 1.2 else "columns" if image.width >= image.height * 1.2 else None
    runs = _separator_runs(image, axis) if axis else []
    length = image.height if axis == "rows" else image.width
    minimum = max(16, round(length * 0.18))
    cuts, retained_runs, last = [], [], 0
    for left, right in runs[:3]:
        if right - last >= minimum and length - right >= minimum:
            cuts.append(right)
            retained_runs.append([left, right])
            last = right
    boundaries = [0, *cuts, length]
    boxes = []
    for start, end in zip(boundaries, boundaries[1:]):
        boxes.append((0, start, image.width, end) if axis == "rows" else (start, 0, end, image.height))
    return boxes, retained_runs if axis == "rows" else [], retained_runs if axis == "columns" else []


def _usable_mask(mask, size):
    if mask.mode != "L" or mask.size != size or not mask.getbbox():
        return False
    histogram = mask.histogram()
    count = size[0] * size[1]
    return sum(histogram[:32]) >= count * 0.01 and sum(histogram[224:]) >= count * 0.02


def _compose_panels(source, canvas_size, zone, records):
    layer = Image.new("RGBA", canvas_size)
    count = len(records)
    gap = min(24, max(0, zone["w"] // 50)) if count > 1 else 0
    available = max(1, zone["w"] - gap * (count - 1))
    ratios = [(record["alpha_crop_box"][2] - record["alpha_crop_box"][0]) /
              (record["alpha_crop_box"][3] - record["alpha_crop_box"][1]) for record in records]
    common_height = min(zone["h"], available / sum(ratios))
    height = max(1, min(zone["h"], round(common_height)))
    widths = [max(1, min(available, round(common_height * ratio))) for ratio in ratios]
    # Integer rounding can add a pixel to several widths. Keep the row inside
    # the zone without changing any source crop or predicted alpha.
    while sum(widths) > available and max(widths) > 1:
        largest = widths.index(max(widths))
        widths[largest] -= 1
    row_width = sum(widths) + gap * (count - 1)
    cursor = zone["x"] + max(0, (zone["w"] - row_width) // 2)
    rgb_source = source.convert("RGB")
    for index, record in enumerate(records):
        box, crop = record["source_box"], record["alpha_crop_box"]
        rgb = rgb_source.crop(box).crop(crop)
        alpha = record.pop("_alpha").crop(crop)
        size = (widths[index], height)
        # Resize RGB independently of predicted alpha: the mask cannot retouch
        # source colors through premultiplied-alpha interpolation.
        photo = rgb.resize(size, Image.Resampling.LANCZOS).convert("RGBA")
        photo.putalpha(alpha.resize(size, Image.Resampling.LANCZOS))
        x = min(zone["x"] + zone["w"] - size[0], cursor)
        y = zone["y"] + zone["h"] - size[1]
        layer.alpha_composite(photo, (x, y))
        record.update(source_crop_size=list(rgb.size), rendered_size=list(size), destination_box=[x, y, x + size[0], y + size[1]])
        cursor += size[0] + gap
    return layer


def prepare_reference_photo(path, canvas_size, focus_zone, intact=False):
    """Return original RGB + alpha and a JSON-safe composition record.

    Opaque extraction is an UNVALIDATED candidate: the caller must audit
    source_mask_path against the original before acceptance. On uncertainty,
    call with intact=True. Trusted uploaded alpha bypasses prediction.
    Runtime/model/mask failures retain the whole photo without any redraw.
    """
    canvas_size, zone = _clamp_geometry(canvas_size, focus_zone)
    with Image.open(path) as loaded:
        source = ImageOps.exif_transpose(loaded).convert("RGBA")
    alpha = source.getchannel("A")
    if alpha.getextrema()[1] == 0:
        raise RuntimeError("The reference image is fully transparent and contains no visible photo.")
    existing_alpha = alpha.getextrema()[0] < 255
    full_box = [0, 0, *source.size]
    info = {
        "strategy": "local_photo_composite", "source_retention": "entire_original_photo",
        "source_retention_note": "The complete original photo is fitted proportionally.",
        "source_crop_applied": False, "source_size": list(source.size),
        "canvas_size": list(canvas_size), "focus_zone": zone, "existing_alpha": existing_alpha,
        "fallback_used": False, "fallback_reasons": [], "intact_requested": bool(intact),
        "segmentation_model": None, "separator_rows": [], "separator_columns": [],
        "people_regenerated": False, "resampling": "lanczos", "requires_mask_validation": False,
        "source_mask_path": None, "panels": [],
    }
    if not existing_alpha and not intact:
        try:
            boxes, rows, columns = _partition_source(source)
            source_mask = Image.new("L", source.size)
            records = []
            for box in boxes:
                panel = source.crop(box)
                mask = _predict_mask(panel)
                if not _usable_mask(mask, panel.size):
                    raise RuntimeError("A reference panel has an unusable candidate mask.")
                source_mask.paste(mask, box[:2])
                records.append({"source_box": list(box), "alpha_crop_box": list(mask.getbbox()),
                                "mask_source": "u2netp_candidate", "fallback_reason": None, "_alpha": mask})
            mask_path = Path(path).parent / f"person-mask-{uuid.uuid4().hex}.png"
            source_mask.save(mask_path, format="PNG")
            info.update(
                source_retention="original_rgb_with_candidate_alpha",
                source_crop_applied=any(record["alpha_crop_box"] != [0, 0, box[2] - box[0], box[3] - box[1]] for record, box in zip(records, boxes)),
                source_retention_note="Original RGB pixels with predicted alpha; all-person coverage requires a separate audit before acceptance.",
                segmentation_model="u2netp", separator_rows=rows, separator_columns=columns,
                requires_mask_validation=True, source_mask_path=str(mask_path), panels=records,
            )
            return _compose_panels(source, canvas_size, zone, records), info
        except Exception as error:
            if info["source_mask_path"]:
                Path(info["source_mask_path"]).unlink(missing_ok=True)
            info.update(source_retention="entire_original_photo", source_crop_applied=False,
                        source_retention_note="The complete original photo is fitted proportionally.",
                        segmentation_model=None, separator_rows=[], separator_columns=[],
                        requires_mask_validation=False, source_mask_path=None)
            info["fallback_used"] = True
            info["fallback_reasons"] = ["Local cutout unavailable or uncertain; the complete original photo was retained."]
            info["fallback_error_type"] = type(error).__name__
    mask_source = "existing_alpha" if existing_alpha else "intact_photo"
    records = [{"source_box": full_box, "alpha_crop_box": list(full_box), "mask_source": mask_source,
                "fallback_reason": info["fallback_reasons"][0] if info["fallback_used"] else None, "_alpha": alpha}]
    info["panels"] = records
    return _compose_panels(source, canvas_size, zone, records), info
