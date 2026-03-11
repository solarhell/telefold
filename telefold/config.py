import json5
from dataclasses import dataclass
from pathlib import Path


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
        data = json5.loads(Path(path).read_text())
        return cls(
            llm=LLMConfig(**data["llm"]),
        )
