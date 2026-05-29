const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function text(value) {
  return value == null || value === "" ? "none" : String(value);
}

function html(value) {
  return text(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function item(contentHtml) {
  const div = document.createElement("div");
  div.className = "item";
  div.innerHTML = contentHtml;
  return div;
}

function renderConsole(trace) {
  if (!Array.isArray(trace) || trace.length === 0) {
    $("runLog").textContent = "No process trace.";
    return;
  }
  $("runLog").replaceChildren(
    ...trace.map((step, index) => {
      const details = document.createElement("details");
      details.open = index === trace.length - 1;
      const summary = document.createElement("summary");
      summary.textContent = `${index + 1}. ${step.step}`;
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(
        {
          input: step.input,
          output: step.output,
        },
        null,
        2
      );
      details.append(summary, pre);
      return details;
    })
  );
}

function renderActivityReport(report, logText = "") {
  if (!report && !logText) {
    $("activityLog").textContent = "No activity log.";
    $("activityReport").textContent = "No activity report.";
    return;
  }
  $("activityLog").textContent = logText || "No human-readable activity log was returned.";
  $("activityReport").textContent = report ? JSON.stringify(report, null, 2) : "No technical report.";
}

async function loadHealth() {
  const data = await api("/api/health");
  $("health").textContent = `Mongo: ${data.database}`;
}

async function loadRuntime() {
  const data = await api("/api/runtime");
  window.mnemosyneRuntime = data;
  const adapter = $("adapter");
  const defaultAdapter = adapter.querySelector("option[value='']");
  defaultAdapter.textContent = `default (${data.default_adapter})`;
  const retrievalMode = $("retrievalMode");
  const defaultRetrievalMode = retrievalMode.querySelector("option[value='']");
  defaultRetrievalMode.textContent = `default (${data.retrieval_mode})`;
  const modelOptions = [
    new Option(`default (${data.default_model})`, ""),
    ...data.known_models.map((model) => new Option(model, model)),
  ];
  $("model").replaceChildren(...modelOptions);
}

async function loadDocuments() {
  const data = await api("/api/documents?limit=6");
  $("documents").replaceChildren(
    ...data.documents.map((doc) =>
      item(`<strong>${html(doc.title)}</strong><div class="muted">${html(doc.document_id)}</div>`)
    )
  );
}

async function loadSessions() {
  const previous = $("sessionId").value || "web";
  const data = await api("/api/sessions?limit=30");
  const options = data.sessions.length
    ? data.sessions.map((session) => {
        const label = `${session.title} (${session.session_id})`;
        return new Option(label, session.session_id);
      })
    : [new Option("web", "web")];
  if (!options.some((option) => option.value === "web")) {
    options.unshift(new Option("Web (web)", "web"));
  }
  $("sessionId").replaceChildren(...options);
  $("sessionId").value = options.some((option) => option.value === previous) ? previous : options[0].value;
  if (!$("historySession").value) {
    $("historySession").value = $("sessionId").value;
  }
}

async function loadHistory() {
  const params = new URLSearchParams();
  params.set("limit", $("historyLimit").value || "6");
  const sessionValue = $("historySession").value || $("sessionId").value;
  if (sessionValue) params.set("session_id", sessionValue);
  if ($("historyQuery").value) params.set("q", $("historyQuery").value);
  if ($("historyAdapter").value) params.set("adapter", $("historyAdapter").value);
  if ($("historyModel").value) params.set("model", $("historyModel").value);
  const data = await api(`/api/history?${params.toString()}`);
  $("history").replaceChildren(
    ...data.exchanges.map((ex) =>
      item(`<strong>${html(ex.query)}</strong><div>${html(text(ex.answer).slice(0, 180))}</div><div class="muted">${html(ex.session_id)} | ${html(ex.adapter)} ${html(ex.model)} | ${html(ex.created_at)}</div>`)
    )
  );
}

async function loadQueue() {
  const data = await api("/api/queue");
  const statuses = Object.entries(data.statuses)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" | ");
  $("queueStatus").textContent = `Queue: ${statuses || "empty"}`;
}

async function loadJobs() {
  const params = new URLSearchParams();
  params.set("limit", $("jobLimit").value || "6");
  if ($("jobStatus").value) params.set("status", $("jobStatus").value);
  if ($("jobQuery").value) params.set("q", $("jobQuery").value);
  if ($("jobReason").value) params.set("reason", $("jobReason").value);
  const data = await api(`/api/jobs?${params.toString()}`);
  $("jobs").replaceChildren(
    ...data.jobs.map((job) =>
      item(`<strong>${html(job.status)}</strong> <span class="muted">${job.reason ? html(job.reason) : ""}</span><div>${html(job.path)}</div><div class="muted">${html(job._id)} | attempts ${html(job.attempts)} | ${html(job.updated_at)}</div>`)
    )
  );
}

async function loadSemanticCandidates() {
  const data = await api("/api/review/semantic-edge-candidates?status=pending&limit=8");
  $("semanticCandidateStatus").textContent = `Candidates: ${data.candidates.length} pending shown`;
  $("semanticCandidates").replaceChildren(
    ...data.candidates.map((candidate) => {
      const el = item(
        `<strong>${html(candidate.relation_type)}</strong>` +
        `<div>${html(candidate.source_title)} -> ${html(candidate.target_title)}</div>` +
        `<div class="muted">${html(candidate.candidate_id)} | labels ${html((candidate.shared_labels || []).join(", "))}</div>`
      );
      const actions = document.createElement("div");
      actions.className = "button-row";
      const accept = document.createElement("button");
      accept.textContent = "Accept";
      accept.addEventListener("click", () => reviewSemanticCandidate(candidate.candidate_id, "accept"));
      const reject = document.createElement("button");
      reject.textContent = "Reject";
      reject.className = "secondary";
      reject.addEventListener("click", () => reviewSemanticCandidate(candidate.candidate_id, "reject"));
      actions.append(accept, reject);
      el.appendChild(actions);
      return el;
    })
  );
}

async function reviewSemanticCandidate(candidateId, action) {
  const data = await api("/api/review/semantic-edge-candidate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate_id: candidateId,
      action,
      reviewer: "web",
      note: `${action} from web operator panel`,
    }),
  });
  $("semanticCandidateStatus").textContent = data.ok
    ? `Candidate ${action}ed`
    : `Review failed: ${data.reason || "unknown"}`;
  await loadSemanticCandidates();
}

