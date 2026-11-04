// Live "currently processing" panel. Polls /api/status; when a job finishes
// the page reloads so the new activity row shows up.
(function () {
  const box = document.getElementById("now-processing");
  if (!box) return;
  const $ = id => document.getElementById(id);
  const fill = $("np-fill");
  let wasActive = !box.hidden;

  function fmt(sec) {
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  function render(cur) {
    if (!cur) {
      box.hidden = true;
      if (wasActive && box.dataset.reload === "1") location.reload();
      wasActive = false;
      return;
    }
    wasActive = true;
    box.hidden = false;
    $("np-file").textContent = cur.file;
    $("np-workflow").textContent = cur.workflow;
    const pct = cur.fraction == null ? null : Math.round(cur.fraction * 100);
    $("np-stage").textContent = cur.stage + (pct == null ? "" : " · " + pct + "%");
    $("np-elapsed").textContent = fmt(cur.elapsed_sec) + " elapsed";
    fill.classList.toggle("indeterminate", pct == null);
    fill.style.width = pct == null ? "" : pct + "%";
  }

  async function tick() {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      render((await r.json()).current);
    } catch (e) { /* server restarting; try again next tick */ }
  }

  tick();
  setInterval(tick, 2000);
})();
