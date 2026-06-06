#!/usr/bin/env python3
"""Local text-similarity profile helper for Mnemosyne (Slice 6 Phase A).

Satisfies the LocalCommandEmbeddingAdapter contract in
``src/mnemosyne/adapters/embedding.py``:

  stdin:  JSON object  ``{"model": "<name>", "text": "<text>"}``
  stdout: JSON object  ``{"vector": [<float>, ...]}``  (one line, no trailing data)
  exit:   0 on success, nonzero on any failure (with diagnostics on stderr)

The helper does not L2-normalise the vector. Mnemosyne does that on the
Python side (``LocalCommandEmbeddingAdapter.embed`` calls ``l2_normalize``).

Decisions baked in (recorded in ``.session-log.md`` 2026-06-05):

- Library:  ``fastembed`` (ONNX-runtime backed, cold-start ~200-400 ms)
- Model:    ``BAAI/bge-small-en-v1.5`` (384 dim, ~130 MB, English retrieval)
- Path:     A — per-call subprocess, no persistent worker.
            Path B (persistent worker via embedding.py change) is deferred
            and requires explicit operator approval.

Install (operator-side, one time):

    .venv/bin/pip install fastembed

First run will download model weights to ``~/.cache/fastembed/`` (~130 MB).

Configure in ``config.yaml`` (operator-side, one time, when ready to test):

    runtime:
      embedding_adapter: local_command
      embedding_model: BAAI/bge-small-en-v1.5
      profile_command: [.venv/bin/python, tools/profile_helper.py]

Verification (operator-side, takes ~1 minute after first model download):

    # 1. Determinism — same text twice must produce identical vectors.
    .venv/bin/mnemosyne embedding-smoke "Taj Mahal test" > /tmp/a.json
    .venv/bin/mnemosyne embedding-smoke "Taj Mahal test" > /tmp/b.json
    diff /tmp/a.json /tmp/b.json   # must be empty

    # 2. Different text — must produce a different vector.
    .venv/bin/mnemosyne embedding-smoke "Some other text" > /tmp/c.json
    diff /tmp/a.json /tmp/c.json   # must be non-empty

    # 3. Dimensions — vector should be length 384.
    python3 -c "import json; print(len(json.load(open('/tmp/a.json'))['result']['vector']))"

    # 4. Atkinson candidate-quality check (357-node corpus):
    .venv/bin/mnemosyne backfill-embeddings --label memory_reference --limit 50
    # then pick 5-8 seed nodes from Atkinson and run
    # `vector-semantic-candidates <node_id>` on each; on ≥ 4/5 seeds,
    # ≥ 3/5 top candidates should look semantically related to a human
    # reviewer. If this fails, do not promote to AMS.

Important: switching from the mock adapter (16 dim) to this helper
(384 dim) forces a one-time rebuild of every stored profile in MongoDB,
because the stored vectors are not dimensionally compatible.

Imports are lazy: ``import fastembed`` only happens when ``embed_text``
is actually called, so ``python -m py_compile tools/profile_helper.py``
works in environments where fastembed has not yet been installed.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Hardcoded model identity for this helper. If a different model is
# requested via stdin (set in runtime.embedding_model), the helper errors
# out rather than silently lying about which model produced the vector.
SUPPORTED_MODEL = "BAAI/bge-small-en-v1.5"

# Module-level cache for the embedder. Under the current per-call
# subprocess contract this only saves time within a single process
# (i.e. it does not help). It is here so that if the helper is ever
# adapted into a persistent-worker mode (Path B) the embedder is
# instantiated once.
_EMBEDDER: Any = None


def load_embedder() -> Any:
    """Return the fastembed TextEmbedding instance, loading lazily."""
    global _EMBEDDER
    if _EMBEDDER is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed in this environment. "
                "Install with: .venv/bin/pip install fastembed"
            ) from exc
        _EMBEDDER = TextEmbedding(model_name=SUPPORTED_MODEL)
    return _EMBEDDER


def embed_text(text: str) -> list[float]:
    """Return the raw embedding vector for ``text`` as a list of floats."""
    embedder = load_embedder()
    # fastembed.embed accepts an iterable of texts and returns a generator
    # of numpy arrays. We always pass a single text and take the first.
    vectors = list(embedder.embed([text]))
    if not vectors:
        raise RuntimeError("fastembed.embed returned no vectors")
    vector = vectors[0]
    # numpy.ndarray -> list[float]. Use tolist() to get plain Python floats.
    return [float(value) for value in vector.tolist()]


def read_request() -> tuple[str, str]:
    """Read the JSON request from stdin and return (model, text).

    Raises RuntimeError with a clear message on any parse problem.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        raise RuntimeError("empty stdin; expected a JSON object")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stdin is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stdin JSON must be an object, not a list/scalar")
    model = payload.get("model")
    text = payload.get("text")
    if not isinstance(model, str) or not model:
        raise RuntimeError("missing or empty 'model' field in stdin JSON")
    if not isinstance(text, str):
        raise RuntimeError("missing or non-string 'text' field in stdin JSON")
    return model, text


def main(argv: list[str]) -> int:
    try:
        model, text = read_request()
    except RuntimeError as exc:
        print(f"profile_helper: {exc}", file=sys.stderr)
        return 2

    # Reject mismatched model requests loudly. This is conservative on
    # purpose: a config that says one model while the helper computes
    # another would silently corrupt the stored profile semantics.
    if model != SUPPORTED_MODEL:
        print(
            "profile_helper: requested model "
            f"{model!r} does not match the model this helper is "
            f"configured to serve ({SUPPORTED_MODEL!r}). Either update "
            "runtime.embedding_model in config.yaml to match, or use a "
            "different helper that supports the requested model.",
            file=sys.stderr,
        )
        return 3

    try:
        vector = embed_text(text)
    except RuntimeError as exc:
        print(f"profile_helper: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001 — top-level guard
        print(
            f"profile_helper: unexpected error while embedding: "
            f"{exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return 5

    # One JSON object, newline-terminated, on stdout. The adapter only
    # reads stdout, so anything written to stderr above is for the
    # operator's eyes only.
    sys.stdout.write(json.dumps({"vector": vector}))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