async function searchNodes() {
  const query = encodeURIComponent($("searchQuery").value);
  const data = await api(`/api/search?query=${query}&limit=8`);
  $("nodes").replaceChildren(
    ...data.nodes.map((node) => {
      const el = item(`<strong>${html(node.title)}</strong><div>${html(node.text_preview)}</div><div class="muted">${html(node.node_id)}</div>`);
      const button = document.createElement("button");
      button.textContent = "Focus";
      button.addEventListener("click", () => {
        $("nodeId").value = node.node_id;
      });
      el.appendChild(button);
      return el;
    })
  );
}

async function ask() {
  const payload = {
    query: $("query").value,
    node_id: $("nodeId").value || null,
    session_id: $("sessionId").value || "web",
    adapter: $("adapter").value || null,
    model: $("model").value || null,
    retrieval_mode: $("retrievalMode").value || null,
  };
  $("answerText").textContent = "Thinking...";
  $("answerMeta").innerHTML = "";
  const timeout = window.mnemosyneRuntime?.ollama_timeout_seconds;
  renderConsole([
    {
      step: "request_started",
      input: payload,
      output: {
        status: "running",
        adapter: payload.adapter || "default",
      model: payload.model || "default",
      retrieval_mode: payload.retrieval_mode || "default",
      timeout_seconds: timeout || null,
      },
    },
  ]);
  $("ask").disabled = true;
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!data.ok) {
      $("answerText").textContent = data.message || JSON.stringify(data, null, 2);
      renderActivityReport(data.activity_report, data.activity_log);
      renderConsole(data.process_trace || [
        {
          step: "request_failed",
          input: payload,
          output: data,
        },
      ]);
      return;
    }
    $("answerText").textContent = data.answer;
    $("answerMeta").innerHTML = `<div class="muted">exchange ${html(data.exchange_id)} | ${html(data.adapter)} ${html(data.model)}</div>`;
    renderActivityReport(data.activity_report, data.activity_log);
    renderConsole(data.process_trace);
    await Promise.all([loadSessions(), loadHistory()]);
  } catch (error) {
    $("answerText").textContent = error.message;
    renderConsole([
      {
        step: "request_failed",
        input: payload,
        output: {
          ok: false,
          reason: "request_failed",
          message: error.message,
          adapter: payload.adapter || "default",
          model: payload.model || "default",
        },
      },
    ]);
  } finally {
    $("ask").disabled = false;
  }
}

