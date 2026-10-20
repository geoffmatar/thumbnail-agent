import base64
import concurrent.futures
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw, ImageOps

with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True), \
     mock.patch.object(Path, "read_text", return_value=""):
    import thumbnail_agent as agent


def image_bytes(size=(512, 768)):
    output = io.BytesIO()
    Image.new("RGB", size, "#355d83").save(output, "PNG")
    return output.getvalue()


def patterned_photo(alpha=False, alternate=False):
    """Distinct synthetic source details; no private photographs are fixtures."""
    image = Image.new("RGBA", (192, 256), (28, 117, 203, 0 if alpha else 255))
    draw = ImageDraw.Draw(image)
    for box, color in (
        ((12, 8, 91, 121), (221, 49, 83, 255)),
        ((99, 8, 180, 121), (34, 184, 103, 255)),
        ((12, 137, 91, 247), (153, 63, 225, 255)),
        ((99, 137, 180, 247), (244, 183, 34, 255)),
    ):
        draw.rectangle(box, fill=color)
    draw.line((20, 30, 75, 85), fill=(11, 19, 27, 255), width=7)
    draw.line((115, 150, 165, 205), fill=(238, 241, 245, 255), width=5)
    if alpha:
        draw.rectangle((20, 249, 170, 254), fill=(53, 102, 176, 128))
    return ImageOps.mirror(image) if alternate else image


def json_response(payload):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def prepared_photo(path, canvas_size, focus_zone, intact=False):
    """Predictable source-photo placement; the real final renderer is tested."""
    with Image.open(path) as original:
        source = original.convert("RGBA")
    resized = ImageOps.contain(source, (180, 220), method=Image.Resampling.LANCZOS)
    x = focus_zone["x"] + (focus_zone["w"] - resized.width) // 2
    y = focus_zone["y"] + (focus_zone["h"] - resized.height) // 2
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    layer.paste(resized, (x, y))
    return layer, {
        "strategy": "local_photo_composite", "canvas_size": list(canvas_size),
        "source_size": list(source.size), "focus_zone": dict(focus_zone),
        "panels": [{
            "source_box": [0, 0, source.width, source.height],
            "alpha_crop_box": [0, 0, source.width, source.height],
            "source_crop_size": list(source.size),
            "destination_box": [x, y, x + resized.width, y + resized.height],
            "rendered_size": list(resized.size),
            "mask_source": "existing_alpha" if source.getchannel("A").getextrema()[0] < 255 else "intact_photo",
            "fallback_reason": None,
        }],
        "existing_alpha": source.getchannel("A").getextrema()[0] < 255,
        "fallback_used": False, "fallback_reasons": [], "segmentation_model": None,
        "separator_rows": [], "separator_columns": [],
        "requires_mask_validation": False,
        "source_retention": "entire_original_photo", "source_crop_applied": False,
    }


class PersonReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reference = self.root / "reference.png"
        patterned_photo().save(self.reference)
        self.reference_bytes = self.reference.read_bytes()
        self.generated_bytes = image_bytes()
        self.brief = {"visual_prompt": "An empty university entrance at sunrise.", "negative_prompt": "text, watermarks", "rationale": "A background for the photographed students."}
        environment = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        paths = mock.patch.multiple(agent, ASSETS_DIR=self.root / "assets", STATE_DIR=self.root / "state", GENERATED_DIR=self.root / "generated", WORK_DIR=self.root / "work")
        paths.start()
        self.addCleanup(paths.stop)
        agent.ensure_dirs()
        network = mock.patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request in offline tests"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def assert_original_photo_metadata(self, result):
        self.assertTrue(result["person_reference_used"])
        self.assertEqual(result["reference_strategy"], "source_photo_composite")
        self.assertEqual(result["reference_preservation"], "original_pixels")
        self.assertIsNone(result["reference_image_model"])
        self.assertIsNone(result["reference_input_fidelity"])
        self.assertTrue(result["reference_composition"]["panels"])

    def assert_background_only(self, prompt):
        text = prompt.lower()
        self.assertIn("background", text)
        self.assertRegex(text, r"(?:no|without|do not (?:include|generate|draw|create|render|depict))[^.]{0,160}(?:people|humans|persons|faces)")

    def api_response(self, request, timeout=None):
        payload = json.loads(request.data)
        if request.full_url == agent.OPENAI_ENDPOINT:
            self.assertNotIn("tools", payload, "Only background planning may inspect the source photo")
            return json_response({"output_text": json.dumps(self.brief)})
        if request.full_url == agent.OPENAI_IMAGES_GENERATE_ENDPOINT:
            self.assert_background_only(payload["prompt"])
            self.assertNotIn("data:image", str(payload))
            self.assertNotIn("input_image", str(payload))
            self.assertNotIn(base64.b64encode(self.reference_bytes).decode("ascii"), str(payload))
            return json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
        self.fail(f"Unexpected API endpoint: {request.full_url}")

    def test_reference_preflight_rejects_missing_empty_and_invalid_files(self):
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        invalid = self.root / "invalid.png"
        invalid.write_bytes(b"this is not an image")
        for reference in (self.root / "missing.png", empty, invalid):
            with self.subTest(reference=reference.name):
                with self.assertRaises(RuntimeError):
                    agent.handle_create("Rescue scene.", "RESCUE | TEAM", client_slug="alliance-latin", person_reference_path=reference)
        self.network.assert_not_called()

    def test_planning_can_see_the_photo_but_only_plans_its_background(self):
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(self.brief)}) as create:
            agent.create_visual_brief("The students arrive at university.", "EVERY SECOND | COUNTS", agent.CLIENTS["zoomex"], extra_prompt="Change their clothes and hairstyle.", person_reference_path=self.reference)
        payload = create.call_args.args[0]
        content = payload["input"][1]["content"]
        images = [part for part in content if part["type"] == "input_image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(base64.b64decode(images[0]["image_url"].split(",", 1)[1]), self.reference_bytes)
        instructions = payload["input"][0]["content"] + " " + content[0]["text"]
        self.assert_background_only(instructions)
        self.assertIn("original", instructions.lower())

    def test_reference_generation_sends_only_a_background_prompt_never_the_photo(self):
        self.network.side_effect = self.api_response
        output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"], self.reference)
        self.assertEqual(output.read_bytes(), self.generated_bytes)
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(self.network.call_args.args[0].full_url, agent.OPENAI_IMAGES_GENERATE_ENDPOINT)

    def test_reference_background_fallback_does_not_send_photo_to_the_image_tool(self):
        response = {"output": [{"type": "image_generation_call", "result": base64.b64encode(self.generated_bytes).decode()}]}
        with mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("temporary background failure")), mock.patch.object(agent, "openai_responses_create", return_value=response) as responses:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"], self.reference)
        self.assertEqual(output.read_bytes(), self.generated_bytes)
        payload = responses.call_args.args[0]
        self.assertEqual([part["type"] for part in payload["input"][0]["content"]], ["input_text"])
        self.assertNotIn("data:image", str(payload))
        self.assert_background_only(payload["input"][0]["content"][0]["text"])

    def test_background_failure_never_returns_a_result_without_the_original_photo(self):
        with mock.patch.object(agent, "prepare_reference_photo", side_effect=prepared_photo), mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("background unavailable")), mock.patch.object(agent, "openai_responses_create", side_effect=RuntimeError("background unavailable")), mock.patch.object(agent, "render_thumbnail") as render:
            with self.assertRaises(RuntimeError):
                agent.handle_create("University journey.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_path=self.reference)
        render.assert_not_called()

    def mask_candidate(self, path, canvas_size, focus_zone, intact=False):
        """A visibly incomplete candidate makes any unsafe acceptance observable."""
        layer, info = prepared_photo(path, canvas_size, focus_zone, intact=intact)
        if intact:
            return layer, info
        box = info["panels"][0]["destination_box"]
        layer.paste((0, 0, 0, 0), (box[0], box[1], box[2], (box[1] + box[3]) // 2))
        mask = Image.new("L", tuple(info["source_size"]), 255)
        mask.paste(0, (0, 0, mask.width, mask.height // 2))
        mask_path = self.root / "private-source-mask.png"
        mask.save(mask_path)
        info.update(requires_mask_validation=True, source_mask_path=str(mask_path))
        info["panels"][0]["mask_source"] = "u2netp"
        return layer, info

    def test_mask_audit_accepts_only_explicit_complete_coverage(self):
        audit = {"all_people_covered": True, "all_heads_and_hair_covered": True,
                 "all_visible_person_parts_covered": True, "reason": "Every visible part is retained."}
        config = agent.CLIENTS["alliance-latin"]
        candidate, info = self.mask_candidate(self.reference, agent.CANVAS_SIZE, agent.client_focus_zone(config))
        with mock.patch.object(agent, "prepare_reference_photo", side_effect=self.mask_candidate) as prepare, mock.patch.object(agent, "validate_reference_mask", return_value=audit) as validate, mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes):
            result = agent.handle_create("University journey.", "SOURCE | PEOPLE", client_slug="alliance-latin", person_reference_path=self.reference)
        prepare.assert_called_once()
        validate.assert_called_once_with(self.reference, info["source_mask_path"])
        composition = result["reference_composition"]
        self.assertIs(composition["mask_validated"], True)
        self.assertFalse(composition["fallback_used"])
        self.assertNotIn("source_mask_path", composition, "The private mask is not public result metadata")
        background = self.root / "audit-background.png"
        background.write_bytes(self.generated_bytes)
        baseline = self.root / "audit-baseline.png"
        agent.render_thumbnail("SOURCE | PEOPLE", background, baseline, config)
        box = info["panels"][0]["destination_box"]
        with Image.open(baseline) as base, Image.open(result["thumbnail"]) as rendered:
            expected = Image.alpha_composite(base.convert("RGBA"), candidate).convert("RGB")
            self.assertEqual(rendered.crop(box).tobytes(), expected.crop(box).tobytes())

    def test_uncertain_or_malformed_mask_audits_retain_the_complete_source_photo(self):
        fields = ("all_people_covered", "all_heads_and_hair_covered", "all_visible_person_parts_covered")
        complete = dict.fromkeys(fields, True)
        failures = [("missing-result", None), ("wrong-result-type", [True, True, True]),
                    ("audit-exception", RuntimeError("Vision service unavailable"))]
        for field in fields:
            for value in (False, "true", 1):
                failures.append((f"{field}-{value!r}", {**complete, field: value}))
            failures.append((f"{field}-missing", {key: value for key, value in complete.items() if key != field}))
        config = agent.CLIENTS["alliance-latin"]
        intact, info = prepared_photo(self.reference, agent.CANVAS_SIZE, agent.client_focus_zone(config), intact=True)
        box = info["panels"][0]["destination_box"]
        with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes):
            for label, audit in failures:
                with self.subTest(audit=label):
                    validation = {"side_effect": audit} if isinstance(audit, Exception) else {"return_value": audit}
                    with mock.patch.object(agent, "prepare_reference_photo", side_effect=self.mask_candidate) as prepare, mock.patch.object(agent, "validate_reference_mask", **validation) as validate:
                        result = agent.handle_create("University journey.", "SOURCE | PEOPLE", client_slug="alliance-latin", person_reference_path=self.reference)
                    validate.assert_called_once()
                    self.assertEqual(prepare.call_count, 2)
                    self.assertIs(prepare.call_args.kwargs["intact"], True)
                    composition = result["reference_composition"]
                    self.assertIs(composition["mask_validated"], False)
                    self.assertIs(composition["fallback_used"], True)
                    self.assertTrue(composition["fallback_reasons"])
                    with Image.open(result["thumbnail"]) as rendered:
                        self.assertEqual(rendered.crop(box).tobytes(), intact.crop(box).convert("RGB").tobytes())

    def test_a_candidate_without_its_mask_fails_closed_before_the_audit(self):
        def missing_mask(*args, **kwargs):
            layer, info = self.mask_candidate(*args, **kwargs)
            info.pop("source_mask_path", None)
            return layer, info

        with mock.patch.object(agent, "prepare_reference_photo", side_effect=missing_mask) as prepare, mock.patch.object(agent, "validate_reference_mask") as validate, mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes):
            result = agent.handle_create("University journey.", "SOURCE | PEOPLE", client_slug="alliance-latin", person_reference_path=self.reference)
        validate.assert_not_called()
        self.assertEqual(prepare.call_count, 2)
        self.assertIs(prepare.call_args.kwargs["intact"], True)
        self.assertIs(result["reference_composition"]["fallback_used"], True)

    def test_existing_alpha_does_not_require_an_automated_mask_audit(self):
        cutout = self.root / "existing-cutout.png"
        patterned_photo(alpha=True).save(cutout)
        with mock.patch.object(agent, "prepare_reference_photo", side_effect=prepared_photo) as prepare, mock.patch.object(agent, "validate_reference_mask") as validate, mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes):
            result = agent.handle_create("University journey.", "SOURCE | PEOPLE", client_slug="alliance-latin", person_reference_path=cutout)
        prepare.assert_called_once()
        validate.assert_not_called()
        self.assertTrue(result["reference_composition"]["existing_alpha"])

    def test_mask_audit_is_a_vision_only_comparison_of_the_source_and_mask(self):
        mask = self.root / "mask.png"
        alpha = Image.new("L", (192, 256), 255)
        alpha.paste(0, (0, 128, 96, 256))
        alpha.paste(128, (96, 0, 192, 128))
        alpha.save(mask)
        audit = {"all_people_covered": True, "all_heads_and_hair_covered": True,
                 "all_visible_person_parts_covered": True, "reason": "All source regions are retained."}
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(audit)}) as create:
            self.assertEqual(agent.validate_reference_mask(self.reference, mask), audit)
        payload = create.call_args.args[0]
        self.assertNotIn("tools", payload)
        images = [part for part in payload["input"][1]["content"] if part["type"] == "input_image"]
        self.assertEqual(len(images), 3)
        for image, path in zip(images, (self.reference, mask)):
            self.assertEqual(image["detail"], "high")
            self.assertEqual(base64.b64decode(image["image_url"].split(",", 1)[1]), path.read_bytes())
        self.assertEqual(images[2]["detail"], "high")
        preview_bytes = base64.b64decode(images[2]["image_url"].split(",", 1)[1])
        with Image.open(self.reference) as source, Image.open(io.BytesIO(preview_bytes)) as preview:
            expected = Image.new("RGB", source.size, (0, 220, 220))
            expected.paste(source.convert("RGB"), mask=alpha)
            self.assertEqual(preview.format, "PNG")
            self.assertEqual(preview.mode, "RGB")
            self.assertEqual(preview.size, source.size)
            self.assertEqual(preview.tobytes(), expected.tobytes())
        instructions = payload["input"][0]["content"].lower()
        self.assertIn("every visible person", instructions)
        self.assertIn("hair", instructions)
        self.assertIn("if uncertain", instructions)

    def test_mask_audit_rejects_mismatched_source_coordinates_before_calling_the_api(self):
        for size in ((193, 256), (192, 255)):
            with self.subTest(mask_size=size):
                mask = self.root / "mismatched-mask.png"
                Image.new("L", size, 255).save(mask)
                with mock.patch.object(agent, "openai_responses_create") as create:
                    result = agent.validate_reference_mask(self.reference, mask)
                create.assert_not_called()
                for field in ("all_people_covered", "all_heads_and_hair_covered", "all_visible_person_parts_covered"):
                    self.assertIs(result[field], False)

    def test_mask_audit_response_validation_rejects_nonboolean_and_unreadable_answers(self):
        mask = self.root / "mask.png"
        Image.new("L", (192, 256), 255).save(mask)
        fields = ("all_people_covered", "all_heads_and_hair_covered", "all_visible_person_parts_covered")
        complete = dict.fromkeys(fields, True)
        malformed = [None, [], {**complete, fields[0]: "true"}, {**complete, fields[1]: 1},
                     {fields[0]: True, fields[1]: True}, "not JSON"]
        for answer in malformed:
            with self.subTest(answer=answer):
                raw = answer if isinstance(answer, str) else json.dumps(answer)
                with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": raw}):
                    result = agent.validate_reference_mask(self.reference, mask)
                self.assertTrue(all(result[field] is False for field in fields))
        with mock.patch.object(agent, "openai_responses_create", side_effect=RuntimeError("Vision service unavailable")):
            result = agent.validate_reference_mask(self.reference, mask)
        self.assertTrue(all(result[field] is False for field in fields))

    def test_non_reference_generation_and_existing_fallback_still_work(self):
        with mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"])
            self.assertEqual(output.read_bytes(), self.generated_bytes)
            generate.assert_called_once()
        response = {"output": [{"type": "image_generation_call", "result": base64.b64encode(self.generated_bytes).decode()}]}
        with mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("temporary failure")), mock.patch.object(agent, "openai_responses_create", return_value=response) as responses:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"])
        self.assertEqual(output.read_bytes(), self.generated_bytes)
        self.assertFalse(any(part["type"] == "input_image" for part in responses.call_args.args[0]["input"][0]["content"]))

    def test_normalization_preserves_pixels_without_jpeg_recompression(self):
        normalized = agent.normalize_reference_image(self.reference)
        with Image.open(self.reference) as original, Image.open(normalized) as result:
            self.assertEqual(result.format, "PNG")
            self.assertEqual(result.size, original.size)
            self.assertEqual(result.convert("RGB").tobytes(), original.convert("RGB").tobytes())

    def test_normalization_keeps_existing_cutout_alpha_including_palette_pngs(self):
        cutout = self.root / "cutout.png"
        patterned_photo(alpha=True).save(cutout)
        palette = self.root / "palette.png"
        indexed = Image.new("P", (8, 8), 0)
        indexed.putpalette([0, 0, 0, 221, 49, 83] + [0] * 762)
        indexed.paste(1, (2, 2, 6, 6))
        indexed.save(palette, transparency=0)
        for source in (cutout, palette):
            with self.subTest(source=source.name):
                normalized = agent.normalize_reference_image(source)
                with Image.open(source) as original, Image.open(normalized) as result:
                    self.assertEqual(result.mode, "RGBA")
                    self.assertEqual(result.tobytes(), original.convert("RGBA").tobytes())

    def test_an_empty_selected_reference_is_not_silently_ignored(self):
        field = SimpleNamespace(filename="portrait.png", file=io.BytesIO(b""))
        with self.assertRaises(RuntimeError):
            agent.save_upload({"person_reference": field}, "person_reference")
        self.assertIsNone(agent.save_upload({}, "person_reference"))

    def test_final_png_preserves_source_pixels_after_every_brands_tint_and_tone_adjustments(self):
        background = self.root / "background.png"
        background.write_bytes(self.generated_bytes)
        for alpha in (False, True):
            photo = self.root / f"source-{alpha}.png"
            patterned_photo(alpha=alpha).save(photo)
            for slug, config in agent.CLIENTS.items():
                with self.subTest(client=slug, alpha=alpha):
                    layer, info = prepared_photo(photo, agent.CANVAS_SIZE, agent.client_focus_zone(config))
                    box = info["panels"][0]["destination_box"]
                    baseline = self.root / f"baseline-{slug}-{alpha}.png"
                    output = self.root / f"overlay-{slug}-{alpha}.png"
                    agent.render_thumbnail("SOURCE | PEOPLE", background, baseline, config)
                    agent.render_thumbnail("SOURCE | PEOPLE", background, output, config, reference_layer=layer)
                    with Image.open(baseline) as base, Image.open(output) as rendered:
                        expected = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
                        self.assertEqual(rendered.crop(box).tobytes(), expected.crop(box).tobytes())
                        if not alpha:
                            self.assertEqual(rendered.crop(box).tobytes(), layer.crop(box).convert("RGB").tobytes())

    def test_reference_pipeline_keeps_original_pixels_and_metadata_for_every_brand(self):
        self.network.side_effect = self.api_response
        os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = "gpt-image-1-mini"
        for slug, config in agent.CLIENTS.items():
            with self.subTest(client=slug):
                with mock.patch.object(agent, "prepare_reference_photo", side_effect=prepared_photo) as prepare:
                    result = agent.handle_create("The pictured students arrive at university.", "MY JOURNEY | BEGINS", client_slug=slug, person_reference_path=self.reference, extra_prompt="Change their hair and outfits.")
                prepare.assert_called_once()
                self.assert_original_photo_metadata(result)
                variants = result.get("thumbnails") or [result]
                self.assertEqual(len(variants), 2 if slug in agent.TWO_THUMBNAIL_CLIENTS else 1)
                layer, info = prepared_photo(self.reference, agent.CANVAS_SIZE, agent.client_focus_zone(config))
                box = info["panels"][0]["destination_box"]
                for variant in variants:
                    self.assert_original_photo_metadata(variant)
                    output = Path(variant["thumbnail"])
                    self.assert_original_photo_metadata(json.loads(output.with_suffix(".json").read_text()))
                    with Image.open(output) as rendered:
                        self.assertEqual(rendered.size, (1080, 1920))
                        self.assertEqual(rendered.crop(box).tobytes(), layer.crop(box).convert("RGB").tobytes())

    def test_simultaneous_matching_titles_cannot_overwrite_another_persons_photo(self):
        alternate = self.root / "other-reference.png"
        patterned_photo(alternate=True).save(alternate)
        with mock.patch.object(agent, "prepare_reference_photo", side_effect=prepared_photo), mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes), mock.patch.object(agent.time, "strftime", return_value="20261020-190000"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda reference: agent.handle_create("University journey.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_path=reference), (self.reference, alternate)))
        self.assertNotEqual(results[0]["thumbnail"], results[1]["thumbnail"])
        for result, photo in zip(results, (self.reference, alternate)):
            config = agent.CLIENTS["alliance-latin"]
            layer, info = prepared_photo(photo, agent.CANVAS_SIZE, agent.client_focus_zone(config))
            box = info["panels"][0]["destination_box"]
            with Image.open(result["thumbnail"]) as rendered:
                self.assertEqual(rendered.crop(box).tobytes(), layer.crop(box).convert("RGB").tobytes())

    def test_http_upload_job_and_download_preserve_original_pixels(self):
        self.network.side_effect = self.api_response
        with mock.patch.object(agent, "prepare_reference_photo", side_effect=prepared_photo), mock.patch.object(agent, "JOBS", {}), mock.patch.object(agent.ThumbnailHandler, "log_message"):
            server = agent.ThreadingHTTPServer(("127.0.0.1", 0), agent.ThumbnailHandler)
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
            thread.start()
            try:
                boundary = "test-person-reference-boundary"
                body = bytearray()
                for name, value in {"client": "alliance-latin", "title": "SOURCE | PEOPLE", "script": "The pictured people lead a rescue."}.items():
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="person_reference"; filename="people.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
                body.extend(self.reference_bytes)
                body.extend(f"\r\n--{boundary}--\r\n".encode())

                def request(method, path, data=None, headers=None):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                    try:
                        connection.request(method, path, body=data, headers=headers or {})
                        response = connection.getresponse()
                        return response.status, dict(response.getheaders()), response.read()
                    finally:
                        connection.close()

                status, _, payload = request("POST", "/api/create", bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
                self.assertEqual(status, 202)
                status_url = json.loads(payload)["status_url"]
                deadline = time.monotonic() + 10
                while True:
                    status, _, payload = request("GET", status_url)
                    self.assertEqual(status, 200)
                    job = json.loads(payload)
                    if job["status"] in {"done", "error"}:
                        break
                    self.assertLess(time.monotonic(), deadline, "Background job did not finish")
                    time.sleep(0.02)
                self.assertEqual(job["status"], "done", job.get("error"))
                result = job["result"]
                self.assert_original_photo_metadata(result)
                self.assertEqual(len(result["thumbnails"]), 1)
                self.assert_original_photo_metadata(result["thumbnails"][0])
                status, headers, png = request("GET", result["download_url"])
                self.assertEqual(status, 200)
                self.assertIn("attachment", headers["Content-Disposition"])
                layer, info = prepared_photo(self.reference, agent.CANVAS_SIZE, agent.client_focus_zone(agent.CLIENTS["alliance-latin"]))
                box = info["panels"][0]["destination_box"]
                with Image.open(io.BytesIO(png)) as rendered:
                    self.assertEqual(rendered.size, (1080, 1920))
                    self.assertEqual(rendered.crop(box).tobytes(), layer.crop(box).convert("RGB").tobytes())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
