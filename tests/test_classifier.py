"""使用本地 HTTP transport 验证真实 SDK 的 Chat Completions 协议。"""

import asyncio
import json

import httpx2
import pytest
from pydantic_ai.providers.openai import OpenAIProvider

from telefold import classifier
from telefold.client import DialogInfo
from telefold.config import LLMConfig


@pytest.mark.parametrize(
    "count,minimum,maximum", [(1, 1, 1), (2, 1, 2), (4, 1, 4), (5, 5, 5), (9, 5, 8)]
)
def test_openai_compatible_classification(monkeypatch, count, minimum, maximum):
    """通过真实 SDK 验证协议、系统约束及不同输入规模的可行分组范围。"""
    requests = []
    folders = [
        {"name": f"分组 {index}", "dialog_ids": list(range(index, count + 1, minimum))}
        for index in range(1, minimum + 1)
    ]

    def respond(request):
        """核对请求语义并返回只经过本地 transport 的结构化分类响应。"""
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "custom-chat-model"
        assert any(
            message.get("content")
            == (
                f"{classifier.SYSTEM_PROMPT}\n本次共 {count} 个对话，请创建 {minimum}-{maximum} 个分组。"
            )
            for message in body["messages"]
        )
        tool = body["tools"][0]["function"]
        assert "folders" in tool["parameters"]["properties"]
        assert tool["parameters"]["properties"]["folders"]["maxItems"] == 8
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
                                        "arguments": json.dumps({"folders": folders}),
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
        """将 provider 绑定到内存 transport，禁止真实服务参与测试。"""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:

            def provider(**kwargs):
                """保留生产代码的自定义接口和密钥，仅替换传输层。"""
                assert kwargs["base_url"] == "https://llm.example.test/v1"
                return OpenAIProvider(http_client=http, **kwargs)

            monkeypatch.setattr(classifier, "OpenAIProvider", provider)
            cfg = LLMConfig(
                api_key="test-key",
                model="custom-chat-model",
                base_url="https://llm.example.test/v1",
            )
            return await classifier.classify(
                cfg, [DialogInfo(index, "技术群", "group") for index in range(1, count + 1)]
            )

    assert asyncio.run(scenario()) == folders
    assert len(requests) == 1


def test_empty_input_does_not_construct_or_call_a_model(monkeypatch):
    """空输入无需配置可用密钥，也不构造 provider 或发送任何模型请求。"""

    def forbidden(*args, **kwargs):
        pytest.fail("空输入不得构造模型")

    monkeypatch.setattr(classifier, "OpenAIChatModel", forbidden)
    assert asyncio.run(classifier.classify(LLMConfig("", "unused"), [])) == []
