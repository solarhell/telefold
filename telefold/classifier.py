"""LLM 对话分类器。"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from telefold.config import LLMConfig
from telefold.client import DialogInfo

SYSTEM_PROMPT = """你是一个 Telegram 对话分组助手。请将给定的对话智能分类到分组中。

规则：
- 创建 5-8 个分组（最多 8 个），名称简短（如"科技资讯"、"Web3"、"工作"、"Tools"）
- 每个对话只能属于一个分组
- 综合利用对话标题、描述、类型、成员数和样本消息来判断最佳分类"""


class Folder(BaseModel):
    name: str
    dialog_ids: list[int]


class ClassifyResult(BaseModel):
    folders: list[Folder]


def _build_prompt(dialogs: list[DialogInfo]) -> str:
    lines = []
    for d in dialogs:
        meta = [f"[ID:{d.id}] {d.title!r} (type={d.dialog_type})"]
        if d.username:
            meta.append(f"  @{d.username}")
        if d.description:
            meta.append(f"  description: {d.description[:200]}")
        if d.participants_count:
            meta.append(f"  members: {d.participants_count}")
        if d.sample_messages:
            samples = [m[:100] for m in d.sample_messages[:3]]
            meta.append(f"  messages: {samples}")
        lines.append("\n".join(meta))
    return "Dialogs:\n\n" + "\n\n".join(lines)


async def classify(
    cfg: LLMConfig,
    dialogs: list[DialogInfo],
) -> list[dict]:
    """调用 LLM 将对话分类到分组。"""
    # 支持任何 OpenAI 兼容 API（OpenAI、Deepseek、Groq、Together 等），通过 base_url 切换
    model = OpenAIChatModel(
        cfg.model,
        provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key),
    )
    agent = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        output_type=ClassifyResult,
        retries=3,
    )

    result = await agent.run(_build_prompt(dialogs))
    return [f.model_dump() for f in result.output.folders]
