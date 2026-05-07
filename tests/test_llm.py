from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.resume_bot.llm import AnthropicVisionClient, MiniMaxAnthropicClient, OpenAICompatibleTextClient, VisionClient


class OpenAICompatibleEndpointTests(unittest.TestCase):
    def test_text_client_accepts_base_or_full_chat_completions_url(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        response.raise_for_status.return_value = None

        with patch("src.resume_bot.llm.requests.post", return_value=response) as post:
            OpenAICompatibleTextClient("https://example.test/v1", "secret", "demo").complete_text("s", "u")
            OpenAICompatibleTextClient(
                "https://example.test/v1/chat/completions",
                "secret",
                "demo",
            ).complete_text("s", "u")

        self.assertEqual(post.call_args_list[0].args[0], "https://example.test/v1/chat/completions")
        self.assertEqual(post.call_args_list[1].args[0], "https://example.test/v1/chat/completions")
        self.assertEqual(post.call_args_list[0].kwargs["json"]["max_tokens"], 2000)

    def test_text_client_keeps_enough_budget_for_reasoning_models(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        response.raise_for_status.return_value = None

        with patch("src.resume_bot.llm.requests.post", return_value=response) as post:
            OpenAICompatibleTextClient("https://example.test/v1", "secret", "demo").complete_text(
                "s",
                "u",
                max_tokens=12,
            )

        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 512)

    def test_anthropic_clients_accept_base_or_full_messages_url(self) -> None:
        response = Mock()
        response.json.return_value = {"content": [{"type": "text", "text": "OK"}]}
        response.raise_for_status.return_value = None
        image_path = Path(__file__).resolve()

        with patch("src.resume_bot.llm.requests.post", return_value=response) as post:
            MiniMaxAnthropicClient(
                "https://example.test/anthropic",
                "secret",
                "demo",
            ).complete_text("s", "u")
            AnthropicVisionClient(
                "https://example.test/anthropic/v1/messages",
                "secret",
                "demo",
            ).extract_text("p", [image_path])

        self.assertEqual(post.call_args_list[0].args[0], "https://example.test/anthropic/v1/messages")
        self.assertEqual(post.call_args_list[1].args[0], "https://example.test/anthropic/v1/messages")

    def test_anthropic_compatible_uses_bearer_auth(self) -> None:
        response = Mock()
        response.json.return_value = {"content": [{"type": "text", "text": "OK"}]}
        response.raise_for_status.return_value = None

        with patch("src.resume_bot.llm.requests.post", return_value=response) as post:
            MiniMaxAnthropicClient(
                "https://example.test/anthropic",
                "secret",
                "demo",
                auth_scheme="bearer",
            ).complete_text("s", "u")

        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["authorization"], "Bearer secret")
        self.assertNotIn("x-api-key", headers)

    def test_vision_client_accepts_base_or_full_chat_completions_url(self) -> None:
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        response.raise_for_status.return_value = None
        image_path = Path(__file__).resolve()

        with patch("src.resume_bot.llm.requests.post", return_value=response) as post:
            VisionClient("https://example.test/v1", "secret", "demo").extract_text("p", [image_path])
            VisionClient(
                "https://example.test/v1/chat/completions",
                "secret",
                "demo",
            ).extract_text("p", [image_path])

        self.assertEqual(post.call_args_list[0].args[0], "https://example.test/v1/chat/completions")
        self.assertEqual(post.call_args_list[1].args[0], "https://example.test/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
