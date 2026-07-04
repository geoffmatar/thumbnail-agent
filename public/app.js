const homeView = document.querySelector("#homeView");
const zoomexView = document.querySelector("#zoomexView");
const openZoomex = document.querySelector("#openZoomex");
const openAlliance = document.querySelector("#openAlliance");
const openAllianceBlack = document.querySelector("#openAllianceBlack");
const openAllianceLgbtq = document.querySelector("#openAllianceLgbtq");
const openSiyata = document.querySelector("#openSiyata");
const backHome = document.querySelector("#backHome");
const createBtn = document.querySelector("#createBtn");
const statusLine = document.querySelector("#statusLine");
const studioLogo = document.querySelector("#studioLogo");
const studioTitle = document.querySelector("#studioTitle");
const titleInput = document.querySelector("#titleInput");
const scriptText = document.querySelector("#scriptText");
const resultsList = document.querySelector("#resultsList");
const progressPanel = document.querySelector("#progressPanel");
const progressLabel = document.querySelector("#progressLabel");
const progressPercent = document.querySelector("#progressPercent");
const progressFill = document.querySelector("#progressFill");
const progressMeta = document.querySelector("#progressMeta");
const previewModal = document.querySelector("#previewModal");
const previewModalImage = document.querySelector("#previewModalImage");
const previewBackdrop = document.querySelector("#previewBackdrop");
const closePreview = document.querySelector("#closePreview");

let progressTimer = null;
let currentClient = "zoomex";

const clients = {
  zoomex: {
    slug: "zoomex",
    bodyClass: "zoomex-mode",
    logo: "/zoomex-logo.png",
    alt: "ZOOMEX",
    title: "Thumbnail agent",
    placeholder: "ZOOMEX",
  },
  "alliance-latin": {
    slug: "alliance-latin",
    bodyClass: "alliance-mode",
    logo: "/alliance-latin-logo.png",
    alt: "Alliance Latin Community",
    title: "Thumbnail agent",
    placeholder: "ALLIANCE",
  },
  "alliance-black": {
    slug: "alliance-black",
    bodyClass: "alliance-black-mode",
    logo: "/alliance-black-logo.png",
    alt: "Alliance Black Community",
    title: "Thumbnail agent",
    placeholder: "ALLIANCE",
  },
  "alliance-lgbtq": {
    slug: "alliance-lgbtq",
    bodyClass: "alliance-lgbtq-mode",
    logo: "/alliance-lgbtq-logo.png",
    alt: "Alliance LGBTQ+",
    title: "Thumbnail agent",
    placeholder: "ALLIANCE",
  },
  siyata: {
    slug: "siyata",
    bodyClass: "siyata-mode",
    logo: "/siyata-card-logo.png",
    alt: "Siyata",
    title: "Thumbnail agent",
    placeholder: "SIYATA",
  },
};

const clientStates = Object.fromEntries(
  Object.keys(clients).map((slug) => [
    slug,
    {
      title: "",
      script: "",
      progress: null,
      progressStartedAt: 0,
      running: false,
      result: null,
      resultVersion: 0,
      statusMessage: "Checking setup...",
      statusIsError: false,
    },
  ]),
);

function showView(view, updateUrl = false) {
  syncCurrentFormState();
  const client = clients[view];
  const isStudio = Boolean(client);
  homeView.hidden = isStudio;
  zoomexView.hidden = !isStudio;
  document.body.classList.toggle("home-mode", !isStudio);
  Object.values(clients).forEach((item) => {
    document.body.classList.toggle(item.bodyClass, isStudio && item.slug === client?.slug);
  });

  if (isStudio) {
    currentClient = client.slug;
    studioLogo.src = client.logo;
    studioLogo.alt = client.alt;
    studioTitle.textContent = client.title;
    hydrateClientState(client.slug);
    loadStatus(client.slug);
  }

  if (!updateUrl) return;
  if (isStudio) {
    window.location.hash = client.slug;
  } else {
    window.history.pushState("", document.title, window.location.pathname + window.location.search);
  }
}

