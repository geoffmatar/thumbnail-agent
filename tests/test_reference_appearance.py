import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True), \
     mock.patch.object(Path, "read_text", return_value=""):
    import thumbnail_agent as agent


FEATURE_FLAGS = (
    "face_shape_preserved", "eyes_and_brows_preserved", "nose_and_mouth_preserved",
    "hair_preserved", "accessories_preserved", "sufficiently_clear",
)


def feature_check(count=2):
    return {
        "references": [{"reference_index": index, **dict.fromkeys(FEATURE_FLAGS, True), "notes": "Synthetic feature fixture."} for index in range(1, count + 1)],
        "notes": "No real faces are used by these offline tests.",
    }


def png_bytes(color, size=(96, 144)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


class ReferenceAppearanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        paths = mock.patch.multiple(agent, ASSETS_DIR=self.root / "assets", STATE_DIR=self.root / "state", GENERATED_DIR=self.root / "generated", WORK_DIR=self.root / "work")
        paths.start()
        self.addCleanup(paths.stop)
        agent.ensure_dirs()
        network = mock.patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request in appearance-review offline tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.references = []
        for index, color in enumerate(("#cc3153", "#22b867")):
            reference = self.root / f"source-{index + 1}.png"
            reference.write_bytes(png_bytes(color))
            self.references.append(reference)
        self.finished = self.root / "finished.png"
        self.finished.write_bytes(png_bytes("#355d83", (1080, 1920)))
        self.generated_bytes = png_bytes("#355d83")
        self.brief = {"visual_prompt": "Two students celebrate arriving at university.", "negative_prompt": "watermarks", "rationale": "A university journey."}
        self.good_gaze = {
            "people_present": True, "visible_people_count": 2,
            "all_faces_front_facing": True, "all_eyes_on_camera": True,
            "all_faces_unobstructed": True, "reason": "Offline passing gaze fixture.",
        }
        for name, value in (
            ("create_visual_brief", self.brief),
            ("openai_images_edit", self.generated_bytes),
            ("openai_images_generate", self.generated_bytes),
            ("validate_camera_facing", self.good_gaze),
        ):
            patcher = mock.patch.object(agent, name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def create(self, references=None, client="alliance-latin"):
        return agent.handle_create("Two students arrive at university.", "MY JOURNEY | BEGINS", client_slug=client, person_reference_paths=self.references if references is None else references)

    def assert_nothing_published(self):
        self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])
        self.assertEqual(list(agent.WORK_DIR.glob("camera-check-*.png")), [], "Rejected or unchecked final candidates must be removed")

    def test_each_category_can_reject_a_valid_review_without_becoming_a_schema_error(self):
        self.assertEqual(set(agent.REFERENCE_APPEARANCE_FLAGS), set(FEATURE_FLAGS))
        self.assertTrue(agent.reference_appearance_check_passes(feature_check(4), 4))
        reversed_records = feature_check()
        reversed_records["references"].reverse()
        self.assertTrue(agent.reference_appearance_check_passes(reversed_records, 2))
        for flag in FEATURE_FLAGS:
            with self.subTest(category=flag):
                check = feature_check()
                check["references"][1][flag] = False
                self.assertTrue(agent.reference_appearance_check_valid(check, 2))
                self.assertFalse(agent.reference_appearance_check_passes(check, 2))

    def test_schema_rejects_missing_nonboolean_duplicate_and_out_of_range_results(self):
        invalid = [None, [], {}, {"references": [], "notes": ""},
                   {"references": tuple(feature_check()["references"]), "notes": ""},
                   {"references": feature_check()["references"], "notes": None}, feature_check(1), feature_check(3)]
        for flag in FEATURE_FLAGS:
            for value in (1, "true", None):
                check = feature_check()
                check["references"][0][flag] = value
                invalid.append(check)
            check = feature_check()
            check["references"][0].pop(flag)
            invalid.append(check)
        for index in (True, "1", 1.0, 0, 3, 2):
            check = feature_check()
            check["references"][0]["reference_index"] = index
            invalid.append(check)
        check = feature_check()
        check["references"][0]["notes"] = True
        invalid.append(check)
        check = feature_check()
        check["references"][0] = None
        invalid.append(check)
        for check in invalid:
            with self.subTest(check=check):
                self.assertFalse(agent.reference_appearance_check_valid(check, 2))
                self.assertFalse(agent.reference_appearance_check_passes(check, 2))

    def test_review_payload_contains_ordered_originals_then_the_finished_thumbnail_only(self):
        selected = list(reversed(self.references))
        check = feature_check()
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(check)}) as create:
            self.assertEqual(agent.validate_reference_appearance(self.finished, selected), check)
        payload = create.call_args.args[0]
        self.assertEqual(payload["model"], "gpt-6-astra")
        self.assertNotIn("tools", payload)
        content = payload["input"][1]["content"]
        images = [part for part in content if part["type"] == "input_image"]
        self.assertEqual(len(images), 3)
        for image, path in zip(images, [*selected, self.finished]):
            self.assertEqual(image["detail"], "high")
            self.assertEqual(base64.b64decode(image["image_url"].split(",", 1)[1]), path.read_bytes())
        labels = [part["text"].lower() for part in content if part["type"] == "input_text"]
        self.assertIn("1", labels[0])
        self.assertIn("2", labels[1])
        self.assertIn("finished", labels[-1])
        # This endpoint reviews visible edit details; no identification result is
        # requested or asserted by the structural tests.
        properties = payload["text"]["format"]["schema"]["properties"]
        self.assertEqual(set(properties), {"references", "notes"})

    def test_feature_review_model_defaults_and_overrides_are_independent_of_planning(self):
        os.environ["OPENAI_MODEL"] = "gpt-5.5"
        for configured, expected in ((None, "gpt-6-astra"), ("  ", "gpt-6-astra"), ("  gpt-6-sol  ", "gpt-6-sol")):
            with self.subTest(configured=configured):
                if configured is None:
                    os.environ.pop("OPENAI_PERSON_REVIEW_MODEL", None)
                else:
                    os.environ["OPENAI_PERSON_REVIEW_MODEL"] = configured
                self.assertEqual(agent.openai_person_review_model(), expected)
                with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(feature_check())}) as create:
                    agent.validate_reference_appearance(self.finished, self.references)
                self.assertEqual(create.call_args.args[0]["model"], expected)
                self.assertEqual(agent.openai_model(), "gpt-5.5")

    def test_actual_review_helper_rejects_malformed_api_answers(self):
        bad_boolean = feature_check()
        bad_boolean["references"][0]["sufficiently_clear"] = "true"
        for answer in (None, {}, feature_check(1), bad_boolean, "not JSON"):
            with self.subTest(answer=answer):
                raw = answer if isinstance(answer, str) else json.dumps(answer)
                with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": raw}):
                    with self.assertRaises(RuntimeError):
                        agent.validate_reference_appearance(self.finished, self.references)

    def test_failed_feature_review_retries_once_using_all_original_photos(self):
        bad = feature_check()
        bad["references"][1].update(eyes_and_brows_preserved=False, notes="Eye and eyebrow shapes changed.")
        reviews = [bad, feature_check()]

        def audit_final(path, reference_paths):
            self.assertEqual(reference_paths, self.references)
            self.assertEqual(Path(path).parent, agent.WORK_DIR)
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 1920))
            self.assert_nothing_published_except_candidate(path)
            return reviews.pop(0)

        with mock.patch.object(agent, "validate_reference_appearance", side_effect=audit_final) as audit:
            result = self.create()
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(self.openai_images_edit.call_count, 2)
        self.assertEqual(self.validate_camera_facing.call_count, 2)
        self.openai_images_generate.assert_not_called()
        for call in self.openai_images_edit.call_args_list:
            self.assertEqual(call.args[1], self.references)
        self.assertNotEqual(result["visual_prompt"], self.brief["visual_prompt"])
        self.assertTrue(self.openai_images_edit.call_args_list[-1].args[0].startswith(result["visual_prompt"]))
        self.assertIs(result["reference_appearance_checked"], True)
        self.assertIs(result["reference_appearance_passed"], True)
        self.assertEqual(result["reference_appearance_check"], feature_check())
        self.assertEqual(result["camera_facing_attempts"], 2)

    def assert_nothing_published_except_candidate(self, path):
        self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])
        self.assertTrue(Path(path).is_file())

    def test_persistently_changed_features_publish_nothing(self):
        bad = feature_check()
        bad["references"][0]["nose_and_mouth_preserved"] = False
        with mock.patch.object(agent, "validate_reference_appearance", return_value=bad) as audit:
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(self.openai_images_edit.call_count, 2)
        self.assert_nothing_published()

    def test_unavailable_or_malformed_reviews_fail_immediately_without_regenerating(self):
        malformed = feature_check()
        malformed["references"][0]["face_shape_preserved"] = 1
        for answer in (RuntimeError("Feature review unavailable"), None, {}, malformed):
            with self.subTest(answer=answer):
                self.openai_images_edit.reset_mock()
                kwargs = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
                with mock.patch.object(agent, "validate_reference_appearance", **kwargs) as audit:
                    with self.assertRaises(RuntimeError):
                        self.create()
                audit.assert_called_once()
                self.openai_images_edit.assert_called_once()
                self.assert_nothing_published()

    def test_gaze_and_feature_rejections_share_one_retry_budget(self):
        bad_gaze = {**self.good_gaze, "all_eyes_on_camera": False}
        self.validate_camera_facing.side_effect = [bad_gaze, self.good_gaze]
        bad_features = feature_check()
        bad_features["references"][0]["hair_preserved"] = False
        with mock.patch.object(agent, "validate_reference_appearance", return_value=bad_features) as audit:
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(self.openai_images_edit.call_count, 2)
        self.assertEqual(self.validate_camera_facing.call_count, 2)
        audit.assert_called_once()
        self.assert_nothing_published()

    def test_feature_review_is_skipped_until_gaze_passes(self):
        self.validate_camera_facing.return_value = {**self.good_gaze, "all_faces_unobstructed": False}
        with mock.patch.object(agent, "validate_reference_appearance") as audit:
            with self.assertRaises(RuntimeError):
                self.create()
        audit.assert_not_called()
        self.assert_nothing_published()

    def test_no_reference_scene_skips_feature_review_and_reports_no_check(self):
        with mock.patch.object(agent, "validate_reference_appearance") as audit:
            result = self.create(references=[])
        audit.assert_not_called()
        self.openai_images_edit.assert_not_called()
        self.openai_images_generate.assert_called_once()
        self.assertIs(result["reference_appearance_checked"], False)
        self.assertIsNone(result["reference_appearance_passed"])
        self.assertIsNone(result["reference_appearance_check"])
        self.assertIsNone(result["reference_appearance_review_model"])

    def test_verified_feature_results_reach_every_option_and_saved_metadata(self):
        os.environ["OPENAI_PERSON_REVIEW_MODEL"] = " gpt-6-sol "
        with mock.patch.object(agent, "validate_reference_appearance", return_value=feature_check()) as audit:
            result = self.create(client="zoomex")
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(len(result["thumbnails"]), 2)
        for item in [result, *result["thumbnails"]]:
            self.assertIs(item["reference_appearance_checked"], True)
            self.assertIs(item["reference_appearance_passed"], True)
            self.assertEqual(item["reference_appearance_check"], feature_check())
            self.assertEqual(item["reference_appearance_review_model"], "gpt-6-sol")
        for item in result["thumbnails"]:
            metadata = json.loads(Path(item["thumbnail"]).with_suffix(".json").read_text())
            self.assertEqual(metadata["reference_appearance_check"], feature_check())
            self.assertIs(metadata["reference_appearance_passed"], True)
            self.assertEqual(metadata["reference_appearance_review_model"], "gpt-6-sol")


if __name__ == "__main__":
    unittest.main()
