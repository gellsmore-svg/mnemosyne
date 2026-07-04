from __future__ import annotations

import hashlib
import json
import threading
from contextlib import asynccontextmanager
from queue import Empty as QueueEmpty
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tirzah.config import load_config
from tirzah.adapters.embedding import embedding_adapter
from tirzah.adapters.discovery import (
    FALLBACK_KNOWN_MODELS,
    model_options_with_fallbacks,
    ollama_model_rows,
    parse_ollama_model_list,
    parse_ollama_model_rows,
    profile_adapter_status,
    runtime_embedding_adapter_allowed,
    runtime_memory_agent_adapter_name,
)
from tirzah.ingestion.status import (
    annotate_embedding_coverage,
    embedding_backfill_batch_failure_reason,
    embedding_backfill_batch_step_ids,
    embedding_backfill_status,
    embedding_coverage,
    ingest_folder_file_rows,
    list_ingestion_epochs,
    process_inbox_activity_log,
    recommended_embedding_backfill_job,
)
from tirzah.db.client import get_database
from tirzah.db.governance import (
    PROCESS_RUN_STATUSES,
    create_process_run,
    get_agent_identity,
    get_governance_policy,
    get_process_object,
    get_process_run,
    get_trust_weighting_profile,
    list_agent_identities,
    list_governance_policies,
    list_process_objects,
    list_process_runs,
    list_trust_weighting_profiles,
    update_process_run,
)
from tirzah.db.health import memory_health_payload
from tirzah.db.indexes import ensure_indexes
from tirzah.db.serializers import serialize_queue_job, serialize_queue_summary
from tirzah.db.repositories import (
    backfill_node_embeddings,
    enqueue_semantic_edge_candidates,
    enqueue_vector_semantic_edge_candidate_batch,
    enqueue_vector_semantic_edge_candidates,
    list_semantic_edge_candidates,
    review_semantic_edge_candidate,
)
from tirzah.db.queue import enqueue_source, queue_summary, recent_jobs
from tirzah.ingestion.embedding_backfill import (
    create_embedding_backfill_job,
    list_embedding_backfill_jobs,
    process_embedding_backfill_batches,
    requeue_processing_embedding_backfill_job,
)
from tirzah.ingestion.files import move_request_file, sha256_file
from tirzah.ingestion.parser import SUPPORTED_SUFFIXES
from tirzah.ingestion.worker import discover_sources, process_next
from tirzah.retrieval.queries import (
    embedding_candidate_report,
    expand_graph_paths,
    expand_proximity,
    graph_edges_for_node,
    list_documents,
    search_nodes,
)
from tirzah.retrieval.trust import trust_temporal_diagnostic_for_node
from tirzah.sessions.exchanges import recent_exchanges
from tirzah.sessions.interaction import answer_query, backfill_chunks, backfill_turn_embeddings
from tirzah.sessions.run import run_traced_interaction
from galeed import (
    EventType,
    Tracer,
    get_bus,
    list_feedback,
    list_trace_events,
    list_trace_sessions,
    record_feedback,
)
from tirzah.planning.execution_store import (
    compact_execution_summary,
    get_plan_execution,
    list_plan_executions,
)
from tirzah.planning.recursive import (
    list_plan_revisions,
    revise_saved_plan,
)
from tirzah.sessions.active_documents import list_active_documents
from tirzah.sessions.continuity import session_continuity
from tirzah.sessions.endorsements import (
    list_generated_output_nodes,
    update_node_endorsement,
)
from tirzah.sessions.output_ingestion import (
    list_output_ingestion_jobs,
    process_next_output_ingestion,
)
from tirzah.sessions.registry import create_session, list_sessions


