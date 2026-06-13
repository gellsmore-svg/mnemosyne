from __future__ import annotations

import hashlib
import subprocess
from math import ceil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tirzah.config import RuntimeConfig, load_config
from tirzah.adapters.embedding import (
    HTTP_BACKED_EMBEDDING_ADAPTERS,
    embedding_adapter,
    local_profile_command,
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
from tirzah.db.indexes import ensure_indexes
from tirzah.db.repositories import (
    backfill_node_embeddings,
    enqueue_semantic_edge_candidates,
    enqueue_vector_semantic_edge_candidate_batch,
    enqueue_vector_semantic_edge_candidates,
    list_semantic_edge_candidates,
    review_semantic_edge_candidate,
)
from tirzah.db.queue import enqueue_source, queue_summary, recent_jobs
from tirzah.ingestion.dates import analyze_source_dates
from tirzah.ingestion.embedding_backfill import (
    create_embedding_backfill_job,
    list_embedding_backfill_jobs,
    process_embedding_backfill_batches,
    requeue_processing_embedding_backfill_job,
)
from tirzah.ingestion.files import move_request_file, sha256_file
from tirzah.ingestion.parser import SUPPORTED_SUFFIXES, read_text_source
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
from tirzah.sessions.interaction import answer_query
from tirzah.sessions.active_documents import list_active_documents
from tirzah.sessions.endorsements import (
    list_generated_output_nodes,
    update_node_endorsement,
)
from tirzah.sessions.output_ingestion import (
    list_output_ingestion_jobs,
    process_next_output_ingestion,
)
from tirzah.sessions.registry import create_session, list_sessions


FALLBACK_KNOWN_MODELS = ["gemma4:latest", "gemma3:1b"]
WEB_EMBEDDING_BACKFILL_MAX_BATCHES = 10
WEB_EMBEDDING_BACKFILL_RECOMMENDED_BATCH_LIMIT = 25


class AskRequest(BaseModel):
    query: str
    node_id: str | None = None
    session_id: str = "web"
    project_domain_id: str | None = None
    conversation_domain_id: str | None = None
    adapter: str | None = None
    model: str | None = None
    retrieval_mode: str | None = None


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

    app = FastAPI(title="Tirzah")
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (static_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database": config.mongo.database}

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
            "available_retrieval_modes": ["direct", "agentic"],
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
        summary = queue_summary(db)
        if summary["oldest_pending"]:
            summary["oldest_pending"]["_id"] = str(summary["oldest_pending"]["_id"])
            summary["oldest_pending"]["created_at"] = summary["oldest_pending"][
                "created_at"
            ].isoformat()
        return {"ok": True, **summary}

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
        rows = []
        for job in recent_jobs(db, limit=limit, status=status, query_text=q, reason=reason):
            job["_id"] = str(job["_id"])
            if job.get("existing_document_id"):
                job["existing_document_id"] = str(job["existing_document_id"])
            if job.get("existing_queue_id"):
                job["existing_queue_id"] = str(job["existing_queue_id"])
            result = job.get("result") or {}
            if result.get("document_id"):
                result["document_id"] = str(result["document_id"])
            for field in ("created_at", "updated_at"):
                if job.get(field):
                    job[field] = job[field].isoformat()
            rows.append(job)
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
        return answer_query(
            db,
            config,
            query=request.query,
            focus_node_id=request.node_id,
            session_id=request.session_id,
            project_domain_id=request.project_domain_id,
            conversation_domain_id=request.conversation_domain_id,
            answer_adapter_name=request.adapter,
            ollama_model=request.model,
            retrieval_mode=request.retrieval_mode,
        )

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

    return app


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


def process_inbox_activity_log(enqueued: list[dict[str, Any]], processed: list[dict[str, Any]]) -> str:
    lines = [
        "Inbox Processing Activity Log",
        f"- Staged source files inspected: {len(enqueued)}.",
        f"- Ingestion jobs processed: {len(processed)}.",
    ]
    if enqueued:
        rejected = [job for job in enqueued if job.get("status") == "rejected"]
        lines.append(f"- Queue intake: {len(enqueued) - len(rejected)} accepted, {len(rejected)} rejected.")
    if not processed:
        lines.append("- Result: no pending ingestion jobs were available to process.")
        return "\n".join(lines)
    for index, result in enumerate(processed, start=1):
        log = result.get("activity_log")
        if log:
            lines.append("")
            lines.append(f"Run {index}")
            lines.append(log)
        else:
            lines.append("")
            lines.append(
                f"Run {index}: {result.get('status', 'unknown')} for "
                f"{result.get('path') or result.get('document_id') or 'unknown source'}."
            )
    return "\n".join(lines)


def ingest_folder_file_rows(ingest_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in discover_sources(ingest_dir):
        try:
            stat = path.stat()
            text, _source_kind = read_text_source(path)
            date_analysis = analyze_source_dates(path, text)
        except Exception as error:
            rows.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "suffix": path.suffix.lower(),
                    "bytes": None,
                    "modified_at": None,
                    "origin_date": None,
                    "origin_date_source": None,
                    "date_candidate_count": 0,
                    "status": "unreadable",
                    "error": error.__class__.__name__,
                    "message": str(error),
                }
            )
            continue
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "suffix": path.suffix.lower(),
                "bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "origin_date": date_analysis.get("origin_date"),
                "origin_date_source": date_analysis.get("origin_date_source"),
                "date_candidate_count": len(date_analysis.get("date_candidates") or []),
                "status": "ready",
            }
        )
    return sorted(rows, key=ingest_folder_sort_key)


