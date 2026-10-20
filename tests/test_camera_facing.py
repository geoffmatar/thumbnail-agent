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


def png_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (96, 144), color).save(output, "PNG")
    return output.getvalue()


def gaze_check(**overrides):
    return {
        "people_present": True, "visible_people_count": 2,
        "all_faces_front_facing": True, "all_eyes_on_camera": True,
        "all_faces_unobstructed": True, "reason": "Every visible face and pair of eyes faces the camera.",
        **overrides,
    }


class CameraFacingTests(unittest.TestCase):
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
        network = mock.patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request in camera-facing offline tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        # Camera tests exercise orientation only. Synthetic source colors use a
        # separate passing edit-feature fixture, tested in its own module.
        appearance = mock.patch.object(agent, "validate_reference_appearance", side_effect=lambda image_path, reference_paths: {
            "references": [{"reference_index": index, **{flag: True for flag in agent.REFERENCE_APPEARANCE_FLAGS}, "notes": "Offline feature fixture."} for index in range(1, len(reference_paths) + 1)],
            "notes": "Synthetic images; feature review is tested separately.",
        })
        appearance.start()
        self.addCleanup(appearance.stop)
        self.references = []
        for index, color in enumerate(("#ce3153", "#22b867")):
            path = self.root / f"person-{index}.png"
            path.write_bytes(png_bytes(color))
            self.references.append(path)
        self.generated_bytes = png_bytes("#355d83")
        self.scene = self.root / "scene.png"
        self.scene.write_bytes(self.generated_bytes)
        self.script = "Two students read a book with their heads down in side profile, avoiding the camera."
        self.extra = "Turn both faces away from the viewer and hide their eyes under their hoods."
        # Model output may contradict the developer rule. The final generation
        # prompt must still enforce the global rule independently of this brief.
        self.brief = {
            "visual_prompt": "Two students in side profile looking down at their open books.",
            "negative_prompt": "watermarks, direct eye contact, looking at the camera",
            "rationale": "A university reading scene.",
        }

    def assert_camera_rule(self, prompt):
        self.assertIn(agent.camera_facing_prompt(), prompt)
        text = agent.camera_facing_prompt().lower()
        self.assertIn("camera", text)
        self.assertIn("eyes", text)
        self.assertIn("face", text)
        self.assertRegex(text, r"overrid|takes priority|higher priority|non.negotiable")

    def test_all_brands_plan_camera_facing_people_despite_conflicting_user_directions(self):
        for slug, config in agent.CLIENTS.items():
            for references in ([], self.references):
                with self.subTest(client=slug, reference_count=len(references)):
                    with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(self.brief)}) as create:
                        agent.create_visual_brief(self.script, "MY JOURNEY | BEGINS", config, extra_prompt=self.extra, person_reference_paths=references)
                    payload = create.call_args.args[0]
                    developer = payload["input"][0]
                    self.assertEqual(developer["role"], "developer")
                    self.assert_camera_rule(developer["content"])
                    content = payload["input"][1]["content"]
                    user_text = " ".join(part["text"] for part in content if part["type"] == "input_text")
                    self.assertIn(self.script, user_text)
                    self.assertIn(self.extra, user_text)
                    images = [part for part in content if part["type"] == "input_image"]
                    self.assertEqual(len(images), len(references))
                    self.assertNotIn("tools", payload)

    def test_local_planning_fallback_keeps_camera_rules_and_negative_constraints(self):
        os.environ.pop("OPENAI_API_KEY")
        for config in agent.CLIENTS.values():
            for references in ([], self.references):
                with self.subTest(client=config["slug"], reference_count=len(references)):
                    brief = agent.create_visual_brief(self.script, "MY JOURNEY | BEGINS", config, extra_prompt=self.extra, person_reference_paths=references)
                    self.assert_camera_rule(brief["visual_prompt"])
                    self.assertIn(agent.camera_facing_negative_prompt(), brief["negative_prompt"])
        self.network.assert_not_called()

    def test_all_brands_enforce_camera_direction_in_final_reference_and_normal_prompts(self):
        for slug, config in agent.CLIENTS.items():
            for references in ([], self.references):
                with self.subTest(client=slug, reference_count=len(references)):
                    with mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes) as edit, mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate:
                        output = agent.generate_subject_image(self.brief, config, person_reference_paths=references)
                    self.assertEqual(output.read_bytes(), self.generated_bytes)
                    if references:
                        edit.assert_called_once()
                        generate.assert_not_called()
                        prompt = edit.call_args.args[0]
                        self.assertEqual(edit.call_args.args[1], references)
                    else:
                        generate.assert_called_once()
                        edit.assert_not_called()
                        prompt = generate.call_args.args[0]
                    self.assert_camera_rule(prompt)
                    self.assertTrue(prompt.rstrip().endswith(agent.camera_facing_prompt()))
                    self.assertIn(agent.camera_facing_negative_prompt(), prompt)
                    self.assertIn("book", prompt.lower(), "Keep the script context while correcting face direction")
                    zone = agent.client_focus_zone(config)
                    self.assertIn(f"x={zone['x']}..{zone['x'] + zone['w']}", prompt)
                    self.assertIn(f"y={zone['y']}..{zone['y'] + zone['h']}", prompt)

    def test_camera_constraints_survive_the_normal_responses_image_fallback(self):
        response = {"output": [{"type": "image_generation_call", "result": base64.b64encode(self.generated_bytes).decode()}]}
        with mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("temporary image service failure")), mock.patch.object(agent, "openai_responses_create", return_value=response) as create:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["alliance-latin"])
        self.assertEqual(output.read_bytes(), self.generated_bytes)
        payload = create.call_args.args[0]
        content = payload["input"][0]["content"]
        self.assertEqual([part["type"] for part in content], ["input_text"])
        self.assert_camera_rule(content[0]["text"])
        self.assertIn(agent.camera_facing_negative_prompt(), content[0]["text"])

    def test_alternate_options_keep_the_same_hard_camera_rule(self):
        for slug in agent.TWO_THUMBNAIL_CLIENTS:
            for references in ([], self.references):
                for option_index in (1, 2):
                    with self.subTest(client=slug, reference_count=len(references), option=option_index):
                        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(self.brief)}) as create:
                            agent.create_visual_brief(self.script, "MY JOURNEY | BEGINS", agent.CLIENTS[slug], variant_index=option_index, variant_count=2, extra_prompt=self.extra, person_reference_paths=references)
                        payload = create.call_args.args[0]
                        self.assert_camera_rule(payload["input"][0]["content"])
                        user_text = " ".join(part["text"] for part in payload["input"][1]["content"] if part["type"] == "input_text")
                        self.assertIn(f"thumbnail option {option_index} of 2", user_text)
                        self.assertIn("visually distinct", user_text)

    def test_global_camera_negatives_are_added_even_when_the_brief_omits_them(self):
        for config in agent.CLIENTS.values():
            with self.subTest(client=config["slug"]):
                negative = agent.combined_negative_prompt({"negative_prompt": "watermarks"}, config)
                self.assertIn("watermarks", negative)
                self.assertIn(agent.camera_facing_negative_prompt(), negative)
                self.assertIn(agent.safe_zone_negative_prompt(config), negative)

    def test_gaze_gate_requires_real_booleans_counts_and_every_visible_person(self):
        self.assertTrue(agent.camera_facing_check_passes(gaze_check(), expected_people=2))
        self.assertTrue(agent.camera_facing_check_passes(gaze_check(visible_people_count=3), expected_people=2))
        self.assertTrue(agent.camera_facing_check_passes(gaze_check(people_present=False, visible_people_count=0, all_faces_front_facing=False, all_eyes_on_camera=False, all_faces_unobstructed=False), expected_people=0))
        invalid = [None, [], {}, gaze_check(visible_people_count=1),
                   gaze_check(people_present=False), gaze_check(visible_people_count=0)]
        for field in ("people_present", "all_faces_front_facing", "all_eyes_on_camera", "all_faces_unobstructed"):
            for value in (False, "true", 1, None):
                invalid.append(gaze_check(**{field: value}))
            missing = gaze_check()
            missing.pop(field)
            invalid.append(missing)
        for value in (True, "2", 2.0, -1, None):
            invalid.append(gaze_check(visible_people_count=value))
        for check in invalid:
            with self.subTest(check=check):
                self.assertFalse(agent.camera_facing_check_passes(check, expected_people=2))
        no_people = gaze_check(people_present=False, visible_people_count=0)
        self.assertFalse(agent.camera_facing_check_passes(no_people, expected_people=1))

    def test_gaze_audit_only_inspects_the_finished_thumbnail_without_identity_references(self):
        Image.new("RGB", (1080, 1920), "#355d83").save(self.scene)
        check = gaze_check()
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(check)}) as create:
            result = agent.validate_camera_facing(self.scene, expected_people=2)
        self.assertEqual(result, check)
        payload = create.call_args.args[0]
        self.assertNotIn("tools", payload)
        images = [part for message in payload["input"] if isinstance(message["content"], list) for part in message["content"] if part.get("type") == "input_image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["detail"], "high")
        self.assertEqual(base64.b64decode(images[0]["image_url"].split(",", 1)[1]), self.scene.read_bytes())
        for reference in self.references:
            self.assertNotIn(base64.b64encode(reference.read_bytes()).decode(), str(payload))

    def test_every_final_brand_option_is_audited_and_records_verified_metadata(self):
        def audit_final(path, expected_people=0):
            self.assertEqual(Path(path).parent, agent.WORK_DIR, "Audit candidates before publishing them")
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 1920))
                self.assertEqual(image.format, "PNG")
            return gaze_check()

        for slug in agent.CLIENTS:
            for references in ([], self.references):
                with self.subTest(client=slug, reference_count=len(references)):
                    with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes) as edit, mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate, mock.patch.object(agent, "validate_camera_facing", side_effect=audit_final) as audit:
                        result = agent.handle_create(self.script, "MY JOURNEY | BEGINS", client_slug=slug, extra_prompt=self.extra, person_reference_paths=references)
                    variants = result.get("thumbnails") or [result]
                    count = 2 if slug in agent.TWO_THUMBNAIL_CLIENTS else 1
                    self.assertEqual(len(variants), count)
                    self.assertEqual(audit.call_count, count)
                    self.assertTrue(all(call.kwargs.get("expected_people", call.args[1] if len(call.args) > 1 else 0) == len(references) for call in audit.call_args_list))
                    self.assertEqual(edit.call_count, count if references else 0)
                    self.assertEqual(generate.call_count, 0 if references else count)
                    for item in [result, *variants]:
                        self.assertIs(item["camera_facing_verified"], True)
                        self.assertEqual(item["camera_facing_check"], gaze_check())
                    for item in variants:
                        output = Path(item["thumbnail"])
                        self.assertTrue(output.is_file())
                        metadata = json.loads(output.with_suffix(".json").read_text())
                        self.assertIs(metadata["camera_facing_verified"], True)
                        self.assertEqual(metadata["camera_facing_check"], gaze_check())

    def test_failed_gaze_regenerates_once_with_the_original_people_and_rechecks_the_render(self):
        bad = gaze_check(all_eyes_on_camera=False, reason="Both people are looking down at the book.")
        for references in ([], self.references):
            with self.subTest(reference_count=len(references)):
                with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes) as edit, mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate, mock.patch.object(agent, "validate_camera_facing", side_effect=[bad, gaze_check()]) as audit:
                    result = agent.handle_create(self.script, "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=references)
                self.assertEqual(audit.call_count, 2)
                generator = edit if references else generate
                self.assertEqual(generator.call_count, 2)
                for call in generator.call_args_list:
                    self.assert_camera_rule(call.args[0])
                    self.assertTrue(call.args[0].rstrip().endswith(agent.camera_facing_prompt()))
                    if references:
                        self.assertEqual(call.args[1], references)
                (generate if references else edit).assert_not_called()
                self.assertIs(result["camera_facing_verified"], True)
                self.assertEqual(result["camera_facing_check"], gaze_check())

    def test_two_failed_gaze_checks_never_publish_a_thumbnail(self):
        with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes) as edit, mock.patch.object(agent, "openai_images_generate") as generate, mock.patch.object(agent, "validate_camera_facing", return_value=gaze_check(all_faces_front_facing=False)) as audit:
            with self.assertRaises(RuntimeError):
                agent.handle_create(self.script, "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=self.references)
        self.assertEqual(edit.call_count, 2)
        self.assertEqual(audit.call_count, 2)
        generate.assert_not_called()
        self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])

    def test_unavailable_gaze_audit_never_publishes_an_unverified_candidate(self):
        with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes), mock.patch.object(agent, "validate_camera_facing", side_effect=RuntimeError("Camera verification unavailable")):
            with self.assertRaises(RuntimeError):
                agent.handle_create(self.script, "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=self.references)
        self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])

    def test_malformed_gaze_results_never_publish_even_when_fields_are_truthy(self):
        for check in (None, {}, gaze_check(all_eyes_on_camera="true"), gaze_check(visible_people_count=True)):
            with self.subTest(check=check):
                with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes), mock.patch.object(agent, "validate_camera_facing", return_value=check):
                    with self.assertRaises(RuntimeError):
                        agent.handle_create(self.script, "MY JOURNEY | BEGINS", client_slug="alliance-latin", person_reference_paths=self.references)
                self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])

    def test_a_scene_without_people_is_allowed_when_no_people_were_referenced(self):
        no_people = gaze_check(people_present=False, visible_people_count=0, all_faces_front_facing=False, all_eyes_on_camera=False, all_faces_unobstructed=False)
        with mock.patch.object(agent, "create_visual_brief", return_value={**self.brief, "visual_prompt": "A delivery robot crosses an empty university courtyard."}), mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes), mock.patch.object(agent, "validate_camera_facing", return_value=no_people):
            result = agent.handle_create("A delivery robot arrives without human characters.", "CAMPUS | ROBOT", client_slug="alliance-latin")
        self.assertIs(result["camera_facing_verified"], True)
        self.assertEqual(result["camera_facing_check"], no_people)

    def test_a_provided_subject_is_rejected_without_replacing_it_when_gaze_fails(self):
        with mock.patch.object(agent, "create_visual_brief", return_value=self.brief), mock.patch.object(agent, "generate_subject_image") as generate, mock.patch.object(agent, "validate_camera_facing", return_value=gaze_check(all_faces_unobstructed=False)) as audit:
            with self.assertRaises(RuntimeError):
                agent.handle_create(self.script, "MY JOURNEY | BEGINS", subject_path=self.scene, client_slug="alliance-latin")
        generate.assert_not_called()
        audit.assert_called_once()
        self.assertEqual(list(agent.GENERATED_DIR.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
