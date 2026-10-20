import base64
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
    import thumbnail_agent as agent


def image_bytes(size=(192, 256), mode="RGB"):
    output = io.BytesIO()
    Image.new(mode, size, "#457b9d").save(output, "PNG")
    return output.getvalue()


def json_response(payload):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def multipart_parts(request):
    headers = f"Content-Type: {request.get_header('Content-type')}\r\nMIME-Version: 1.0\r\n\r\n"
    message = BytesParser(policy=policy.default).parsebytes(headers.encode("ascii") + request.data)
    return {
        part.get_param("name", header="content-disposition"): part
        for part in message.iter_parts()
    }


class PersonReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reference = self.root / "reference.png"
        self.reference_bytes = image_bytes()
        self.reference.write_bytes(self.reference_bytes)
        self.generated_bytes = image_bytes((512, 768))
        self.brief = {
            "visual_prompt": "Place the people from the reference in a cinematic rescue scene.",
            "negative_prompt": "text, watermarks",
            "rationale": "Show the script's rescue scene.",
        }
        environment = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        paths = mock.patch.multiple(
            agent,
            ASSETS_DIR=self.root / "assets",
            STATE_DIR=self.root / "state",
            GENERATED_DIR=self.root / "generated",
            WORK_DIR=self.root / "work",
        )
        paths.start()
        self.addCleanup(paths.stop)
        agent.ensure_dirs()
        network = mock.patch.object(
            agent.urllib.request,
            "urlopen",
            side_effect=AssertionError("Unexpected network request in offline tests"),
        )
        self.network = network.start()
        self.addCleanup(network.stop)

    def mock_image_response(self):
        self.network.side_effect = None
        self.network.return_value = json_response({
            "data": [{"b64_json": base64.b64encode(self.generated_bytes).decode("ascii")}]
        })

    def test_reference_model_configuration_precedence(self):
        self.assertEqual(agent.openai_reference_image_model(), "gpt-image-1")
        os.environ["OPENAI_IMAGE_MODEL"] = "gpt-image-1.5"
        self.assertEqual(agent.openai_reference_image_model(), "gpt-image-1.5")
        os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = " gpt-image-2 "
        self.assertEqual(agent.openai_reference_image_model(), "gpt-image-2")
        os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = " "
        self.assertEqual(agent.openai_reference_image_model(), "gpt-image-1.5")

    def test_edit_request_keeps_image_bytes_and_requests_high_fidelity(self):
        for model in ("gpt-image-1", "gpt-image-1.5"):
            with self.subTest(model=model):
                os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = model
                self.mock_image_response()
                result = agent.openai_images_edit("Preserve these faces. Café scene.", self.reference)
                request = self.network.call_args.args[0]
                parts = multipart_parts(request)
                self.assertEqual(request.full_url, "https://api.openai.com/v1/images/edits")
                self.assertEqual(request.method, "POST")
                self.assertEqual(parts["model"].get_payload(decode=True).decode(), model)
                self.assertEqual(parts["input_fidelity"].get_payload(decode=True), b"high")
                self.assertEqual(parts["size"].get_payload(decode=True), b"1024x1536")
                self.assertEqual(parts["quality"].get_payload(decode=True), b"medium")
                self.assertEqual(parts["prompt"].get_payload(decode=True).decode(), "Preserve these faces. Café scene.")
                image_part = parts.get("image") or parts.get("image[]")
                self.assertIsNotNone(image_part)
                self.assertEqual(image_part.get_content_type(), "image/png")
                self.assertEqual(image_part.get_payload(decode=True), self.reference_bytes)
                self.assertNotIn("response_format", parts)
                self.assertEqual(result, self.generated_bytes)

    def test_newer_models_omit_unsupported_fidelity_parameter(self):
        for model in ("gpt-image-2", "gpt-image-2-2026-04-21", "gpt-image-2.5-sunburst"):
            with self.subTest(model=model):
                os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = model
                self.mock_image_response()
                agent.openai_images_edit("Preserve these faces.", self.reference)
                parts = multipart_parts(self.network.call_args.args[0])
                self.assertNotIn("input_fidelity", parts)
                self.assertEqual(parts["model"].get_payload(decode=True).decode(), model)

    def test_low_fidelity_only_model_is_rejected_before_network(self):
        os.environ["OPENAI_REFERENCE_IMAGE_MODEL"] = "gpt-image-1-mini"
        with self.assertRaises(RuntimeError):
            agent.openai_images_edit("Preserve these faces.", self.reference)
        self.network.assert_not_called()

    def test_reference_preflight_rejects_missing_empty_and_invalid_files(self):
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        invalid = self.root / "invalid.png"
        invalid.write_bytes(b"this is not an image")
        for reference in (self.root / "missing.png", empty, invalid):
            with self.subTest(reference=reference.name):
                with self.assertRaises(RuntimeError):
                    agent.handle_create(
                        "Rescue scene.", "RESCUE | TEAM", client_slug="alliance-latin",
                        person_reference_path=reference,
                    )
        self.network.assert_not_called()

    def test_edit_api_errors_and_missing_image_data_are_reported(self):
        self.network.side_effect = urllib.error.HTTPError(
            "https://api.openai.com/v1/images/edits", 429, "rate limited", {},
            io.BytesIO(b'{"error":{"message":"Reference edit rate limit reached"}}'),
        )
        with self.assertRaisesRegex(RuntimeError, "Reference edit rate limit reached"):
            agent.openai_images_edit("Preserve these faces.", self.reference)
        self.network.side_effect = None
        self.network.return_value = json_response({"data": []})
        with self.assertRaisesRegex(RuntimeError, "did not return generated image data"):
            agent.openai_images_edit("Preserve these faces.", self.reference)

    def test_brief_receives_the_reference_in_addition_to_the_script(self):
        with mock.patch.object(agent, "openai_responses_create", return_value={"output_text": json.dumps(self.brief)}) as create:
            agent.create_visual_brief(
                "The people evacuate a building.",
                "EVERY SECOND | COUNTS",
                agent.CLIENTS["zoomex"],
                has_person_reference=True,
                extra_prompt="Make them look concerned and wear rescue jackets.",
                person_reference_path=self.reference,
            )
        payload = create.call_args.args[0]
        content = payload["input"][1]["content"]
        images = [part for part in content if part["type"] == "input_image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["detail"], "high")
        self.assertEqual(base64.b64decode(images[0]["image_url"].split(",", 1)[1]), self.reference_bytes)
        text = " ".join(part.get("text", "") for part in content)
        self.assertIn("The people evacuate a building.", text)
        self.assertIn("rescue jackets", text)
        self.assertNotIn("use a fictional or generic person", str(payload))

    def test_reference_generation_never_falls_back_to_a_new_person(self):
        errors = [
            TimeoutError("timed out"),
            RuntimeError("OpenAI image edit failed"),
            *[
                urllib.error.HTTPError("https://api.openai.com/v1/images/edits", code, "error", {}, io.BytesIO(b'{}'))
                for code in (400, 401, 429, 500)
            ],
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__, code=getattr(error, "code", None)):
                with mock.patch.object(agent, "openai_images_edit", side_effect=error), \
                     mock.patch.object(agent, "openai_images_generate") as generate, \
                     mock.patch.object(agent, "openai_responses_create") as responses:
                    with self.assertRaises(Exception):
                        agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"], self.reference)
                    generate.assert_not_called()
                    responses.assert_not_called()

    def test_reference_prompt_preserves_identity_without_fictional_substitution(self):
        with mock.patch.object(agent, "openai_images_edit", return_value=self.generated_bytes) as edit:
            agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"], self.reference)
        prompt = edit.call_args.args[0].lower()
        self.assertNotIn("identity guidance only", prompt)
        self.assertNotIn("use a fictional person", prompt)
        self.assertIn("facial", prompt)
        self.assertIn("expression", prompt)
        self.assertTrue("clothing" in prompt or "wardrobe" in prompt)
        self.assertTrue("every" in prompt or "all people" in prompt)

    def test_non_reference_generation_and_existing_fallback_still_work(self):
        with mock.patch.object(agent, "openai_images_generate", return_value=self.generated_bytes) as generate, \
             mock.patch.object(agent, "openai_images_edit") as edit:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"])
            self.assertEqual(output.read_bytes(), self.generated_bytes)
            generate.assert_called_once()
            edit.assert_not_called()
        response = {"output": [{"type": "image_generation_call", "result": base64.b64encode(self.generated_bytes).decode()}]}
        with mock.patch.object(agent, "openai_images_generate", side_effect=RuntimeError("temporary failure")), \
             mock.patch.object(agent, "openai_responses_create", return_value=response) as responses:
            output = agent.generate_subject_image(self.brief, agent.CLIENTS["zoomex"])
            self.assertEqual(output.read_bytes(), self.generated_bytes)
            payload = responses.call_args.args[0]
            self.assertFalse(any(part["type"] == "input_image" for part in payload["input"][0]["content"]))

    def test_normalization_preserves_pixels_without_jpeg_recompression(self):
        normalized = agent.normalize_reference_image(self.reference)
        with Image.open(self.reference) as original, Image.open(normalized) as result:
            self.assertEqual(result.format, "PNG")
            self.assertEqual(result.size, original.size)
            self.assertEqual(result.convert("RGB").tobytes(), original.convert("RGB").tobytes())

    def test_an_empty_selected_reference_is_not_silently_ignored(self):
        field = SimpleNamespace(filename="portrait.png", file=io.BytesIO(b""))
        with self.assertRaises(RuntimeError):
            agent.save_upload({"person_reference": field}, "person_reference")
        self.assertIsNone(agent.save_upload({}, "person_reference"))

    def test_reference_pipeline_renders_expected_results_for_every_brand(self):
        def api_response(request, timeout=None):
            if request.full_url == agent.OPENAI_ENDPOINT:
                payload = json.loads(request.data)
                user_content = payload["input"][1]["content"]
                self.assertTrue(any(part["type"] == "input_image" for part in user_content))
                return json_response({"output_text": json.dumps(self.brief)})
            if request.full_url == agent.OPENAI_IMAGES_EDIT_ENDPOINT:
                parts = multipart_parts(request)
                self.assertEqual(parts["input_fidelity"].get_payload(decode=True), b"high")
                return json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
            self.fail(f"Unexpected API endpoint: {request.full_url}")

        self.network.side_effect = api_response
        for slug in agent.CLIENTS:
            with self.subTest(client=slug):
                result = agent.handle_create(
                    "The people evacuate a building.",
                    "EVERY SECOND | COUNTS",
                    client_slug=slug,
                    person_reference_path=self.reference,
                    extra_prompt="Concerned expressions, rescue jackets.",
                )
                variants = result.get("thumbnails") or [result]
                self.assertEqual(len(variants), 2 if slug in agent.TWO_THUMBNAIL_CLIENTS else 1)
                for variant in variants:
                    self.assertTrue(variant["person_reference_used"])
                    self.assertEqual(variant["reference_strategy"], "images_edit")
                    self.assertEqual(variant["reference_image_model"], "gpt-image-1")
                    self.assertEqual(variant["reference_input_fidelity"], "high")
                    output = Path(variant["thumbnail"])
                    self.assertTrue(output.with_suffix(".json").is_file())
                    with Image.open(output) as rendered:
                        self.assertEqual(rendered.size, (1080, 1920))

    def test_http_upload_background_job_and_download_keep_reference_settings(self):
        def api_response(request, timeout=None):
            if request.full_url == agent.OPENAI_ENDPOINT:
                return json_response({"output_text": json.dumps(self.brief)})
            if request.full_url == agent.OPENAI_IMAGES_EDIT_ENDPOINT:
                parts = multipart_parts(request)
                image_part = parts.get("image") or parts.get("image[]")
                with Image.open(io.BytesIO(image_part.get_payload(decode=True))) as uploaded:
                    self.assertEqual(uploaded.format, "PNG")
                    self.assertEqual(uploaded.size, (192, 256))
                return json_response({"data": [{"b64_json": base64.b64encode(self.generated_bytes).decode()}]})
            self.fail(f"Unexpected API endpoint: {request.full_url}")

        self.network.side_effect = api_response
        with mock.patch.object(agent, "JOBS", {}), \
             mock.patch.object(agent.ThumbnailHandler, "log_message"):
            server = agent.ThreadingHTTPServer(("127.0.0.1", 0), agent.ThumbnailHandler)
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
            thread.start()
            try:
                boundary = "test-person-reference-boundary"
                body = bytearray()
                for name, value in {
                    "client": "alliance-latin", "title": "RESCUE | TEAM",
                    "script": "The pictured people lead a rescue.",
                }.items():
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

                status, _, payload = request("POST", "/api/create", bytes(body), {
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                })
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
                self.assertEqual(result["reference_strategy"], "images_edit")
                self.assertEqual(result["reference_image_model"], "gpt-image-1")
                self.assertEqual(result["reference_input_fidelity"], "high")
                self.assertTrue(result["person_reference_used"])
                self.assertEqual(len(result["thumbnails"]), 1)
                status, headers, png = request("GET", result["download_url"])
                self.assertEqual(status, 200)
                self.assertIn("attachment", headers["Content-Disposition"])
                with Image.open(io.BytesIO(png)) as thumbnail:
                    self.assertEqual(thumbnail.size, (1080, 1920))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