def ingest_folder_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (row.get("origin_date") or "9999-12-31", row.get("path") or "")


def list_ingestion_epochs(db: Any, limit: int = 8) -> list[dict[str, Any]]:
    rows = db.documents.aggregate(
        [
            {
                "$group": {
                    "_id": "$ingestion_epoch",
                    "document_count": {"$sum": 1},
                    "dated_document_count": {
                        "$sum": {
                            "$cond": [
                                {"$ne": [{"$ifNull": ["$source.origin_date", None]}, None]},
                                1,
                                0,
                            ]
                        }
                    },
                    "first_created_at": {"$min": "$created_at"},
                    "last_updated_at": {"$max": "$updated_at"},
                    "earliest_origin_date": {
                        "$min": {
                            "$cond": [
                                {"$ne": [{"$ifNull": ["$source.origin_date", None]}, None]},
                                "$source.origin_date",
                                "9999-12-31",
                            ]
                        }
                    },
                    "latest_origin_date": {"$max": "$source.origin_date"},
                }
            },
            {"$sort": {"last_updated_at": -1}},
            {"$limit": max(1, min(int(limit), 50))},
        ]
    )
    epochs = []
    for row in rows:
        earliest_origin_date = row.get("earliest_origin_date")
        if earliest_origin_date == "9999-12-31":
            earliest_origin_date = None
        epochs.append(
            {
                "ingestion_epoch": row.get("_id") or "unknown",
                "document_count": row.get("document_count", 0),
                "dated_document_count": row.get("dated_document_count", 0),
                "first_created_at": serialize_web_value(row.get("first_created_at")),
                "last_updated_at": serialize_web_value(row.get("last_updated_at")),
                "earliest_origin_date": earliest_origin_date,
                "latest_origin_date": row.get("latest_origin_date"),
            }
        )
    return epochs


