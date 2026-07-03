"""Runtime adapter/model discovery and status (moved out of the web layer).

Everything here answers "what can this runtime actually run?": which Ollama
models are installed (parsed from ``ollama list``), how they should be
presented as options (size categories, fallbacks for configured-but-missing
models), and whether the configured profile/embedding adapter is usable under
the ingestion/retrieval no-HTTP policy. The web ``/api/runtime`` and
``/api/ingestion/status`` endpoints render these results; no HTTP concerns
live here.
"""

from __future__ import annotations

import subprocess
from typing import Any

from tirzah.adapters.embedding import (
    HTTP_BACKED_EMBEDDING_ADAPTERS,
    local_profile_command,
)
from tirzah.config import RuntimeConfig

FALLBACK_KNOWN_MODELS = ["gemma4:latest", "gemma3:1b"]


def ollama_model_rows(runtime_config: RuntimeConfig) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            [str(runtime_config.ollama_executable), "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return parse_ollama_model_rows(completed.stdout)


def parse_ollama_model_list(output: str) -> list[str]:
    return [model["name"] for model in parse_ollama_model_rows(output)]


def parse_ollama_model_rows(output: str) -> list[dict[str, Any]]:
    models = []
    seen = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("NAME"):
            continue
        parts = stripped.split()
        name = parts[0]
        if not name or name in seen:
            continue
        seen.add(name)
        size = parse_ollama_size(parts[2], parts[3]) if len(parts) >= 4 else None
        models.append(model_option(name=name, size_bytes=size, discovered=True))
    return sorted(models, key=model_sort_key)


def model_options_with_fallbacks(
    discovered: list[dict[str, Any]],
    fallback_names: list[str],
) -> list[dict[str, Any]]:
    models = [dict(model) for model in discovered]
    seen = {model["name"] for model in models}
    for name in fallback_names:
        if name and name not in seen:
            seen.add(name)
            models.append(model_option(name=name, size_bytes=None, discovered=False))
    return sorted(models, key=model_sort_key)


def model_option(name: str, size_bytes: int | None, discovered: bool) -> dict[str, Any]:
    size_category = model_size_category(size_bytes)
    label = f"{name} ({size_category})" if size_category != "unknown" else name
    return {
        "name": name,
        "label": label,
        "size_bytes": size_bytes,
        "size_category": size_category,
        "discovered": discovered,
    }


def parse_ollama_size(value: str, unit: str) -> int | None:
    try:
        number = float(value)
    except ValueError:
        return None
    multipliers = {
        "kb": 1024,
        "mb": 1024**2,
        "gb": 1024**3,
        "tb": 1024**4,
    }
    multiplier = multipliers.get(unit.lower())
    if multiplier is None:
        return None
    return int(number * multiplier)


def model_size_category(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "unknown"
    gib = size_bytes / 1024**3
    if gib >= 12:
        return "large"
    if gib >= 4:
        return "medium"
    return "small"


def model_sort_key(model: dict[str, Any]) -> tuple[int, int, str]:
    size = model.get("size_bytes")
    return (0 if size is not None else 1, -(size or 0), model.get("name") or "")


def runtime_memory_agent_adapter_name(runtime: RuntimeConfig) -> str:
    if runtime.memory_agent_adapter:
        return runtime.memory_agent_adapter
    if runtime.answer_adapter == "ollama_http":
        return "ollama_cli"
    return runtime.answer_adapter


def runtime_embedding_adapter_allowed(runtime: RuntimeConfig) -> bool:
    if getattr(runtime, "allow_http_ingestion_adapters", False):
        return True
    return runtime.embedding_adapter not in HTTP_BACKED_EMBEDDING_ADAPTERS


def profile_adapter_status(runtime: RuntimeConfig) -> dict[str, Any]:
    adapter = runtime.embedding_adapter
    if adapter in HTTP_BACKED_EMBEDDING_ADAPTERS and not runtime.allow_http_ingestion_adapters:
        return {
            "status": "http_adapter_blocked",
            "adapter": adapter,
            "ready": False,
            "message": "Configured profile adapter is HTTP-backed and blocked for ingestion/retrieval memory operations.",
        }
    if adapter == "local_command":
        command = local_profile_command(runtime.profile_command)
        return {
            "status": "ready" if command else "missing_profile_command",
            "adapter": adapter,
            "ready": bool(command),
            "command_configured": bool(command),
            "message": (
                "Local command profile adapter is configured."
                if command
                else "Configure runtime.profile_command before profile generation."
            ),
        }
    if adapter == "mock":
        return {
            "status": "stub_profile_adapter",
            "adapter": adapter,
            "ready": True,
            "message": "Deterministic stub profile adapter is available for diagnostics.",
        }
    return {
        "status": "unknown_profile_adapter",
        "adapter": adapter,
        "ready": False,
        "message": "Configured profile adapter is not recognized.",
    }
