(() => {
  const bar = document.getElementById("global-progress");
  function showBar() {
    if (bar) bar.classList.add("on");
  }
  function hideBar() {
    if (bar) bar.classList.remove("on");
  }

  document.querySelectorAll("form[data-ajax='1']").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      showBar();
      try {
        const fd = new FormData(form);
        const res = await fetch(form.action, { method: "POST", body: fd, credentials: "same-origin" });
        if (!res.ok) throw new Error(String(res.status));
        window.location.reload();
      } catch {
        hideBar();
        alert("Save failed — check your inputs and try again.");
      }
    });
  });

  const logBox = document.getElementById("console-out");
  async function refreshLogs() {
    if (!logBox) return;
    try {
      const r = await fetch("/api/logs?limit=250", { credentials: "same-origin" });
      const j = await r.json();
      const lines = (j.logs || [])
        .map((l) => `[${l.ts}] ${l.level} ${l.logger}: ${l.message}`)
        .join("\n");
      logBox.textContent = lines || "No logs yet.";
      logBox.scrollTop = logBox.scrollHeight;
    } catch {
      logBox.textContent = "Could not load logs.";
    }
  }

  const live = document.querySelector("main[data-live-console]");
  if (live) {
    refreshLogs();
    setInterval(refreshLogs, 4000);
  }

  const snap = document.getElementById("live-snapshot");
  async function refreshSnap() {
    if (!snap) return;
    try {
      const r = await fetch("/api/summary", { credentials: "same-origin" });
      const j = await r.json();
      const s = j.snapshot || {};
      snap.innerHTML = `
        <div class="stat mini stat-glow"><b>${s.latency_ms ?? "—"}</b><span class="muted">Latency (ms)</span></div>
        <div class="stat mini stat-glow"><b>${s.guild_member_count ?? "—"}</b><span class="muted">Members</span></div>
        <div class="stat mini stat-glow"><b>${j.open ?? "—"}</b><span class="muted">Open (sync)</span></div>
        <div class="stat mini stat-glow"><b>${(j.counts && j.counts.logs) ?? "—"}</b><span class="muted">Log rows</span></div>
      `;
    } catch {
      snap.textContent = "Snapshot unavailable.";
    }
  }
  if (snap) {
    refreshSnap();
    setInterval(refreshSnap, 8000);
  }
})();
