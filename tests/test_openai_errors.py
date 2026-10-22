import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PIL import Image

# Offline imports never read the developer's .env or reuse real credentials.
with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "offline-error-test-key"}, clear=True), \
     mock.patch.object(Path, "read_text", return_value=""):
    import thumbnail_agent as agent


KNOWN_LIMIT_CODES = (
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "credit_balance_exhausted", "insufficient_quota", "rate_limit_exceeded", "slow_down",
)


class OpenAIErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reference = self.root / "synthetic-reference.png"
        Image.new("RGB", (48, 64), "#ab426d").save(self.reference)
        self.key = "offline-error-test-key"
        self.prompt = "private-scene-draft-marker: the students arrive at university"
        self.photo_base64 = base64.b64encode(self.reference.read_bytes()).decode()
        self.reflection = f"{self.key}; {self.prompt}; data:image/png;base64,{self.photo_base64}"
        environment = mock.patch.dict(os.environ, {"OPENAI_API_KEY": self.key}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        network = mock.patch.object(agent.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request in error-classification offline tests"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.operations = (
            ("planning", agent.OPENAI_ENDPOINT, lambda: agent.openai_responses_create({"model": "offline-model", "input": self.prompt})),
            ("generation", agent.OPENAI_IMAGES_GENERATE_ENDPOINT, lambda: agent.openai_images_generate(self.prompt)),
            ("reference edit", agent.OPENAI_IMAGES_EDIT_ENDPOINT, lambda: agent.openai_images_edit(self.prompt, [self.reference])),
        )

    def http_error(self, status, body, endpoint=agent.OPENAI_ENDPOINT):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        return urllib.error.HTTPError(endpoint, status, "provider error", {}, io.BytesIO(body))

    def assert_no_reflection(self, message):
        for private in (self.key, self.prompt, self.photo_base64, "data:image/png;base64"):
            self.assertNotIn(private, message)

    def test_safe_messages_distinguish_auth_permissions_spend_credits_quota_and_rate_limits(self):
        authentication = agent.safe_openai_http_error(401)
        permission = agent.safe_openai_http_error(403)
        self.assertNotEqual(authentication, permission)
        self.assertRegex(authentication.lower(), r"auth|credential|api key")
        self.assertRegex(permission.lower(), r"permission|access")
        organization = agent.safe_openai_http_error(429, "organization_spend_limit_exceeded")
        project = agent.safe_openai_http_error(429, "project_spend_limit_exceeded")
        self.assertNotEqual(organization, project)
        self.assertIn("organization", organization.lower())
        self.assertIn("project", project.lower())
        for message in (organization, project):
            self.assertRegex(message.lower(), r"spend|monthly|budget")
            self.assertRegex(message.lower(), r"limit|budget")
        credit = agent.safe_openai_http_error(429, "credit_balance_exhausted")
        quota = agent.safe_openai_http_error(429, "insufficient_quota")
        rate = agent.safe_openai_http_error(429, "rate_limit_exceeded")
        slowdown = agent.safe_openai_http_error(429, "slow_down")
        self.assertRegex(credit.lower(), r"credit|balance")
        self.assertRegex(quota.lower(), r"quota|billing")
        self.assertRegex(rate.lower(), r"rate|too many requests")
        self.assertRegex(slowdown.lower(), r"slow|traffic|rate")
        self.assertNotEqual(credit, quota)
        self.assertNotEqual(quota, rate)
        for status, message in ((401, authentication), (403, permission), *((429, value) for value in (organization, project, credit, quota, rate, slowdown))):
            self.assertIn(f"HTTP {status}", message)
            self.assert_no_reflection(message)

    def test_provider_code_extraction_returns_only_whitelisted_codes(self):
        for code in KNOWN_LIMIT_CODES:
            with self.subTest(code=code):
                error = self.http_error(429, {"error": {"code": code, "message": self.reflection, "type": self.reflection}})
                self.assertEqual(agent.safe_openai_error_code(error), code)

    def test_malformed_nonjson_and_unknown_codes_are_discarded(self):
        bodies = (
            b"<html>provider failed</html>", b"\xffnot-json", None, [], {},
            {"error": []}, {"error": {"message": self.reflection}},
            {"error": {"code": None, "message": self.reflection}},
            {"error": {"code": 429, "message": self.reflection}},
            {"error": {"code": {"value": "organization_spend_limit_exceeded"}, "message": self.reflection}},
            {"error": {"code": self.reflection, "message": self.reflection}},
        )
        for index, body in enumerate(bodies):
            with self.subTest(body=index):
                self.assertIsNone(agent.safe_openai_error_code(self.http_error(429, body)))

    def test_all_three_request_paths_preserve_safe_error_categories_without_provider_reflections(self):
        cases = ((401, "invalid_api_key"), (403, "permission_denied"), *((429, code) for code in KNOWN_LIMIT_CODES))
        for status, code in cases:
            for name, endpoint, operation in self.operations:
                with self.subTest(path=name, status=status, code=code):
                    self.network.reset_mock()
                    self.network.side_effect = self.http_error(status, {"error": {"code": code, "message": self.reflection, "param": self.reflection}}, endpoint)
                    with self.assertRaises(RuntimeError) as caught:
                        operation()
                    self.network.assert_called_once()
                    self.assertEqual(str(caught.exception), agent.safe_openai_http_error(status, code))
                    self.assert_no_reflection(str(caught.exception))

    def test_all_three_paths_use_safe_fallbacks_for_unknown_or_unreadable_provider_bodies(self):
        bodies = (
            f"not-json {self.reflection}".encode(),
            {"error": {"code": self.reflection, "message": self.reflection}},
            {"error": {"code": ["organization_spend_limit_exceeded"], "message": self.reflection}},
            {"unexpected": self.reflection},
        )
        for status in (400, 429):
            for index, body in enumerate(bodies):
                for name, endpoint, operation in self.operations:
                    with self.subTest(path=name, status=status, body=index):
                        self.network.reset_mock()
                        self.network.side_effect = self.http_error(status, body, endpoint)
                        with self.assertRaises(RuntimeError) as caught:
                            operation()
                        self.network.assert_called_once()
                        self.assertEqual(str(caught.exception), agent.safe_openai_http_error(status))
                        self.assert_no_reflection(str(caught.exception))


if __name__ == "__main__":
    unittest.main()
