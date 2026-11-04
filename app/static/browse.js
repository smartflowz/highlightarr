// Folder picker for the workflow form. Talks to GET /api/browse, which only
// lists folders under the server's MEDIA_ROOTS.
(function () {
  let modal, pathEl, listEl, errEl, upBtn, selectBtn, currentPath = "", target = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;   // textContent: folder names are never parsed as HTML
    return e;
  }

  function build() {
    modal = el("div", "modal-backdrop");
    modal.hidden = true;
    const box = el("div", "modal");
    box.appendChild(el("h3", null, "Select a folder"));
    pathEl = el("code", "modal-path");
    box.appendChild(pathEl);
    errEl = el("div", "notice err");
    errEl.hidden = true;
    box.appendChild(errEl);
    listEl = el("ul", "folder-list");
    box.appendChild(listEl);
    const actions = el("div", "actions");
    upBtn = el("button", "btn secondary", "Up");
    selectBtn = el("button", "btn", "Select this folder");
    const cancel = el("button", "btn secondary", "Cancel");
    [upBtn, selectBtn, cancel].forEach(b => (b.type = "button"));
    actions.append(upBtn, selectBtn, cancel);
    box.appendChild(actions);
    modal.appendChild(box);
    document.body.appendChild(modal);

    cancel.onclick = close;
    modal.addEventListener("click", e => { if (e.target === modal) close(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !modal.hidden) close(); });
    selectBtn.onclick = () => {
      if (target && currentPath) target.value = currentPath;
      close();
    };
  }

  function close() { modal.hidden = true; }

  async function load(path) {
    errEl.hidden = true;
    let data;
    try {
      const r = await fetch("/api/browse?path=" + encodeURIComponent(path || ""));
      data = await r.json();
      if (!r.ok) throw new Error(data.error || r.statusText);
    } catch (e) {
      errEl.textContent = e.message;
      errEl.hidden = false;
      selectBtn.disabled = true;
      return;
    }
    currentPath = data.path;
    selectBtn.disabled = false;
    pathEl.textContent = data.path;
    upBtn.disabled = !data.parent;
    upBtn.onclick = () => data.parent && load(data.parent);
    listEl.replaceChildren();
    if (!data.dirs.length) listEl.appendChild(el("li", "empty", "No subfolders"));
    data.dirs.forEach(d => {
      const li = el("li", "folder", "📁 " + d.name);
      li.onclick = () => load(d.path);
      listEl.appendChild(li);
    });
  }

  document.querySelectorAll(".browse-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (!modal) build();
      target = document.querySelector('input[name="' + btn.dataset.for + '"]');
      modal.hidden = false;
      load(target.value.trim());
    });
  });
})();
