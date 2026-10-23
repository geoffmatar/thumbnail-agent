import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PIL import Image

import higgsfield_client as client


REQUEST_ID = "3c90c3cc-0d44-4b50-8888-8dd25736052a"
STATUS_URL = f"https://api.higgsfield.ai/requests/{REQUEST_ID}/status"
IMAGE_URL = "https://cdn.example.com/output.png"


class Response:
    def __init__(self, value, headers=None):
        self.data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.data[:size]


class HiggsfieldClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.clock = 100.0
        self.requests = []
        self.fixture = io.BytesIO()
        Image.new("RGB", (90, 160), "#cd8460").save(self.fixture, "PNG")
        self.png = self.fixture.getvalue()
        patches = [
            mock.patch.dict(os.environ, {"HF_API_KEY_ID": "offline-id", "HF_API_KEY_SECRET": "offline-secret"}, clear=True),
            mock.patch.object(client._OPENER, "open", side_effect=AssertionError("Unexpected outbound HTTP request")),
            mock.patch.object(client.socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]),
            mock.patch.object(client.time, "monotonic", side_effect=lambda: self.clock),
            mock.patch.object(client.time, "sleep", side_effect=self.advance),
            mock.patch.object(client.random, "uniform", return_value=0),
        ]
        self.environment, self.network, self.dns, *_ = [patch.start() for patch in patches]
        for patch in patches:
            self.addCleanup(patch.stop)

    def advance(self, seconds):
        self.clock += seconds

    def serve(self, responses):
        responses = list(responses)

        def open_request(request, timeout):
            self.requests.append(request)
            self.assertGreater(timeout, 0)
            if not responses:
                self.fail("Unexpected extra HTTP request")
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return Response(result)

        self.network.side_effect = open_request
        return responses

    def receipt(self, **changes):
        return {"request_id": REQUEST_ID, "status_url": STATUS_URL, "status": "queued", **changes}

    def completed(self, **changes):
        return {"request_id": REQUEST_ID, "status": "completed", "images": [{"url": IMAGE_URL}], **changes}

    def error(self, status):
        return urllib.error.HTTPError(STATUS_URL, status, "offline-secret private prompt", {}, io.BytesIO(b'private prompt offline-secret'))

    def photo(self, index=0):
        path = self.root / f"reference-{index}.png"
        Image.new("RGB", (32 + index, 64), (20 + index, 100, 160)).save(path)
        return path

    def generation_posts(self):
        return [request for request in self.requests if request.get_method() == "POST" and request.full_url.endswith(client.DEFAULT_MODEL)]

    def test_configuration_uses_separate_server_credentials_and_verified_model(self):
        self.assertTrue(client.configured())
        self.assertEqual(client.image_model(False), client.DEFAULT_MODEL)
        self.assertEqual(client.image_model(True), client.DEFAULT_MODEL)
        self.assertEqual(client.image_quality(), "high")
        self.assertEqual(client.image_size(), "9:16 / 2K")
        with mock.patch.dict(os.environ, {"HF_IMAGE_MODEL": "unverified/model"}):
            with self.assertRaisesRegex(client.HiggsfieldError, "not supported"):
                client.generate_image("scene")
        with mock.patch.dict(os.environ, {"HF_API_KEY_SECRET": ""}):
            self.assertFalse(client.configured())
            with self.assertRaisesRegex(client.HiggsfieldError, "HF_API_KEY_SECRET"):
                client.generate_image("scene")
            with self.assertRaises(client.HiggsfieldError):
                client.estimate_image("scene", [IMAGE_URL])
        self.network.assert_not_called()
        self.dns.assert_not_called()

    def test_text_generation_uses_explicit_sunburst_settings_and_private_auth_only_on_api(self):
        self.serve([self.receipt(), self.completed(), self.png])
        data = client.generate_image("private visual scene")
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (90, 160))
            self.assertEqual(image.format, "PNG")
        self.assertEqual([request.get_method() for request in self.requests], ["POST", "GET", "GET"])
        submit, poll, download = self.requests
        self.assertEqual(submit.full_url, client.API_BASE + "/" + client.DEFAULT_MODEL)
        self.assertEqual(json.loads(submit.data), {"prompt": "private visual scene", "quality": "high", "resolution": "2k", "aspect_ratio": "9:16", "enhance_prompt": False})
        self.assertEqual(submit.get_header("Authorization"), "Key offline-id:offline-secret")
        self.assertEqual(poll.get_header("Authorization"), "Key offline-id:offline-secret")
        self.assertIsNone(download.get_header("Authorization"))
        self.assertTrue(submit.get_header("Idempotency-key"))
        self.assertEqual(len(self.generation_posts()), 1)

    def test_four_ordered_references_signed_upload_headers_and_no_credential_leak(self):
        responses = []
        for index in range(4):
            responses += [{"upload_url": f"https://uploads.example.com/{index}?signed=opaque", "public_url": f"https://cdn.example.com/reference-{index}.png", "upload_headers": {"Content-Type": "image/png", "x-upload-token": f"signed-{index}"}}, b""]
        responses += [self.receipt(), self.completed(), self.png]
        self.serve(responses)
        client.generate_image("compose these people", [self.photo(index) for index in range(4)])
        submit = self.generation_posts()[0]
        self.assertEqual(json.loads(submit.data)["image_urls"], [f"https://cdn.example.com/reference-{index}.png" for index in range(4)])
        puts = [request for request in self.requests if request.get_method() == "PUT"]
        self.assertEqual(len(puts), 4)
        for index, request in enumerate(puts):
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(request.get_header("X-upload-token"), f"signed-{index}")
            with Image.open(io.BytesIO(request.data)) as photo:
                self.assertEqual(photo.getpixel((0, 0)), (20 + index, 100, 160, 255))
        for request in self.requests:
            if not request.full_url.startswith(client.API_BASE + "/"):
                self.assertIsNone(request.get_header("Authorization"))

    def test_all_references_preflight_before_any_upload_and_at_most_four(self):
        for references in ([self.photo(), self.root / "missing.png"], [self.photo()] * 5):
            with self.subTest(references=len(references)):
                with self.assertRaises(client.HiggsfieldError):
                    client.generate_image("scene", references)
        self.network.assert_not_called()

    def test_ambiguous_generation_timeout_or_503_never_resubmits(self):
        for error in (urllib.error.URLError("private prompt offline-secret"), self.error(503)):
            self.requests.clear()
            self.serve([error])
            with self.subTest(error=type(error).__name__), self.assertRaises(client.HiggsfieldError) as caught:
                client.generate_image("private prompt")
            self.assertTrue(caught.exception.ambiguous_submission)
            self.assertTrue(caught.exception.idempotency_key)
            self.assertNotIn("offline-secret", str(caught.exception))
            self.assertNotIn("private prompt", str(caught.exception))
            self.assertEqual(len(self.generation_posts()), 1)

    def test_poll_retries_only_gets_and_keeps_original_submission(self):
        self.serve([self.receipt(), self.error(429), urllib.error.URLError("unavailable"), self.error(500), self.completed(), self.png])
        client.generate_image("scene")
        self.assertEqual(len(self.generation_posts()), 1)
        self.assertEqual([request.full_url for request in self.requests[1:-1]], [STATUS_URL] * 4)

    def test_poll_timeout_is_bounded_without_a_second_generation(self):
        self.serve([self.receipt(), self.error(500)])
        with self.assertRaisesRegex(client.HiggsfieldError, "Timed out") as caught:
            client.generate_image("scene", timeout=3)
        self.assertEqual(caught.exception.request_id, REQUEST_ID)
        self.assertEqual(len(self.generation_posts()), 1)
        self.assertEqual(self.clock, 103)

    def test_terminal_errors_are_safe_and_do_not_download_or_regenerate(self):
        for status in ("failed", "nsfw", "canceled"):
            self.requests.clear()
            self.serve([self.receipt(), {"request_id": REQUEST_ID, "status": status, "error": "offline-secret private prompt"}])
            with self.subTest(status=status), self.assertRaises(client.HiggsfieldError) as caught:
                client.generate_image("private prompt")
            self.assertNotIn("offline-secret", str(caught.exception))
            self.assertNotIn("private prompt", str(caught.exception))
            self.assertEqual(len(self.requests), 2)

    def test_authentication_or_permission_failure_does_not_retry_or_echo_body(self):
        for status in (401, 403):
            self.requests.clear()
            self.serve([self.error(status)])
            with self.subTest(status=status), self.assertRaises(client.HiggsfieldError) as caught:
                client.generate_image("private prompt")
            self.assertEqual(len(self.requests), 1)
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("offline-secret", str(caught.exception))

    def test_unsafe_authenticated_status_urls_never_receive_credentials(self):
        for url in ("https://evil.example/requests/" + REQUEST_ID + "/status", "http://api.higgsfield.ai/requests/" + REQUEST_ID + "/status", STATUS_URL + "?forward=1", "https://api.higgsfield.ai/other"):
            self.requests.clear()
            self.serve([self.receipt(status_url=url)])
            with self.subTest(url=url), self.assertRaises(client.HiggsfieldError):
                client.generate_image("scene")
            self.assertEqual(len(self.requests), 1)

    def test_private_media_host_or_unsafe_upload_headers_fail_before_put(self):
        for headers in ({"Authorization": "Key attacker"}, {"X-Upload": "bad\r\nheader"}, {"Host": "private.example"}):
            self.requests.clear()
            self.serve([{"upload_url": "https://uploads.example.com/image", "public_url": IMAGE_URL, "upload_headers": headers}])
            with self.subTest(headers=list(headers)), self.assertRaises(client.HiggsfieldError):
                client.generate_image("scene", [self.photo()])
            self.assertEqual(len(self.requests), 1)
        self.requests.clear()
        self.serve([self.receipt(), self.completed()])
        self.dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with self.assertRaisesRegex(client.HiggsfieldError, "publicly reachable"):
            client.generate_image("scene")
        self.assertEqual(len(self.requests), 2)

    def test_receipt_malformed_or_unknown_status_fails_without_new_submission(self):
        for receipt in (b"not JSON private prompt offline-secret", self.receipt(request_id="not-a-uuid"), self.receipt(status="surprise")):
            self.requests.clear()
            self.serve([receipt])
            with self.subTest(receipt=type(receipt).__name__), self.assertRaises(client.HiggsfieldError):
                client.generate_image("scene")
            self.assertEqual(len(self.requests), 1)

    def test_download_rejects_non_images_wrong_aspect_and_oversized_body(self):
        square = io.BytesIO()
        Image.new("RGB", (64, 64)).save(square, "PNG")
        for data in (b"<html>private prompt offline-secret</html>", square.getvalue()):
            self.requests.clear()
            self.serve([self.receipt(), self.completed(), data])
            with self.subTest(data=data[:8]), self.assertRaisesRegex(client.HiggsfieldError, "valid image"):
                client.generate_image("scene")
        self.requests.clear()
        self.serve([self.receipt(), self.completed(), b"x" * 33])
        with mock.patch.object(client, "MAX_MEDIA_BYTES", 32), self.assertRaisesRegex(client.HiggsfieldError, "larger"):
            client.generate_image("scene")

    def test_estimate_is_read_only_cost_route_with_same_settings_and_filters_fields(self):
        self.serve([{"credits": "1.500", "usd": "0.094", "message": "offline-secret", "extra": "ignored"}])
        result = client.estimate_image("scene", [IMAGE_URL])
        self.assertEqual(result, {"credits": "1.500", "usd": "0.094"})
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.full_url, client.API_BASE + "/estimate/" + client.DEFAULT_MODEL)
        self.assertEqual(json.loads(request.data)["image_urls"], [IMAGE_URL])
        self.assertIsNone(request.get_header("Idempotency-key"))

    def test_redirect_handler_never_forwards_a_request(self):
        request = client.urllib.request.Request(client.API_BASE, headers={"Authorization": "Key offline-id:offline-secret"})
        self.assertIsNone(client._NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://evil.example"))


if __name__ == "__main__":
    unittest.main()
