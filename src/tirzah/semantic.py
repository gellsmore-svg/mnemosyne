"""Semantic precision via Mahalath (the Tirzah->Mahalath seam).

This is the integration the readiness report (Noa docs) calls the one piece that
makes semantic precision *used*, not merely stored: during retrieval, resolve the
key terms in the query + retrieved context to **MPL labels and their senses** from
Mahalath's ontology, so the answer can be conditioned on a precise meaning rather
than a surface word.

Design choices (Noa docs/integration.md):
- **Symbolic labels only** — we resolve terms to MPL labels/senses, never compare
  embeddings across systems (their dimensions differ).
- **Package-level call** into `mahalath.retrieval.search_terms`, which already
  respects `is_stale`, so it is as safe as the HTTP path and needs no running
  Mahalath web service. Mahalath is an *optional* dependency: if it is not
  installed or its store is unreachable, resolution degrades to a no-op and
  retrieval is unaffected.

The resolver is injected (a `TermResolver`), so the term-extraction and rendering
logic here are pure and unit-testable without Mahalath or a database.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class SemanticLabel:
    """A term resolved to a Mahalath MPL label and its sense(s)."""

    term: str
    mpl_label: str
    canonical_term: str
    senses: list[str] = field(default_factory=list)  # Mahalath "frames" (meaning contexts)
    match_kind: str = ""
    is_stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Mahalath's search_terms already does fuzzy matching (substring scorer + the
# $text stemmed index). match_kind tells us how strong a hit is: label/exact/alias
# are confident; partial/text are FUZZY. Since we attach a precise sense, a fuzzy
# hit may carry the wrong sense — so we can either drop them (strict) or flag them
# as approximate, never assert them as exact.
STRONG_MATCH_KINDS = frozenset({"label", "exact", "alias"})


def is_strong(label: "SemanticLabel") -> bool:
    return label.match_kind in STRONG_MATCH_KINDS


# --- Tirzah <-> Mahalath seam contract -------------------------------------
# The fields Tirzah requires of a resolved label, and of the Mahalath
# `search_terms` match it is mapped from. Encoded so a contract test fails if
# either side drifts (e.g. Mahalath renames a match attribute).
SEMANTIC_LABEL_FIELDS: tuple[str, ...] = ("term", "mpl_label", "canonical_term", "senses", "match_kind", "is_stale")
MAHALATH_MATCH_ATTRS: tuple[str, ...] = ("mpl_label", "canonical_term", "frames", "match_kind", "is_stale")


def match_to_label(term: str, match: Any) -> SemanticLabel:
    """Map a Mahalath ``search_terms`` match onto a :class:`SemanticLabel`.

    This is the literal field contract Tirzah depends on Mahalath to provide;
    keeping it one function makes the seam unit-testable without Mahalath installed.
    """
    return SemanticLabel(
        term=term,
        mpl_label=match.mpl_label,
        canonical_term=match.canonical_term,
        senses=list(match.frames),
        match_kind=match.match_kind,
        is_stale=match.is_stale,
    )


def validate_semantic_label(label: Any) -> list[str]:
    """Return conformance errors for a resolver's label (empty list = conformant)."""
    data = label.to_dict() if isinstance(label, SemanticLabel) else label
    if not isinstance(data, dict):
        return ["semantic label must be an object"]
    errors = [f"missing field: {f}" for f in SEMANTIC_LABEL_FIELDS if f not in data]
    if "senses" in data and not isinstance(data["senses"], list):
        errors.append("senses must be a list")
    return errors


class TermResolver(Protocol):
    """Resolve human terms to ontology matches. Implemented by `MahalathResolver`
    (real) and trivially faked in tests."""

    def resolve(self, terms: list[str]) -> list[SemanticLabel]:
        ...


