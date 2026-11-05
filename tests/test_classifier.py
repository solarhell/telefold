"""使用本地 HTTP transport 验证真实 SDK 的 Chat Completions 协议。"""

import asyncio
import json

import httpx2
from pydantic_ai.providers.openai import OpenAIProvider

from telefold import classifier
from telefold.client import DialogInfo
from telefold.config import LLMConfig


def test_openai_compatible_classification(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "custom-chat-model"
        tool = body["tools"][0]["function"]
        assert "folders" in tool["parameters"]["properties"]
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-test",
                                    "type": "function",
                                    "function": {
                                        "name": tool["name"],
                                        "arguments": json.dumps(
                                            {"folders": [{"name": "技术", "dialog_ids": [1]}]}
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:

            def provider(**kwargs):
                assert kwargs["base_url"] == "https://llm.example.test/v1"
                return OpenAIProvider(http_client=http, **kwargs)

            monkeypatch.setattr(classifier, "OpenAIProvider", provider)
            cfg = LLMConfig(
                api_key="test-key",
                model="custom-chat-model",
                base_url="https://llm.example.test/v1",
            )
            return await classifier.classify(cfg, [DialogInfo(1, "技术群", "group")])

    assert asyncio.run(scenario()) == [{"name": "技术", "dialog_ids": [1]}]
    assert len(requests) == 1
