"""Offline integration coverage for provider routing and the finished thumbnail.

No real credentials, network requests, or generated photos are used here.
"""

import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


ENVIRONMENT = {
    "THUMBNAIL_IMAGE_PROVIDER": "higgsfield",
    "THUMBNAIL_VISION_PROVIDER": "gemini",
    "HF_API_KEY_ID": "offline-hf-id",
    "HF_API_KEY_SECRET": "offline-hf-secret",
    "GEMINI_API_KEY": "offline-gemini-key",
}

# Prevent .env loading even when this test is run independently of the suite.
with mock.patch.dict(os.environ, ENVIRONMENT, clear=True), \
     mock.patch.object(Path, "read_text", return_value=""):
    import thumbnail_agent as agent


def png(color, size=(128, 192)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


class ProviderWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch(mock.patch.dict(os.environ, ENVIRONMENT, clear=True))
        self.patch(mock.patch.multiple(
            agent, ASSETS_DIR=self.root / "assets", STATE_DIR=self.root / "state",
            GENERATED_DIR=self.root / "generated", WORK_DIR=self.root / "work", JOBS={},
        ))
        agent.ensure_dirs()
        self.forbidden = []
        for name in ("openai_responses_create", "openai_images_generate", "openai_images_edit"):
            forbidden = self.patch(mock.patch.object(agent, name, side_effect=AssertionError("Unexpected OpenAI call")))
            self.forbidden.append(forbidden)
        # Cover both urlopen and clients' redirect-safe opener entry points.
        self.network = self.patch(mock.patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network access")))
        self.hf_network = self.patch(mock.patch.object(agent.higgsfield_client._OPENER, "open", side_effect=AssertionError("Unexpected Higgsfield network access")))
        self.gemini_network = self.patch(mock.patch.object(agent.gemini_client, "_open_request", side_effect=AssertionError("Unexpected Gemini network access")))
        self.references = []
        for index, color in enumerate(("#cc3153", "#22b867", "#993fe1", "#f4b722")):
            reference = self.root / f"person-{index + 1}" / "portrait.png"
            reference.parent.mkdir()
            reference.write_bytes(png(color, (64 + index, 96 + index)))
            self.references.append(reference)
        self.generated_bytes = png("#355d83", (288, 512))
        self.expected_references = self.references[:2]
        self.appearance_result = None
        self.gaze_error = None
        self.events = []
        self.events_lock = threading.Lock()
        self.generate = self.patch(mock.patch.object(agent.higgsfield_client, "generate_image", side_effect=self.higgsfield_image))
        self.review = self.patch(mock.patch.object(agent.gemini_client, "generate_json", side_effect=self.gemini_json))

    def patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def tearDown(self):
        for forbidden in self.forbidden + [self.network, self.hf_network, self.gemini_network]:
            forbidden.assert_not_called()

    def record(self, stage, payload):
        with self.events_lock:
            self.events.append((stage, payload))

    def higgsfield_image(self, prompt, reference_paths=(), timeout=600):
        self.record("image", {"prompt": prompt, "references": list(reference_paths), "timeout": timeout})
        self.assertEqual(list(reference_paths), self.expected_references)
        return self.generated_bytes

    def passing_appearance(self):
        return {
            "references": [
                {"reference_index": index, **dict.fromkeys(agent.REFERENCE_APPEARANCE_FLAGS, True), "notes": "Offline synthetic feature fixture."}
                for index in range(1, len(self.expected_references) + 1)
            ],
            "notes": "Synthetic colors; no real faces or identity decisions.",
        }

    def gemini_json(self, **payload):
        properties = payload["schema"]["properties"]
        if "visual_prompt" in properties:
            self.record("planning", payload)
            return {"visual_prompt": "Students celebrate arriving at university.", "negative_prompt": "watermarks", "rationale": "A university journey."}
        if "people_present" in properties:
            self.record("gaze", payload)
            if self.gaze_error:
                raise self.gaze_error
            count = len(self.expected_references)
            return {
                "people_present": count > 0, "visible_people_count": count,
                "all_faces_front_facing": True, "all_eyes_on_camera": True,
                "all_faces_unobstructed": True, "reason": "Offline gaze fixture.",
            }
        if "references" in properties:
            self.record("appearance", payload)
            return self.passing_appearance() if self.appearance_result is None else self.appearance_result
        self.fail("Unexpected Gemini schema; route all planning and review through the mocked provider")

    def create(self, references=None, client="alliance-latin"):
        if references is not None:
            self.expected_references = references
        return agent.handle_create(
            "Two students arrive at university.", "OUR JOURNEY | BEGINS",
            client_slug=client, person_reference_paths=self.expected_references,
            extra_prompt="A natural campus scene.",
        )

    @staticmethod
    def image_content(payload):
        return [base64.b64decode(part["image_url"].split(",", 1)[1])
                for part in payload["content"] if part.get("type") == "input_image"]

    def assert_metadata(self, result, count):
        self.assertIs(result["used_ai"], True)
        self.assertEqual(result["image_provider"], "higgsfield")
        self.assertEqual(result["vision_provider"], "gemini")
        self.assertEqual(result["image_model"], "marketing-studio/image/sunburst")
        self.assertEqual(result["person_reference_count"], count)
        self.assertIs(result["person_reference_used"], count > 0)
        self.assertIs(result["reference_appearance_checked"], count > 0)
        self.assertIs(result["reference_appearance_passed"], True if count else None)
        self.assertEqual(result["reference_appearance_review_model"], "gemini-3.8-flash" if count else None)
        self.assertEqual(result["camera_facing_rule"], "best_effort")
        if count:
            self.assertEqual(result["reference_strategy"], "multi_image_edit")
            self.assertEqual(result["reference_preservation"], "identity_guided")
            self.assertEqual(result["reference_image_model"], "marketing-studio/image/sunburst")
            self.assertEqual(result["reference_image_quality"], "high")
            self.assertEqual(result["reference_input_fidelity"], "reference_guided")
            self.assertIsNone(result["reference_input_fidelity_parameter"])

    def assert_png(self, path):
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1080, 1920))
        self.assertNotEqual(Path(path).read_bytes(), self.generated_bytes, "Review and output must use the branded final canvas")

    def assert_no_publication(self):
        self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])
        self.assertEqual(list(agent.WORK_DIR.glob("camera-check-*.png")), [])
        self.assertEqual(list(agent.WORK_DIR.glob("best-candidate-*.png")), [])

    def test_reference_planning_image_edit_and_review_receive_ordered_originals_and_final_brand_canvas(self):
        result = self.create()
        self.assertEqual([stage for stage, _ in self.events], ["planning", "image", "gaze", "appearance"])
        self.assert_metadata(result, 2)
        self.assert_png(result["thumbnail"])
        self.assertEqual(result["design_asset"], str(agent.get_client_config("alliance-latin")["design_path"]))
        originals = [reference.read_bytes() for reference in self.expected_references]
        planning = next(payload for stage, payload in self.events if stage == "planning")
        appearance = next(payload for stage, payload in self.events if stage == "appearance")
        gaze = next(payload for stage, payload in self.events if stage == "gaze")
        self.assertEqual(self.image_content(planning), originals)
        self.assertEqual(self.image_content(appearance), originals + [Path(result["thumbnail"]).read_bytes()])
        self.assertEqual(self.image_content(gaze), [Path(result["thumbnail"]).read_bytes()])
        self.assertEqual(appearance["model_name"], "gemini-3.8-flash")
        self.assertEqual(planning["model_name"], "gemini-3.5-flash-lite")
        self.assertEqual(gaze["model_name"], "gemini-3.5-flash-lite")
        saved = json.loads(Path(result["thumbnail"]).with_suffix(".json").read_text())
        self.assert_metadata(saved, 2)
        self.assertEqual([reference.read_bytes() for reference in self.expected_references], originals)

    def test_four_references_remain_separate_and_ordered(self):
        result = self.create(references=self.references)
        self.assert_metadata(result, 4)
        self.assertEqual(self.generate.call_args.kwargs["reference_paths"], self.references)
        appearance = next(payload for stage, payload in self.events if stage == "appearance")
        self.assertEqual(self.image_content(appearance)[:-1], [path.read_bytes() for path in self.references])

    def test_no_reference_scene_still_uses_higgsfield_and_gemini_without_openai(self):
        result = self.create(references=[])
        self.assert_metadata(result, 0)
        self.assert_png(result["thumbnail"])
        self.assertEqual([stage for stage, _ in self.events], ["planning", "image", "gaze"])
        self.assertNotIn("reference_paths", self.generate.call_args.kwargs)

    def test_zoomex_dual_options_publish_provider_metadata_for_every_public_option(self):
        job_id = "offline-provider-job"
        agent.JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0, "message": "Queued", "started_at": time.time()}
        agent.run_create_job(job_id, "Students arrive at university.", "OUR JOURNEY | BEGINS", "zoomex", person_reference_paths=self.expected_references)
        job = agent.get_job(job_id)
        self.assertEqual(job["status"], "done", job.get("error"))
        result = job["result"]
        self.assert_metadata(result, 2)
        self.assertEqual(len(result["thumbnails"]), 2)
        self.assertEqual([item["option_index"] for item in result["thumbnails"]], [1, 2])
        self.assertEqual(len({item["filename"] for item in result["thumbnails"]}), 2)
        for item in result["thumbnails"]:
            self.assert_metadata(item, 2)
            self.assert_png(agent.GENERATED_DIR / item["filename"])
            self.assertEqual(item["download_url"], "/api/download/" + item["filename"])
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual([stage for stage, _ in self.events].count("planning"), 2)
        self.assertEqual([stage for stage, _ in self.events].count("appearance"), 2)

    def test_missing_vision_configuration_fails_before_either_provider_can_spend(self):
        for references in ([], self.references[:2]):
            with self.subTest(reference_count=len(references)), mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
                with self.assertRaisesRegex(RuntimeError, "Gemini.*planning.*appearance"):
                    self.create(references=references)
                self.generate.assert_not_called()
                self.review.assert_not_called()
                self.assert_no_publication()

    def test_invalid_image_or_review_model_fails_before_either_provider_can_spend(self):
        cases = (
            ("HF_IMAGE_MODEL", "unsupported-image-model", RuntimeError, "HF_IMAGE_MODEL"),
            ("GEMINI_REVIEW_MODEL", "https://example.invalid/gemini-model", ValueError, "Gemini model configuration"),
        )
        for name, value, error_type, message in cases:
            with self.subTest(setting=name), mock.patch.dict(os.environ, {name: value}):
                with self.assertRaisesRegex(error_type, message):
                    self.create()
                self.generate.assert_not_called()
                self.review.assert_not_called()
                self.assertEqual(self.events, [])
                self.assert_no_publication()

    def test_failed_feature_review_retries_once_from_originals_and_never_publishes(self):
        self.appearance_result = self.passing_appearance()
        self.appearance_result["references"][1]["eyes_and_brows_preserved"] = False
        with self.assertRaisesRegex(RuntimeError, "faces or hair changed"):
            self.create()
        self.assertEqual(self.generate.call_count, 2)
        for call in self.generate.call_args_list:
            self.assertEqual(call.kwargs["reference_paths"], self.expected_references)
        self.assert_no_publication()

    def test_malformed_required_appearance_review_fails_without_extra_generation(self):
        self.appearance_result = {"references": [], "notes": "Incomplete provider response"}
        with self.assertRaisesRegex(RuntimeError, "Could not check the faces"):
            self.create()
        self.assertEqual(self.generate.call_count, 1)
        self.assert_no_publication()

    def test_higgsfield_timeout_never_falls_back_or_resubmits(self):
        self.generate.side_effect = TimeoutError("Higgsfield request timed out")
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            self.create()
        self.assertEqual(self.generate.call_count, 1)
        self.assertEqual([stage for stage, _ in self.events], ["planning"])
        self.assert_no_publication()

    def test_unavailable_optional_gaze_does_not_skip_required_appearance_review(self):
        self.gaze_error = RuntimeError("Offline optional gaze service unavailable")
        result = self.create()
        self.assert_metadata(result, 2)
        self.assertIsNone(result["camera_facing_verified"])
        self.assertEqual(self.generate.call_count, 1)
        self.assertEqual([stage for stage, _ in self.events].count("appearance"), 1)

    def test_status_exposes_selected_provider_configuration_and_workflow_without_openai(self):
        handler = agent.ThumbnailHandler.__new__(agent.ThumbnailHandler)
        handler.path = "/api/status?client=leverage-edu"
        handler.send_json = mock.Mock(side_effect=lambda payload, *args: payload)
        status = handler.do_GET()
        self.assertIs(status["openai_configured"], False)
        self.assertEqual(status["image_provider"], "higgsfield")
        self.assertEqual(status["vision_provider"], "gemini")
        for name in ("image_provider_configured", "vision_provider_configured", "generation_configured"):
            self.assertIs(status[name], True)
        self.assertEqual(status["reference_workflow_version"], 8)
        self.assertEqual(status["person_image_model"], "marketing-studio/image/sunburst")
        self.assertEqual(status["person_review_model"], "gemini-3.8-flash")
        self.assertIs(status["camera_facing_required"], False)
        self.assertIs(status["reference_appearance_check_required"], True)
        self.assertIs(status["allow_browser_key_setup"], False)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            status = handler.do_GET()
        self.assertIs(status["image_provider_configured"], True)
        self.assertIs(status["vision_provider_configured"], False)
        self.assertIs(status["generation_configured"], False)
        self.review.assert_not_called()
        self.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