async function createSession() {
  const title = $("newSessionTitle").value || null;
  const data = await api("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  $("newSessionTitle").value = "";
  await loadSessions();
  $("sessionId").value = data.session.session_id;
  $("historySession").value = data.session.session_id;
  await loadHistory();
}

async function processInbox() {
  $("processResult").textContent = "Processing...";
  const data = await api("/api/process-inbox", { method: "POST" });
  $("processResult").textContent = JSON.stringify(data, null, 2);
  await Promise.all([loadQueue(), loadJobs(), loadDocuments(), searchNodes()]);
}

async function uploadSourceFiles() {
  const files = Array.from($("sourceUpload").files || []);
  if (!files.length) {
    $("processResult").textContent = "Select .md/.txt files to stage.";
    return;
  }
  $("processResult").textContent = "Staging files...";
  const staged = [];
  for (const file of files) {
    const content = await file.text();
    staged.push(
      await api("/api/upload-source", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, content }),
      })
    );
  }
  $("sourceUpload").value = "";
  $("processResult").textContent = JSON.stringify({ ok: true, staged }, null, 2);
  await loadQueue();
}

async function browseIngestFolder() {
  const data = await api("/api/ingest-folder");
  $("ingestFiles").innerHTML = data.files.length
    ? data.files
        .map(
          (file) =>
            `<div class="item"><strong>${escapeHtml(file.name)}</strong><br><small>${escapeHtml(
              file.path
            )} · ${file.bytes} bytes</small></div>`
        )
        .join("")
    : `<div class="item">No supported files in ${escapeHtml(data.path)}.</div>`;
}

async function refresh() {
  await Promise.all([
    loadHealth(),
    loadRuntime(),
    loadSessions(),
    loadDocuments(),
    loadHistory(),
    loadQueue(),
    loadJobs(),
    loadSemanticCandidates(),
    searchNodes(),
  ]);
}

$("ask").addEventListener("click", ask);
$("createSession").addEventListener("click", createSession);
$("search").addEventListener("click", searchNodes);
$("refresh").addEventListener("click", refresh);
$("uploadSource").addEventListener("click", uploadSourceFiles);
$("browseIngest").addEventListener("click", browseIngestFolder);
$("processInbox").addEventListener("click", processInbox);
$("loadHistory").addEventListener("click", loadHistory);
$("loadJobs").addEventListener("click", loadJobs);
$("loadSemanticCandidates").addEventListener("click", loadSemanticCandidates);
$("sessionId").addEventListener("change", () => {
  $("historySession").value = $("sessionId").value;
  loadHistory();
});
$("historyQuery").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadHistory();
});
$("historySession").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadHistory();
});
$("historyModel").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadHistory();
});
$("historyLimit").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadHistory();
});
$("historyAdapter").addEventListener("change", loadHistory);
$("jobQuery").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadJobs();
});
$("jobReason").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadJobs();
});
$("jobLimit").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadJobs();
});
$("jobStatus").addEventListener("change", loadJobs);
$("query").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") ask();
});

refresh().catch((error) => {
  $("health").textContent = error.message;
});
