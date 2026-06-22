"""Regression: the Tirzah->Mahalath semantic seam, A/B + strict behaviour.

Locks in the behaviour the fresh-install test exercised on live data (Noa's
semantic_smoke.py covers the live end-to-end): given a realistic ingested node and
Mahalath-style labels,
  - semantic OFF  -> no "Semantic Precision (MPL)" block (prompt unchanged);
  - semantic ON   -> the block resolves the strong term to its MPL label/sense;
  - strict mode   -> drops the FUZZY (partial/text) match, so an approximate false
                     positive (the "Physical -> vorton" class of error) never lands.
"""

from __future__ import annotations

from tirzah.retrieval.queries import build_prompt_envelope
from tirzah.semantic import SemanticLabel


# A context shaped like compile_context's output for a real ingested CBO node.
CBO_CONTEXT = {
    "document": {"title": "CBO", "document_id": "cbo-doc-1"},
    "focus_node_id": "cbo-node-1",
    "records": [{
        "role": "focus", "distance": 0, "title": "Substrate",
        "node_id": "cbo-node-1", "labels": ["source_chunk"],
        "endorsement_label": "unreviewed",
        "provenance": {"source_path": "cbo.md", "ingestion_epoch": "2026-06-22-cbo"},
        "text": "The substrate is the configuration host for physical entities.",
        "text_preview": "The substrate is the configuration host for physical entities.",
    }],
}
QUERY = "what does substrate mean here?"


class LiveLikeResolver:
    """Mirrors live Mahalath: a confident hit for 'substrate' and a FUZZY (text)
    hit for 'physical' — the latter is the approximate false positive strict drops."""

    def resolve(self, terms):
        out = []
        low = [t.casefold() for t in terms]
        if "substrate" in low:
            out.append(SemanticLabel(term="substrate", mpl_label="MPL-902",
                                     canonical_term="substrate", senses=["physical"],
                                     match_kind="exact"))
        if "physical" in low:
            out.append(SemanticLabel(term="physical", mpl_label="MPL-903",
                                     canonical_term="vorton", senses=["physical"],
                                     match_kind="text"))  # fuzzy
        return out


def _env(**kw):
    return build_prompt_envelope(CBO_CONTEXT, query=QUERY, token_budget=600,
                                 reserved_response_tokens=50, **kw)


def test_semantic_off_has_no_mpl_block():
    env = _env()  # no resolver
    assert env["semantic"] == [] and env["semantic_summary"] == ""
    assert "Semantic Precision" not in env["prompt_text"]


def test_semantic_on_resolves_strong_term():
    env = _env(resolver=LiveLikeResolver())
    assert "## Semantic Precision (MPL)" in env["prompt_text"]
    assert "[MPL-902]" in env["prompt_text"]
    labels = {l["term"]: l for l in env["semantic"]}
    assert labels["substrate"]["mpl_label"] == "MPL-902"


def test_strict_excludes_approximate_false_positive():
    loose = _env(resolver=LiveLikeResolver(), semantic_strict=False)
    strict = _env(resolver=LiveLikeResolver(), semantic_strict=True)

    loose_terms = {l["term"] for l in loose["semantic"]}
    strict_terms = {l["term"] for l in strict["semantic"]}

    # loose keeps both, flags the fuzzy one as approximate
    assert loose_terms == {"substrate", "physical"}
    assert "approx match" in loose["prompt_text"]
    # strict keeps only the confident match — no MPL-903/vorton false positive
    assert strict_terms == {"substrate"}
    assert "MPL-903" not in strict["prompt_text"]
    assert "approx match" not in strict["prompt_text"]
