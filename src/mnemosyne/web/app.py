from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mnemosyne.config import load_config
from mnemosyne.db.client import get_database
from mnemosyne.db.governance import (
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
from mnemosyne.db.indexes import ensure_indexes
from mnemosyne.db.repositories import (
    list_semantic_edge_candidates,
    review_semantic_edge_candidate,
)
from mnemosyne.db.queue import enqueue_source, queue_summary, recent_jobs
from mnemosyne.ingestion.files import move_request_file, sha256_file
from mnemosyne.ingestion.parser import SUPPORTED_SUFFIXES
from mnemosyne.ingestion.worker import discover_sources, process_next
from mnemosyne.retrieval.queries import list_documents, search_nodes
from mnemosyne.retrieval.trust import trust_temporal_diagnostic_for_node
from mnemosyne.sessions.exchanges import recent_exchanges
from mnemosyne.sessions.interaction import answer_query
from mnemosyne.sessions.active_documents import list_active_documents
from mnemosyne.sessions.endorsements import (
    list_generated_output_nodes,
    update_node_endorsement,
)
from mnemosyne.sessions.output_ingestion import (
    list_output_ingestion_jobs,
    process_next_output_ingestion,
)
from mnemosyne.sessions.registry import create_session, list_sessions


class AskRequest(BaseModel):
    query: str
    node_id: str | None = None
    session_id: str = "web"
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


def create_app() -> FastAPI:
    config = load_config()
    db = get_database(config.mongo)
    ensure_indexes(db)

    app = FastAPI(title="Mnemosyne")
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
        return {
            "ok": True,
            "default_adapter": config.runtime.answer_adapter,
            "default_model": config.runtime.ollama_model,
            "memory_agent_adapter": config.runtime.memory_agent_adapter or config.runtime.answer_adapter,
            "memory_agent_model": config.runtime.memory_agent_model or config.runtime.ollama_model,
            "retrieval_mode": config.runtime.retrieval_mode,
            "available_retrieval_modes": ["direct", "agentic"],
            "available_adapters": ["mock", "ollama_cli", "ollama_http"],
            "known_models": sorted(
                {
                    config.runtime.ollama_model,
                    config.runtime.memory_agent_model or config.runtime.ollama_model,
                    "gemma3:1b",
                }
            ),
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

    @app.get("/api/queue")
    def queue() -> dict[str, Any]:
        summary = queue_summary(db)
        if summary["oldest_pending"]:
            summary["oldest_pending"]["_id"] = str(summary["oldest_pending"]["_id"])
            summary["oldest_pending"]["created_at"] = summary["oldest_pending"][
                "created_at"
            ].isoformat()
        return {"ok": True, **summary}

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
            for field in ("created_at", "updated_at"):
                if job.get(field):
                    job[field] = job[field].isoformat()
            rows.append(job)
        return {"ok": True, "jobs": rows}

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
        return {"ok": True, "enqueued": enqueued, "processed": processed}

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


app = create_app()
