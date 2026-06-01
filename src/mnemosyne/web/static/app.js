const $ = (id) => document.getElementById(id);
const DISPLAY_MODE_KEY = "mnemosyne.displayMode";

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

function renderRunningActivity(payload) {
  $("activityReport").textContent = "Technical report will appear when the run finishes.";
  $("activityLog").replaceChildren(
    activityEntry("Prompt received", `Session ${payload.session_id || "web"}. Mnemosyne is preparing the request.`),
    activityEntry("Context selection started", "The retrieval controller is checking whether memory context should be used."),
    activityEntry("Waiting for model handoff", "If context is found, Mnemosyne will hand a readable prompt/context package to the selected LLM.")
  );
}

function renderActivityReport(report, logText = "", trace = []) {
  $("activityReport").textContent = report ? JSON.stringify(report, null, 2) : "No technical report.";
  if (Array.isArray(trace) && trace.length) {
    $("activityLog").replaceChildren(...trace.map((step, index) => activityEntryForTrace(step, index)));
    return;
  }
  if (!report && !logText) {
    $("activityLog").textContent = "No activity log.";
    return;
  }
  $("activityLog").textContent = logText || "No human-readable activity log was returned.";
}

function activityEntryForTrace(step, index) {
  const name = step.step || "step";
  const output = step.output || {};
  const entry = activityEntry(`${index + 1}. ${activityTitle(name)}`, activitySummary(step));
  const payload = activityPayload(step);
  if (payload) {
    const details = document.createElement("details");
    details.className = "activity-details";
    const summary = document.createElement("summary");
    summary.textContent = payload.title;
    const pre = document.createElement("pre");
    pre.textContent = payload.text;
    details.append(summary, pre);
    entry.appendChild(details);
  }
  if (output.ok === false) entry.classList.add("blocked");
  return entry;
}

function activityEntry(title, body) {
  const section = document.createElement("section");
  section.className = "activity-entry";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const textNode = document.createElement("p");
  textNode.textContent = body;
  section.append(heading, textNode);
  return section;
}

function activityTitle(stepName) {
  return {
    user_prompt: "Prompt captured",
    retrieval_context: "Mnemosyne built the context package",
    memory_agent_iteration: "Mnemosyne handed retrieval planning to the memory-agent LLM",
    answer_adapter: "Mnemosyne handed the final prompt to the answer LLM",
    save_exchange: "Answer saved for continuity",
    request_started: "Request started",
    request_failed: "Request failed",
  }[stepName] || stepName.replaceAll("_", " ");
}

function activitySummary(step) {
  const input = step.input || {};
  const output = step.output || {};
  switch (step.step) {
    case "user_prompt":
      return `Received "${text(input.query)}" for session ${text(input.session_id)}.`;
    case "retrieval_context":
      return contextSummary(output);
    case "memory_agent_iteration":
      return memoryAgentSummary(input, output);
    case "answer_adapter":
      return answerAdapterSummary(input, output);
    case "save_exchange":
      return `Saved exchange ${text(output.exchange_id)} and updated continuity metadata.`;
    case "request_started":
      return `Preparing ${text(input.retrieval_mode || output.retrieval_mode)} retrieval with ${text(input.adapter || output.adapter)}.`;
    case "request_failed":
      return text(output.message || output.reason || "The request failed before completion.");
    default:
      return "Step completed.";
  }
}

function answerAdapterSummary(input, output) {
  const decision = input.controller_decision;
  const decisionText = decision && decision.reason ? ` Controller: ${decision.reason}` : "";
  return `Final answer call used ${text(input.adapter)} / ${text(input.model)} and ${output.ok === false ? "did not complete" : "returned an answer"}.${decisionText}${evidenceSummaryText(input.evidence_summary)}`;
}