def embedding_coverage(db: Any, label: str | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {"status": {"$ne": "superseded"}}
    label_filter = str(label).strip() if label else None
    if label_filter:
        query["labels"] = label_filter
    embedded_query = {
        **query,
        "embedding.vector": {"$exists": True},
    }
    total = db.nodes.count_documents(query)
    embedded = db.nodes.count_documents(embedded_query)
    missing = max(0, total - embedded)
    percent = round((embedded / total) * 100, 1) if total else 0.0
    return annotate_embedding_coverage({
        "total_active_nodes": total,
        "embedded_active_nodes": embedded,
        "missing_active_embeddings": missing,
        "embedded_percent": percent,
        "profiles": embedding_profile_counts(db, embedded_query),
        "label": label_filter,
    })


def embedding_profile_counts(db: Any, embedded_query: dict[str, Any]) -> list[dict[str, Any]]:
    rows = db.nodes.aggregate(
        [
            {"$match": embedded_query},
            {
                "$group": {
                    "_id": {
                        "adapter": "$embedding.adapter",
                        "model": "$embedding.model",
                        "dimensions": "$embedding.dimensions",
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
    )
    return [
        {
            "adapter": (row.get("_id") or {}).get("adapter"),
            "model": (row.get("_id") or {}).get("model"),
            "dimensions": (row.get("_id") or {}).get("dimensions"),
            "count": row.get("count", 0),
            "is_mock": str((row.get("_id") or {}).get("adapter") or "").startswith("mock"),
        }
        for row in rows
    ]


def annotate_embedding_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    total = int(coverage.get("total_active_nodes") or 0)
    embedded = int(coverage.get("embedded_active_nodes") or 0)
    missing = int(coverage.get("missing_active_embeddings") or 0)
    profiles = coverage.get("profiles") or []
    mock_count = sum(int(profile.get("count") or 0) for profile in profiles if profile.get("is_mock"))
    profile_count = len(profiles)
    label = coverage.get("label")
    scope = f" for label `{label}`" if label else ""

    if total == 0:
        status = "empty"
        summary = f"No active nodes are available{scope}."
        action = "Ingest source documents before building text similarity profiles or reviewing profile-based semantic candidates."
    elif embedded == 0:
        status = "not_started"
        summary = f"{total} active node(s){scope} exist, but none have text similarity profiles yet."
        action = "Queue a scoped profile backfill job, then process bounded batches until coverage begins to rise."
    elif missing > 0:
        status = "incomplete"
        summary = f"{embedded} of {total} active node(s){scope} have text similarity profiles; {missing} still need profiles."
        action = "Continue processing profile backfill job batches before relying on profile-based candidate review."
    elif mock_count:
        status = "mock_only" if mock_count == embedded else "mixed_mock"
        summary = f"All active nodes{scope} have text similarity profiles, but {mock_count} node(s) use stub profiles."
        action = "Re-run a forced profile backfill with a local model-backed profile adapter before treating profile-based relationships as semantic evidence."
    else:
        status = "ready"
        summary = f"All {total} active node(s){scope} have model-backed text similarity profiles."
        action = "Preview profile matches, enqueue promising semantic-edge candidates, and review them before answer use."

    warnings = []
    if profile_count > 1:
        warnings.append(f"{profile_count} profile representations are present; compare models and dimensions before broad profile-based review.")
    if mock_count and missing:
        warnings.append("Some existing text similarity profiles are stubs while other active nodes still have no profiles.")

    return {
        **coverage,
        "status": status,
        "summary": summary,
        "recommended_action": action,
        "warnings": warnings,
    }


def embedding_backfill_status(
    db: Any,
    coverage: dict[str, Any],
    limit: int = 20,
    embedding_adapter_allowed: bool = True,
    configured_embedding_adapter: str | None = None,
    profile_adapter_status: dict[str, Any] | None = None,
    recommended_batch_limit: int = WEB_EMBEDDING_BACKFILL_RECOMMENDED_BATCH_LIMIT,
    web_max_batches: int = WEB_EMBEDDING_BACKFILL_MAX_BATCHES,
) -> dict[str, Any]:
    jobs = list_embedding_backfill_jobs(db, limit=limit)
    counts: dict[str, int] = {}
    for job in jobs:
        status = job.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1

    next_job = next(
        (job for job in jobs if job.get("status") in {"pending", "processing", "blocked"}),
        None,
    )
    missing = int(coverage.get("missing_active_embeddings") or 0)
    adapter_blocked = not embedding_adapter_allowed and (
        missing > 0 or coverage.get("status") in {"mock_only", "mixed_mock"}
    )
    adapter_status = profile_adapter_status or {}
    local_command_missing = adapter_status.get("status") == "missing_profile_command" and (
        missing > 0 or coverage.get("status") in {"mock_only", "mixed_mock"}
    )
    if adapter_blocked:
        status = "embedding_adapter_blocked"
        summary = (
            f"Configured profile adapter `{configured_embedding_adapter or 'unknown'}` is not allowed "
            "for ingestion or retrieval memory operations."
        )
        action = "Configure a local non-HTTP profile adapter before queueing or processing profile backfill."
    elif local_command_missing:
        status = "profile_command_missing"
        summary = "The local command profile adapter is selected, but no profile command is configured."
        action = "Configure runtime.profile_command before queueing or processing profile backfill."
    elif counts.get("pending"):
        status = "pending"
        summary = f"{counts['pending']} recent profile backfill job(s) are queued."
        action = "Process the next bounded backfill batches and refresh ingestion status."
    elif counts.get("processing"):
        status = "processing"
        summary = f"{counts['processing']} recent profile backfill job(s) are marked processing."
        action = "Refresh status after the current worker finishes; if this persists, inspect the job for a stuck processing state."
    elif counts.get("blocked"):
        status = "blocked"
        summary = f"{counts['blocked']} recent profile backfill job(s) are blocked."
        action = "Open the latest job log, resolve the recorded error, then queue a new scoped backfill if needed."
    elif coverage.get("status") in {"mock_only", "mixed_mock"}:
        status = "real_backfill_needed"
        summary = "Active nodes have text similarity profiles, but at least some are stubs."
        action = "Configure a local model-backed profile adapter, then queue a forced profile backfill before profile-based review."
    elif missing > 0:
        status = "needed"
        summary = "No recent active backfill job is queued, but active nodes still lack text similarity profiles."
        action = "Queue a profile backfill job with a conservative batch size."
    else:
        status = "not_needed"
        summary = "No profile backfill job is currently needed for active-node coverage."
        action = "Move to profile-match preview and reviewed semantic-edge candidate work."

    return {
        "status": status,
        "summary": summary,
        "recommended_action": action,
        "recommended_job": None
        if adapter_blocked or local_command_missing
        else recommended_embedding_backfill_job(
            coverage,
            recommended_batch_limit=recommended_batch_limit,
            web_max_batches=web_max_batches,
        ),
        "recent_status_counts": counts,
        "recent_jobs_checked": len(jobs),
        "next_job": next_job,
    }


def recommended_embedding_backfill_job(
    coverage: dict[str, Any],
    *,
    recommended_batch_limit: int = WEB_EMBEDDING_BACKFILL_RECOMMENDED_BATCH_LIMIT,
    web_max_batches: int = WEB_EMBEDDING_BACKFILL_MAX_BATCHES,
) -> dict[str, Any] | None:
    configured_batch_limit = max(1, min(int(recommended_batch_limit or 1), 1000))
    configured_web_batches = max(1, min(int(web_max_batches or 1), 100))
    missing = int(coverage.get("missing_active_embeddings") or 0)
    coverage_status = coverage.get("status")
    if coverage_status in {"mock_only", "mixed_mock"}:
        target_count = int(coverage.get("total_active_nodes") or coverage.get("embedded_active_nodes") or 0)
        if target_count <= 0:
            return None
        batch_limit = min(configured_batch_limit, target_count)
        total_batches = ceil(target_count / batch_limit) if batch_limit else 0
        recommended_web_batches = min(configured_web_batches, total_batches)
        return {
            "batch_limit": batch_limit,
            "force": True,
            "missing_embedding_only": False,
            "requires_real_adapter": True,
            "estimated_total_batches": total_batches,
            "recommended_web_batches": recommended_web_batches,
            "estimated_nodes_per_web_run": batch_limit * recommended_web_batches,
            "summary": (
                f"After configuring a local model-backed profile adapter, queue a forced backfill with batch limit {batch_limit}; "
                f"process up to {recommended_web_batches} batch(es) per web run. "
                f"Current coverage needs about {total_batches} total forced batch(es)."
            ),
        }
    if missing <= 0:
        return None
    batch_limit = min(configured_batch_limit, missing)
    total_batches = ceil(missing / batch_limit) if batch_limit else 0
    recommended_web_batches = min(configured_web_batches, total_batches)
    return {
        "batch_limit": batch_limit,
        "force": False,
        "missing_embedding_only": True,
        "requires_real_adapter": False,
        "estimated_total_batches": total_batches,
        "recommended_web_batches": recommended_web_batches,
        "estimated_nodes_per_web_run": batch_limit * recommended_web_batches,
        "summary": (
            f"Queue a missing-profile job with batch limit {batch_limit}; "
            f"process up to {recommended_web_batches} batch(es) per web run. "
            f"Current coverage needs about {total_batches} total batch(es)."
        ),
    }


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


def embedding_backfill_batch_failure_reason(result: dict[str, Any]) -> str:
    for batch in reversed(result.get("results") or []):
        batch_result = batch.get("result") or {}
        if batch_result.get("reason"):
            return batch_result["reason"]
    return "embedding_backfill_job_failed"


def embedding_backfill_batch_step_ids(result: dict[str, Any]) -> list[str]:
    steps = []
    for index, batch in enumerate(result.get("results") or [], start=1):
        status = batch.get("status") or "unknown"
        steps.append(f"embedding_backfill_job_batch_{index}_{status}")
    return steps


def serialize_web_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


app = create_app()
