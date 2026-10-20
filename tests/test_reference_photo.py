import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

import reference_photo as reference


class ReferencePhotoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def save(self, image, name="reference.png", **kwargs):
        path = self.root / name
        image.save(path, **kwargs)
        return path

    def assert_inside_zone(self, info):
        zone = info["focus_zone"]
        for panel in info["panels"]:
            left, top, right, bottom = panel["destination_box"]
            self.assertGreaterEqual(left, zone["x"])
            self.assertGreaterEqual(top, zone["y"])
            self.assertLessEqual(right, zone["x"] + zone["w"])
            self.assertLessEqual(bottom, zone["y"] + zone["h"])
            self.assertEqual(bottom, zone["y"] + zone["h"])

    def assert_matches_complete_source(self, source, layer, info):
        self.assertEqual(len(info["panels"]), 1)
        panel = info["panels"][0]
        self.assertEqual(panel["source_box"], [0, 0, *source.size])
        self.assertEqual(panel["alpha_crop_box"], [0, 0, *source.size])
        expected_photo = source.convert("RGBA").resize(panel["rendered_size"], Image.Resampling.LANCZOS)
        expected = Image.new("RGBA", layer.size)
        expected.alpha_composite(expected_photo, tuple(panel["destination_box"][:2]))
        self.assertEqual(layer.tobytes(), expected.tobytes())
        self.assertFalse(info["source_crop_applied"])
        self.assertFalse(info["people_regenerated"])
        self.assertIsNone(info["segmentation_model"])
        self.assertFalse(info["fallback_used"])
        self.assertEqual(info["source_retention"], "entire_original_photo")
        self.assert_inside_zone(info)
        json.dumps(info)

    def test_ordinary_photo_is_complete_and_only_proportionally_resized(self):
        source = Image.new("RGB", (20, 40), (77, 126, 211))
        layer, info = reference.prepare_reference_photo(self.save(source), (200, 200), {"x": 20, "y": 30, "w": 100, "h": 100})
        self.assertEqual(info["panels"][0]["mask_source"], "intact_photo")
        self.assertEqual(info["panels"][0]["rendered_size"], [50, 100])
        self.assert_matches_complete_source(source, layer, info)

    def test_existing_alpha_preserves_full_file_and_all_semitransparent_pixels(self):
        source = Image.new("RGBA", (20, 30), (32, 118, 192, 0))
        draw = ImageDraw.Draw(source)
        draw.rectangle((2, 4, 15, 25), fill=(23, 157, 219, 255))
        draw.rectangle((5, 6, 12, 12), fill=(212, 101, 48, 128))
        layer, info = reference.prepare_reference_photo(self.save(source), (100, 120), {"x": 10, "y": 15, "w": 60, "h": 80})
        self.assertTrue(info["existing_alpha"])
        self.assertEqual(info["panels"][0]["mask_source"], "existing_alpha")
        self.assert_matches_complete_source(source, layer, info)

    def test_two_people_and_their_hair_hands_and_divider_are_not_inferred_or_removed(self):
        source = Image.new("RGB", (100, 160), "#556677")
        draw = ImageDraw.Draw(source)
        draw.rectangle((10, 0, 40, 70), fill="#b55f44")
        draw.rectangle((55, 90, 90, 159), fill="#3996b7")
        draw.rectangle((0, 75, 99, 84), fill="#dddddd")
        # Fingers overlap the divider; a saliency mask or gutter crop could omit them.
        draw.rectangle((30, 72, 35, 86), fill="#b55f44")
        layer, info = reference.prepare_reference_photo(self.save(source), (140, 200), {"x": 20, "y": 20, "w": 100, "h": 160})
        retained = layer.crop((20, 20, 120, 180)).convert("RGB")
        self.assertEqual(retained.tobytes(), source.tobytes())
        self.assertEqual(retained.getpixel((31, 80)), (181, 95, 68))
        self.assertFalse(info["separator_rows"] or info["separator_columns"])
        self.assert_matches_complete_source(source, layer, info)

    def test_disconnected_existing_alpha_keeps_every_component_and_transparent_margin(self):
        source = Image.new("RGBA", (30, 30))
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 0, 8, 10), fill=(11, 121, 181, 255))
        draw.rectangle((26, 26, 29, 29), fill=(201, 77, 33, 255))
        layer, info = reference.prepare_reference_photo(self.save(source), source.size, None)
        self.assertEqual(layer.getpixel((0, 0)), (11, 121, 181, 255))
        self.assertEqual(layer.getpixel((29, 29)), (201, 77, 33, 255))
        self.assert_matches_complete_source(source, layer, info)

    def test_landscape_photo_is_contained_without_cover_crop_or_axis_distortion(self):
        source = Image.new("RGB", (120, 30), "#336699")
        layer, info = reference.prepare_reference_photo(self.save(source), (100, 100), {"x": 10, "y": 10, "w": 80, "h": 80})
        self.assertEqual(info["panels"][0]["rendered_size"], [80, 20])
        self.assertEqual(info["panels"][0]["destination_box"], [10, 70, 90, 90])
        self.assert_matches_complete_source(source, layer, info)

    def test_focus_and_canvas_bounds_are_clamped(self):
        source = Image.new("RGB", (10, 20), "#285078")
        layer, info = reference.prepare_reference_photo(self.save(source), (80, 100), {"x": -20, "y": -10, "w": 100, "h": 110})
        self.assertEqual(info["focus_zone"], {"x": 0, "y": 0, "w": 80, "h": 100})
        self.assert_matches_complete_source(source, layer, info)
        tiny_layer, tiny_info = reference.prepare_reference_photo(self.save(source), (0, -5), {"x": 50, "y": 100, "w": 0, "h": -5})
        self.assertEqual(tiny_layer.size, (1, 1))
        self.assert_inside_zone(tiny_info)

    def test_exif_orientation_is_applied_before_preserving_the_complete_photo(self):
        source = Image.new("RGB", (20, 40), "#285078")
        ImageDraw.Draw(source).rectangle((0, 0, 8, 8), fill="white")
        exif = source.getexif()
        exif[274] = 6
        path = self.save(source, "rotated.jpg", exif=exif)
        with Image.open(path) as loaded:
            corrected = ImageOps.exif_transpose(loaded).convert("RGBA")
        layer, info = reference.prepare_reference_photo(path, (100, 100), None)
        self.assertEqual(info["source_size"], [40, 20])
        self.assert_matches_complete_source(corrected, layer, info)

    def test_fully_transparent_upload_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "fully transparent"):
            reference.prepare_reference_photo(self.save(Image.new("RGBA", (20, 20))), (100, 100), None)


if __name__ == "__main__":
    unittest.main()
