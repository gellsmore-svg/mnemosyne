# Cross-Document Scaling Strategy

Date: 2026-06-05

Status: design doc for Slice 4 (REM consolidation) and the cross-document
portions of Slice 6 Phase B at scale. This is a forward-looking architectural
strategy. Nothing here is implemented yet; it is the agreed direction for the
work after Slice 6 Phase A finishes.

## Provenance

Operator-authored architectural strategy (sections 1, 3 outline, 4 outline,
5 outline), Claude integration and annotation work (introduction, section 2
commitments named, section 6, section 7, alignment cross-refs throughout).
The headline commitments in section 2 — labels-as-immutable, clusters-as-
emergent, recursive tiering as a consequence of REM, cross-cluster bridge
discovery as a parallel mechanism — were settled in the 2026-06-05 operator
review of Claude's open-question list. Three options in earlier drafts were
explicitly rejected by the operator in that review and are not part of this
strategy: pruning of low-value edges, summarisation of old content, and
sharding by domain.

**2026-06-06 amendment (Claude):** Section 2.5 added (cluster governance
scoping derived from membership), section 3 extended with identity
filtering at every tier, section 6.4 added (cluster governance derivation
follow-on questions), section 7 cross-references extended. The amendment
addresses a composition gap surfaced during the cluster-5
(governance/lifecycle) documentation pass: without it, the cluster
directory becomes an information leak when governance scoping starts
filtering at the chunk level. No prior section text was modified.

---

## 1. The scaling problem

The current corpus already has ~175,687 nodes (mostly AMS-labelled chunks).
Mnemosyne preserves source material at chunk granularity by design, so each
preserved passage is independently addressable for retrieval, provenance,
endorsement, and (once Slice 6 lands) text similarity profiling. The
chunk-as-addressable-unit commitment is the right shape for a memory system,
but it creates a sharp combinatorial problem the moment cross-document
semantic discovery enters the picture.

Naive cross-mapping is O(N²): comparing every chunk against every other
chunk would mean ~30 billion pair comparisons for AMS alone, and it grows
quadratically as the corpus does. Full LLM-driven cross-mapping during
ingestion is similarly impractical: it would force every new chunk to be
compared against everything already stored, with model-call cost on every
comparison.

The corpus will continue to grow. AMS is ~94 nodes per document averaged
across 1,868 documents; future corpora at the same chunking density will
add proportionally. The architecture needs to scale by *not* materialising
the dense cross-document edge set in the first place, while still preserving
the ability to find non-obvious connections across documents and domains.

**Goal:** Preserve rich intra-document structure and chunk-level provenance
while enabling efficient, focused cross-document traversal that still
admits serendipitous or unusual connections.

---

## 2. Architectural commitments

Four commitments together make this strategy work. None of them is novel
in isolation; what they do together is keep the cross-document edge set
sparse without losing semantic coverage.

### 2.1 Labels are immutable, like source

The ingestion LLM (Slice 7, when it lands) generates labels freely for each
new chunk. It does **not** consult the existing label set during ingestion.
Each label is recorded verbatim — the exact string the model chose — and
once written, is never edited. Typos, awkward phrasings, and outdated
terminology are preserved as-is.

This rule borrows the source-preservation discipline already applied to
chunk text and applies it to labels. The benefit is the same: no
ingestion-time lookup cost over a growing label set, full audit visibility
of every labelling decision, and no rewrite churn when the system later
discovers that two labels should be related.

### 2.2 Clusters are emergent, like derived content

REM correlates labels post-hoc into label-clusters. This is asynchronous,
batched, and operates over the immutable label set without modifying it.
A label becomes a member of one or more clusters; clusters carry the
relationships, the labels themselves do not.

