"""MetaResponsesClient against a fake HTTP layer; no network calls."""

import io
import json
import socket
import unittest
from unittest.mock import patch
import urllib.error

from crisis_command.agents.model_client import MetaResponsesClient, UnavailableModelClient
from crisis_command.agents import model_client
from crisis_command.operations.reports import IntakeContext
from crisis_command.operations.service import extraction_schema

SECRET = "test-key-not-real"


def fake_response(payload: dict):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return Response(json.dumps(payload).encode())


def completed(text: str) -> dict:
    return {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": text}]},
    ]}


class MetaResponsesClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MetaResponsesClient(SECRET, "test-model", "https://example.invalid/v1/responses")

    def call(self):
        return self.client.complete_json(system="s", user="u", schema={"type": "object"}, timeout=5)

    def test_returns_parsed_json_and_sends_strict_unstored_request(self) -> None:
        with patch("urllib.request.urlopen", return_value=fake_response(completed('{"a": 1}'))) as opened:
            self.assertEqual(self.call(), {"a": 1})
        request = opened.call_args.args[0]
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SECRET}")

    def test_truncated_response_raises_instead_of_returning_partial_output(self) -> None:
        payload = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                   "output": []}
        with patch("urllib.request.urlopen", return_value=fake_response(payload)):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                self.call()

    def test_non_json_output_raises(self) -> None:
        with patch("urllib.request.urlopen", return_value=fake_response(completed("not json"))):
            with self.assertRaisesRegex(RuntimeError, "valid JSON"):
                self.call()

    def test_timeout_is_reported_as_timeout(self) -> None:
        with patch("urllib.request.urlopen", side_effect=socket.timeout()):
            with self.assertRaises(TimeoutError):
                self.call()

    def test_http_error_does_not_leak_the_key(self) -> None:
        error = urllib.error.HTTPError("https://example.invalid", 401, "no", {}, io.BytesIO(b""))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                self.call()
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SECRET, repr(self.client.__dict__.get("model_id")))


class ClientFromEnvTests(unittest.TestCase):
    def test_missing_settings_give_an_unavailable_client(self) -> None:
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(model_client, "_env_file_values", return_value={}):
            self.assertIsInstance(model_client.model_client_from_env(), UnavailableModelClient)

    def test_complete_settings_give_a_meta_client(self) -> None:
        settings = {"META_API_KEY": SECRET, "META_MODEL": "m", "META_BASE_URL": "https://x.invalid"}
        with patch.dict("os.environ", settings, clear=True), \
                patch.object(model_client, "_env_file_values", return_value={}):
            client = model_client.model_client_from_env()
        self.assertIsInstance(client, MetaResponsesClient)
        self.assertEqual(client.reasoning_effort, model_client.DEFAULT_REASONING_EFFORT)


class ExtractionSchemaTests(unittest.TestCase):
    def test_schema_is_strict_and_limited_to_the_scenario(self) -> None:
        context = IntakeContext(frozenset({"Hospital"}), frozenset({"bridge"}),
                                frozenset({"rescue"}))
        schema = extraction_schema(context)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["location"]["enum"], ["Hospital", None])
        self.assertEqual(schema["properties"]["road_id"]["enum"], ["bridge", None])
        self.assertEqual(schema["properties"]["required_services"]["items"]["enum"], ["rescue"])


if __name__ == "__main__":
    unittest.main()
