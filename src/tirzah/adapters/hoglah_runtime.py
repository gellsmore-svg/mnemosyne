"""Shared Hoglah submit/await machinery for Tirzah's hoglah adapters.

Tirzah routes generation and embedding through a Hoglah queue daemon the same
way Mahalath does: Tirzah is a PURE SUBMITTER (Hoglah client constructed with
start_worker=False, so it never executes jobs or recovers another worker's
in-flight jobs — Hoglah ADR-016). A SEPARATE `hoglah run --real` daemon, pointed
at the same db_path + output_dir, executes each job against Ollama and delivers
the terminal result either by writing `<output_dir>/<job_id>.json` (poll) or by
POSTing it to a URL Tirzah supplies per job (callback).

From Tirzah's process this is local IPC (a local SQLite queue + a local result
file / localhost callback); the daemon owns the Ollama HTTP call.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from typing import Any


def import_hoglah_client():
    try:
        return import_module("hoglah").Hoglah
    except ImportError as error:  # pragma: no cover - exercised via factory
        raise RuntimeError(
            "The Hoglah adapters require the optional Hoglah package. "
            'Install with `pip install "tirzah[hoglah]"` or choose another adapter.'
        ) from error


class _CallbackReceiver:
    """Generic stdlib HTTP receiver for Hoglah result callbacks.

    Matches each POSTed result to a waiting submitter by job_id, holding a
    result that arrives before its waiter has registered (the submit/register
    race) until the waiter asks for it.
    """

    def __init__(self, host: str, port: int, path: str = "/hoglah/callback") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, dict[str, Any]] = {}

        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
                if self.path != receiver.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    doc = json.loads(raw.decode("utf-8"))
                    job_id = doc["job_id"]
                except (ValueError, KeyError):
                    self.send_response(400)
                    self.end_headers()
                    return
                receiver._deliver(job_id, doc)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: Any) -> None:  # silence default logging
                pass

        self._server = ThreadingHTTPServer((host, port), _Handler)
        self.host = host
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="tirzah-hoglah-cb"
        )
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def _deliver(self, job_id: str, doc: dict[str, Any]) -> None:
        with self._lock:
            self._results[job_id] = doc
            ev = self._events.get(job_id)
        if ev is not None:
            ev.set()

    def wait_for(self, job_id: str, timeout: float) -> dict[str, Any] | None:
        with self._lock:
            if job_id in self._results:
                return self._results.pop(job_id)
            ev = self._events.setdefault(job_id, threading.Event())
        ev.wait(timeout)
        with self._lock:
            self._events.pop(job_id, None)
            return self._results.pop(job_id, None)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class HoglahJobRunner:
    """Submit generation/embedding jobs into the shared queue and await results.

    One instance is created per adapter; it owns the Hoglah submitter client and
    (in callback mode) the HTTP receiver.
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self.output_dir = Path(config.hoglah_output_dir).expanduser()
        self.poll_interval_seconds = 0.5
        self.transport = getattr(config, "hoglah_transport", "store")

        self._receiver: _CallbackReceiver | None = None
        self._callback_url: str | None = None
        self._client: Any | None = None
        self._submitter: Any | None = None

        if self.transport != "store":
            # Messaging path: publish a job-request over a broker and await the
            # result over the same broker. No SQLite client, no callback receiver.
            from hoglah.messaging_submitter import MessagingSubmitter, make_submitter_transport

            self.delivery = "messaging"
            self._submitter = MessagingSubmitter(
                make_submitter_transport(
                    self.transport,
                    kafka_bootstrap_servers=getattr(config, "hoglah_kafka_bootstrap_servers", None),
                    kafka_input_topic=getattr(config, "hoglah_kafka_input_topic", None),
                    kafka_results_topic=getattr(config, "hoglah_kafka_results_topic", None),
                    rabbitmq_url=getattr(config, "hoglah_rabbitmq_url", None),
                    rabbitmq_input_queue=getattr(config, "hoglah_rabbitmq_input_queue", None),
                    redis_url=getattr(config, "hoglah_redis_url", None),
                    redis_input_stream=getattr(config, "hoglah_redis_input_stream", None),
                    redis_results_stream=getattr(config, "hoglah_redis_results_stream", None),
                )
            )
            return

        # Store path (default): the shared SQLite queue + poll/callback delivery.
        Hoglah = import_hoglah_client()
        self.delivery = getattr(config, "hoglah_delivery", "poll")
        if self.delivery not in ("poll", "callback"):
            raise ValueError(
                f"Unknown hoglah_delivery {self.delivery!r}; expected 'poll' or 'callback'."
            )
        if self.delivery == "callback":
            self._receiver = _CallbackReceiver(
                config.hoglah_callback_host, config.hoglah_callback_port
            )
            self._callback_url = self._receiver.url

        # Pure submitter: no worker, no interrupted-job recovery (Hoglah ADR-016).
        self._client = Hoglah(
            config={
                "db_path": str(Path(config.hoglah_db_path).expanduser()),
                "output_dir": str(self.output_dir),
            },
            start_worker=False,
        )

    @property
    def wait_timeout_seconds(self) -> float:
        configured = self.config.hoglah_wait_timeout_seconds
        return float(configured if configured is not None else self.config.ollama_timeout_seconds)

    # -- submission ---------------------------------------------------------

    def run_generate(
        self,
        prompt_text: str,
        *,
        model: str,
        fmt: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timeout = self.wait_timeout_seconds
        if self._submitter is not None:
            return self._submitter.submit(
                kind="generate",
                prompt=prompt_text,
                model=model,
                timeout=timeout,
                tags=tags,
                metadata=metadata,
                fmt=fmt,
            )
        job_id = self._client.submit(
            prompt=prompt_text,
            model=model,
            format=fmt,
            timeout_seconds=int(timeout),
            callback_url=self._callback_url,
            tags=tags,
            metadata=metadata,
        )
        return self._await_result(job_id, timeout)

    def run_embedding(self, text: str, *, model: str) -> dict[str, Any]:
        timeout = self.wait_timeout_seconds
        if self._submitter is not None:
            return self._submitter.submit(
                kind="embed",
                prompt=text,
                model=model,
                timeout=timeout,
                tags=["tirzah"],
                metadata={"source": "tirzah"},
            )
        job_id = self._client.submit_embedding(
            text,
            model=model,
            timeout_seconds=int(timeout),
            callback_url=self._callback_url,
            tags=["tirzah"],
            metadata={"source": "tirzah"},
        )
        return self._await_result(job_id, timeout)

    # -- delivery -----------------------------------------------------------

    def _read_output_file(self, job_id: str) -> dict[str, Any] | None:
        dest = self.output_dir / f"{job_id}.json"
        if not dest.exists():
            return None
        try:
            return json.loads(dest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _await_result(self, job_id: str, timeout: float) -> dict[str, Any]:
        if self._receiver is not None:
            doc = self._receiver.wait_for(job_id, timeout)
            if doc is not None:
                return doc
            doc = self._read_output_file(job_id)  # push missed; try the file
            if doc is not None:
                return doc
            raise TimeoutError(
                f"Hoglah job {job_id}: no callback within {timeout:.0f}s and no "
                f"output file in {self.output_dir}. Is the `hoglah run` daemon "
                f"running and able to reach {self._callback_url}?"
            )

        deadline = time.monotonic() + timeout
        while True:
            doc = self._read_output_file(job_id)
            if doc is not None:
                return doc
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Hoglah job {job_id} produced no result within {timeout:.0f}s "
                    f"(looked in {self.output_dir}). Is the `hoglah run` daemon "
                    f"running against the same db_path/output_dir?"
                )
            time.sleep(self.poll_interval_seconds)

    def close(self) -> None:
        if self._submitter is not None:
            self._submitter.close()
            self._submitter = None
        if self._receiver is not None:
            self._receiver.close()
            self._receiver = None