This is the architectural complement to commitment 2.1. Because labels
are immutable, cluster membership is the only place where lexical
equivalence ("Relational Substrate" ≈ "relational substrate" ≈ "the RS
framework") gets resolved. Cluster membership is therefore derived
content: regeneratable from the labels and the chunk profiles, and
versionable per ingestion epoch.

REM may also propose **cross-cluster equivalence** (a label-cluster in
one domain mapping to a label-cluster in another). These are recorded as
mapping edges between clusters, not by merging the clusters into one.

### 2.3 The directory tier is a recursive consequence of REM

A higher-order LLM pass labels the *clusters themselves*, producing
cluster-level meta-labels. REM then correlates those meta-labels into
super-clusters. This recurses as far as the corpus needs it to. The
directory that the memory-agent sees at retrieval time is the topmost
layer of this stack, and it is small enough to fit comfortably in
context.

For the current AMS scale, three tiers fall out naturally:

| Tier | What it contains | Approx count for AMS |
|---|---|---|
| Tier 0 | Raw LLM-generated labels per chunk, verbatim, immutable | 500k–800k unique strings (high duplication) |
| Tier 1 | Label-clusters discovered by REM over Tier 0 | ~100–500 |
| Tier 2 | Domain-level meta-clusters from a second-order LLM pass over Tier 1 | ~10–30 |

At retrieval time the memory-agent receives Tier 2 by default (~10–30
items, trivially fits in context). Picking one or more Tier 2 entries
expands the corresponding Tier 1 sub-directories (~30 items each).
Picking one or more Tier 1 entries opens the chunks. **At no point does
the agent see the full corpus or even the full Tier 0 label set.** This
is the architectural answer to the directory-size-at-scale problem: tiers
are not designed in, they are derived by running REM at higher orders.

Tier construction is asynchronous, versioned per ingestion epoch, and
fully regeneratable from Tier 0. New tiers can be added as the corpus
grows without changing what already exists at lower tiers.

### 2.4 Cross-cluster bridge discovery is a parallel mechanism

The default retrieval path (directory → cluster → chunks within cluster)
is efficient but conservative: it surfaces what is *expected* given the
chosen cluster. Breakthrough connections — the ones that bridge two
otherwise-separate areas of the corpus — would never surface from a pure
cluster walk, because the breakthrough is precisely the link the cluster
structure does not yet acknowledge.

A separate REM pass exists for this. It scans for chunk-level high-
similarity pairs whose source chunks belong to *different* clusters.
When chunk *A* in cluster *X* turns out to have a strong profile match
to chunk *B* in cluster *Y*, that pair is enqueued as a candidate edge
distinguished from ordinary label-cluster candidates by a separate
`candidate_source: cross_cluster_bridge`. Operators review these
candidates through the same review queue as other semantic edges.
High-confidence accepted bridges can influence retrieval when the query
straddles both clusters.

This is a deliberate exploration mechanism. Cross-cluster bridge
discovery is the system actively looking for novelty, not waiting for
novelty to surface accidentally. It is also the mechanism that makes the
strategy compatible with finding connections "the corpus does not yet
know it has."

### 2.5 Clusters inherit governance scoping from their members

The governance schema plan (`docs/governance-schema-plan.md`) specifies
identity_visibility, identity_exclusions, sensitivity, authority_layer,
valid_from / valid_until, and review_status fields for nodes and graph
edges. When clusters land in `semantic_map` they must carry parallel
fields, otherwise the cluster directory becomes an information leak: an
agent restricted from accessing certain chunks could still see the
meta-label of a cluster containing those chunks (for example, a cluster
named "classified_security_audit" would leak the existence of restricted
content even when its members are filtered out).

**Cluster-level governance fields are derived from the membership, not
authored independently.** The derivation rules are restrictive by default:

- `identity_visibility` for a cluster = **intersection** of its members'
  `identity_visibility` — only identities allowed by every member can
  see the cluster.
- `identity_exclusions` for a cluster = **union** of its members'
  `identity_exclusions` — any identity excluded by any member is
  excluded from the cluster.
- `sensitivity` for a cluster = **maximum** of its members' sensitivity
  (cluster is as restricted as its most-restricted member).
- `authority_layer` for a cluster = **maximum** of its members'
  authority_layer.
- `review_status` for a cluster = **least-progressed** of its members
  (unreviewed dominates reviewed; reviewed dominates deprecated).

These derivations apply recursively up the directory tier stack. A
Tier 1 cluster's scoping is the intersection-or-maximum of its Tier 0
member labels; a Tier 2 super-cluster's scoping is the
intersection-or-maximum of its Tier 1 members; and so on. New members
joining a cluster can only tighten its scoping, never loosen it (because
intersection only shrinks and maximum only rises). A cluster that loses
a member through epoch transition is recomputed against its remaining
members; loosening only occurs when the restrictive member is gone.

This commitment forces a discipline on the LLM-generated meta-labelling
pass in 4.2: the meta-label must not summarise restricted content in
words that themselves leak the existence of that content. The meta-label
is itself content for visibility purposes. In practice this means the
ingestion adapter should be given the membership's collective
`sensitivity` ceiling when generating meta-labels, so the meta-label can
be authored at or below that ceiling rather than reaching above it.

---

## 3. Layered retrieval strategy

The retrieval path that follows from the commitments above is layered,
with each layer cheap and bounded:

1. **Fast deterministic filters.** Operator-declared labels (`ams_domain`,
   `external_corpus`, `memory_reference`, etc.), provenance tier, session
   active documents, usage scores. These already exist in the
   implementation and are the cheapest pass. They scope the search
   without touching profiles or clusters.

2. **Profile / vector candidates.** Already partially implemented in
   Slice 6 Phase B as `vector-semantic-candidates`. A bounded scan over
   stored profiles produces candidate near-neighbours. The scan is
   focused by the cluster directory at higher tiers — the agent has
   already picked which Tier 1 sub-directory to look in, so the scan
   operates over the chunks within that cluster, not the whole corpus.

3. **Agentic graph traversal in promising subgraphs.** The memory-agent
   uses the existing read-only tools (`expand_proximity`,
   `expand_graph_paths`, `compile_context`) but their scope is bounded
   by the cluster the agent has already selected. The agent iterates,
   inspects results, refines its selection, and stops when context is
   sufficient. This is the same iterative loop the agentic retrieval
   already implements; what is new is the cluster-aware scoping.

4. **Hierarchical summaries at cluster / community level.** For broad
   queries, the directory tier itself is enough — the agent returns a
   cluster-level summary instead of drilling into chunks. This is the
   GraphRAG-style move at the top of the tier stack.

5. **Cross-cluster bridge edges** (from 2.4) participate in ranking once
   reviewed. They surface when the query straddles two clusters and
   provide the breakthrough-connection capability.

The agent can deliberately broaden its scope at any iteration — for
example, walking up from Tier 1 back to Tier 2 to pick a different
cluster, or following a reviewed cross-cluster bridge into a Tier 1
subgraph it did not initially select. The architecture supports
exploration; the default retrieval just does not impose it.

**Identity filtering applies at every tier of the directory, not only at
the chunk level.** Per commitment 2.5, the directory the agent sees is
already filtered: clusters whose membership-derived `identity_visibility`
excludes the active agent identity, or whose `identity_exclusions`
include it, are not visible in the agent's directory view. The agent
does not learn that a hidden cluster exists from the directory. This
holds at every tier: hidden Tier 2 super-clusters elide their Tier 1
sub-clusters too. The corresponding `expand_graph_paths`,
`expand_proximity`, and `semantic_candidates` tool calls also respect
the same identity scoping at every hop, so the agent cannot traverse
into a hidden cluster through a neighbour either.

---

## 4. Ingestion and async consolidation flow

This separates the *per-document* work (cheap, fast, intra-document)
from the *cross-document* work (expensive, async, batched).

### 4.1 Ingestion (per document, online)

When a new document is ingested via the Slice 7 LLM-assisted pipeline:

- Gemma (or whichever ingestion adapter is configured) performs
  intelligent chunking, intra-document relation inference, proximity
  inference, and hierarchical structure recognition. This is the
  rich-intra-document work that the architecture has always intended.
- Gemma also emits a small set of free-form labels per chunk (commitment
  2.1). The model decides how many labels and what each says; the
  ingestion runtime stores them verbatim.
- Each chunk's text similarity profile is computed via the configured
  embedding adapter (Slice 6 Phase A output) and stored alongside the
  chunk.
- **No cross-document comparison happens at ingestion time.** No lookup
  against existing labels, no similarity scan against the whole corpus,
  no cluster assignment. Ingestion remains cheap and bounded per
  document.

### 4.2 Async consolidation (REM, batched)

Several REM passes run asynchronously over recently-ingested content:

- **Label-cluster correlation (Tier 0 → Tier 1).** Lightweight model or
  profile-based clustering proposes that new Tier 0 labels join existing
  clusters or seed new ones. Gemma confirms high-stakes proposals via
  the candidate/review queue. The "lighter models for candidates, Gemma
  for high-value reviews" split named in REQ-CON-07 is the cost
  discipline here.
- **Meta-labelling (Tier 1 → Tier 2).** A periodic LLM pass over
  freshly-grown Tier 1 clusters produces meta-labels. REM correlates
  these into Tier 2.
- **Cross-cluster bridge scan (commitment 2.4).** A periodic pass over
  recently-ingested chunks looks for high-similarity pairs across
  different Tier 1 clusters. Strong bridges become candidate edges.
- **Cluster-internal edge promotion.** Within a Tier 1 cluster, normal
  embedding-similarity candidates feed the existing semantic-edge review
  queue. This is the Slice 6 Phase B mechanism applied within a bounded
  scope rather than over the whole corpus.

REM prioritises recent, high-usage, and novel content. The novelty
priority is what surfaces new senses for human review before they get
buried by the rest of the corpus.

---

## 5. Working-session subgraph maps

During an agentic session, the memory-agent may build a working map of
the chunks and clusters it found relevant. Two storage modes are useful:

- **Ephemeral.** The map exists only for the current session, never
  written to MongoDB. The default.
- **Persistent.** The agent (or the operator) chooses to save the map
  for reuse, versioning, or later review. Stored in MongoDB and exported
  as a human-readable Markdown summary so the operator can inspect what
  the agent considered relevant. Tentative collection: `session_maps`
  or `working_subgraphs`.

### 5.1 Policy point: this is a meaningful change to agent write authority

The current architecture is strictly read-only for the memory-agent.
Persistent working maps are agent-authored content that survives the
session, which crosses the line the project has held so far. The
operator review on 2026-06-05 indicated this is acceptable in principle
but the policy details should be pinned down explicitly:

- Persistent maps **start in the `unreviewed` state**, same as LLM-output
  ingestion already does.
- They **do not feed retrieval ranking** until they have been explicitly
  reviewed and endorsed.
- They are **regeneratable from authoritative state** wherever possible;
  the canonical source for retrieval is still the underlying chunks,
  clusters, and edges. A persistent map is an *index over* that state,
  not a substitute for it.
- They are **subject to the same ingestion epoch versioning** as other
  derived content. A new epoch can supersede old maps.
- They are **visible in the activity log** like any other Mongo write.
  The transparency-first principle applies.

This policy keeps persistent maps consistent with the source-authority
and reviewability commitments while still allowing the agent to
contribute durable scaffolding for future sessions.

---

## 6. Open questions before Slice 4 implementation

Three things should be settled before implementation work starts.

### 6.1 Vector backend (DQ-007)

The current `vector-semantic-candidates` implementation is a bounded
Python scan. That works at small operator-driven scan limits but is not
sufficient for REM passes that have to look across the whole corpus
(label-cluster correlation, cross-cluster bridge discovery). The choice
is between:

- Mongo `$vectorSearch`, if the local install supports it
- A local FAISS or Chroma index alongside Mongo
- Continuing with brute scan plus caching, if the scan can be made fast
  enough at AMS scale

Pick one, pin it in `architecture-decisions.md` (turn DQ-007 into ADR-N).
The strategy in this doc is backend-agnostic, but the implementation
will assume one.

### 6.2 Per-chunk label count tuning (Slice 7)

The labels-are-immutable commitment is robust to whatever count the
ingestion LLM chooses, but in practice the choice affects everything
downstream:

- Too few labels per chunk → Tier 0 → Tier 1 correlation has thin
  evidence to work with; cluster discovery is unreliable.
- Too many labels per chunk → Tier 0 explodes faster, REM correlation
  cost grows, the long tail of single-use labels dominates.

A starting target of 3–7 labels per chunk is plausible but should be
validated against real ingestion runs. This is a Slice 7 tuning
question, not an architectural one.

### 6.3 Taxonomy refresh cadence

How often does the Tier 1 → Tier 2 meta-labelling pass run? Per ingestion
epoch is the simplest answer; that pairs taxonomy refresh with the
existing versioning surface. Manual operator-triggered refresh is the
fallback. Continuous re-evaluation is overkill at current corpus growth
rates.

### 6.4 Cluster governance derivation — confirm the restrictive defaults

Commitment 2.5 picks intersection-for-visibility and maximum-for-
sensitivity by default, on the principle that a cluster cannot be more
permissive than its most-restricted member. Two follow-ons to confirm
before implementation starts:

- Is there ever a case where an operator-authored override should loosen
  a derived cluster scoping? For example, a cluster might be derived as
  restricted because one member is restricted by accident, where the
  operator wants the cluster itself to be accessible. The architecture
  probably says "no — fix the member's scoping instead," because
  override paths create a quiet way to leak. Worth confirming.
- Should governance fields on `semantic_map` cluster records be eagerly
  stored at REM time (cheap to compute on derivation, cheap to read at
  retrieval) or lazily computed on each retrieval (slower to read but
  always current)? Eager storage with epoch-based invalidation is the
  natural fit for the rest of the architecture; flagging it because the
  alternative is non-trivial.

---

## 7. Cross-reference to existing requirements

This strategy is largely a coherent statement of pieces that have been
in the requirements since v0.3 but were never composed into one design:

| This strategy | Existing requirement / decision |
|---|---|
| Sense / label clusters with polysemy support (2.2) | REQ-CON-03 to REQ-CON-05, REQ-SEM-01 to REQ-SEM-04 |
| Lighter model for candidates, Gemma for high-value review (4.2) | REQ-CON-07 |
| Async REM as a scheduled background process (4.2) | REQ-CON-01, REQ-CON-02 |
| Low-confidence edge review queue (4.2, 2.4) | REQ-CON-06 (default threshold 5.0 per DQ-004) |
| Agentic traversal with bounded read-only tools (3) | Existing `compile_context`, `expand_proximity`, `expand_graph_paths` plus the agentic retrieval loop |
| Ingestion epoch versioning of derived content (2.2, 2.3, 5.1) | Slice 2 non-destructive rebuild |
| Adapter boundary for LLM calls (4.1, 4.2) | ADR-004 |
| Source preservation discipline applied to labels (2.1) | ADR-010 (chunk-level provenance), Source Authority principle in `consolidated-requirements-and-design.md` |
| Memory as cognitive infrastructure (5) | Memory As Cognitive Infrastructure principle |
| Transparency-first activity logs for persistent maps (5.1) | Transparency First principle |
| Adapter HTTP boundary applied to embedding generation (4.1) | Local Memory Interface Boundary principle |
| Cluster governance fields derived from membership (2.5) | `governance-schema-plan.md` node/edge governance fields, agent identity layer in `mnemosyne-cognitive-architecture-draft.md` |
| Identity filtering at every directory tier (3) | `governance-schema-plan.md` retrieval-order step 1 (hard exclusions from identity and sensitivity) |

What is new — explicitly so — is the composition: labels-as-immutable and
clusters-as-emergent together, recursive tiering as a derived consequence,
and cross-cluster bridge discovery as a parallel mechanism. These are
named here for the first time as load-bearing architectural commitments
rather than implementation tactics.

---

## Out of scope (rejected on 2026-06-05)

For completeness, three options that appeared in earlier drafts were
explicitly rejected:

- **Pruning of low-value edges.** Violates source-preservation discipline
  and the architecture's existing commitment to non-destructive rebuilds.
  The Slice 2 "explicit garbage collection of derived content" stays as
  an operator escape hatch; routine pruning does not.
- **Summarisation of old content.** Same rationale. Source is preserved,
  even when its labels and clusters evolve.
- **Sharding by domain.** Conflicts with the cross-document discovery
  goal — physical separation makes cross-domain bridges harder. Logical
  scoping via labels is the supported path.

---

## Implementation order

The work this strategy enables fits into the existing slice plan
without inventing a new slice:

1. **Slice 6 Phase A finish.** Live task. Choose and configure the local
   profile adapter (see `.session-log.md` 2026-06-03 [claude] entry).
   This strategy assumes profiles exist per chunk.
2. **Slice 6 Phase B finish.** Cluster-internal candidate/review at small
   scale. The existing review queue mechanism applies; what is new is
   that cluster membership becomes a `candidate_source` distinguished
   from raw `embedding_similarity`.
3. **Slice 4 (REM consolidation) design.** This document is that design.
   Open questions in section 6 should be resolved before implementation
   starts.
4. **Slice 7 (LLM-assisted ingestion).** Adds the free-form per-chunk
   label generation described in 4.1. Per-chunk label count tuning
   (6.2) belongs here.
5. **Slice 8 (ranking and trust integration).** Cluster membership and
   cross-cluster bridge weight become ranking inputs.

Persistent working-session subgraph maps (section 5) are a cross-cutting
addition that can land alongside Slice 4 or be deferred to a later slice
once cluster directory work is in place.
