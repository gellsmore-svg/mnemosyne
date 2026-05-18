from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any
from urllib import request

from mnemosyne.config import RuntimeConfig


class MockAnswerAdapter:
    name = "mock_answer"

    def answer(self, prompt: dict[str, Any]) -> dict[str, Any]:
        included = prompt.get("context_metadata", {}).get("included", [])
        context_text = prompt.get("context_text", "")
        answer_lines = [
            "Mock answer based on retrieved context.",
            "",
            summarize_context_text(context_text),
        ]
        return {
            "adapter": self.name,
            "answer": "\n".join(answer_lines).strip(),
            "used_node_ids": [record["node_id"] for record in included],
            "confidence": "mock",
        }


def summarize_context_text(context_text: str) -> str:
    body_lines = [
        line.strip()
        for line in context_text.splitlines()
        if line.strip()
        and not line.startswith("#")
        and not line.startswith("- Node ID:")
        and not line.startswith("- Labels:")
        and not line.startswith("- Endorsement:")
        and not line.startswith("- Source:")
        and not line.startswith("Document:")
        and not line.startswith("Document ID:")
        and not line.startswith("Focus Node ID:")
        and not line.startswith("##")
    ]
    return " ".join(body_lines[:6]) or "No usable context text was retrieved."


class OllamaCliAnswerAdapter:
    name = "ollama_cli"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def answer(self, prompt: dict[str, Any]) -> dict[str, Any]:
        env = os.environ.copy()
        env.setdefault("NO_COLOR", "1")
        env.setdefault("TERM", "dumb")
        try:
            completed = subprocess.run(
                [
                    str(self.config.ollama_executable),
                    "run",
                    "--nowordwrap",
                    self.config.ollama_model,
                ],
                input=prompt["prompt_text"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.config.ollama_timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(
                f"Ollama CLI timed out after {self.config.ollama_timeout_seconds}s "
                f"while running {self.config.ollama_model}."
            ) from error
        answer_text = clean_ollama_output(completed.stdout)
        if not answer_text:
            raise RuntimeError("Ollama CLI returned an empty response.")
        return answer_payload(self.name, self.config.ollama_model, prompt, answer_text)


class OllamaHttpAnswerAdapter:
    name = "ollama_http"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def answer(self, prompt: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(
            {
                "model": self.config.ollama_model,
                "prompt": prompt["prompt_text"],
                "stream": False,
            }
        ).encode("utf-8")
        req = request.Request(
            f"{self.config.ollama_base_url.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.config.ollama_timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        return answer_payload(self.name, self.config.ollama_model, prompt, data.get("response", ""))


def answer_adapter(config: RuntimeConfig):
    if config.answer_adapter == "mock":
        return MockAnswerAdapter()
    if config.answer_adapter == "ollama_cli":
        return OllamaCliAnswerAdapter(config)
    if config.answer_adapter == "ollama_http":
        return OllamaHttpAnswerAdapter(config)
    raise ValueError(f"Unknown answer adapter: {config.answer_adapter}")


def answer_payload(adapter: str, model: str, prompt: dict[str, Any], answer_text: str) -> dict[str, Any]:
    included = prompt.get("context_metadata", {}).get("included", [])
    return {
        "adapter": adapter,
        "model": model,
        "answer": answer_text.strip(),
        "used_node_ids": [record["node_id"] for record in included],
        "confidence": "model" if adapter.startswith("ollama") else "mock",
    }


def clean_ollama_output(text: str) -> str:
    rewritten = apply_terminal_rewrites(text)
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rewritten)
    without_spinners = re.sub(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*", "", without_ansi)
    without_controls = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", without_spinners)
    return without_controls.strip()


def apply_terminal_rewrites(text: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\x1b":
            output.append(char)
            index += 1
            continue
        match = re.match(r"\x1b\[([0-9]*)([A-Za-z])", text[index:])
        if not match:
            index += 1
            continue
        amount = int(match.group(1) or "1")
        command = match.group(2)
        if command == "D":
            for _ in range(min(amount, len(output))):
                output.pop()
        elif command == "K":
            while output and output[-1] != "\n":
                output.pop()
        elif command == "G":
            while output and output[-1] != "\n":
                output.pop()
        index += len(match.group(0))
    return "".join(output)
