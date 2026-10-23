import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

from PIL import Image

import gemini_client as gemini


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ok", "count", "notes"],
    "properties": {"ok": {"type": "boolean"}, "count": {"type": "integer"}, "notes": {"type": "string"}},
}
RESULT = {"ok": True, "count": 2, "notes": "Offline synthetic-image review."}


def response_bytes(result=RESULT, finish="STOP", **extra):
    return json.dumps({"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(result)}]}}], **extra}).encode()


class GeminiClientTests(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "offline-gemini-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.images = []
        for index, color in enumerate(("#dd2233", "#22bb55", "#3366cc", "#bbaa33", "#7744aa")):
            path = self.root / f"synthetic-{index}.png"
            Image.new("RGB", (80 + index, 120), color).save(path)
            self.images.append(path)
        network = mock.patch.object(gemini, "_open_request", side_effect=AssertionError("Unexpected Gemini network request in offline test"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def respond(self, body=None):
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(response_bytes() if body is None else body)

    def payload(self):
        request = self.network.call_args.args[0]
        return request, json.loads(request.data)

    def inline(self, path):
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()

    def test_independent_stable_defaults_and_trimmed_model_overrides(self):
        self.assertEqual(gemini.model(), "gemini-3.5-flash-lite")
        self.assertEqual(gemini.model(review=True), "gemini-3.8-flash")
        with mock.patch.dict(os.environ, {"GEMINI_MODEL": " gemini-3.6-flash ", "GEMINI_REVIEW_MODEL": "  "}):
            self.assertEqual(gemini.model(), "gemini-3.6-flash")
            self.assertEqual(gemini.model(review=True), "gemini-3.8-flash")
        with mock.patch.dict(os.environ, {"GEMINI_REVIEW_MODEL": " gemini-3.8-flash-custom "}):
            self.assertEqual(gemini.model(review=True), "gemini-3.8-flash-custom")
            self.assertEqual(gemini.model(), "gemini-3.5-flash-lite")
        for invalid in ("https://evil.test/model", "gemini-a?key=private", "../model", "models/gemini-3.8-flash"):
            with self.subTest(invalid=invalid), mock.patch.dict(os.environ, {"GEMINI_MODEL": invalid}):
                with self.assertRaises(ValueError):
                    gemini.model()
        self.network.assert_not_called()

    def test_missing_key_is_local_and_does_not_use_other_provider_credentials(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "different-dummy-key", "GOOGLE_API_KEY": "unrelated-google-key"}, clear=True):
            self.assertFalse(gemini.configured())
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                gemini.generate_json("Review", "A synthetic scene")
        self.assertTrue(gemini.configured())
        for bad_key in ("private-key\ninvalid-header", "private-key\rinvalid-header", "private-key\tinvalid-header", "private-key\x7finvalid-header", "private-key\n", "\rprivate-key"):
            with self.subTest(invalid_key_control=True), mock.patch.dict(os.environ, {"GEMINI_API_KEY": bad_key}):
                self.assertFalse(gemini.configured())
                with self.assertRaises(RuntimeError) as error:
                    gemini.generate_json("Review", "Context")
                self.assertNotIn("private-key", str(error.exception))
                self.assertNotIn("invalid-header", str(error.exception))
        self.network.assert_not_called()

    def test_ordered_originals_and_finished_image_preserve_labels_and_header_only_auth(self):
        self.respond()
        content = []
        labels = [f"Original reference photo {index}." for index in range(1, 5)] + ["Finished thumbnail edit to review."]
        before = [path.read_bytes() for path in self.images]
        for label, path in zip(labels, self.images):
            content.extend([{"type": "input_text", "text": label}, {"type": "input_image", "image_url": self.inline(path), "detail": "high"}])
        self.assertEqual(gemini.generate_json("Compare only visible features.", content=content, schema=SCHEMA, model_name=gemini.model(review=True)), RESULT)
        request, payload = self.payload()
        self.assertEqual(request.full_url, gemini.API_BASE + "/gemini-3.8-flash:generateContent")
        self.assertNotIn("offline-gemini-key", request.full_url)
        self.assertNotIn("offline-gemini-key", request.data.decode())
        self.assertEqual(dict((name.lower(), value) for name, value in request.header_items())["x-goog-api-key"], "offline-gemini-key")
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "Compare only visible features."}]})
        self.assertEqual(payload["generationConfig"]["responseJsonSchema"], SCHEMA)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertNotIn("tools", payload)
        parts = payload["contents"][0]["parts"]
        self.assertEqual([part["text"] for part in parts if "text" in part], labels)
        self.assertEqual(len(parts), 10)
        for index, part in enumerate(parts[1::2]):
            image = Image.open(io.BytesIO(base64.b64decode(part["inlineData"]["data"])))
            expected = Image.open(io.BytesIO(before[index])).getpixel((20, 20))
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(image.getpixel((20, 20)), expected)))
            self.assertEqual(part["inlineData"]["mimeType"], "image/jpeg")
        self.assertEqual([path.read_bytes() for path in self.images], before)
        self.assertEqual(self.network.call_count, 1)

    def test_labeled_path_interface_and_exif_orientation_strip_metadata_without_crop(self):
        source = self.root / "orientation.jpeg"
        image = Image.new("RGB", (50, 30), "#dd3355")
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "private photographer metadata"
        image.save(source, exif=exif)
        self.respond()
        gemini.generate_json("Review", "User context", image_paths=[("Original profile reference.", source)], schema=SCHEMA)
        parts = self.payload()[1]["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "User context"})
        self.assertEqual(parts[1], {"text": "Original profile reference."})
        normalized = Image.open(io.BytesIO(base64.b64decode(parts[2]["inlineData"]["data"])))
        self.assertEqual(normalized.size, (30, 50))
        self.assertEqual(dict(normalized.getexif()), {})

    def test_large_image_is_resized_proportionally_and_existing_alpha_keeps_png(self):
        source = self.root / "large.png"
        Image.new("RGB", (4096, 2048), "#447788").save(source)
        transparent = self.root / "alpha.png"
        exif = Image.Exif()
        exif[315] = "private photographer metadata"
        Image.new("RGBA", (100, 150), (22, 77, 122, 128)).save(transparent, exif=exif)
        self.respond()
        gemini.generate_json("Review", "Context", image_paths=[source, transparent])
        images = [part["inlineData"] for part in self.payload()[1]["contents"][0]["parts"] if "inlineData" in part]
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(images[0]["data"]))).size, (2048, 1024))
        self.assertEqual(images[1]["mimeType"], "image/png")
        alpha = Image.open(io.BytesIO(base64.b64decode(images[1]["data"])))
        self.assertEqual(alpha.size, (100, 150))
        self.assertEqual(alpha.getpixel((10, 10)), (22, 77, 122, 128))
        self.assertEqual(dict(alpha.getexif()), {})

    def test_invalid_images_remote_urls_mixed_interfaces_and_six_images_fail_before_network(self):
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        invalid = self.root / "invalid.png"
        invalid.write_text("not an image")
        cases = [
            {"image_paths": [empty]}, {"image_paths": [invalid]},
            {"image_paths": [self.root / "missing.png"]},
            {"image_paths": [self.images[0]] * 6},
            {"content": [{"type": "input_image", "image_url": "https://evil.test/private.png"}]},
            {"content": [{"type": "input_image", "image_url": "data:image/png;base64,%%%"}]},
            {"content": [{"type": "input_audio", "data": "unknown"}]},
            {"content": [], "image_paths": self.images},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValueError):
                    gemini.generate_json("Review", "Context", **kwargs)
        self.network.assert_not_called()

    def test_total_encoded_json_request_limit_is_checked_before_network(self):
        self.assertEqual(gemini.MAX_REQUEST_BYTES, 20_000_000)
        with mock.patch.object(gemini, "MAX_REQUEST_BYTES", 1000):
            with self.assertRaisesRegex(ValueError, "20MB"):
                gemini.generate_json("Review", "私" * 500, image_paths=[self.images[0]])
        self.network.assert_not_called()

    def test_schema_rejects_wrong_flags_integer_booleans_missing_and_extra_fields(self):
        for result in ({**RESULT, "ok": "true"}, {**RESULT, "count": True}, {"ok": True, "count": 2}, {**RESULT, "extra": True}):
            with self.subTest(result=result):
                self.respond(response_bytes(result))
                with self.assertRaisesRegex(RuntimeError, "invalid JSON review"):
                    gemini.generate_json("Review", "Context", schema=SCHEMA)

    def test_safety_refusal_truncation_and_malformed_response_fail_closed(self):
        invalid = [
            b"not JSON", b"[]", b"{}", response_bytes(finish="SAFETY"), response_bytes(finish="MAX_TOKENS"),
            response_bytes(finish="RECITATION"), response_bytes(promptFeedback={"blockReason": "SAFETY"}),
            response_bytes(promptFeedback={"safetyRatings": [{"blocked": True}]}),
            json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "```json\n{}\n```"}]}}]}).encode(),
            json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true,"ok":false}'}]}}]}).encode(),
            json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"value":NaN}'}]}}]}).encode(),
            json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"value":1e9999}'}]}}]}).encode(),
            json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"functionCall": {"name": "unexpected"}}]}}]}).encode(),
            response_bytes([]),
        ]
        for body in invalid:
            with self.subTest(body=body[:80]):
                self.respond(body)
                with self.assertRaises(RuntimeError):
                    gemini.generate_json("Review", "Private context", schema=SCHEMA)

    def test_non_thought_text_parts_are_combined_and_thoughts_do_not_become_output(self):
        self.respond(json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "internal reasoning that is not JSON", "thought": True},
            {"text": '{"ok":true,"count":'}, {"text": '2,"notes":"complete"}'},
        ]}}]}).encode())
        self.assertEqual(gemini.generate_json("Review", "Context", schema=SCHEMA), {"ok": True, "count": 2, "notes": "complete"})

    def test_provider_errors_never_echo_credentials_private_prompts_or_bodies_and_never_retry(self):
        secret_body = b'offline-gemini-key PRIVATE-PROMPT data:image/png;base64,PRIVATEPHOTO'
        for status, expected in ((400, "settings"), (401, "authorize"), (403, "authorize"), (404, "model"), (429, "quota"), (503, "temporarily"), (302, "settings")):
            with self.subTest(status=status):
                self.network.reset_mock()
                self.network.side_effect = urllib.error.HTTPError("https://provider.test", status, secret_body.decode(), {}, io.BytesIO(secret_body))
                with self.assertRaisesRegex(RuntimeError, expected) as error:
                    gemini.generate_json("Review PRIVATE-PROMPT", "Context")
                message = str(error.exception)
                self.assertIn(f"HTTP {status}", message)
                for secret in ("offline-gemini-key", "PRIVATE-PROMPT", "PRIVATEPHOTO", "provider.test"):
                    self.assertNotIn(secret, message)
                self.assertEqual(self.network.call_count, 1)

    def test_timeout_is_positive_finite_capped_and_network_errors_are_safe(self):
        for timeout in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                gemini.generate_json("Review", "Context", timeout=timeout)
        self.network.assert_not_called()
        self.respond()
        gemini.generate_json("Review", "Context", timeout=900)
        self.assertGreater(self.network.call_args.args[1], 0)
        self.assertLessEqual(self.network.call_args.args[1], 180)
        for failure in (TimeoutError("offline-gemini-key"), urllib.error.URLError("PRIVATE-PROMPT offline-gemini-key")):
            self.network.side_effect = failure
            with self.assertRaises(RuntimeError) as error:
                gemini.generate_json("Review", "Context")
            self.assertNotIn("offline-gemini-key", str(error.exception))
            self.assertNotIn("PRIVATE-PROMPT", str(error.exception))

    def test_preprocessing_deadline_and_oversized_response_are_bounded(self):
        with mock.patch.object(gemini.time, "monotonic", side_effect=[100, 102]):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                gemini.generate_json("Review", "Context", timeout=1)
        self.network.assert_not_called()
        self.respond(b"x" * 101)
        with mock.patch.object(gemini, "MAX_RESPONSE_BYTES", 100):
            with self.assertRaisesRegex(RuntimeError, "oversized"):
                gemini.generate_json("Review", "Context")

    def test_redirect_handler_refuses_to_forward_api_key_or_images(self):
        request = gemini.urllib.request.Request("https://generativelanguage.googleapis.com", data=b"private-image", headers={"x-goog-api-key": "offline-gemini-key"})
        self.assertIsNone(gemini._NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.test"))


if __name__ == "__main__":
    unittest.main()
