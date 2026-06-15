from __future__ import annotations

import json
import sys
from typing import Any


SUPPORTED_MODEL = "BAAI/bge-small-en-v1.5"
_EMBEDDER: Any = None
HELP_TEXT = f"""usage: tirzah-profile-helper [--worker]

Local text-similarity profile helper for Tirzah.

Reads JSON requests with fields "model" and "text" and writes JSON responses
containing "vector". Supported model: {SUPPORTED_MODEL}

Options:
  --worker    read one JSON request per stdin line and write one response per line
  -h, --help  show this help message and exit
"""


def load_embedder() -> Any:
    global _EMBEDDER
    if _EMBEDDER is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed. Install Tirzah with the profiles extra "
                "or run: python -m pip install fastembed"
            ) from exc
        _EMBEDDER = TextEmbedding(model_name=SUPPORTED_MODEL)
    return _EMBEDDER


def embed_text(text: str) -> list[float]:
    vectors = list(load_embedder().embed([text]))
    if not vectors:
        raise RuntimeError("fastembed returned no vectors")
    return [float(value) for value in vectors[0].tolist()]


def profile_response(model: str, text: str) -> tuple[int, dict[str, Any] | None, str | None]:
    if model != SUPPORTED_MODEL:
        return (
            3,
            None,
            f"requested model {model!r} does not match supported model {SUPPORTED_MODEL!r}",
        )
    try:
        return 0, {"vector": embed_text(text)}, None
    except RuntimeError as exc:
        return 4, None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return 5, None, f"{exc.__class__.__name__}: {exc}"


def request_fields(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise RuntimeError("stdin JSON must be an object")
    model = payload.get("model")
    text = payload.get("text")
    if not isinstance(model, str) or not model:
        raise RuntimeError("missing or empty 'model' field")
    if not isinstance(text, str):
        raise RuntimeError("missing or non-string 'text' field")
    return model, text


def worker_error(code: int, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": f"profile_helper: {message}"}}


def run_worker() -> int:
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            model, text = request_fields(json.loads(raw))
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(worker_error(2, f"stdin is not valid JSON: {exc}")))
            sys.stdout.write("\n")
            sys.stdout.flush()
            continue
        except RuntimeError as exc:
            sys.stdout.write(json.dumps(worker_error(2, str(exc))))
            sys.stdout.write("\n")
            sys.stdout.flush()
            continue
        code, response, message = profile_response(model, text)
        sys.stdout.write(json.dumps(response if code == 0 else worker_error(code, message or "failed")))
        sys.stdout.write("\n")
        sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv
    if any(arg in {"-h", "--help"} for arg in args[1:]):
        sys.stdout.write(HELP_TEXT)
        return 0
    if "--worker" in args[1:]:
        return run_worker()
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            raise RuntimeError("empty stdin; expected a JSON object")
        model, text = request_fields(json.loads(raw))
    except json.JSONDecodeError as exc:
        print(f"profile_helper: stdin is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"profile_helper: {exc}", file=sys.stderr)
        return 2
    code, response, message = profile_response(model, text)
    if code != 0:
        print(f"profile_helper: {message or 'failed'}", file=sys.stderr)
        return code
    sys.stdout.write(json.dumps(response))
    sys.stdout.write("\n")
    return 0


def main_cli() -> int:
    return main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main_cli())