function syncViewFromUrl() {
  const slug = window.location.hash.replace("#", "");
  showView(clients[slug] ? slug : "home");
}

async function readJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes <= 0) return `${remainder}s`;
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

function getClientState(clientSlug = currentClient) {
  return clientStates[clientSlug];
}

function syncCurrentFormState() {
  const state = getClientState();
  if (!state || zoomexView.hidden) return;
  state.title = titleInput.value;
  state.script = scriptText.value;
}

function renderStatus(clientSlug = currentClient) {
  if (clientSlug !== currentClient) return;
  const state = getClientState(clientSlug);
  statusLine.textContent = state.statusMessage;
  statusLine.classList.toggle("error", state.statusIsError);
}

function setStatus(message, isError = false, clientSlug = currentClient) {
  const state = getClientState(clientSlug);
  if (!state) return;
  state.statusMessage = message;
  state.statusIsError = isError;
  renderStatus(clientSlug);
}

function resultItems(result) {
  if (!result) return [];
  if (Array.isArray(result.thumbnails) && result.thumbnails.length) return result.thumbnails;
  return [result];
}

function createResultCard(item, index, state, client) {
  const card = document.createElement("article");
  card.className = "result-card";

  const wrap = document.createElement("div");
  wrap.className = "preview-wrap";
  wrap.dataset.placeholder = client.placeholder;

  const image = document.createElement("img");
  image.className = "result-image";
  image.alt = item?.option_label
    ? `${item.option_label} generated thumbnail preview`
    : "Generated thumbnail preview";

  if (item?.thumbnail_url) {
    image.src = `${item.thumbnail_url}?v=${state.resultVersion}`;
    image.style.display = "block";
    wrap.classList.add("has-preview");
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "button");
    wrap.setAttribute("aria-label", `Preview ${image.alt}`);
    wrap.dataset.previewSrc = image.src;
    wrap.dataset.previewAlt = image.alt;
    wrap.title = "Click to preview";
  } else {
    image.style.display = "none";
    wrap.removeAttribute("tabindex");
    wrap.removeAttribute("role");
    wrap.removeAttribute("aria-label");
  }

  wrap.appendChild(image);
  card.appendChild(wrap);

  const link = document.createElement("a");
  link.className = "secondary-btn result-download";
  link.href = item?.download_url || item?.thumbnail_url || "#";
  link.download = item?.filename || `thumbnail-${index + 1}.png`;
  link.textContent = "Download PNG";
  link.hidden = !item?.thumbnail_url;
  card.appendChild(link);

  return card;
}

function renderResultState(clientSlug = currentClient) {
  if (clientSlug !== currentClient) return;
  const state = getClientState(clientSlug);
  const client = clients[clientSlug];
  const items = resultItems(state.result);

  resultsList.replaceChildren();
  resultsList.classList.toggle("has-results", items.length > 0);
  resultsList.classList.toggle("multiple-results", items.length > 1);
  resultsList.classList.toggle("single-result", items.length <= 1);

  if (!items.length) {
    resultsList.appendChild(createResultCard(null, 0, state, client));
    return;
  }

  items.forEach((item, index) => {
    resultsList.appendChild(createResultCard(item, index, state, client));
  });
}

function displayProgressValue(job, state) {
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const elapsed = job.elapsed_seconds ?? Math.round((Date.now() - state.progressStartedAt) / 1000);
  if (job.status === "done" || job.status === "error") return progress;
  if (progress >= 34 && progress < 82) {
    return Math.min(78, progress + Math.floor(Math.max(0, elapsed - 24) / 4));
  }
  if (progress >= 6 && progress < 34) {
    return Math.min(30, progress + Math.floor(Math.max(0, elapsed - 4) / 3));
  }
  return progress;
}