function memoryAgentSummary(input, output) {
  const toolResults = output.tool_results || [];
  const failed = toolResults.filter((result) => result && result.ok === false);
  const okCount = toolResults.filter((result) => result && result.ok !== false).length;
  const status = text(output.stop_reason || output.status || "a decision");
  if (failed.length) {
    const first = failed[0];
    return `Memory-agent iteration ${text(input.iteration)} used ${text(input.adapter)} / ${text(input.model)}. Mnemosyne ran ${toolResults.length} memory-tool call(s): ${okCount} succeeded, ${failed.length} need repair. First issue: ${text(first.tool)} - ${text(first.error)}.`;
  }
  return `Memory-agent iteration ${text(input.iteration)} used ${text(input.adapter)} / ${text(input.model)} and returned ${status}.`;
}

function contextSummary(output) {
  const metadata = output.context_metadata || {};
  const included = metadata.included || [];
  const status = output.retrieval_status || metadata.retrieval_status || "unknown";
  const decision = output.controller_decision || metadata.controller_decision || output.retrieval_decision || metadata.retrieval_decision || {};
  const owner = decision.current_owner ? ` (${decision.current_owner})` : "";
  const reason = decision.reason ? ` Controller${owner}: ${decision.reason}` : "";
  if (included.length) {
    return `Mnemosyne selected ${included.length} repository context record(s). Retrieval status: ${status}.${reason}`;
  }
  return `Mnemosyne did not include repository nodes. Retrieval status: ${status}.${reason}`;
}

function evidenceSummaryText(summary) {
  if (!summary || typeof summary !== "object") return "";
  const parts = [];
  if (summary.included_node_count != null) {
    parts.push(`${summary.included_node_count} repository node(s) included`);
  }
  if (summary.source_fallback_used) {
    parts.push("source-file fallback used");
  }
  if (summary.tool_result_count != null) {
    parts.push(`${summary.tool_result_count} memory-tool result(s): ${summary.successful_tool_count || 0} successful, ${summary.failed_tool_count || 0} failed`);
  }
  if (Array.isArray(summary.source_documents) && summary.source_documents.length) {
    const names = summary.source_documents
      .slice(0, 3)
      .map((document) => document.title || document.document_id || "untitled source");
    const more = summary.source_documents.length > names.length ? `, plus ${summary.source_documents.length - names.length} more` : "";
    parts.push(`sources: ${names.join(", ")}${more}`);
  }
  return parts.length ? ` Evidence: ${parts.join("; ")}.` : "";
}

function activityPayload(step) {
  const input = step.input || {};
  const output = step.output || {};
  if (step.step === "retrieval_context" && output.context_text) {
    const metadata = output.context_metadata || {};
    const controllerDecision = output.controller_decision || metadata.controller_decision;
    const retrievalDecision = output.retrieval_decision || metadata.retrieval_decision;
    return {
      title: "Show controller decision and assembled context",
      text: [
        "Controller decision:",
        "",
        controllerDecision ? JSON.stringify(controllerDecision, null, 2) : "No controller decision recorded.",
        "",
        "Retrieval guardrail detail:",
        "",
        retrievalDecision ? JSON.stringify(retrievalDecision, null, 2) : "No retrieval guardrail detail recorded.",
        "",
        "Evidence summary:",
        "",
        metadata.evidence_summary ? JSON.stringify(metadata.evidence_summary, null, 2) : "No evidence summary recorded.",
        "",
        "Assembled context:",
        "",
        output.context_text,
      ].join("\n"),
    };
  }
  if (step.step === "memory_agent_iteration" && input.prompt_text) {
    const failed = (output.tool_results || []).filter((result) => result && result.ok === false);
    if (failed.length) {
      return {
        title: "Show LLM prompt and memory-tool repair guidance",
        text: [
          "Prompt sent to memory-agent LLM:",
          "",
          input.prompt_text,
          "",
          "Memory-tool repair guidance from failed calls:",
          "",
          JSON.stringify(failed.map((result) => ({
            tool: result.tool,
            arguments: result.arguments,
            error: result.error,
            usage: result.usage,
            repair_instruction: result.repair_instruction,
          })), null, 2),
        ].join("\n"),
      };
    }
    return {
      title: "Show prompt sent to memory-agent LLM",
      text: input.prompt_text,
    };
  }
  if (step.step === "answer_adapter" && input.prompt_text) {
    return {
      title: input.controller_decision
        ? "Show controller decision, prompt, and context sent to answer LLM"
        : "Show prompt and context sent to answer LLM",
      text: [
        input.controller_decision
          ? ["Controller decision:", "", JSON.stringify(input.controller_decision, null, 2), ""].join("\n")
          : "",
        input.evidence_summary
          ? ["Evidence summary:", "", JSON.stringify(input.evidence_summary, null, 2), ""].join("\n")
          : "",
        "Prompt and context sent to answer LLM:",
        "",
        input.prompt_text,
      ].filter(Boolean).join("\n"),
    };
  }
  return null;
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    const active = button.dataset.tab === tabId;
    button.classList.toggle("active", active);
    button.classList.toggle("secondary", !active);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === tabId);
  });
  $("appShell").classList.toggle("ask-mode", tabId === "askTab");
  $("appShell").classList.toggle("browse-mode", tabId === "browseTab");
  $("appShell").classList.toggle("ingestion-mode", tabId === "ingestionTab");
}

