from dataclasses import dataclass
from pathlib import Path

import json5


@dataclass
class LLMConfig:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"


@dataclass
class Config:
    llm: LLMConfig

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """读取 JSONC 文件并构造 LLM 配置，缺少字段或格式无效时直接报错。"""
        data = json5.loads(Path(path).read_text())
        return cls(
            llm=LLMConfig(**data["llm"]),
        )
