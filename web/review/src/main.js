import "./style.css";

const DISPOSITIONS = ["", "accept", "reject", "needs-fix"];
const state = {
  project: null,
  projectSha256: null,
  annotations: { pages: {} },
  annotationsSha256: null,
  reviewer: "",
  pageIndex: 0,
  regionIndex: -1,
  saving: false,
  message: null,
};

const root = document.querySelector("#app");

function assetUrl(id) {
  return `/api/assets/${encodeURIComponent(id)}`;
}

function page() {
  return state.project.pages[state.pageIndex];
}

function region() {
  return state.regionIndex < 0 ? null : (page().regions ?? [])[state.regionIndex] ?? null;
}

function pageAnnotation(pageId = page().id) {
  return (state.annotations.pages[pageId] ??= { regions: {} });
}

function regionAnnotation(regionId = region()?.id) {
  if (!regionId) return null;
  return (pageAnnotation().regions[regionId] ??= {});
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function dispositionOptions(value, scope) {
  const acceptLabel = scope === "region"
    ? "accept — confirm source, scan, and layout"
    : "accept — confirm page/source mapping";
  const labels = { "": "not decided", accept: acceptLabel, reject: "reject", "needs-fix": "needs fix" };
  return DISPOSITIONS.map((item) => `<option value="${item}"${item === value ? " selected" : ""}>${labels[item]}</option>`).join("");
}

function rectStyle(rect) {
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(Number.isFinite)) return "display:none";
  const [x, y, width, height] = rect;
  return `left:${x * 100}%;top:${y * 100}%;width:${width * 100}%;height:${height * 100}%`;
}

function media(viewAssetId, rect, label) {
  if (!viewAssetId) return `<div class="media-empty">No ${escapeHtml(label)} asset declared for this page.</div>`;
  return `<div class="media-stage" aria-label="${escapeHtml(label)}">
    <img src="${assetUrl(viewAssetId)}" alt="${escapeHtml(label)} for ${escapeHtml(page().label ?? page().id)}" />
    <button class="region-overlay" style="${rectStyle(rect)}" title="Selected region" aria-label="Selected region"></button>
  </div>`;
}

function selectedText(kind) {
  const selected = region();
  if (!selected) return "Select a region to compare source, OCR, and canonical text.";
  return selected[`${kind}_text`] ?? "No text was supplied for this region.";
}

function flash(kind, text) {
  state.message = { kind, text };
  render();
}

function render() {
  if (!state.project) {
    root.innerHTML = `<main class="loading">Loading local review project…</main>`;
    return;
  }
  const currentPage = page();
  const selected = region();
  const currentAnnotation = pageAnnotation(currentPage.id);
  const selectedAnnotation = selected ? regionAnnotation(selected.id) : null;
  const instructions = state.project.review_instructions ?? {};
  const scope = selected ? "region" : "page";
  const reviewInstruction = instructions[scope] ?? (selected
    ? "Accept confirms this region's authoritative source text, scan evidence, and generated layout agree."
    : "Accept confirms this displayed page is correctly mapped to its source and generated views.");
  const pages = state.project.pages;
  const regions = currentPage.regions ?? [];
  root.innerHTML = `
    <header class="masthead">
      <div><p class="eyebrow">LMDOC · LOCAL REVIEW</p><h1>${escapeHtml(state.project.title ?? state.project.document_id ?? "Review project")}</h1></div>
      <div class="identity"><label>Reviewer <input id="reviewer" maxlength="200" autocomplete="name" value="${escapeHtml(state.reviewer)}" placeholder="Your name" /></label><button class="save" data-action="save" ${state.saving ? "disabled" : ""}>${state.saving ? "Saving…" : "Save annotations"}</button></div>
    </header>
    ${state.message ? `<div class="notice ${state.message.kind}" role="status">${escapeHtml(state.message.text)}</div>` : ""}
    <main class="workspace">
      <aside class="queue" aria-label="Page and region navigation">
        <div class="queue-heading"><h2>Pages</h2><span>${pages.length}</span></div>
        <nav class="page-list">${pages.map((item, index) => `<button class="page-item ${index === state.pageIndex ? "active" : ""}" data-page="${index}" aria-current="${index === state.pageIndex ? "page" : "false"}"><strong>${escapeHtml(item.label ?? item.id)}</strong><small>${(item.regions ?? []).length} regions</small></button>`).join("")}</nav>
        <div class="queue-heading regions-heading"><h2>Regions</h2><span>${regions.length}</span></div>
        <nav class="region-list">${regions.map((item, index) => `<button class="region-item ${index === state.regionIndex ? "active" : ""}" data-region="${index}"><strong>${escapeHtml(item.label ?? item.id)}</strong><small>${escapeHtml(item.kind ?? "region")}</small></button>`).join("") || `<p class="muted">No regions declared.</p>`}</nav>
      </aside>
      <section class="content">
        <div class="toolbar"><button data-action="previous" ${state.pageIndex === 0 ? "disabled" : ""}>← Previous</button><p><strong>${escapeHtml(currentPage.label ?? currentPage.id)}</strong><span> ${state.pageIndex + 1} / ${pages.length}</span></p><button data-action="next" ${state.pageIndex === pages.length - 1 ? "disabled" : ""}>Next →</button></div>
        <div class="comparison-grid">
          <section class="view-panel"><h2>Reference scan</h2>${media(currentPage.reference_asset_id, selected?.reference_box, "Reference scan")}</section>
          <section class="view-panel"><h2>Generated replica</h2>${media(currentPage.generated_asset_id, selected?.generated_box ?? selected?.reference_box, "Generated replica")}</section>
        </div>
        <section class="review-card" aria-labelledby="decision-title">
          <div class="decision-title"><div><p class="eyebrow">${selected ? "REGION REVIEW" : "PAGE REVIEW"}</p><h2 id="decision-title">${escapeHtml(selected?.label ?? currentPage.label ?? currentPage.id)}</h2></div><label>Disposition <select id="disposition">${dispositionOptions(selectedAnnotation?.disposition ?? currentAnnotation.disposition ?? "", scope)}</select></label></div>
          <p class="review-instruction">${escapeHtml(reviewInstruction)}</p>
          ${selected ? `<div class="text-grid">
            <label>Recovered source<textarea readonly>${escapeHtml(selectedText("source"))}</textarea></label>
            <label>OCR evidence<textarea readonly>${escapeHtml(selectedText("ocr"))}</textarea></label>
            <label class="canonical">Corrected canonical text<textarea id="canonical-text" placeholder="Leave unchanged when the canonical text is correct">${escapeHtml(selectedAnnotation?.canonical_text ?? selected.canonical_text ?? "")}</textarea></label>
          </div>` : `<p class="muted">Choose a region to make a text correction. Page dispositions and notes can still be recorded here.</p>`}
          <label>Review notes<textarea id="notes" placeholder="What should the pipeline preserve or fix?">${escapeHtml(selectedAnnotation?.notes ?? currentAnnotation.notes ?? "")}</textarea></label>
        </section>
      </section>
    </main>
    <footer>Keyboard: <kbd>←</kbd>/<kbd>→</kbd> pages, <kbd>↑</kbd>/<kbd>↓</kbd> regions, <kbd>Ctrl</kbd>+<kbd>Enter</kbd> save. Your annotations are written beside the review project; source and reference files are never edited.</footer>`;
  bindEvents();
}