function renderProgress(clientSlug = currentClient) {
  if (clientSlug !== currentClient) return;
  const state = getClientState(clientSlug);
  const progressState = state.progress || {
    status: "idle",
    progress: 0,
    message: "Ready to create.",
    elapsed_seconds: 0,
  };
  const elapsed = progressState.elapsed_seconds ?? Math.round((Date.now() - state.progressStartedAt) / 1000);
  const progress = displayProgressValue(progressState, state);
  const roundedProgress = Math.round(progress);

  progressPanel.hidden = false;
  progressPanel.classList.toggle("error", progressState.status === "error");
  progressFill.style.width = `${roundedProgress}%`;
  progressPercent.textContent = `${roundedProgress}%`;
  progressLabel.textContent = progressState.message || "Creating thumbnail...";

  if (progressState.status === "idle") {
    progressMeta.textContent = "Add a title and script, then create a thumbnail.";
  } else if (progressState.status === "done") {
    progressMeta.textContent = `Finished in ${formatDuration(elapsed)}`;
  } else if (progressState.status === "error") {
    progressMeta.textContent = `Stopped after ${formatDuration(elapsed)}`;
  } else {
    progressMeta.textContent = `Elapsed ${formatDuration(elapsed)}`;
  }
}

function updateProgress(job, clientSlug = currentClient) {
  const state = getClientState(clientSlug);
  if (!state) return;
  state.progress = { ...(state.progress || {}), ...job };
  if (state.progress.status === "done" || state.progress.status === "error") {
    state.running = false;
  }
  renderProgress(clientSlug);
}

function ensureProgressTimer() {
  if (progressTimer) return;
  progressTimer = window.setInterval(() => {
    renderProgress(currentClient);
    if (!Object.values(clientStates).some((state) => state.running)) {
      stopProgress();
    }
  }, 1000);
}

function startProgress(clientSlug = currentClient) {
  const state = getClientState(clientSlug);
  state.progressStartedAt = Date.now();
  state.running = true;
  state.result = null;
  state.progress = {
    status: "queued",
    progress: 0,
    message: "Starting thumbnail generation...",
    elapsed_seconds: 0,
  };
  updateCreateButtonState();
  renderResultState(clientSlug);
  renderProgress(clientSlug);
  ensureProgressTimer();
}

function stopProgress() {
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = null;
}

function updateCreateButtonState() {
  const state = getClientState();
  createBtn.disabled = Boolean(state?.running);
}

function hydrateClientState(clientSlug) {
  const state = getClientState(clientSlug);
  titleInput.value = state.title;
  scriptText.value = state.script;
  renderResultState(clientSlug);
  renderProgress(clientSlug);
  renderStatus(clientSlug);
  updateCreateButtonState();
}

