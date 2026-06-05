"""Process-wide settings, loaded from environment / .env."""
from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_model: str = os.getenv("LLM_MODEL", "ollama/qwen2.5-coder:7b")
    llm_api_base: str = os.getenv("LLM_API_BASE", "http://localhost:11434")
    sandbox_image: str = os.getenv("SANDBOX_IMAGE", "go-issue-agent-sandbox:dev")
    github_token: str | None = os.getenv("GITHUB_TOKEN")


settings = Settings()
