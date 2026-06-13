from __future__ import annotations

import json
import os
import re
import subprocess
from importlib import import_module
from typing import Any
from urllib import request

from tirzah.config import RuntimeConfig


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
        cmd = ollama_cli_command(self.config)
        try:
            completed = subprocess.run(
                cmd,
                input=prompt["prompt_text"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.config.ollama_timeout_seconds,
                env=env,
            )
        except subprocess.CalledProcessError as error:
            if not uses_optional_ollama_flags(cmd) or not is_unsupported_flag_error(error):
                raise RuntimeError(ollama_error_message(error)) from error
            completed = self._run_without_optional_flags(prompt, env)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(
                f"Ollama CLI timed out after {self.config.ollama_timeout_seconds}s "
                f"while running {self.config.ollama_model}."
            ) from error
        answer_text = clean_ollama_output(completed.stdout)
        if not answer_text:
            raise RuntimeError("Ollama CLI returned an empty response.")
        return answer_payload(self.name, self.config.ollama_model, prompt, answer_text)

    def _run_without_optional_flags(self, prompt: dict[str, Any], env: dict[str, str]):
        try:
            return subprocess.run(
                ollama_cli_command(self.config, include_optional_flags=False),
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
        except subprocess.CalledProcessError as error:
            raise RuntimeError(ollama_error_message(error)) from error


class OllamaHttpAnswerAdapter:
    name = "ollama_http"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def answer(self, prompt: dict[str, Any]) -> dict[str, Any]:
        request_body = {
            "model": self.config.ollama_model,
            "prompt": prompt["prompt_text"],
            "stream": False,
        }
        if self.config.ollama_format:
            request_body["format"] = self.config.ollama_format
        think_value = ollama_think_http_value(self.config.ollama_think)
        if think_value is not None:
            request_body["think"] = think_value
        payload = json.dumps(request_body).encode("utf-8")
        req = request.Request(
            f"{self.config.ollama_base_url.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.config.ollama_timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        return answer_payload(self.name, self.config.ollama_model, prompt, data.get("response", ""))


class HoglahAnswerAdapter:
    name = "hoglah"

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def answer(self, prompt: dict[str, Any]) -> dict[str, Any]:
        Hoglah = import_hoglah_client()
        client_config = {
            "db_path": self.config.hoglah_db_path,
            "ollama_host": self.config.hoglah_ollama_host,
        }
        wait_timeout = (
            self.config.hoglah_wait_timeout_seconds
            if self.config.hoglah_wait_timeout_seconds is not None
            else self.config.ollama_timeout_seconds
        )
        with Hoglah(config=client_config, use_real=self.config.hoglah_use_real) as client:
            job_id = client.submit(
                prompt=prompt["prompt_text"],
                model=self.config.ollama_model,
                format=self.config.ollama_format,
                tags=["tirzah"],
                metadata={"source": "tirzah"},
            )
            result = client.wait(job_id, timeout=wait_timeout)
        status = getattr(result.status, "value", result.status)
        if status != "completed":
            detail = result.error or f"Hoglah job {result.job_id} ended with status {status}."
            raise RuntimeError(detail)
        if not result.output:
            raise RuntimeError(f"Hoglah job {result.job_id} completed without output.")
        payload = answer_payload(self.name, self.config.ollama_model, prompt, result.output)
        payload["hoglah_job_id"] = result.job_id
        if result.truncated:
            payload["truncated"] = True
            payload["truncation_reason"] = result.truncation_reason
        return payload


def import_hoglah_client():
    try:
        return import_module("hoglah").Hoglah
    except ImportError as error:
        raise RuntimeError(
            "Hoglah answer adapter requires the optional Hoglah package. "
            'Install with `pip install "tirzah[hoglah]"` or set '
            "`runtime.answer_adapter` to another adapter."
        ) from error


def answer_adapter(config: RuntimeConfig):
    if config.answer_adapter == "mock":
        return MockAnswerAdapter()
    if config.answer_adapter == "ollama_cli":
        return OllamaCliAnswerAdapter(config)
    if config.answer_adapter == "ollama_http":
        return OllamaHttpAnswerAdapter(config)
    if config.answer_adapter == "hoglah":
        return HoglahAnswerAdapter(config)
    raise ValueError(f"Unknown answer adapter: {config.answer_adapter}")


def ollama_cli_command(config: RuntimeConfig, include_optional_flags: bool = True) -> list[str]:
    cmd = [
        str(config.ollama_executable),
        "run",
        "--nowordwrap",
    ]
    if include_optional_flags:
        if config.ollama_format:
            cmd.extend(["--format", config.ollama_format])
        think_value = ollama_think_cli_value(config.ollama_think)
        if think_value is not None:
            cmd.append(f"--think={think_value}")
        if config.ollama_hide_thinking:
            cmd.append("--hidethinking")
    cmd.append(config.ollama_model)
    return cmd


def ollama_think_cli_value(value: bool | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    return value.strip().lower()


def ollama_think_http_value(value: bool | str | None) -> bool | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return lowered


def uses_optional_ollama_flags(cmd: list[str]) -> bool:
    return any(
        item == "--format" or item == "--hidethinking" or item.startswith("--think=")
        for item in cmd
    )


def is_unsupported_flag_error(error: subprocess.CalledProcessError) -> bool:
    text = "\n".join(str(part or "") for part in [error.stderr, error.stdout, error])
    lowered = text.lower()
    return "unknown flag" in lowered or "flag provided but not defined" in lowered


def ollama_error_message(error: subprocess.CalledProcessError) -> str:
    detail = clean_ollama_output("\n".join(str(part or "") for part in [error.stderr, error.stdout]))
    if detail:
        return f"Ollama CLI failed: {detail}"
    return f"Ollama CLI failed with exit code {error.returncode}."


def answer_payload(adapter: str, model: str, prompt: dict[str, Any], answer_text: str) -> dict[str, Any]:
    included = prompt.get("context_metadata", {}).get("included", [])
    return {
        "adapter": adapter,
        "model": model,
        "answer": answer_text.strip(),
        "used_node_ids": [record["node_id"] for record in included],
        "confidence": "model" if adapter.startswith("ollama") or adapter == "hoglah" else "mock",
    }


def clean_ollama_output(text: str) -> str:
    rewritten = apply_terminal_rewrites(text)
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rewritten)
    without_spinners = re.sub(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*", "", without_ansi)
    without_controls = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", without_spinners)
    repaired_wraps = repair_duplicate_wrap_fragments(without_controls)
    return repaired_wraps.strip()


def repair_duplicate_wrap_fragments(text: str) -> str:
    """Remove stale line-end prefixes left by Ollama terminal wrapping."""
    return re.sub(
        r"(?<![A-Za-z])([A-Za-z]{1,20})\n(?=\1[A-Za-z])",
        "",
        text,
    )


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
