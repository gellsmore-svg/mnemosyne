from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mnemosyne.config import load_config
from mnemosyne.db.client import get_database
from mnemosyne.db.indexes import ensure_indexes
from mnemosyne.db.queue import enqueue_source, queue_summary, recent_jobs
from mnemosyne.ingestion.files import move_request_file, sha256_file
from mnemosyne.ingestion.worker import discover_sources, process_next
from mnemosyne.retrieval.queries import list_documents, search_nodes
from mnemosyne.sessions.exchanges import recent_exchanges
from mnemosyne.sessions.interaction import answer_query
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


app = create_app()
