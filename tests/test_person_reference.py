import base64
import concurrent.futures
import email.policy
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

# Never import the developer's real credentials into an offline test process.
with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True), \
     mock.patch.object(Path, "read_text", return_value=""):
    import thumbnail_agent as agent

SUNBURST_MODEL = "gpt-image-2.5-sunburst-2026-09-08"


def image_bytes(size=(512, 768), color="#355d83"):
    output = io.BytesIO()
    image = Image.new("RGB", size, color)
    ImageDraw.Draw(image).rectangle((3, 5, size[0] // 2, size[1] // 3), fill="#e87b3f")
    image.save(output, "PNG")
    return output.getvalue()


def json_response(payload):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def multipart_parts(request):
    header = request.get_header("Content-type")
    message = BytesParser(policy=email.policy.default).parsebytes(
        f"Content-Type: {header}\r\nMIME-Version: 1.0\r\n\r\n".encode() + request.data
    )
    if not message.is_multipart():
        raise AssertionError("Images edits must use multipart form-data")
    return list(message.iter_parts())


class PersonReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Same filename, different files: neither person may be overwritten or
        # deduplicated when constructing a multipart edit request.
        self.references = []
        for index, color in enumerate(("#cc3153", "#22b867", "#993fe1", "#f4b722")):
            reference = self.root / f"person-{index + 1}" / "portrait.png"
            reference.parent.mkdir()
            reference.write_bytes(image_bytes((48 + index, 64 + index), color))
            self.references.append(reference)
        self.reference = self.references[0]
        self.generated_bytes = image_bytes()
        self.brief = {
            "visual_prompt": "The referenced students celebrate arriving on a university campus at sunrise.",
            "negative_prompt": "text, watermarks",
            "rationale": "A natural scene matching the student's university journey.",
        }
        environment = mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": "offline-test-key",
            # Legacy transport cases opt in explicitly; production person edits
            # now default independently to Sunburst and high output quality.
            "OPENAI_PERSON_IMAGE_MODEL": "gpt-image-1",
            "OPENAI_PERSON_IMAGE_QUALITY": "medium",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        paths = mock.patch.multiple(agent, ASSETS_DIR=self.root / "assets", STATE_DIR=self.root / "state", GENERATED_DIR=self.root / "generated", WORK_DIR=self.root / "work")
        paths.start()
        self.addCleanup(paths.stop)
        agent.ensure_dirs()
        network = mock.patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request in offline tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        # Transport/rendering fixtures are synthetic colors, so gaze evaluation
        # is exercised separately in test_camera_facing.py.
        gaze = mock.patch.object(agent, "validate_camera_facing", return_value={
            "people_present": True, "visible_people_count": 2,
            "all_faces_front_facing": True, "all_eyes_on_camera": True,
            "all_faces_unobstructed": True, "reason": "Offline passing gaze fixture.",
        })
        gaze.start()
        self.addCleanup(gaze.stop)
        appearance = mock.patch.object(agent, "validate_reference_appearance", side_effect=lambda image_path, reference_paths: {
            "references": [{"reference_index": index, **{flag: True for flag in agent.REFERENCE_APPEARANCE_FLAGS}, "notes": "Offline feature fixture."} for index in range(1, len(reference_paths) + 1)],
            "notes": "Synthetic images; feature review is tested separately.",
        })
        appearance.start()
        self.addCleanup(appearance.stop)

    def assert_reference_metadata(self, result, count):
        self.assertIs(result["camera_facing_verified"], True)
        self.assertIs(result["camera_facing_check"]["all_eyes_on_camera"], True)
        self.assertIs(result["person_reference_used"], count > 0)
        self.assertEqual(result["person_reference_count"], count)
        self.assertIs(result["reference_appearance_checked"], count > 0)
        self.assertIs(result["reference_appearance_passed"], True if count else None)
        self.assertEqual(result["reference_appearance_review_model"], "gpt-6-astra" if count else None)
        if count:
            self.assertEqual(len(result["reference_appearance_check"]["references"]), count)
        else:
            self.assertIsNone(result["reference_appearance_check"])
        if count:
            self.assertEqual(result["reference_strategy"], "multi_image_edit")
            self.assertEqual(result["reference_preservation"], "identity_guided")
            self.assertEqual(result["reference_image_model"], "gpt-image-1")
            self.assertEqual(result["reference_input_fidelity"], "high")
            self.assertEqual(result["reference_input_fidelity_parameter"], "high")
            self.assertEqual(result["reference_image_quality"], "medium")
            self.assertEqual(result["reference_image_size"], "1024x1536")
        else:
            self.assertIsNone(result["reference_strategy"])
            self.assertIsNone(result["reference_preservation"])

    def edit_parts(self, request, expected_references, model="gpt-image-1", quality="medium", size="1024x1536", input_fidelity="high"):
        self.assertEqual(request.full_url, agent.OPENAI_IMAGES_EDIT_ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer offline-test-key")
        parts = multipart_parts(request)
        files = [part for part in parts if part.get_filename() is not None]
        self.assertEqual([part.get_param("name", header="content-disposition") for part in files], ["image[]"] * len(expected_references))
        self.assertEqual([part.get_payload(decode=True) for part in files], [path.read_bytes() for path in expected_references])
        self.assertTrue(all(part.get_filename() for part in files))
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode() for part in parts if part.get_filename() is None}
        self.assertEqual(fields["model"], model)
        if input_fidelity is None:
            self.assertNotIn("input_fidelity", fields)
        else:
            self.assertEqual(fields["input_fidelity"], input_fidelity)
        self.assertEqual(fields["size"], size)
        self.assertEqual(fields["quality"], quality)
        return fields

    def mocked_openai(self, expected_references, **settings):
        def respond(request, timeout=None):
            if request.full_url == agent.OPENAI_ENDPOINT:
                payload = json.loads(request.data)
                self.assertNotIn("tools", payload, "Reference planning must not invoke another image generator")
                images = [part for part in payload["input"][1]["content"] if part["type"] == "input_image"]
                self.assertEqual([base64.b64decode(image["image_url"].split(",", 1)[1]) for image in images], [path.read_bytes() for path in expected_references])
                return json_response({"output_text": json.dumps(self.brief)})
            self.edit_parts(request, expected_references, **settings)
            return json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
        return respond

    def test_edit_multipart_retains_every_selected_reference_in_order(self):
        # Deliberately nonalphabetic selection order; preserve the user's person
        # mapping rather than sorting paths or collapsing equal basenames.
        selected = [self.references[2], self.references[0], self.references[3], self.references[1]]
        self.network.return_value = json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
        self.network.side_effect = None
        result = agent.openai_images_edit("A natural group scene.", selected)
        self.assertEqual(result, self.generated_bytes)
        self.network.assert_called_once()
        fields = self.edit_parts(self.network.call_args.args[0], selected)
        self.assertEqual(fields["prompt"], "A natural group scene.")

    def test_reference_edit_fails_before_network_without_a_key(self):
        os.environ.pop("OPENAI_API_KEY")
        with self.assertRaises(RuntimeError):
            agent.openai_images_edit("A university scene.", self.references[:2])
        self.network.assert_not_called()

    def test_reference_fidelity_follows_the_dedicated_person_model(self):
        os.environ["OPENAI_IMAGE_MODEL"] = "gpt-image-1-mini"
        os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = "gpt-image-1-mini"
        self.network.side_effect = None
        self.network.return_value = json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
        for model, fidelity in (("gpt-image-1", "high"), ("gpt-image-1.5", "high"), ("gpt-image-2", None), (SUNBURST_MODEL, None)):
            with self.subTest(model=model):
                os.environ["OPENAI_PERSON_IMAGE_MODEL"] = model
                agent.openai_images_edit("A university scene.", self.references[:2])
                fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode() for part in multipart_parts(self.network.call_args.args[0]) if part.get_filename() is None}
                self.assertEqual(fields["model"], model)
                if fidelity:
                    self.assertEqual(fields["input_fidelity"], fidelity)
                else:
                    self.assertNotIn("input_fidelity", fields)
        self.network.reset_mock()
        os.environ["OPENAI_PERSON_IMAGE_MODEL"] = "gpt-image-1-mini"
        with self.assertRaises(RuntimeError):
            agent.openai_images_edit("A university scene.", self.references[:2])
        self.network.assert_not_called()

    def test_default_person_request_uses_sunburst_high_and_exact_portrait_ratio_despite_legacy_general_settings(self):
        os.environ.pop("OPENAI_PERSON_IMAGE_MODEL")
        os.environ.pop("OPENAI_PERSON_IMAGE_QUALITY")
        os.environ["OPENAI_IMAGE_MODEL"] = "gpt-image-1"
        os.environ["OPENAI_IMAGE_QUALITY"] = "low"
        self.network.side_effect = None
        self.network.return_value = json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
        self.assertEqual(agent.openai_images_edit("A natural university scene.", self.references[:2]), self.generated_bytes)
        self.network.assert_called_once()
        self.edit_parts(self.network.call_args.args[0], self.references[:2], model=SUNBURST_MODEL, quality="high", size="1152x2048", input_fidelity=None)
        self.assertEqual(agent.openai_image_model(), "gpt-image-1")
        self.assertEqual(agent.openai_image_quality(), "low")

    def test_blank_person_settings_use_the_supported_defaults(self):
        os.environ["OPENAI_PERSON_IMAGE_MODEL"] = "  "
        os.environ["OPENAI_PERSON_IMAGE_QUALITY"] = "  "
        self.assertEqual(agent.openai_person_image_model(), SUNBURST_MODEL)
        self.assertEqual(agent.openai_person_image_quality(), "high")
        self.assertIsNone(agent.reference_input_fidelity_parameter())
        os.environ["OPENAI_PERSON_IMAGE_MODEL"] = "  gpt-image-1.5  "
        os.environ["OPENAI_PERSON_IMAGE_QUALITY"] = " high "
        self.assertEqual(agent.openai_person_image_model(), "gpt-image-1.5")
        self.assertEqual(agent.openai_person_image_quality(), "high")
        self.assertEqual(agent.reference_input_fidelity_parameter(), "high")
        self.network.assert_not_called()

    def test_normal_generation_defaults_to_sunburst_medium_with_explicit_supported_dimensions(self):
        self.network.side_effect = None
        self.network.return_value = json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
        self.assertEqual(agent.openai_images_generate("A university campus."), self.generated_bytes)
        payload = json.loads(self.network.call_args.args[0].data)
        self.assertEqual(payload["model"], SUNBURST_MODEL)
        self.assertEqual(payload["quality"], "medium")
        self.assertEqual(payload["size"], "1152x2048")
        self.assertNotIn("input_fidelity", payload)

    def test_default_person_settings_reach_every_variant_and_saved_metadata(self):
        os.environ.pop("OPENAI_PERSON_IMAGE_MODEL")
        os.environ.pop("OPENAI_PERSON_IMAGE_QUALITY")
        os.environ["OPENAI_IMAGE_MODEL"] = "gpt-image-1"
        self.generated_bytes = image_bytes((1152, 2048))
        self.network.side_effect = self.mocked_openai(self.references[:2], model=SUNBURST_MODEL, quality="high", size="1152x2048", input_fidelity=None)
        result = agent.handle_create("The students celebrate arriving at university.", "MY JOURNEY | BEGINS", client_slug="zoomex", person_reference_paths=self.references[:2])
        self.assertEqual(len(result["thumbnails"]), 2)
        for item in [result, *result["thumbnails"]]:
            self.assertEqual(item["reference_image_model"], SUNBURST_MODEL)
            self.assertEqual(item["reference_image_quality"], "high")
            self.assertEqual(item["reference_image_size"], "1152x2048")
            self.assertEqual(item["reference_input_fidelity"], "high")
            self.assertIsNone(item["reference_input_fidelity_parameter"])
        for item in result["thumbnails"]:
            output = Path(item["thumbnail"])
            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["reference_image_model"], SUNBURST_MODEL)
            self.assertEqual(metadata["reference_image_size"], "1152x2048")
            self.assertEqual(metadata["reference_image_quality"], "high")
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1080, 1920))

    def test_reference_preflight_rejects_every_invalid_photo_before_any_api_call(self):
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        invalid = self.root / "invalid.png"
        invalid.write_bytes(b"this is not an image")
        transparent = self.root / "transparent-rgba.png"
        Image.new("RGBA", (48, 64), (221, 49, 83, 0)).save(transparent)
        palette = self.root / "transparent-palette.png"
        Image.new("P", (48, 64), 0).save(palette, transparency=0)
        for reference in (self.root / "missing.png", empty, invalid, transparent, palette):
            with self.subTest(reference=reference.name):
                with self.assertRaises(RuntimeError):
                    agent.handle_create("University journey.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=[self.reference, reference])
                with self.assertRaises(RuntimeError):
                    agent.openai_images_edit("A university scene.", [self.reference, reference])
        self.network.assert_not_called()

    def test_path_coercion_preserves_selection_order_and_handles_legacy_strings(self):
        self.assertEqual(agent.coerce_person_reference_paths(str(self.reference)), [self.reference])
        selected = [str(self.references[2]), self.references[0]]
        self.assertEqual(agent.coerce_person_reference_paths(person_reference_paths=selected), [self.references[2], self.references[0]])
        self.assertEqual(agent.coerce_person_reference_paths(), [])

    def test_more_than_four_references_and_conflicting_input_forms_are_rejected(self):
        cases = (
            {"person_reference_paths": self.references + [self.reference]},
            {"person_reference_path": self.reference, "person_reference_paths": self.references[1:2]},
            {"subject_path": self.reference, "person_reference_paths": self.references[:2]},
        )
        for kwargs in cases:
            with self.subTest(arguments=tuple(kwargs)):
                with self.assertRaises(RuntimeError):
                    agent.handle_create("University journey.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", **kwargs)
        self.network.assert_not_called()
        for selected in ([], self.references + [self.reference]):
            with self.subTest(edit_reference_count=len(selected)):
                with self.assertRaises(RuntimeError):
                    agent.openai_images_edit("A university scene.", selected)
        self.network.assert_not_called()

    def test_planning_sees_all_people_as_ordered_identity_references_for_the_script(self):
        selected = self.references[:2]
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(self.brief)}) as create:
            agent.create_visual_brief("They celebrate their university admission.", "MY JOURNEY | BEGINS", agent.CLIENTS["zoomex"], extra_prompt="Smiling in graduation robes.", person_reference_paths=selected)
        payload = create.call_args.args[0]
        self.assertNotIn("tools", payload)
        content = payload["input"][1]["content"]
        images = [part for part in content if part["type"] == "input_image"]
        self.assertEqual(len(images), 2)
        for part, reference in zip(images, selected):
            self.assertEqual(part["detail"], "high")
            self.assertEqual(base64.b64decode(part["image_url"].split(",", 1)[1]), reference.read_bytes())
        text = payload["input"][0]["content"] + " " + " ".join(part["text"] for part in content if part["type"] == "input_text")
        self.assertLess(text.lower().index("photo 1"), text.lower().index("photo 2"))
        for word in ("face", "hair", "expression", "pose"):
            self.assertIn(word, text.lower())
        self.assertRegex(text.lower(), r"outfit|clothing")
        self.assertIn("They celebrate their university admission.", text)
        self.assertIn("Smiling in graduation robes.", text)
        self.assertNotIn("BACKGROUND ONLY", text)
        self.assertNotIn("generate only the environment", text.lower())

    def test_multi_reference_generation_uses_only_the_edit_endpoint(self):
        selected = self.references[:2]
        self.network.side_effect = self.mocked_openai(selected)
        with mock.patch.object(agent, "openai_images_generate") as generate, mock.patch.object(agent, "openai_responses_create") as fallback:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"], person_reference_paths=selected)
        self.assertEqual(output.read_bytes(), self.generated_bytes)
        generate.assert_not_called()
        fallback.assert_not_called()
        self.network.assert_called_once()
        prompt = self.edit_parts(self.network.call_args.args[0], selected)["prompt"]
        self.assertIn("university", prompt.lower())
        self.assertNotIn("BACKGROUND ONLY", prompt)

    def test_failed_reference_edit_never_falls_back_to_generic_generation(self):
        with mock.patch.object(agent, "openai_images_edit", side_effect=RuntimeError("Reference editing unavailable")) as edit, mock.patch.object(agent, "openai_images_generate") as generate, mock.patch.object(agent, "openai_responses_create") as fallback:
            with self.assertRaises(RuntimeError):
                agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"], person_reference_paths=self.references[:2])
        edit.assert_called_once()
        generate.assert_not_called()
        fallback.assert_not_called()

    def test_planning_provider_reflections_never_reach_public_job_errors(self):
        photo_base64 = base64.b64encode(self.reference.read_bytes()).decode()
        private_marker = "provider-private-reflection"
        reflected = f"{private_marker}: offline-test-key data:image/png;base64,{photo_base64}"
        with mock.patch.object(agent, "JOBS", {}), mock.patch.object(agent, "openai_images_edit") as edit, mock.patch.object(agent, "openai_images_generate") as generate:
            for code in (401, 400, 429):
                with self.subTest(status=code):
                    self.network.reset_mock()
                    self.network.side_effect = urllib.error.HTTPError(
                        agent.OPENAI_ENDPOINT, code, "provider error", {},
                        io.BytesIO(json.dumps({"error": {"message": reflected}}).encode()),
                    )
                    job_id = agent.start_create_job(
                        "The students arrive at university.", "MY JOURNEY | BEGINS",
                        client_slug="alliance-latin", person_reference_paths=self.references[:2],
                    )
                    deadline = time.monotonic() + 3
                    while True:
                        job = agent.get_job(job_id)
                        if job["status"] in {"done", "error"}:
                            break
                        self.assertLess(time.monotonic(), deadline, "Planning failure did not finish the job")
                        time.sleep(0.01)
                    self.assertEqual(job["status"], "error")
                    self.assertIn(f"HTTP {code}", job["error"])
                    public_payload = json.dumps(job)
                    for sensitive in ("offline-test-key", photo_base64, private_marker):
                        self.assertNotIn(sensitive, public_payload)
                    self.network.assert_called_once()
                    self.assertEqual(self.network.call_args.args[0].full_url, agent.OPENAI_ENDPOINT)
        edit.assert_not_called()
        generate.assert_not_called()

    def test_reference_edit_http_errors_are_safe_and_do_not_retry_or_fallback(self):
        sensitive = f'offline-test-key {base64.b64encode(self.reference.read_bytes()).decode()}'
        for code in (401, 429, 500):
            with self.subTest(status=code):
                error = urllib.error.HTTPError(agent.OPENAI_IMAGES_EDIT_ENDPOINT, code, "provider error", {}, io.BytesIO(json.dumps({"error": {"message": sensitive}}).encode()))
                self.network.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    agent.openai_images_edit("A university scene.", self.references[:2])
                self.assertNotIn("offline-test-key", str(caught.exception))
                self.assertNotIn(base64.b64encode(self.reference.read_bytes()).decode(), str(caught.exception))
                self.assertEqual(self.network.call_count, 1)
                self.network.reset_mock()

    def test_edit_transport_and_missing_image_errors_are_explicit(self):
        for response in (
            urllib.error.URLError("offline"), {"data": []}, {"unexpected": True}, None, [],
            {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]},
        ):
            with self.subTest(response=type(response).__name__):
                self.network.reset_mock()
                if isinstance(response, Exception):
                    self.network.side_effect = response
                else:
                    self.network.side_effect = None
                    self.network.return_value = json_response(response)
                with self.assertRaises(RuntimeError):
                    agent.openai_images_edit("A university scene.", self.references[:2])
                self.network.assert_called_once()
        self.network.reset_mock()
        unreadable = mock.MagicMock()
        unreadable.__enter__.return_value = unreadable
        unreadable.read.return_value = b"<html>upstream failure</html>"
        self.network.side_effect = None
        self.network.return_value = unreadable
        with self.assertRaises(RuntimeError):
            agent.openai_images_edit("A university scene.", self.references[:2])
        self.network.assert_called_once()

    def test_legacy_single_reference_still_routes_to_identity_editing(self):
        self.network.side_effect = self.mocked_openai([self.reference])
        result = agent.handle_create("The student arrives at university.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_path=self.reference)
        self.assert_reference_metadata(result, 1)
        self.assertTrue(Path(result["thumbnail"]).is_file())

    def test_multi_person_pipeline_metadata_and_variant_counts_for_every_brand(self):
        selected = self.references[:2]
        self.network.side_effect = self.mocked_openai(selected)
        for slug in agent.CLIENTS:
            with self.subTest(client=slug):
                result = agent.handle_create("The two students arrive at university.", "MY JOURNEY | BEGINS", client_slug=slug, person_reference_paths=selected)
                self.assert_reference_metadata(result, 2)
                variants = result.get("thumbnails") or [result]
                self.assertEqual(len(variants), 2 if slug in agent.TWO_THUMBNAIL_CLIENTS else 1)
                for variant in variants:
                    self.assert_reference_metadata(variant, 2)
                    output = Path(variant["thumbnail"])
                    self.assert_reference_metadata(json.loads(output.with_suffix(".json").read_text()), 2)
                    with Image.open(output) as rendered:
                        self.assertEqual(rendered.format, "PNG")
                        self.assertEqual(rendered.size, (1080, 1920))

    def test_non_reference_generation_and_its_existing_fallback_still_work(self):
        with mock.patch.object(agent, "openai_images_edit") as edit, mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"])
            self.assertEqual(output.read_bytes(), self.generated_bytes)
            generate.assert_called_once()
            edit.assert_not_called()
        response = {"output": [{"type": "image_generation_call", "result": base64.b64encode(self.generated_bytes).decode()}]}
        with mock.patch.object(agent, "openai_images_edit") as edit, mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("temporary failure")), mock.patch.object(agent, "openai_responses_create", return_value=response):
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"])
        self.assertEqual(output.read_bytes(), self.generated_bytes)
        edit.assert_not_called()

    def test_normal_responses_fallback_explicitly_uses_its_own_model_quality_and_size(self):
        response = {"output": [{"type": "image_generation_call", "result": base64.b64encode(self.generated_bytes).decode()}]}
        for model, quality, size in (("gpt-image-1.5", "high", "1024x1536"), (SUNBURST_MODEL, "xhigh", "1152x2048")):
            with self.subTest(model=model):
                os.environ["OPENAI_IMAGE_MODEL"] = model
                os.environ["OPENAI_IMAGE_QUALITY"] = quality
                with mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("temporary image service failure")), mock.patch.object(agent, "openai_responses_create", return_value=response) as create:
                    output = agent.generate_subject_image(self.brief, agent.CLIENTS["alliance-latin"])
                self.assertEqual(output.read_bytes(), self.generated_bytes)
                tool = create.call_args.args[0]["tools"][0]
                self.assertEqual(tool["type"], "image_generation")
                self.assertEqual(tool["model"], model)
                self.assertEqual(tool["quality"], quality)
                self.assertEqual(tool["size"], size)

    def test_no_reference_creation_reports_zero_people_and_keeps_normal_generation(self):
        with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate, mock.patch.object(agent, "openai_images_edit") as edit:
            result = agent.handle_create("University journey.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=[])
        self.assert_reference_metadata(result, 0)
        generate.assert_called_once()
        edit.assert_not_called()

    def test_normalization_preserves_existing_source_pixels_and_alpha(self):
        source = self.root / "alpha.png"
        original = Image.new("RGBA", (48, 64), (192, 36, 73, 255))
        ImageDraw.Draw(original).rectangle((3, 5, 20, 25), fill=(42, 135, 221, 128))
        original.save(source)
        for path in (self.reference, source):
            with self.subTest(source=path.name):
                normalized = agent.normalize_reference_image(path)
                with Image.open(path) as before, Image.open(normalized) as after:
                    self.assertEqual(after.format, "PNG")
                    self.assertEqual(after.size, before.size)
                    self.assertEqual(after.convert("RGBA").tobytes(), before.convert("RGBA").tobytes())

    def test_an_empty_selected_reference_is_not_silently_ignored(self):
        field = SimpleNamespace(filename="portrait.png", file=io.BytesIO(b""))
        with self.assertRaises(RuntimeError):
            agent.save_upload({"person_reference": field}, "person_reference")
        self.assertIsNone(agent.save_upload({}, "person_reference"))

    def upload_field(self, index=0, data=None):
        return SimpleNamespace(filename="portrait.png", file=io.BytesIO(self.references[index].read_bytes() if data is None else data))

    def assert_uploaded_people(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(len(set(actual)), len(actual), "Equal original filenames must not overwrite a different photo")
        for actual_path, expected_path in zip(actual, expected):
            with Image.open(actual_path) as actual_image, Image.open(expected_path) as original:
                self.assertEqual(actual_image.convert("RGBA").tobytes(), original.convert("RGBA").tobytes())

    def test_numbered_upload_slots_are_collected_in_person_order(self):
        form = {
            "person_reference_4": self.upload_field(3),
            "person_reference_2": self.upload_field(1),
            "person_reference": self.upload_field(0),
            "person_reference_3": self.upload_field(2),
        }
        self.assert_uploaded_people(agent.save_person_reference_uploads(form), self.references)
        self.network.assert_not_called()

    def test_repeated_unindexed_uploads_preserve_order(self):
        form = {"person_reference": [self.upload_field(2), self.upload_field(0)]}
        self.assert_uploaded_people(agent.save_person_reference_uploads(form), [self.references[2], self.references[0]])

    def test_ambiguous_duplicate_empty_and_excess_uploads_are_rejected(self):
        cases = (
            {"person_reference": [self.upload_field(0), self.upload_field(1)], "person_reference_2": self.upload_field(2)},
            {"person_reference": [self.upload_field(0), self.upload_field(0)]},
            {"person_reference": self.upload_field(0), "person_reference_2": self.upload_field(data=b"")},
            {"person_reference": [self.upload_field(index % 4) for index in range(5)]},
        )
        for form in cases:
            with self.subTest(fields=tuple(form)):
                with self.assertRaises(RuntimeError):
                    agent.save_person_reference_uploads(form)
        self.network.assert_not_called()

    def test_simultaneous_matching_titles_have_distinct_output_filenames(self):
        with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes), mock.patch.object(agent.time, "strftime", return_value="20261020-190000"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda reference: agent.handle_create("University journey.", "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=[reference]), self.references[:2]))
        self.assertNotEqual(results[0]["thumbnail"], results[1]["thumbnail"])
        for result in results:
            self.assertTrue(Path(result["thumbnail"]).is_file())
            self.assert_reference_metadata(result, 1)

    def test_http_two_reference_upload_job_and_download(self):
        expected_bytes = [path.read_bytes() for path in self.references[:2]]

        def respond(request, timeout=None):
            if request.full_url == agent.OPENAI_ENDPOINT:
                payload = json.loads(request.data)
                images = [part for part in payload["input"][1]["content"] if part["type"] == "input_image"]
                self.assertEqual(len(images), 2)
                return json_response({"output_text": json.dumps(self.brief)})
            self.assertEqual(request.full_url, agent.OPENAI_IMAGES_EDIT_ENDPOINT)
            files = [part for part in multipart_parts(request) if part.get_filename() is not None]
            self.assertEqual(len(files), 2)
            # Upload normalization is lossless for these PNGs; both people must
            # reach the edit request even though their original filenames match.
            for part, expected in zip(files, expected_bytes):
                with Image.open(io.BytesIO(part.get_payload(decode=True))) as actual, Image.open(io.BytesIO(expected)) as original:
                    self.assertEqual(actual.convert("RGBA").tobytes(), original.convert("RGBA").tobytes())
            return json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})

        self.network.side_effect = respond
        with mock.patch.object(agent, "JOBS", {}), mock.patch.object(agent.ThumbnailHandler, "log_message"):
            server = agent.ThreadingHTTPServer(("127.0.0.1", 0), agent.ThumbnailHandler)
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
            thread.start()
            try:
                boundary = "test-person-reference-boundary"
                body = bytearray()
                for name, value in {"client": "alliance-latin", "title": "MY JOURNEY | BEGINS", "script": "The two photographed students arrive at university."}.items():
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
                for name, payload in zip(("person_reference", "person_reference_2"), expected_bytes):
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="portrait.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
                    body.extend(payload)
                    body.extend(b"\r\n")
                body.extend(f"--{boundary}--\r\n".encode())

                def request(method, path, data=None, headers=None):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                    try:
                        connection.request(method, path, body=data, headers=headers or {})
                        response = connection.getresponse()
                        return response.status, dict(response.getheaders()), response.read()
                    finally:
                        connection.close()

                status, _, payload = request("GET", "/api/status")
                self.assertEqual(status, 200)
                readiness = json.loads(payload)
                self.assertIs(readiness["camera_facing_required"], False)
                self.assertEqual(readiness["camera_facing_rule"], "best_effort")
                self.assertIs(readiness["reference_appearance_check_required"], True)
                self.assertEqual(readiness["reference_workflow_version"], 8)
                self.assertEqual(readiness["person_image_model"], "gpt-image-1")
                self.assertEqual(readiness["person_image_quality"], "medium")
                self.assertEqual(readiness["person_image_size"], "1024x1536")
                self.assertEqual(readiness["person_review_model"], "gpt-6-astra")
                status, _, payload = request("POST", "/api/create", bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
                self.assertEqual(status, 202, payload)
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
                self.assert_reference_metadata(result, 2)
                self.assertEqual(len(result["thumbnails"]), 1)
                self.assert_reference_metadata(result["thumbnails"][0], 2)
                status, headers, png = request("GET", result["download_url"])
                self.assertEqual(status, 200)
                self.assertIn("attachment", headers["Content-Disposition"])
                with Image.open(io.BytesIO(png)) as rendered:
                    self.assertEqual(rendered.format, "PNG")
                    self.assertEqual(rendered.size, (1080, 1920))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