# Common words that are never worth resolving to an ontology entry.
_STOPWORDS = frozenset(
    """the a an and or but if then else of to in on at by for with from as is are was
    were be been being this that these those it its their his her our your my we you they
    i he she them us not no yes do does did has have had will would can could should may
    might must about into over under than so such which who whom whose what when where why
    how all any each more most other some only own same too very just also""".split()
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")
_QUOTED_RE = re.compile(r"[\"“‘']([A-Za-z][A-Za-z \-']{1,40})[\"”’']")


def extract_candidate_terms(*texts: str, limit: int = 24) -> list[str]:
    """Deterministically pull candidate terms out of the query + context.

    Generous on purpose: the resolver only matches terms that exist in Mahalath's
    ontology, so over-extracting is harmless (unmatched terms yield nothing). The
    *query* is read first so the user's own terms rank ahead of context terms.
    Quoted phrases are kept whole (they are usually the precise term in question).
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def add(term: str) -> None:
        key = term.casefold()
        if key in seen or key in _STOPWORDS:
            return
        seen.add(key)
        ordered.append(term)

    for text in texts:
        if not text:
            continue
        for quoted in _QUOTED_RE.findall(text):
            add(quoted.strip())
    for text in texts:
        if not text:
            continue
        for word in _WORD_RE.findall(text):
            add(word)
            if len(ordered) >= limit:
                return ordered[:limit]
    return ordered[:limit]


def annotate(
    query: str,
    context_text: str,
    resolver: TermResolver,
    *,
    limit: int = 24,
    strict: bool = False,
) -> list[SemanticLabel]:
    """Resolve the key terms of a query + context to MPL labels.

    Always drops stale entries (Mahalath marks superseded ones). With `strict=True`,
    also drops FUZZY matches (partial/text) and keeps only confident
    label/exact/alias hits — use it when a wrong sense is worse than no sense.
    """
    terms = extract_candidate_terms(query or "", context_text or "", limit=limit)
    if not terms:
        return []
    labels = resolver.resolve(terms) or []
    # One label per term; stable order = the order terms were offered to the resolver.
    deduped: dict[str, SemanticLabel] = {}
    for label in labels:
        if label.is_stale or not label.mpl_label:
            continue
        if strict and not is_strong(label):
            continue
        deduped.setdefault(label.term.casefold(), label)
    return list(deduped.values())


def render_prompt_block(labels: list[SemanticLabel]) -> str:
    """The instruction block injected into the LLM prompt so the answer is
    conditioned on the precise sense (empty string when there is nothing to add)."""
    if not labels:
        return ""
    lines = ["## Semantic Precision (MPL)",
             "Interpret these terms in the precise sense given; prefer it over the everyday reading:"]
    for label in labels:
        sense = f" — sense: {', '.join(label.senses)}" if label.senses else ""
        approx = "" if is_strong(label) else "  (approx match — treat as a hint)"
        lines.append(f"- {label.term} = [{label.mpl_label}] {label.canonical_term}{sense}{approx}")
    return "\n".join(lines)


def summarize_labels(labels: list[SemanticLabel]) -> str:
    """A one-line human summary for output ('interpreted as …')."""
    if not labels:
        return ""
    parts = [f"{l.term}→{l.mpl_label}" + (f" ({l.senses[0]})" if l.senses else "") for l in labels]
    return "interpreted as: " + "; ".join(parts)


@dataclass
class MahalathResolver:
    """Real resolver: package-level `mahalath.retrieval.search_terms` over Mahalath's
    Mongo. Optional + fail-soft — any import/connection error yields no labels rather
    than breaking retrieval."""

    mongo_uri: str = "mongodb://localhost:27017"
    database: str = "mahalath_dev"
    language: str = "en"
    per_term_timeout_ms: int = 2000
    last_error: str | None = field(default=None, init=False)
    last_error_type: str | None = field(default=None, init=False)

    def resolve(self, terms: list[str]) -> list[SemanticLabel]:
        self.last_error = None
        self.last_error_type = None
        try:
            from pymongo import MongoClient

            from mahalath.retrieval import Filters, search_terms
        except Exception as error:
            self.last_error = str(error)
            self.last_error_type = type(error).__name__
            return []  # Mahalath not installed — degrade to no-op
        try:
            client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=self.per_term_timeout_ms)
            db = client[self.database]
            out: list[SemanticLabel] = []
            for term in terms:
                matches = search_terms(db, [term], filters=Filters(language=self.language), limit=1)
                if not matches:
                    continue
                out.append(match_to_label(term, matches[0]))
            return out
        except Exception as error:
            self.last_error = str(error)
            self.last_error_type = type(error).__name__
            return []  # store unreachable / schema drift — fail soft
        finally:
            try:
                client.close()  # type: ignore[name-defined]
            except Exception:
                pass


def make_resolver(config: Any) -> TermResolver | None:
    """Build a resolver from a RuntimeConfig if semantic precision is enabled."""
    if not getattr(config, "mahalath_enabled", False):
        return None
    return MahalathResolver(
        mongo_uri=getattr(config, "mahalath_mongo_uri", "mongodb://localhost:27017"),
        database=getattr(config, "mahalath_mongo_db", "mahalath_dev"),
        language=getattr(config, "mahalath_language", "en"),
    )