async function downloadThumbnail(event) {
  const downloadLink = event.target.closest(".result-download");
  if (!downloadLink || downloadLink.hidden || !downloadLink.href || downloadLink.href.endsWith("#")) return;
  event.preventDefault();

  try {
    const response = await fetch(downloadLink.href);
    const contentType = response.headers.get("Content-Type") || "";
    if (!response.ok || !contentType.startsWith("image/")) {
      throw new Error("Download failed. Please create the thumbnail again.");
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const temporaryLink = document.createElement("a");
    temporaryLink.href = objectUrl;
    temporaryLink.download = downloadLink.download || "thumbnail.png";
    document.body.appendChild(temporaryLink);
    temporaryLink.click();
    temporaryLink.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function openImagePreview(previewWrap) {
  const previewSrc = previewWrap?.dataset.previewSrc;
  if (!previewSrc) return;
  previewModalImage.src = previewSrc;
  previewModalImage.alt = previewWrap.dataset.previewAlt || "Generated thumbnail large preview";
  previewModal.hidden = false;
  document.body.classList.add("preview-open");
  try {
    closePreview.focus({ preventScroll: true });
  } catch {
    closePreview.focus();
  }
}

function closeImagePreview() {
  if (previewModal.hidden) return;
  previewModal.hidden = true;
  previewModalImage.removeAttribute("src");
  document.body.classList.remove("preview-open");
}

function previewThumbnail(event) {
  if (event.target.closest(".result-download")) return;
  const previewWrap = event.target.closest(".preview-wrap.has-preview");
  if (!previewWrap) return;
  openImagePreview(previewWrap);
}

function previewThumbnailWithKeyboard(event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  const previewWrap = event.target.closest(".preview-wrap.has-preview");
  if (!previewWrap) return;
  event.preventDefault();
  openImagePreview(previewWrap);
}

async function loadStatus(clientSlug = currentClient) {
  try {
    const status = await readJson(`/api/status?client=${encodeURIComponent(clientSlug)}`);
    const designText = status.design_ready ? status.client.design_label : "design missing";
    const fontText = status.font_ready ? status.client.font_label : "font missing";
    setStatus(`${designText} | ${fontText} | ready to create`, false, clientSlug);
    if (!status.openai_configured) {
      setStatus("Server OpenAI key missing. Ask the app admin to set OPENAI_API_KEY.", true, clientSlug);
    }
  } catch (error) {
    setStatus(error.message, true, clientSlug);
  }
}

async function waitForJob(statusUrl, clientSlug) {
  while (true) {
    const job = await readJson(statusUrl);
    updateProgress(job, clientSlug);

    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error || job.message || "Thumbnail creation failed.");

    await sleep(1400);
  }
}

async function createThumbnail() {
  syncCurrentFormState();
  const jobClient = currentClient;
  const state = getClientState(jobClient);
  const title = titleInput.value.trim();
  if (!title) {
    setStatus("Add the thumbnail title first.", true, jobClient);
    titleInput.focus();
    return;
  }

  const script = scriptText.value.trim();
  if (!script) {
    setStatus("Paste a script first.", true, jobClient);
    scriptText.focus();
    return;
  }

  const form = new FormData();
  form.append("client", jobClient);
  form.append("title", title);
  form.append("script", script);

  state.result = null;
  updateCreateButtonState();
  renderResultState(jobClient);
  startProgress(jobClient);
  setStatus("Creating thumbnail...", false, jobClient);

  try {
    const response = await fetch("/api/create", { method: "POST", body: form });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Thumbnail creation failed.");

    const statusUrl = payload.status_url || `/api/jobs/${payload.job_id}`;
    const result = await waitForJob(statusUrl, jobClient);

    state.result = result;
    state.resultVersion = Date.now();
    renderResultState(jobClient);
    const createdCount = resultItems(result).length;
    const doneMessage = createdCount > 1
      ? `Done. Saved ${createdCount} new thumbnails.`
      : "Done. Saved as a new thumbnail.";
    setStatus(result.used_ai ? doneMessage : "Done with local fallback.", false, jobClient);
  } catch (error) {
    updateProgress({
      status: "error",
      progress: 100,
      message: error.message,
      elapsed_seconds: Math.round((Date.now() - state.progressStartedAt) / 1000),
    }, jobClient);
    setStatus(error.message, true, jobClient);
  } finally {
    state.running = false;
    updateCreateButtonState();
    if (!Object.values(clientStates).some((clientState) => clientState.running)) {
      stopProgress();
    }
  }
}

openZoomex.addEventListener("click", () => showView("zoomex", true));
openAlliance.addEventListener("click", () => showView("alliance-latin", true));
openAllianceBlack.addEventListener("click", () => showView("alliance-black", true));
openAllianceLgbtq.addEventListener("click", () => showView("alliance-lgbtq", true));
openSiyata.addEventListener("click", () => showView("siyata", true));
backHome.addEventListener("click", () => showView("home", true));
window.addEventListener("hashchange", syncViewFromUrl);
titleInput.addEventListener("input", () => {
  getClientState().title = titleInput.value;
});
scriptText.addEventListener("input", () => {
  getClientState().script = scriptText.value;
});
resultsList.addEventListener("click", downloadThumbnail);
resultsList.addEventListener("click", previewThumbnail);
resultsList.addEventListener("keydown", previewThumbnailWithKeyboard);
previewBackdrop.addEventListener("click", closeImagePreview);
closePreview.addEventListener("click", closeImagePreview);
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeImagePreview();
});

syncViewFromUrl();
createBtn.addEventListener("click", createThumbnail);