UI_NOT_BUILT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Tirzah</title>
<style>body{font-family:system-ui,sans-serif;background:#0f1117;color:#e6e8ee;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.box{max-width:560px;padding:2rem;line-height:1.6}code{background:#1d212c;padding:2px 6px;border-radius:6px}
a{color:#7c9cff}</style></head><body><div class="box">
<h1>Tirzah</h1><p>The web UI (Mahlah) has not been built yet.</p>
<p>Build and install it:</p><pre><code>scripts/build_ui.sh</code></pre>
<p>Or run the Mahlah dev server for hot reload:</p>
<pre><code>cd ../Mahlah &amp;&amp; npm install &amp;&amp; npm run dev</code></pre>
<p>then open <a href="http://localhost:5273">http://localhost:5273</a>. The API is live here.</p>
</div></body></html>"""


class AskRequest(BaseModel):
    query: str
    node_id: str | None = None
    session_id: str = "web"
    project_domain_id: str | None = None
    conversation_domain_id: str | None = None
    adapter: str | None = None
    model: str | None = None
    retrieval_mode: str | None = None
    web_research: bool | None = None
    recursive_planning: bool | None = None


class FeedbackRequest(BaseModel):
    text: str
    session_id: str = "web"
    trace_id: str | None = None
    message_id: str | None = None
    source: str = "user"
    kind: str | None = None  # e.g. bug | ui | reasoning | idea
    context: dict[str, Any] | None = None  # references to the current answer/process/log


class RevisePlanRequest(BaseModel):
    new_information: dict[str, Any]
    session_id: str = "web"


class CreateSessionRequest(BaseModel):
    title: str | None = None
    session_id: str | None = None


class EndorseNodeRequest(BaseModel):
    node_id: str
    endorsement: str
    reviewer: str = "user"
    note: str | None = None


class ReviewSemanticEdgeCandidateRequest(BaseModel):
    candidate_id: str
    action: str
    reviewer: str = "user"
    note: str | None = None
    weight: float = 0.7
    confidence: float = 0.6


class EnqueueSemanticEdgeCandidatesRequest(BaseModel):
    node_id: str
    candidate_source: str = "label_overlap"
    include_same_document: bool = False
    relation_type: str = "related_to"
    created_by: str = "web"
    min_similarity: float = 0.75
    limit: int = 10
    candidate_scan_limit: int | None = None


class EnqueueVectorSemanticBatchRequest(BaseModel):
    label: str | None = None
    document_id: str | None = None
    focus_limit: int = 25
    candidates_per_node: int = 2
    include_same_document: bool = False
    relation_type: str = "related_to"
    created_by: str = "web"
    min_similarity: float = 0.75
    candidate_scan_limit: int | None = None
    exclude_node_keys: list[str] = []
    dry_run: bool = True


class CreateProcessRunRequest(BaseModel):
    process_id: str
    session_id: str = "web"
    identity_id: str | None = None
    current_step_id: str | None = None
    status: str = "active"


class UpdateProcessRunRequest(BaseModel):
    status: str | None = None
    current_step_id: str | None = None
    completed_step_id: str | None = None
    exchange_id: str | None = None
    exception: dict[str, Any] | None = None


class UploadSourceRequest(BaseModel):
    filename: str
    content: str


class BackfillEmbeddingsRequest(BaseModel):
    limit: int = 50
    label: str | None = None
    document_id: str | None = None
    force: bool = False


class RequeueEmbeddingBackfillJobRequest(BaseModel):
    reason: str = "operator_requeued_processing_job"
    actor: str = "web"


def create_app() -> FastAPI:
    config = load_config()
    db = get_database(config.mongo)
    ensure_indexes(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Restart-safe catch-up: embed/chunk any turns the in-process queue missed.
        # Runs on real serve startup (not on plain TestClient import). No-op if the
        # respective feature is off. Background + best-effort.
        if config.retrieval.conversation_semantic_recall:
            threading.Thread(
                target=backfill_turn_embeddings,
                args=(db, config, config.runtime),
                kwargs={"limit": 1000},
                daemon=True,
            ).start()
        if config.retrieval.conversation_chunking:
            threading.Thread(
                target=backfill_chunks,
                args=(db, config, config.runtime),
                kwargs={"limit": 50},  # chunking is a slow LLM call; modest per-startup catch-up
                daemon=True,
            ).start()
        yield

    app = FastAPI(title="Tirzah", lifespan=lifespan)
    # Single UI: the backend serves the built Mahlah front end (the old hand-rolled
    # static UI is retired). Built assets are installed into web/static by
    # scripts/build_ui.sh (gitignored). In dev, run Mahlah on :5273 instead.
    static_dir = Path(__file__).parent / "static"
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        index_file = static_dir / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return UI_NOT_BUILT_HTML

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database": config.mongo.database}

    @app.get("/api/memory-health")
    def memory_health() -> dict[str, Any]:
        return memory_health_payload(db)

    @app.get("/api/capabilities")
    def capabilities(format: str = "full") -> dict[str, Any]:
        # Keturah manifest of Tirzah's LLM-consumable interfaces; ?format=mcp for MCP.
        from tirzah.manifest import build_manifest

        built = build_manifest()
        return built.to_mcp() if format == "mcp" else built.to_dict()

    @app.get("/api/registry")
    def registry(format: str = "full") -> dict[str, Any]:
        # Federated view: Tirzah + every importable sibling manifest. ?format=mcp for
        # the union as MCP tools/list (tool names namespaced product.tool).
        from tirzah.manifest import family_registry

        reg = family_registry()
        return reg.to_mcp() if format == "mcp" else reg.to_dict()

    @app.get("/api/runtime")
    def runtime() -> dict[str, Any]:
        discovered_models = ollama_model_rows(config.runtime)
        model_options = model_options_with_fallbacks(
            discovered_models,
            [
                config.runtime.ollama_model,
                config.runtime.memory_agent_model or config.runtime.ollama_model,
                *FALLBACK_KNOWN_MODELS,
            ],
        )
        return {
            "ok": True,
            "default_adapter": config.runtime.answer_adapter,
            "default_model": config.runtime.ollama_model,
            "default_embedding_adapter": config.runtime.embedding_adapter,
            "default_embedding_model": config.runtime.embedding_model,
            "profile_backfill_recommended_batch_limit": config.runtime.profile_backfill_recommended_batch_limit,
            "profile_backfill_web_max_batches": config.runtime.profile_backfill_web_max_batches,
            "memory_agent_adapter": runtime_memory_agent_adapter_name(config.runtime),
            "memory_agent_adapter_policy": "local_only_no_http",
            "memory_agent_model": config.runtime.memory_agent_model or config.runtime.ollama_model,
            "retrieval_mode": config.runtime.retrieval_mode,
            "available_retrieval_modes": ["direct", "agentic", "deep"],
            "available_adapters": ["mock", "ollama_cli", "ollama_http"],
            "available_embedding_adapters": ["mock", "local_command"],
            "non_compliant_embedding_adapters": ["ollama_http", "ollama_powershell"],
            "embedding_adapter_policy": "ingestion_and_retrieval_no_http",
            "profile_adapter_status": profile_adapter_status(config.runtime),
            "known_models": [model["name"] for model in model_options],
            "discovered_models": [model["name"] for model in discovered_models],
            "model_options": model_options,
            "ollama_timeout_seconds": config.runtime.ollama_timeout_seconds,
        }

    @app.get("/api/documents")
    def documents(limit: int = 10) -> dict[str, Any]:
        return {"ok": True, "documents": list_documents(db, limit=limit)}

    @app.get("/api/sessions")
    def sessions(limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "sessions": list_sessions(db, limit=limit)}

    @app.get("/api/active-documents")
    def active_documents(session_id: str = "web", limit: int = 20) -> dict[str, Any]:
        return {
            "ok": True,
            "session_id": session_id,
            "documents": list_active_documents(db, session_id=session_id, limit=limit),
        }

    @app.get("/api/governance/agent-identities")
    def governance_agent_identities(limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "identities": list_agent_identities(db, limit=limit)}

    @app.get("/api/governance/agent-identities/{identity_id}")
    def governance_agent_identity(identity_id: str) -> dict[str, Any]:
        identity = get_agent_identity(db, identity_id)
        return {"ok": identity is not None, "identity": identity}

    @app.get("/api/governance/trust-weighting-profiles")
    def governance_trust_weighting_profiles(limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "profiles": list_trust_weighting_profiles(db, limit=limit)}

    @app.get("/api/governance/trust-weighting-profiles/{weighting_profile_id}")
    def governance_trust_weighting_profile(weighting_profile_id: str) -> dict[str, Any]:
        profile = get_trust_weighting_profile(db, weighting_profile_id)
        return {"ok": profile is not None, "profile": profile}

    @app.get("/api/governance/trust-diagnostics/nodes/{node_id}")
    def governance_trust_diagnostic(
        node_id: str,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        diagnostic = trust_temporal_diagnostic_for_node(
            db,
            node_id,
            weighting_profile_id=profile_id,
        )
        return {"ok": diagnostic is not None, "result": diagnostic}

    @app.get("/api/governance/policies")
    def governance_policies(limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "policies": list_governance_policies(db, limit=limit)}

    @app.get("/api/governance/policies/{policy_id}")
    def governance_policy(policy_id: str) -> dict[str, Any]:
        policy = get_governance_policy(db, policy_id)
        return {"ok": policy is not None, "policy": policy}

    @app.get("/api/governance/process-objects")
    def governance_process_objects(limit: int = 20) -> dict[str, Any]:
        return {"ok": True, "processes": list_process_objects(db, limit=limit)}

    @app.get("/api/governance/process-objects/{process_id}")
    def governance_process_object(process_id: str) -> dict[str, Any]:
        process = get_process_object(db, process_id)
        return {"ok": process is not None, "process": process}

    @app.get("/api/governance/process-runs")
    def governance_process_runs(
        limit: int = 20,
        session_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "runs": list_process_runs(
                db,
                session_id=session_id,
                status=status,
                limit=limit,
            ),
        }

    @app.get("/api/governance/process-runs/{run_id}")
    def governance_process_run(run_id: str) -> dict[str, Any]:
        run = get_process_run(db, run_id)
        return {"ok": run is not None, "run": run}

    @app.post("/api/governance/process-runs")
    def governance_create_process_run(request: CreateProcessRunRequest) -> dict[str, Any]:
        if request.status not in PROCESS_RUN_STATUSES:
            return {"ok": False, "error": "unsupported_process_run_status", "run": None}
        return {
            "ok": True,
            "run": create_process_run(
                db,
                process_id=request.process_id,
                session_id=request.session_id,
                identity_id=request.identity_id,
                current_step_id=request.current_step_id,
                status=request.status,
            ),
        }

    @app.patch("/api/governance/process-runs/{run_id}")
    def governance_update_process_run(
        run_id: str,
        request: UpdateProcessRunRequest,
    ) -> dict[str, Any]:
        if request.status is not None and request.status not in PROCESS_RUN_STATUSES:
            return {"ok": False, "error": "unsupported_process_run_status", "run": None}
        run = update_process_run(
            db,
            run_id,
            status=request.status,
            current_step_id=request.current_step_id,
            completed_step_id=request.completed_step_id,
            exchange_id=request.exchange_id,
            exception=request.exception,
        )
        return {"ok": run is not None, "run": run}

    @app.get("/api/output-ingestion")
    def output_ingestion(
        limit: int = 20,
        status: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "jobs": list_output_ingestion_jobs(
                db,
                limit=limit,
                status=status,
                session_id=session_id,
            ),
        }

    @app.post("/api/process-output-ingestion")
    def process_output_ingestion() -> dict[str, Any]:
        return process_next_output_ingestion(db)

    @app.get("/api/review/generated-output")
    def generated_output_review(
        limit: int = 20,
        endorsement: str | None = None,
    ) -> dict[str, Any]:
        try:
            nodes = list_generated_output_nodes(
                db,
                limit=limit,
                endorsement_label=endorsement,
            )
        except ValueError as error:
            return {"ok": False, "reason": "invalid_endorsement_label", "error": str(error)}
        return {
            "ok": True,
            "nodes": nodes,
        }

    @app.post("/api/review/endorse-node")
    def endorse_node(request: EndorseNodeRequest) -> dict[str, Any]:
        return update_node_endorsement(
            db,
            node_id=request.node_id,
            endorsement_label=request.endorsement,
            reviewer=request.reviewer,
            note=request.note,
        )

    @app.get("/api/review/semantic-edge-candidates")
    def semantic_edge_candidates(
        limit: int = 20,
        status: str | None = "pending",
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "candidates": list_semantic_edge_candidates(
                db,
                status=status,
                limit=limit,
            ),
        }

    @app.get("/api/review/vector-semantic-candidates")
    def vector_semantic_candidates(
        node_id: str,
        limit: int = 10,
        include_same_document: bool = False,
        min_similarity: float = 0.75,
        candidate_scan_limit: int | None = None,
    ) -> dict[str, Any]:
        return embedding_candidate_report(
            db,
            node_id=node_id,
            limit=limit,
            include_same_document=include_same_document,
            min_similarity=min_similarity,
            candidate_scan_limit=candidate_scan_limit,
        )

    @app.post("/api/review/enqueue-semantic-edge-candidates")
    def enqueue_semantic_edge_review_candidates(
        request: EnqueueSemanticEdgeCandidatesRequest,
    ) -> dict[str, Any]:
        source = str(request.candidate_source or "label_overlap").strip().lower()
        if source == "embedding_similarity":
            return enqueue_vector_semantic_edge_candidates(
                db,
                node_id=request.node_id,
                limit=request.limit,
                include_same_document=request.include_same_document,
                relation_type=request.relation_type,
                created_by=request.created_by,
                min_similarity=request.min_similarity,
                candidate_scan_limit=request.candidate_scan_limit,
            )
        if source == "label_overlap":
            return enqueue_semantic_edge_candidates(
                db,
                node_id=request.node_id,
                limit=request.limit,
                include_same_document=request.include_same_document,
                relation_type=request.relation_type,
                created_by=request.created_by,
            )
        return {
            "ok": False,
            "reason": "invalid_candidate_source",
            "candidate_source": request.candidate_source,
        }

    @app.post("/api/review/enqueue-vector-semantic-batch")
    def enqueue_vector_semantic_batch_review_candidates(
        request: EnqueueVectorSemanticBatchRequest,
    ) -> dict[str, Any]:
        return enqueue_vector_semantic_edge_candidate_batch(
            db,
            label=request.label,
            document_id=request.document_id,
            focus_limit=request.focus_limit,
            candidates_per_node=request.candidates_per_node,
            include_same_document=request.include_same_document,
            relation_type=request.relation_type,
            created_by=request.created_by,
            min_similarity=request.min_similarity,
            candidate_scan_limit=request.candidate_scan_limit,
            exclude_node_keys=request.exclude_node_keys,
            dry_run=request.dry_run,
        )

    @app.post("/api/review/semantic-edge-candidate")
    def review_semantic_edge(request: ReviewSemanticEdgeCandidateRequest) -> dict[str, Any]:
        return review_semantic_edge_candidate(
            db,
            candidate_id=request.candidate_id,
            action=request.action,
            reviewer=request.reviewer,
            note=request.note,
            weight=request.weight,
            confidence=request.confidence,
        )

    @app.post("/api/sessions")
    def new_session(request: CreateSessionRequest) -> dict[str, Any]:
        return {"ok": True, "session": create_session(db, title=request.title, session_id=request.session_id)}

    @app.get("/api/search")
    def search(query: str = "", label: str | None = None, limit: int = 10) -> dict[str, Any]:
        return {
            "ok": True,
            "nodes": search_nodes(db, query=query or None, label=label, limit=limit),
        }

    @app.get("/api/graph/edges/{node_id}")
    def graph_edges(
        node_id: str,
        direction: str = "both",
        relation_type: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "node_id": node_id,
            "edges": graph_edges_for_node(
                db,
                node_id=node_id,
                direction=direction,
                relation_type=relation_type,
                limit=limit,
            ),
        }

    @app.get("/api/graph/proximity/{node_id}")
    def graph_proximity(
        node_id: str,
        direction: str = "both",
        relation_type: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "node_id": node_id,
            "nodes": expand_proximity(
                db,
                node_id=node_id,
                direction=direction,
                relation_type=relation_type,
                limit=limit,
            ),
        }

    @app.get("/api/graph/paths/{node_id}")
    def graph_paths(
        node_id: str,
        direction: str = "both",
        relation_type: str | None = None,
        max_depth: int = 2,
        limit: int = 10,
        branch_limit: int = 5,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "node_id": node_id,
            "paths": expand_graph_paths(
                db,
                node_id=node_id,
                direction=direction,
                relation_type=relation_type,
                max_depth=max_depth,
                limit=limit,
                branch_limit=branch_limit,
            ),
        }

    @app.get("/api/queue")
    def queue() -> dict[str, Any]:
        return {"ok": True, **serialize_queue_summary(queue_summary(db))}

    @app.get("/api/ingestion/status")
    def ingestion_status(limit: int = 8, label: str | None = None) -> dict[str, Any]:
        embedding = embedding_coverage(db, label=label)
        return {
            "ok": True,
            "epochs": list_ingestion_epochs(db, limit=limit),
            "embedding": embedding,
            "embedding_backfill": embedding_backfill_status(
                db,
                embedding,
                embedding_adapter_allowed=runtime_embedding_adapter_allowed(config.runtime),
                configured_embedding_adapter=config.runtime.embedding_adapter,
                profile_adapter_status=profile_adapter_status(config.runtime),
                recommended_batch_limit=config.runtime.profile_backfill_recommended_batch_limit,
                web_max_batches=config.runtime.profile_backfill_web_max_batches,
            ),
            "runs": list_process_runs(
                db,
                session_id="ingestion",
                limit=limit,
            ),
        }

    @app.post("/api/backfill-embeddings")
    def backfill_embeddings(request: BackfillEmbeddingsRequest) -> dict[str, Any]:
        process_run = create_process_run(
            db,
            process_id="embedding_backfill",
            session_id="ingestion",
            current_step_id="embedding_backfill_running",
            status="active",
        )
        try:
            embedder = embedding_adapter(config.runtime)
        except ValueError as error:
            result = {
                "ok": False,
                "reason": "embedding_adapter_not_allowed",
                "message": str(error),
            }
            update_process_run(
                db,
                process_run["run_id"],
                status="blocked",
                current_step_id="embedding_backfill_blocked",
                completed_step_id="embedding_backfill_adapter_check",
                exception={
                    "reason": result["reason"],
                    "proposal": "Configure a local non-HTTP text similarity profile adapter before ingestion/backfill.",
                    "details": result,
                },
            )
            return {**result, "process_run_id": process_run["run_id"], "process_status": "blocked"}
        result = backfill_node_embeddings(
            db,
            embedder,
            limit=request.limit,
            label=request.label,
            document_id=request.document_id,
            force=request.force,
        )
        process_status = "completed" if result.get("ok") else "blocked"
        update_process_run(
            db,
            process_run["run_id"],
            status=process_status,
            current_step_id="embedding_backfill_completed" if result.get("ok") else "embedding_backfill_blocked",
            completed_step_id="embedding_backfill_batch",
            exception=None
            if result.get("ok")
            else {
                "reason": result.get("reason") or "embedding_backfill_failed",
                "proposal": "Inspect sampled profile-building errors, reduce scope, or verify the profile adapter.",
                "details": {
                    "error_count": result.get("error_count"),
                    "errors": result.get("errors", []),
                },
            },
        )
        return {
            **result,
            "process_run_id": process_run["run_id"],
            "process_status": process_status,
        }

    @app.get("/api/embedding-backfill-jobs")
    def embedding_backfill_jobs(limit: int = 10, status: str | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "jobs": list_embedding_backfill_jobs(db, status=status, limit=limit),
        }

    @app.post("/api/embedding-backfill-jobs")
    def create_embedding_backfill(request: BackfillEmbeddingsRequest) -> dict[str, Any]:
        return {
            "ok": True,
            "job": create_embedding_backfill_job(
                db,
                batch_limit=request.limit,
                label=request.label,
                document_id=request.document_id,
                force=request.force,
                created_by="web",
            ),
        }

    @app.post("/api/embedding-backfill-jobs/{job_id}/requeue")
    def requeue_embedding_backfill_job(
        job_id: str,
        request: RequeueEmbeddingBackfillJobRequest,
    ) -> dict[str, Any]:
        result = requeue_processing_embedding_backfill_job(
            db,
            job_id,
            reason=request.reason,
            actor=request.actor,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result)
        return result

    @app.post("/api/process-embedding-backfill-job")
    def process_embedding_backfill_job(max_batches: int = 1) -> dict[str, Any]:
        bounded_batches = max(1, min(max_batches, config.runtime.profile_backfill_web_max_batches))
        process_run = create_process_run(
            db,
            process_id="embedding_backfill_job_batch",
            session_id="ingestion",
            current_step_id="embedding_backfill_job_batch_running",
            status="active",
        )
        try:
            embedder = embedding_adapter(config.runtime)
        except ValueError as error:
            result = {
                "ok": False,
                "status": "blocked",
                "reason": "embedding_adapter_not_allowed",
                "message": str(error),
                "requested_batches": bounded_batches,
                "processed_batches": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "results": [],
            }
            update_process_run(
                db,
                process_run["run_id"],
                status="blocked",
                current_step_id="embedding_backfill_job_batch_blocked",
                completed_step_id="embedding_backfill_job_adapter_check",
                exception={
                    "reason": result["reason"],
                    "proposal": "Configure a local non-HTTP text similarity profile adapter before processing profile jobs.",
                    "details": result,
                },
            )
            return {**result, "process_run_id": process_run["run_id"], "process_status": "blocked"}
        result = process_embedding_backfill_batches(
            db,
            embedder,
            max_batches=bounded_batches,
        )
        if result.get("status") == "idle":
            update_process_run(
                db,
                process_run["run_id"],
                status="completed",
                current_step_id="embedding_backfill_job_idle",
                completed_step_id="embedding_backfill_job_poll",
            )
            return {**result, "process_run_id": process_run["run_id"], "process_status": "completed"}
        process_status = "blocked" if result.get("status") == "blocked" else "completed"
        for step_id in embedding_backfill_batch_step_ids(result):
            update_process_run(
                db,
                process_run["run_id"],
                completed_step_id=step_id,
            )
        update_process_run(
            db,
            process_run["run_id"],
            status=process_status,
            current_step_id="embedding_backfill_job_batch_blocked"
            if process_status == "blocked"
            else "embedding_backfill_job_batch_processed",
            completed_step_id="embedding_backfill_job_run",
            exception=None
            if process_status == "completed"
            else {
                "reason": embedding_backfill_batch_failure_reason(result),
                "proposal": "Inspect the profile backfill job result, fix the adapter or scope, then queue a new job if appropriate.",
                "details": result,
            },
        )
        return {**result, "process_run_id": process_run["run_id"], "process_status": process_status}

    @app.get("/api/jobs")
    def jobs(
        limit: int = 10,
        status: str | None = None,
        q: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        rows = [
            serialize_queue_job(job)
            for job in recent_jobs(db, limit=limit, status=status, query_text=q, reason=reason)
        ]
        return {"ok": True, "jobs": rows}

    @app.get("/api/ingest-folder")
    def ingest_folder() -> dict[str, Any]:
        files = ingest_folder_file_rows(config.paths.ingest)
        return {
            "ok": True,
            "path": str(config.paths.ingest),
            "ordering": "origin_date_then_path",
            "files": files,
            "count": len(files),
        }

    @app.post("/api/process-inbox")
    def process_inbox() -> dict[str, Any]:
        enqueued = []
        for path in discover_sources(config.paths.ingest):
            checksum = sha256_file(path)
            job = enqueue_source(db, path, checksum)
            dead_letter_path = None
            if job["status"] == "rejected" and path.exists():
                dead_letter_path = move_request_file(path, config.paths.dead_letter / "duplicate", checksum)
                db.queue.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"details.dead_letter_path": str(dead_letter_path)}},
                )
            enqueued.append(
                {
                    "job_id": str(job["_id"]),
                    "path": job["path"],
                    "status": job["status"],
                    "reason": job.get("reason"),
                    "dead_letter_path": str(dead_letter_path) if dead_letter_path else None,
                }
            )
        processed = []
        while True:
            result = process_next(db, config)
            if result["status"] == "idle":
                break
            processed.append(result)
        return {
            "ok": True,
            "enqueued": enqueued,
            "processed": processed,
            "activity_log": process_inbox_activity_log(enqueued, processed),
        }

    @app.post("/api/upload-source")
    def upload_source(request: UploadSourceRequest) -> dict[str, Any]:
        filename = safe_upload_filename(request.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported source type: {suffix or '<none>'}",
            )
        config.paths.ingest.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        destination = unique_upload_path(config.paths.ingest, filename, content_hash)
        destination.write_text(request.content, encoding="utf-8", newline="")
        return {
            "ok": True,
            "status": "staged",
            "path": str(destination),
            "filename": destination.name,
            "checksum_sha256": content_hash,
            "bytes": destination.stat().st_size,
        }

    @app.post("/api/ask")
    def ask(request: AskRequest) -> dict[str, Any]:
        # One mechanism: web, CLI ask, and CLI chat all route through
        # run_traced_interaction, producing the same 3-channel contract + trace.
        return run_traced_interaction(
            db,
            config,
            query=request.query,
            session_id=request.session_id,
            executor=answer_query,
            planning_enabled=request.recursive_planning,
            focus_node_id=request.node_id,
            project_domain_id=request.project_domain_id,
            conversation_domain_id=request.conversation_domain_id,
            answer_adapter_name=request.adapter,
            ollama_model=request.model,
            retrieval_mode=request.retrieval_mode,
            web_research=request.web_research,
        )

    @app.get("/api/plan-executions")
    def plan_executions(
        session_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        rows = list_plan_executions(db, session_id, status=status, limit=limit)
        return {
            "ok": True,
            "session_id": session_id,
            "executions": [compact_execution_summary(row) for row in rows],
        }

    @app.get("/api/plan-executions/{plan_id}")
    def plan_execution_detail(
        plan_id: str,
        revision: int,
        session_id: str,
    ) -> dict[str, Any]:
        row = get_plan_execution(db, plan_id, revision, session_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Unknown plan execution: {plan_id}")
        return {"ok": True, "execution": row}

    @app.get("/api/plans/{plan_id}")
    def plan_revisions(plan_id: str, limit: int = 20) -> dict[str, Any]:
        revisions = list_plan_revisions(db, plan_id, limit=limit)
        if not revisions:
            raise HTTPException(status_code=404, detail=f"Unknown plan: {plan_id}")
        return {"ok": True, "plan_id": plan_id, "revisions": revisions, "latest": revisions[-1]}

    @app.post("/api/plans/{plan_id}/revise")
    def revise_request_plan(plan_id: str, request: RevisePlanRequest) -> dict[str, Any]:
        try:
            plan = revise_saved_plan(
                db,
                config,
                plan_id=plan_id,
                new_information=request.new_information,
                session_id=request.session_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "plan": plan.to_dict()}

    @app.get("/api/history")
    def history(
        limit: int = 10,
        session_id: str | None = None,
        q: str | None = None,
        adapter: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "exchanges": recent_exchanges(
                db,
                limit=limit,
                session_id=session_id,
                query_text=q,
                adapter=adapter,
                model=model,
            ),
        }

    @app.get("/api/session-continuity")
    def continuity(session_id: str = "default", limit: int = 5) -> dict[str, Any]:
        return {"ok": True, **session_continuity(db, session_id=session_id, limit=limit)}

    # --- Trace / process channel (separate from the answer) -----------------
    @app.get("/api/trace/sessions")
    def trace_sessions(limit: int = 200) -> dict[str, Any]:
        """List sessions in the trace store (for the log browser / Mizpah)."""
        return {"ok": True, "sessions": list_trace_sessions(db, limit=limit)}

    @app.get("/api/trace/events")
    def trace_events(
        trace_id: str | None = None,
        session_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Replay persisted process events for a trace/session (dev-log initial load / poll)."""
        events = list_trace_events(db, trace_id=trace_id, session_id=session_id, limit=limit)
        return {"ok": True, "traceId": trace_id, "sessionId": session_id, "events": events}

    @app.get("/api/trace/stream")
    def trace_stream(trace_id: str | None = None, session_id: str | None = None, replay: bool = True):
        """Live process/log stream (SSE) for the process panel and dev-log window.

        Linked to a trace or session. With ``replay=true`` (default) it first
        replays recent history then streams new events — used by the dev-log
        window. With ``replay=false`` it streams only new events — used by the
        live process panel, which wants just the current request's steps.
        """
        channel = trace_id or session_id or "*"
        bus = get_bus()

        def event_stream():
            yield ": connected\n\n"
            if replay:
                for event in list_trace_events(db, trace_id=trace_id, session_id=session_id, limit=200):
                    yield _sse_frame(event)
            with bus.subscribe(channel) as subscription:
                while True:
                    try:
                        event = subscription.get(timeout=15)
                        yield _sse_frame(event.to_dict())
                    except QueueEmpty:
                        yield ": keepalive\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # --- Feedback (free-text brain-dump tied to the current trace) ----------
    @app.post("/api/feedback")
    def submit_feedback(request: FeedbackRequest) -> dict[str, Any]:
        """Capture developer/AI feedback against the current session/trace.

        Stored as a structured ``feedback`` record AND mirrored into the trace
        stream as a ``feedback.submitted`` event, without disrupting the chat.
        """
        record = record_feedback(
            db,
            text=request.text,
            session_id=request.session_id,
            trace_id=request.trace_id,
            message_id=request.message_id,
            source=request.source,
            kind=request.kind,
            context=request.context,
        )
        tracer = Tracer(
            trace_id=request.trace_id,
            session_id=request.session_id,
            db=db,
            source="feedback",
            message_id=request.message_id,
        )
        tracer.emit(
            EventType.FEEDBACK_SUBMITTED,
            summary=(request.text[:120] + "…") if len(request.text) > 120 else request.text,
            feedback_id=record["feedback_id"],
            author=request.source,
            kind=request.kind,
        )
        return {"ok": True, "feedbackId": record["feedback_id"], "traceId": tracer.trace_id, "feedback": record}

    @app.get("/api/feedback")
    def feedback_list(session_id: str | None = None, trace_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"ok": True, "feedback": list_feedback(db, session_id=session_id, trace_id=trace_id, limit=limit)}

    return app


def _sse_frame(event: dict[str, Any]) -> str:
    """Format one trace event as a Server-Sent Events frame.

    Data-only (no ``event:`` line) so a single ``EventSource.onmessage`` handler
    receives every event regardless of type; the type lives in the JSON payload.
    """
    return f"data: {json.dumps(event)}\n\n"


def safe_upload_filename(filename: str) -> str:
    name = Path(filename or "").name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="filename is required")
    return name


def unique_upload_path(directory: Path, filename: str, content_hash: str) -> Path:
    path = directory / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    return directory / f"{stem}-{content_hash[:12]}{suffix}"

app = create_app()
