"""LLM 对话分类器。"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from telefold.client import DialogInfo
from telefold.config import LLMConfig
from telefold.planner import (
    FOLDER_TITLE_MAX_LENGTH,
    MAX_CLASSIFIED_FOLDERS,
    classification_limits,
    validate_classification,
)

SYSTEM_PROMPT = """你是一个 Telegram 对话分组助手。请将给定的对话智能分类到分组中。

规则：
- 对话数不少于 5 时创建 5-8 个分组；不足 5 时创建 1 到对话数个分组
- 分组数不得超过对话数或 8 个，名称不超过 12 个字符（如"科技资讯"、"Web3"、"工作"）
- 每个对话必须且只能属于一个分组，不得遗漏或编造对话 ID；分组名称不得使用 NSFW 或陌生人
- 综合利用对话标题、描述、类型、成员数和样本消息来判断最佳分类"""


class Folder(BaseModel):
    """模型生成的非空分组，名称遵守 Telegram 的长度限制。"""

    name: str = Field(min_length=1, max_length=FOLDER_TITLE_MAX_LENGTH)
    dialog_ids: list[int] = Field(min_length=1)


class ClassifyResult(BaseModel):
    """仅承载模型分类结果，固定分组由计划层补充。"""

    folders: list[Folder] = Field(min_length=1, max_length=MAX_CLASSIFIED_FOLDERS)


def _build_prompt(dialogs: list[DialogInfo]) -> str:
    """组合有限长度的对话元数据和消息样本，供模型判断分组。"""
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
    """使用 Chat Completions 获取非空分组；空输入不调用模型。"""
    if not dialogs:
        return []
    # 支持任何 OpenAI 兼容 API（OpenAI、Deepseek、Groq、Together 等），通过 base_url 切换
    model = OpenAIChatModel(
        cfg.model,
        provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key),
    )
    minimum, maximum = classification_limits(dialogs)
    instructions = (
        f"{SYSTEM_PROMPT}\n本次共 {len(dialogs)} 个对话，请创建 {minimum}-{maximum} 个分组。"
    )
    agent = Agent(
        model,
        instructions=instructions,
        output_type=ClassifyResult,
        retries=3,
    )

    @agent.output_validator
    def validate_output(output: ClassifyResult) -> ClassifyResult:
        """让模型修正不合规分类；重试耗尽时由 SDK 报错并阻止计划执行。"""
        try:
            validate_classification([folder.model_dump() for folder in output.folders], dialogs)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return output

    result = await agent.run(_build_prompt(dialogs))
    return [f.model_dump() for f in result.output.folders]
