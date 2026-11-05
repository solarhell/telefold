"""通过本地 HTTP transport 验证真实 SDK 的协议、语义校验和重试预算。"""

import asyncio
import json

import httpx2
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.providers.openai import OpenAIProvider

from telefold import classifier
from telefold.client import DialogInfo
from telefold.config import LLMConfig


def _folders(count, group_count):
    """构造覆盖全部输入且互不重叠的测试分组，不推导业务数量策略。"""
    return [
        {"name": f"分组 {index}", "dialog_ids": list(range(index, count + 1, group_count))}
        for index in range(1, group_count + 1)
    ]


def _completion(body, folders, sequence=1):
    """模拟服务器返回结构化工具结果，各轮使用独立的调用 ID。"""
    return httpx2.Response(
        200,
        json={
            "id": f"chatcmpl-test-{sequence}",
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
                                "id": f"call-test-{sequence}",
                                "type": "function",
                                "function": {
                                    "name": body["tools"][0]["function"]["name"],
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


@pytest.fixture
def run_classification(monkeypatch):
    """使用真实模型、provider 和 SDK，仅将 HTTP 传输替换为本地应答。"""

    def run(dialogs, responder):
        """在独立事件循环中执行一次完整分类，传输层全程由调用方控制。"""

        def transport(request):
            """每轮都核对 Chat Completions 接口、认证和模型选择。"""
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-key"
            assert json.loads(request.content)["model"] == "custom-chat-model"
            return responder(request)

        async def scenario():
            """将 provider 绑定到内存 transport，禁止真实服务参与测试。"""
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as http:

                def provider(**kwargs):
                    """保留生产代码的自定义接口和密钥，仅替换传输层。"""
                    assert kwargs["base_url"] == "https://llm.example.test/v1"
                    return OpenAIProvider(http_client=http, **kwargs)

                monkeypatch.setattr(classifier, "OpenAIProvider", provider)
                cfg = LLMConfig("test-key", "custom-chat-model", "https://llm.example.test/v1")
                return await classifier.classify(cfg, dialogs)

        return asyncio.run(scenario())

    return run


@pytest.mark.parametrize(
    "count,minimum,maximum", [(1, 1, 1), (2, 1, 2), (4, 1, 4), (5, 5, 5), (9, 5, 8)]
)
def test_openai_compatible_classification(run_classification, count, minimum, maximum):
    """验证系统约束及不同输入规模的可行范围，合规输出只需一次请求。"""
    requests = []
    folders = _folders(count, minimum)

    def respond(request):
        """核对完整系统指令和结构化响应 schema，然后返回合规分类。"""
        requests.append(request)
        body = json.loads(request.content)
        assert any(
            message.get("content")
            == (
                f"{classifier.SYSTEM_PROMPT}\n本次共 {count} 个对话，请创建 {minimum}-{maximum} 个分组。"
            )
            for message in body["messages"]
        )
        tool = body["tools"][0]["function"]
        assert tool["parameters"]["properties"]["folders"]["maxItems"] == 8
        return _completion(body, folders)

    dialogs = [DialogInfo(index, "技术群", "group") for index in range(1, count + 1)]
    assert run_classification(dialogs, respond) == folders
    assert len(requests) == 1


@pytest.mark.parametrize(
    "count,invalid,reason",
    [
        (9, [{"name": "技术", "dialog_ids": list(range(1, 10))}], "5-8"),
        (
            2,
            [
                {"name": "一", "dialog_ids": [1]},
                {"name": "二", "dialog_ids": [2]},
                {"name": "三", "dialog_ids": [1]},
            ],
            "1-2",
        ),
        (2, [{"name": "技术", "dialog_ids": [1]}], "遗漏"),
        (2, [{"name": "技术", "dialog_ids": [1, 99]}], "未知"),
        (2, [{"name": "NSFW", "dialog_ids": [1, 2]}], "保留"),
        (2, [{"name": "一", "dialog_ids": [1]}, {"name": "二", "dialog_ids": [1, 2]}], "重复"),
    ],
)
def test_invalid_classification_is_retried_before_returning(
    run_classification, count, invalid, reason
):
    """语义不合规时必须将错误反馈给模型，只向调用方返回修正后的完整结果。"""
    requests = []
    corrected = _folders(count, 5 if count == 9 else 1)

    def respond(request):
        """第一轮故意违规，第二轮检查重试反馈后返回正确分类。"""
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) > 1:
            assert any(
                message.get("role") == "tool" and reason in str(message.get("content"))
                for message in body["messages"]
            )
        return _completion(body, invalid if len(requests) == 1 else corrected, len(requests))

    dialogs = [DialogInfo(index, "技术群", "group") for index in range(1, count + 1)]
    assert run_classification(dialogs, respond) == corrected
    assert len(requests) == 2


def test_invalid_group_count_stops_after_retry_budget(run_classification):
    """九个对话持续返回一个分组时，三次修正失败后停止而非返回危险计划。"""
    requests = []

    def respond(request):
        """持续返回覆盖完整但分组数量错误的结果。"""
        body = json.loads(request.content)
        requests.append(body)
        return _completion(
            body, [{"name": "技术", "dialog_ids": list(range(1, 10))}], len(requests)
        )

    dialogs = [DialogInfo(index, "技术群", "group") for index in range(1, 10)]
    with pytest.raises(UnexpectedModelBehavior):
        run_classification(dialogs, respond)
    assert len(requests) == 4


def test_empty_input_does_not_construct_or_call_a_model(monkeypatch):
    """空输入无需配置可用密钥，也不构造 provider 或发送任何模型请求。"""

    def forbidden(*args, **kwargs):
        pytest.fail("空输入不得构造模型")

    monkeypatch.setattr(classifier, "OpenAIChatModel", forbidden)
    assert asyncio.run(classifier.classify(LLMConfig("", "unused"), [])) == []
