import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from forge.provider import ModelConfig, Provider, ProviderError


class MockServer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                owner.requests.append((self.path, dict(self.headers), json.loads(body)))
                status, headers, payload = owner.responses.pop(0)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start()
        self.url = "http://127.0.0.1:{}".format(self.server.server_port)
        return self
    def __exit__(self, *args):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def ok(message=None):
    return {"choices": [{"message": message or {"role": "assistant", "content": "done"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def provider(url, **kwargs):
    return Provider(ModelConfig("test-secret", "chosen-model", url, **kwargs))


class ProviderTests(unittest.TestCase):
    def test_payload_auth_tools_usage_and_tool_calls_preserved(self):
        msg = {"role": "assistant", "content": None,
               "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]}
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        with MockServer([(200, {}, ok(msg))]) as server:
            result = provider(server.url).complete([{"role": "user", "content": "hi"}], tools)
        path, headers, payload = server.requests[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-secret")
        self.assertEqual(payload["model"], "chosen-model")
        self.assertEqual(payload["tools"], tools)
        self.assertEqual(payload["max_completion_tokens"], 4096)
        self.assertNotIn("temperature", payload)
        self.assertEqual(result, {"message": msg, "input_tokens": 7, "output_tokens": 3})

    def test_token_parameter_temperature_and_env(self):
        with patch.dict("os.environ", {"AI_API_KEY": "k", "AI_MODEL": "m", "AI_TOKEN_PARAMETER": "max_tokens", "AI_TEMPERATURE": "0.2"}, clear=True):
            cfg = ModelConfig.from_env()
        with MockServer([(200, {}, ok())]) as server:
            provider(server.url, token_parameter=cfg.token_parameter, temperature=cfg.temperature).complete([])
        self.assertEqual(server.requests[0][2]["max_tokens"], 4096)
        self.assertEqual(server.requests[0][2]["temperature"], 0.2)

    def test_retryable_status_retries_at_most_three_times_and_caps_delay(self):
        responses = [(429, {"Retry-After": "99"}, {}), (503, {}, {}), (200, {}, ok())]
        with MockServer(responses) as server, patch("forge.provider.time.sleep") as sleep:
            result = provider(server.url).complete([])
        self.assertEqual(len(server.requests), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5.0, 1.0])
        self.assertEqual(result["input_tokens"], 7)

    def test_nonretryable_and_exhausted_errors_do_not_expose_body_or_key(self):
        with MockServer([(401, {}, {"error": "test-secret"})]) as server:
            with self.assertRaisesRegex(ProviderError, "authentication failed") as caught:
                provider(server.url).complete([])
        self.assertNotIn("test-secret", str(caught.exception))
        with MockServer([(500, {}, {}), (502, {}, {}), (504, {}, {})]) as server, patch("forge.provider.time.sleep"):
            with self.assertRaisesRegex(ProviderError, "HTTP 504"):
                provider(server.url).complete([])
        self.assertEqual(len(server.requests), 3)

    def test_malformed_response_and_missing_env(self):
        with MockServer([(200, {}, b"not-json")]) as server:
            with self.assertRaisesRegex(ProviderError, "malformed"):
                provider(server.url).complete([])
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ProviderError, "AI_API_KEY"):
                ModelConfig.from_env()

    def test_base_url_policy(self):
        with self.assertRaisesRegex(ProviderError, "HTTPS"):
            ModelConfig("k", "m", "http://example.com/v1")
        with self.assertRaises(ProviderError):
            ModelConfig("k", "m", "https://user:pass@example.com/v1")

    def test_response_validation_and_message_allowlist(self):
        message = {"role": "assistant", "content": "ok", "provider_private_field": "discard"}
        with MockServer([(200, {}, ok(message))]) as server:
            result = provider(server.url).complete([])
        self.assertEqual(result["message"], {"role": "assistant", "content": "ok"})
        bad_responses = [
            {"choices": [{"message": {"role": "user", "content": "x"}}]},
            {"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}]},
            {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "same", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                               {"id": "same", "type": "function", "function": {"name": "g", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "content": "x"}}], "usage": {"prompt_tokens": -1}},
        ]
        for response in bad_responses:
            with MockServer([(200, {}, response)]) as server:
                with self.assertRaisesRegex(ProviderError, "malformed"):
                    provider(server.url).complete([])

    def test_response_size_limit_and_secret_repr(self):
        with self.assertRaisesRegex(ProviderError, "8 MiB"):
            with MockServer([(200, {}, b"x" * (8 * 1024 * 1024 + 1))]) as server:
                provider(server.url).complete([])
        self.assertNotIn("test-secret", repr(ModelConfig("test-secret", "m", "https://example.com/v1")))

    def test_config_rejects_non_finite_values(self):
        for kwargs in ({"timeout": float("inf")}, {"temperature": float("nan")}, {"max_output_tokens": 1.5}):
            with self.assertRaises(ProviderError):
                ModelConfig("k", "m", "https://example.com/v1", **kwargs)


if __name__ == "__main__":
    unittest.main()