function editAnnotation() {
  const selected = region();
  const target = selected ? regionAnnotation(selected.id) : pageAnnotation();
  target.disposition = document.querySelector("#disposition").value || undefined;
  target.notes = document.querySelector("#notes").value || undefined;
  if (selected) target.canonical_text = document.querySelector("#canonical-text").value || undefined;
}

function selectPage(index) {
  editAnnotation();
  state.pageIndex = Math.max(0, Math.min(index, state.project.pages.length - 1));
  state.regionIndex = -1;
  render();
}

function selectRegion(index) {
  editAnnotation();
  state.regionIndex = index;
  render();
}

function bindEvents() {
  root.querySelectorAll("[data-page]").forEach((element) => element.addEventListener("click", () => selectPage(Number(element.dataset.page))));
  root.querySelectorAll("[data-region]").forEach((element) => element.addEventListener("click", () => selectRegion(Number(element.dataset.region))));
  root.querySelector("[data-action='previous']")?.addEventListener("click", () => selectPage(state.pageIndex - 1));
  root.querySelector("[data-action='next']")?.addEventListener("click", () => selectPage(state.pageIndex + 1));
  root.querySelector("[data-action='save']")?.addEventListener("click", save);
  root.querySelector("#reviewer").addEventListener("input", (event) => { state.reviewer = event.target.value; });
}

async function save() {
  editAnnotation();
  if (!state.reviewer.trim()) {
    flash("error", "Enter your reviewer name before saving.");
    document.querySelector("#reviewer")?.focus();
    return;
  }
  state.saving = true;
  render();
  try {
    const response = await fetch("/api/annotations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_sha256: state.projectSha256, base_annotations_sha256: state.annotationsSha256, reviewer: state.reviewer.trim(), annotations: state.annotations }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error ?? `Save failed (${response.status})`);
    state.annotationsSha256 = result.annotations_sha256;
    state.annotations = result.value.annotations;
    state.message = { kind: "success", text: "Annotations saved and bound to this exact project manifest." };
  } catch (error) {
    state.message = { kind: "error", text: error.message };
  } finally {
    state.saving = false;
    render();
  }
}

document.addEventListener("keydown", (event) => {
  if (!state.project || event.target.matches("input, textarea, select")) return;
  if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); save(); return; }
  if (event.key === "ArrowLeft") { event.preventDefault(); selectPage(state.pageIndex - 1); }
  if (event.key === "ArrowRight") { event.preventDefault(); selectPage(state.pageIndex + 1); }
  if (event.key === "ArrowUp") { event.preventDefault(); selectRegion(Math.max(0, state.regionIndex - 1)); }
  if (event.key === "ArrowDown") { event.preventDefault(); selectRegion(Math.min((page().regions ?? []).length - 1, state.regionIndex + 1)); }
});

async function boot() {
  try {
    const [projectResponse, annotationResponse] = await Promise.all([fetch("/api/project"), fetch("/api/annotations")]);
    const project = await projectResponse.json();
    const annotationInfo = await annotationResponse.json();
    if (!projectResponse.ok) throw new Error(project.error ?? "Could not load review project");
    if (!annotationResponse.ok) throw new Error(annotationInfo.error ?? "Could not load annotations");
    state.project = project;
    state.projectSha256 = projectResponse.headers.get("X-Lispmdoc-Project-SHA256");
    state.annotationsSha256 = annotationInfo.annotations_sha256;
    if (annotationInfo.value?.project_sha256 === state.projectSha256) {
      state.annotations = annotationInfo.value.annotations ?? { pages: {} };
      state.reviewer = annotationInfo.value.reviewer ?? "";
    }
    if (!state.projectSha256) throw new Error("Review server did not provide a project digest");
    render();
  } catch (error) {
    root.innerHTML = `<main class="fatal"><h1>Review project unavailable</h1><p>${escapeHtml(error.message)}</p><p>Set <code>LISPMDOC_REVIEW_PROJECT</code> to a valid project manifest, then restart the Vite server.</p></main>`;
  }
}

boot();