function requestedDisplayMode() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get("display");
  if (fromUrl === "epaper" || fromUrl === "standard") return fromUrl;
  return localStorage.getItem(DISPLAY_MODE_KEY) || "standard";
}

function setDisplayMode(mode) {
  const epaper = mode === "epaper";
  document.body.classList.toggle("epaper", epaper);
  localStorage.setItem(DISPLAY_MODE_KEY, epaper ? "epaper" : "standard");
  $("displayMode").textContent = epaper ? "Standard" : "E-paper";
  $("displayMode").setAttribute("aria-pressed", epaper ? "true" : "false");
}

function toggleDisplayMode() {
  setDisplayMode(document.body.classList.contains("epaper") ? "standard" : "epaper");
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
    ...(data.model_options || data.known_models.map((model) => ({ name: model, label: model }))).map(
      (model) => new Option(model.label || model.name, model.name)
    ),
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

async function loadIngestionStatus() {
  const data = await api("/api/ingestion/status?limit=8");
  if (!data.ok || !Array.isArray(data.epochs) || !Array.isArray(data.runs)) {
    $("ingestionStatus").innerHTML = `<div class="item">Ingestion status is unavailable.</div>`;
    return;
  }
  const epochItems = data.epochs.length
    ? data.epochs.map((epoch, index) => {
        const range = [epoch.earliest_origin_date, epoch.latest_origin_date]
          .filter(Boolean)
          .join(" to ");
        return (
          `<div class="item">` +
          `<strong>${index === 0 ? "Current epoch: " : "Epoch: "}${html(epoch.ingestion_epoch)}</strong>` +
          `<div class="muted">${html(String(epoch.document_count))} document(s)` +
          ` | ${html(String(epoch.dated_document_count || 0))} dated` +
          `${range ? ` | origin range ${html(range)}` : ""}` +
          `${epoch.last_updated_at ? ` | updated ${html(epoch.last_updated_at)}` : ""}</div>` +
          `</div>`
        );
      })
    : [`<div class="item">No ingestion epochs recorded yet.</div>`];
  const runItems = data.runs.length
    ? data.runs.map((run) =>
        `<div class="item"><strong>${html(run.status)} · ${html(run.process_id)}</strong>` +
        `<div>${html(run.current_step_id || "no current step")}</div>` +
        `<div class="muted">${html(run.run_id)} | ${html(run.updated_at || "")}</div></div>`
      )
    : [`<div class="item">No ingestion process runs recorded yet.</div>`];
  $("ingestionStatus").innerHTML =
    `<h3>Epochs</h3>${epochItems.join("")}<h3>Recent Ingestion Runs</h3>${runItems.join("")}`;
}

async function loadJobs() {
  const params = new URLSearchParams();
  params.set("limit", $("jobLimit").value || "6");
  if ($("jobStatus").value) params.set("status", $("jobStatus").value);
  if ($("jobQuery").value) params.set("q", $("jobQuery").value);
  if ($("jobReason").value) params.set("reason", $("jobReason").value);
  const data = await api(`/api/jobs?${params.toString()}`);
  $("jobs").replaceChildren(
    ...data.jobs.map((job) => {
      const result = job.result || {};
      const log = result.activity_log || "";
      const meta = [
        job._id,
        `attempts ${job.attempts}`,
        job.updated_at,
        result.ingestion_epoch ? `epoch ${result.ingestion_epoch}` : "",
      ].filter(Boolean).join(" | ");
      const el = item(
        `<strong>${html(job.status)}</strong> <span class="muted">${job.reason ? html(job.reason) : ""}</span>` +
        `<div>${html(job.path)}</div>` +
        `<div class="muted">${html(meta)}</div>`
      );
      if (log) {
        const details = document.createElement("details");
        details.className = "technical-report";
        details.innerHTML = `<summary>Readable ingestion log</summary><pre>${html(log)}</pre>`;
        el.appendChild(details);
      }
      return el;
    })
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
  renderRunningActivity(payload);
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
      renderActivityReport(data.activity_report, data.activity_log, data.process_trace);
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
    renderActivityReport(data.activity_report, data.activity_log, data.process_trace);
    renderConsole(data.process_trace);
    await Promise.all([loadSessions(), loadHistory()]);
  } catch (error) {
    $("answerText").textContent = error.message;
    renderActivityReport(null, "", [
      {
        step: "request_failed",
        input: payload,
        output: {
          ok: false,
          reason: "request_failed",
          message: error.message,
        },
      },
    ]);
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
  $("processResult").textContent =
    data.activity_log || JSON.stringify(data, null, 2);
  await Promise.all([loadQueue(), loadIngestionStatus(), loadJobs(), loadDocuments(), searchNodes()]);
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
            `<div class="item"><strong>${html(file.name)}</strong><br><small>${html(
              file.path
            )} · ${file.bytes == null ? "unreadable" : `${html(String(file.bytes))} bytes`}` +
            `${file.status === "unreadable" ? ` · ${html(file.error || "read error")}` : ""}` +
            `${file.origin_date ? ` · origin ${html(file.origin_date)} (${html(file.origin_date_source || "unknown")})` : ""}` +
            ` · ${html(String(file.date_candidate_count || 0))} date candidate(s)</small></div>`
        )
        .join("")
    : `<div class="item">No supported files in ${html(data.path)}.</div>`;
}

async function refresh() {
  await Promise.all([
    loadHealth(),
    loadRuntime(),
    loadSessions(),
    loadDocuments(),
    loadHistory(),
    loadQueue(),
    loadIngestionStatus(),
    loadJobs(),
    loadSemanticCandidates(),
    searchNodes(),
  ]);
}

$("ask").addEventListener("click", ask);
$("createSession").addEventListener("click", createSession);
$("search").addEventListener("click", searchNodes);
$("refresh").addEventListener("click", refresh);
$("displayMode").addEventListener("click", toggleDisplayMode);
$("uploadSource").addEventListener("click", uploadSourceFiles);
$("browseIngest").addEventListener("click", browseIngestFolder);
$("processInbox").addEventListener("click", processInbox);
$("loadIngestionStatus").addEventListener("click", loadIngestionStatus);
$("loadHistory").addEventListener("click", loadHistory);
$("loadJobs").addEventListener("click", loadJobs);
$("loadSemanticCandidates").addEventListener("click", loadSemanticCandidates);
document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});
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

setDisplayMode(requestedDisplayMode());

refresh().catch((error) => {
  $("health").textContent = error.message;
});
