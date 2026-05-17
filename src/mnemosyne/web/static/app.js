const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function text(value) {
  return value == null || value === "" ? "none" : String(value);
}

function item(html) {
  const div = document.createElement("div");
  div.className = "item";
  div.innerHTML = html;
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
      item(`<strong>${doc.title}</strong><div class="muted">${doc.document_id}</div>`)
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
  const sessionValue = $("historySession").value || $("sessionId").value;
  const session = encodeURIComponent(sessionValue);
  const suffix = session ? `&session_id=${session}` : "";
  const data = await api(`/api/history?limit=6${suffix}`);
  $("history").replaceChildren(
    ...data.exchanges.map((ex) =>
      item(`<strong>${ex.query}</strong><div>${text(ex.answer).slice(0, 180)}</div><div class="muted">${ex.adapter} ${text(ex.model)}</div>`)
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
  const data = await api("/api/jobs?limit=6");
  $("jobs").replaceChildren(
    ...data.jobs.map((job) =>
      item(`<strong>${job.status}</strong> <span class="muted">${job.reason || ""}</span><div>${job.path}</div><div class="muted">${job._id}</div>`)
    )
  );
}

async function searchNodes() {
  const query = encodeURIComponent($("searchQuery").value);
  const data = await api(`/api/search?query=${query}&limit=8`);
  $("nodes").replaceChildren(
    ...data.nodes.map((node) => {
      const el = item(`<strong>${node.title}</strong><div>${node.text_preview}</div><div class="muted">${node.node_id}</div>`);
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
    $("answerMeta").innerHTML = `<div class="muted">exchange ${data.exchange_id} | ${data.adapter} ${text(data.model)}</div>`;
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

async function refresh() {
  await Promise.all([
    loadHealth(),
    loadRuntime(),
    loadSessions(),
    loadDocuments(),
    loadHistory(),
    loadQueue(),
    loadJobs(),
    searchNodes(),
  ]);
}

$("ask").addEventListener("click", ask);
$("createSession").addEventListener("click", createSession);
$("search").addEventListener("click", searchNodes);
$("refresh").addEventListener("click", refresh);
$("processInbox").addEventListener("click", processInbox);
$("loadHistory").addEventListener("click", loadHistory);
$("sessionId").addEventListener("change", () => {
  $("historySession").value = $("sessionId").value;
  loadHistory();
});
$("query").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") ask();
});

refresh().catch((error) => {
  $("health").textContent = error.message;
});
