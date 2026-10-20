import hashlib
import json
from types import SimpleNamespace
from unittest import mock
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

    def prepare_intact(self, *args):
        return reference.prepare_reference_photo(*args, intact=True)

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
        expected_photo = source.convert("RGB").resize(panel["rendered_size"], Image.Resampling.LANCZOS).convert("RGBA")
        expected_photo.putalpha(source.convert("RGBA").getchannel("A").resize(panel["rendered_size"], Image.Resampling.LANCZOS))
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
        layer, info = self.prepare_intact(self.save(source), (200, 200), {"x": 20, "y": 30, "w": 100, "h": 100})
        self.assertEqual(info["panels"][0]["mask_source"], "intact_photo")
        self.assertEqual(info["panels"][0]["rendered_size"], [50, 100])
        self.assert_matches_complete_source(source, layer, info)

    def test_existing_alpha_preserves_full_file_and_all_semitransparent_pixels(self):
        source = Image.new("RGBA", (20, 30), (32, 118, 192, 0))
        draw = ImageDraw.Draw(source)
        draw.rectangle((2, 4, 15, 25), fill=(23, 157, 219, 255))
        draw.rectangle((5, 6, 12, 12), fill=(212, 101, 48, 128))
        layer, info = self.prepare_intact(self.save(source), (100, 120), {"x": 10, "y": 15, "w": 60, "h": 80})
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
        layer, info = self.prepare_intact(self.save(source), (140, 200), {"x": 20, "y": 20, "w": 100, "h": 160})
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
        layer, info = self.prepare_intact(self.save(source), source.size, None)
        self.assertEqual(layer.getpixel((0, 0)), (11, 121, 181, 255))
        self.assertEqual(layer.getpixel((29, 29)), (201, 77, 33, 255))
        self.assert_matches_complete_source(source, layer, info)

    def test_landscape_photo_is_contained_without_cover_crop_or_axis_distortion(self):
        source = Image.new("RGB", (120, 30), "#336699")
        layer, info = self.prepare_intact(self.save(source), (100, 100), {"x": 10, "y": 10, "w": 80, "h": 80})
        self.assertEqual(info["panels"][0]["rendered_size"], [80, 20])
        self.assertEqual(info["panels"][0]["destination_box"], [10, 70, 90, 90])
        self.assert_matches_complete_source(source, layer, info)

    def test_focus_and_canvas_bounds_are_clamped(self):
        source = Image.new("RGB", (10, 20), "#285078")
        layer, info = self.prepare_intact(self.save(source), (80, 100), {"x": -20, "y": -10, "w": 100, "h": 110})
        self.assertEqual(info["focus_zone"], {"x": 0, "y": 0, "w": 80, "h": 100})
        self.assert_matches_complete_source(source, layer, info)
        tiny_layer, tiny_info = self.prepare_intact(self.save(source), (0, -5), {"x": 50, "y": 100, "w": 0, "h": -5})
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
        layer, info = self.prepare_intact(path, (100, 100), None)
        self.assertEqual(info["source_size"], [40, 20])
        self.assert_matches_complete_source(corrected, layer, info)

    def test_fully_transparent_upload_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "fully transparent"):
            self.prepare_intact(self.save(Image.new("RGBA", (20, 20))), (100, 100), None)


    def test_explicit_intact_mode_never_predicts_or_partitions(self):
        source = Image.new("RGB", (20, 40), "#5c7842")
        with mock.patch.object(reference, "_predict_mask", side_effect=AssertionError("prediction forbidden")), mock.patch.object(reference, "_partition_source", side_effect=AssertionError("partition forbidden")):
            layer, info = reference.prepare_reference_photo(self.save(source), source.size, None, intact=True)
        self.assertTrue(info["intact_requested"])
        self.assertFalse(info["requires_mask_validation"])
        self.assertIsNone(info["source_mask_path"])
        self.assert_matches_complete_source(source, layer, info)

    def test_trusted_upload_alpha_bypasses_candidate_prediction(self):
        source = Image.new("RGBA", (30, 40), (71, 99, 201, 0))
        ImageDraw.Draw(source).rectangle((2, 1, 24, 39), fill=(200, 122, 51, 255))
        with mock.patch.object(reference, "_predict_mask", side_effect=AssertionError("prediction forbidden")):
            layer, info = reference.prepare_reference_photo(self.save(source), source.size, None)
        self.assertFalse(info["requires_mask_validation"])
        self.assertEqual(info["panels"][0]["mask_source"], "existing_alpha")
        self.assert_matches_complete_source(source, layer, info)

    def test_missing_runtime_or_model_and_bad_masks_retain_whole_photo(self):
        source = Image.new("RGB", (40, 40), "#5c7842")
        path = self.save(source)
        for error in (ModuleNotFoundError("No onnxruntime"), RuntimeError("Missing model")):
            with self.subTest(error=type(error).__name__), mock.patch.object(reference, "_predict_mask", side_effect=error):
                layer, info = reference.prepare_reference_photo(path, source.size, None)
                self.assertTrue(info["fallback_used"])
                self.assertFalse(info["requires_mask_validation"])
                self.assertIsNone(info["source_mask_path"])
                self.assertEqual(layer.convert("RGB").tobytes(), source.tobytes())
                self.assertEqual(info["panels"][0]["source_box"], [0, 0, 40, 40])
        for mask in (Image.new("L", source.size), Image.new("L", source.size, 255), Image.new("L", (2, 2))):
            with self.subTest(mask_size=mask.size), mock.patch.object(reference, "_predict_mask", return_value=mask):
                layer, info = reference.prepare_reference_photo(path, source.size, None)
                self.assertTrue(info["fallback_used"])
                self.assertFalse(info["requires_mask_validation"])
                self.assertEqual(layer.convert("RGB").tobytes(), source.tobytes())

    def test_candidate_retains_source_rgb_and_requires_independent_coverage_audit(self):
        source = Image.new("RGB", (100, 100), "#123456")
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 0, 49, 99), fill="#bc7962")
        draw.rectangle((50, 0, 99, 99), fill="#9871b8")
        # This plausible mask deliberately omits an entire second person. It
        # MUST be marked unvalidated; area checks cannot authorize its use.
        mask = Image.new("L", source.size)
        ImageDraw.Draw(mask).rectangle((0, 0, 49, 99), fill=255)
        with mock.patch.object(reference, "_predict_mask", return_value=mask):
            layer, info = reference.prepare_reference_photo(self.save(source), (50, 100), None)
        self.assertTrue(info["requires_mask_validation"])
        self.assertFalse(info["fallback_used"])
        self.assertEqual(info["panels"][0]["mask_source"], "u2netp_candidate")
        with Image.open(info["source_mask_path"]) as audit_mask:
            self.assertEqual(audit_mask.mode, "L")
            self.assertEqual(audit_mask.size, source.size)
            self.assertEqual(audit_mask.tobytes(), mask.tobytes())
        self.assertEqual(layer.convert("RGB").tobytes(), source.crop((0, 0, 50, 100)).tobytes())
        self.assertFalse(info["people_regenerated"])
        json.dumps(info)

    def test_collage_partitions_keep_every_row_and_finger_on_gutter(self):
        source = Image.new("RGB", (100, 200), "#526798")
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 95, 99, 104), fill="#eeeeee")
        draw.rectangle((48, 91, 51, 103), fill="#bb7055")
        boxes, rows, columns = reference._partition_source(source)
        self.assertEqual(boxes, [(0, 0, 100, 105), (0, 105, 100, 200)])
        self.assertEqual(rows, [[95, 105]])
        self.assertEqual(columns, [])
        reconstructed = Image.new("RGB", source.size)
        for box in boxes:
            reconstructed.paste(source.crop(box), box[:2])
        self.assertEqual(reconstructed.tobytes(), source.tobytes())
        self.assertEqual(source.crop(boxes[0]).getpixel((49, 102)), (187, 112, 85))

    def test_two_panel_audit_mask_maps_back_to_original_coordinates(self):
        source = Image.new("RGB", (60, 160), "#526798")
        boxes = [(0, 0, 60, 80), (0, 80, 60, 160)]
        masks = []
        for bounds in ((5, 0, 40, 79), (20, 2, 59, 79)):
            mask = Image.new("L", (60, 80))
            ImageDraw.Draw(mask).rectangle(bounds, fill=255)
            masks.append(mask)
        with mock.patch.object(reference, "_partition_source", return_value=(boxes, [[75, 80]], [])), mock.patch.object(reference, "_predict_mask", side_effect=masks):
            layer, info = reference.prepare_reference_photo(self.save(source), (160, 100), None)
        expected_mask = Image.new("L", source.size)
        for box, mask in zip(boxes, masks):
            expected_mask.paste(mask, box[:2])
        with Image.open(info["source_mask_path"]) as audit_mask:
            self.assertEqual(audit_mask.tobytes(), expected_mask.tobytes())
        self.assertEqual(len(info["panels"]), 2)
        self.assert_inside_zone(info)
        self.assertEqual(info["source_size"], [60, 160])

    def test_session_is_cached_with_low_memory_single_thread_cpu_options(self):
        fake_model = self.root / "model.onnx"
        fake_model.write_bytes(b"test model")
        options = SimpleNamespace()
        session = object()
        ort = SimpleNamespace(SessionOptions=lambda: options, ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"), InferenceSession=mock.Mock(return_value=session))
        with mock.patch.dict("sys.modules", {"onnxruntime": ort}), mock.patch.object(reference, "MODEL_PATH", fake_model), mock.patch.object(reference, "MODEL_SHA256", hashlib.sha256(b"test model").hexdigest()), mock.patch.object(reference, "_SESSION", None):
            self.assertIs(reference._get_session_locked(), session)
            self.assertIs(reference._get_session_locked(), session)
        self.assertEqual(options.intra_op_num_threads, 1)
        self.assertEqual(options.inter_op_num_threads, 1)
        self.assertFalse(options.enable_cpu_mem_arena)
        self.assertFalse(options.enable_mem_pattern)
        self.assertEqual(options.execution_mode, "sequential")
        self.assertEqual(ort.InferenceSession.call_count, 1)
        self.assertEqual(ort.InferenceSession.call_args.kwargs["providers"], ["CPUExecutionProvider"])

    def test_multiple_people_share_height_and_keep_proportional_widths(self):
        source = Image.new("RGB", (100, 200), "#577793")
        records = [
            {"source_box": [0, 0, 100, 100], "alpha_crop_box": [10, 0, 90, 100], "_alpha": Image.new("L", (100, 100), 255)},
            {"source_box": [0, 100, 100, 200], "alpha_crop_box": [25, 0, 75, 100], "_alpha": Image.new("L", (100, 100), 255)},
        ]
        zone = {"x": 20, "y": 30, "w": 200, "h": 180}
        layer = reference._compose_panels(source, (240, 240), zone, records)
        self.assertEqual(records[0]["rendered_size"][1], records[1]["rendered_size"][1])
        self.assertEqual(records[0]["destination_box"][1], records[1]["destination_box"][1])
        for record in records:
            width, height = record["rendered_size"]
            crop_width, crop_height = record["source_crop_size"]
            self.assertLessEqual(abs(width - height * crop_width / crop_height), 1)
            x, y, right, bottom = record["destination_box"]
            self.assertEqual(bottom, zone["y"] + zone["h"])
            expected = source.crop(record["source_box"]).crop(record["alpha_crop_box"]).resize((width, height), Image.Resampling.LANCZOS)
            self.assertEqual(layer.crop((x, y, right, bottom)).convert("RGB").tobytes(), expected.tobytes())


if __name__ == "__main__":
    unittest.main()
