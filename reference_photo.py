"""Contain complete original photos without generating, masking, or retouching people."""

from PIL import Image, ImageOps


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


def prepare_reference_photo(path, canvas_size, focus_zone):
    """Fit the entire EXIF-corrected source into the focus zone, bottom aligned.

    Original RGB and supplied alpha receive only a uniform geometric resize.
    Keeping the complete photo also retains every person, strand of hair, hand,
    background, and collage divider, without relying on a predicted mask.
    """
    canvas_size, zone = _clamp_geometry(canvas_size, focus_zone)
    with Image.open(path) as loaded:
        source = ImageOps.exif_transpose(loaded).convert("RGBA")
    alpha_range = source.getchannel("A").getextrema()
    if alpha_range[1] == 0:
        raise RuntimeError("The reference image is fully transparent and contains no visible photo.")
    existing_alpha = alpha_range[0] < 255
    scale = min(zone["w"] / source.width, zone["h"] / source.height)
    rendered_size = (
        max(1, min(zone["w"], round(source.width * scale))),
        max(1, min(zone["h"], round(source.height * scale))),
    )
    resized = source.resize(rendered_size, Image.Resampling.LANCZOS)
    x = zone["x"] + (zone["w"] - resized.width) // 2
    y = zone["y"] + zone["h"] - resized.height
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    layer.alpha_composite(resized, (x, y))
    full_source_box = [0, 0, source.width, source.height]
    info = {
        "strategy": "local_photo_composite",
        "source_retention": "entire_original_photo",
        "source_retention_note": "The complete uploaded photo, including its background and any collage dividers, is fitted proportionally.",
        "source_crop_applied": False,
        "source_size": list(source.size),
        "canvas_size": list(canvas_size),
        "focus_zone": zone,
        "existing_alpha": existing_alpha,
        "fallback_used": False,
        "fallback_reasons": [],
        "segmentation_model": None,
        "separator_rows": [],
        "separator_columns": [],
        "people_regenerated": False,
        "resampling": "lanczos",
        "panels": [{
            "source_box": full_source_box,
            "alpha_crop_box": list(full_source_box),
            "source_crop_size": list(source.size),
            "destination_box": [x, y, x + resized.width, y + resized.height],
            "rendered_size": list(resized.size),
            "mask_source": "existing_alpha" if existing_alpha else "intact_photo",
            "fallback_reason": None,
        }],
    }
    return layer, info
