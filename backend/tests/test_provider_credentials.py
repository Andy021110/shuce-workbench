import io
import json
import unittest
from unittest.mock import patch

from app.config import Settings
from app.model_client import ModelClient


def config(**overrides):
    values = dict(
        api_key="chat-test-key", embedding_api_key="embedding-test-key", rerank_api_key="rerank-test-key",
        llm_base_url="https://api.deepseek.com", llm_model="deepseek-flash",
        embedding_base_url="https://example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        rerank_base_url="https://example.cn-beijing.maas.aliyuncs.com/compatible-api/v1",
        max_retries=0,
    )
    values.update(overrides)
    return Settings(**values)


class ProviderCredentialTests(unittest.TestCase):
    def test_requests_use_each_capabilitys_key_and_deepseek_payload(self):
        replies = [
            {"choices": [{"message": {"content": "ok"}}]},
            {"data": [{"index": 0, "embedding": [1, 0]}]},
            {"results": [{"index": 0, "relevance_score": 0.9}]},
        ]
        requests = []

        def respond(request, **kwargs):
            requests.append(request)
            return io.BytesIO(json.dumps(replies[len(requests) - 1]).encode())

        client = ModelClient(config())
        with patch("urllib.request.urlopen", side_effect=respond):
            self.assertEqual(client.chat("system", "hello"), "ok")
            self.assertEqual(client.embed(["a"]), [[1.0, 0.0]])
            self.assertEqual(client.rerank("a", ["a"], 1), [(0, 0.9)])
        self.assertEqual(
            [request.get_header("Authorization") for request in requests],
            ["Bearer chat-test-key", "Bearer embedding-test-key", "Bearer rerank-test-key"],
        )
        chat_body = json.loads(requests[0].data)
        self.assertEqual(chat_body["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", chat_body)

    def test_missing_embedding_key_never_sends_chat_key_to_another_host(self):
        client = ModelClient(config(embedding_api_key=""))
        with patch("urllib.request.urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "EMBEDDING_API_KEY"):
                client.embed(["a"])
        network.assert_not_called()

    def test_missing_rerank_key_never_sends_chat_key_to_another_host(self):
        client = ModelClient(config(rerank_api_key=""))
        with patch("urllib.request.urlopen") as network:
            with self.assertRaisesRegex(RuntimeError, "RERANK_API_KEY"):
                client.rerank("a", ["a"], 1)
        network.assert_not_called()

    def test_legacy_same_host_configuration_can_share_key(self):
        settings = config(
            llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            embedding_base_url="", embedding_api_key="", rerank_api_key="",
            rerank_base_url="https://dashscope.aliyuncs.com/compatible-api/v1",
        )
        self.assertEqual(settings.api_key_for("embedding"), "chat-test-key")
        self.assertEqual(settings.api_key_for("rerank"), "chat-test-key")

    def test_public_status_and_repr_do_not_expose_keys(self):
        settings = config()
        status = settings.public_status()
        self.assertTrue(status["api_key_configured"])
        self.assertTrue(status["embedding_api_key_configured"])
        self.assertTrue(status["rerank_api_key_configured"])
        for secret in (settings.api_key, settings.embedding_api_key, settings.rerank_api_key):
            self.assertNotIn(secret, repr(settings))
            self.assertNotIn(secret, json.dumps(status))


if __name__ == "__main__":
    unittest.main()
