const homeView = document.querySelector("#homeView");
const zoomexView = document.querySelector("#zoomexView");
const openZoomex = document.querySelector("#openZoomex");
const openAlliance = document.querySelector("#openAlliance");
const openAllianceBlack = document.querySelector("#openAllianceBlack");
const backHome = document.querySelector("#backHome");
const createBtn = document.querySelector("#createBtn");
const statusLine = document.querySelector("#statusLine");
const studioLogo = document.querySelector("#studioLogo");
const studioTitle = document.querySelector("#studioTitle");
const titleInput = document.querySelector("#titleInput");
const scriptText = document.querySelector("#scriptText");
const personReference = document.querySelector("#personReference");
const referenceFileName = document.querySelector("#referenceFileName");
const preview = document.querySelector("#preview");
const previewWrap = document.querySelector("#previewWrap");
const downloadLink = document.querySelector("#downloadLink");
const progressPanel = document.querySelector("#progressPanel");
const progressLabel = document.querySelector("#progressLabel");
const progressPercent = document.querySelector("#progressPercent");
const progressFill = document.querySelector("#progressFill");
const progressMeta = document.querySelector("#progressMeta");

let progressStartedAt = 0;
let progressTimer = null;
let latestProgress = null;
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
};

function showView(view, updateUrl = false) {
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
    previewWrap.dataset.placeholder = client.placeholder;
    loadStatus();
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

function displayProgressValue(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const elapsed = job.elapsed_seconds ?? Math.round((Date.now() - progressStartedAt) / 1000);
  if (job.status === "done" || job.status === "error") return progress;
  if (progress >= 34 && progress < 82) {
    return Math.min(78, progress + Math.floor(Math.max(0, elapsed - 24) / 4));
  }
  if (progress >= 6 && progress < 34) {
    return Math.min(30, progress + Math.floor(Math.max(0, elapsed - 4) / 3));
  }
  return progress;
}

function updateProgress(job) {
  latestProgress = { ...(latestProgress || {}), ...job };
  const elapsed = latestProgress.elapsed_seconds ?? Math.round((Date.now() - progressStartedAt) / 1000);
  const progress = displayProgressValue(latestProgress);
  const roundedProgress = Math.round(progress);

  progressPanel.hidden = false;
  progressPanel.classList.toggle("error", latestProgress.status === "error");
  progressFill.style.width = `${roundedProgress}%`;
  progressPercent.textContent = `${roundedProgress}%`;
  progressLabel.textContent = latestProgress.message || "Creating thumbnail...";

  if (latestProgress.status === "done") {
    progressMeta.textContent = `Finished in ${formatDuration(elapsed)}`;
  } else if (latestProgress.status === "error") {
    progressMeta.textContent = `Stopped after ${formatDuration(elapsed)}`;
  } else {
    progressMeta.textContent = `Elapsed ${formatDuration(elapsed)}`;
  }
}

function startProgress() {
  progressStartedAt = Date.now();
  latestProgress = {
    status: "queued",
    progress: 0,
    message: "Starting thumbnail generation...",
    elapsed_seconds: 0,
  };
  updateProgress(latestProgress);
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = window.setInterval(() => updateProgress(latestProgress), 1000);
}

function stopProgress() {
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = null;
}

async function downloadThumbnail(event) {
  if (downloadLink.hidden || !downloadLink.href || downloadLink.href.endsWith("#")) return;
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

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", isError);
}

function updateReferenceFileName() {
  const file = personReference.files && personReference.files[0];
  referenceFileName.textContent = file
    ? file.name
    : "Upload image";
}

async function loadStatus() {
  try {
    const status = await readJson(`/api/status?client=${encodeURIComponent(currentClient)}`);
    const designText = status.design_ready ? status.client.design_label : "design missing";
    const fontText = status.font_ready ? status.client.font_label : "font missing";
    setStatus(`${designText} | ${fontText} | ready to create`);
    if (!status.openai_configured) {
      setStatus("Server OpenAI key missing. Ask the app admin to set OPENAI_API_KEY.", true);
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function waitForJob(statusUrl) {
  while (true) {
    const job = await readJson(statusUrl);
    updateProgress(job);

    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error || job.message || "Thumbnail creation failed.");

    await sleep(1400);
  }
}

async function createThumbnail() {
  const title = titleInput.value.trim();
  if (!title) {
    setStatus("Add the thumbnail title first.", true);
    titleInput.focus();
    return;
  }

  const script = scriptText.value.trim();
  if (!script) {
    setStatus("Paste a script first.", true);
    scriptText.focus();
    return;
  }

  const form = new FormData();
  form.append("client", currentClient);
  form.append("title", title);
  form.append("script", script);
  if (personReference.files && personReference.files[0]) {
    form.append("person_reference", personReference.files[0]);
  }

  createBtn.disabled = true;
  downloadLink.hidden = true;
  startProgress();
  setStatus("Creating thumbnail...");

  try {
    const response = await fetch("/api/create", { method: "POST", body: form });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Thumbnail creation failed.");

    const statusUrl = payload.status_url || `/api/jobs/${payload.job_id}`;
    const result = await waitForJob(statusUrl);

    preview.src = `${result.thumbnail_url}?v=${Date.now()}`;
    preview.style.display = "block";
    downloadLink.href = result.download_url || result.thumbnail_url;
    downloadLink.download = result.filename || "thumbnail.png";
    downloadLink.hidden = false;
    setStatus(result.used_ai ? "Done. Saved as a new thumbnail." : "Done with local fallback.");
  } catch (error) {
    updateProgress({
      status: "error",
      progress: 100,
      message: error.message,
      elapsed_seconds: Math.round((Date.now() - progressStartedAt) / 1000),
    });
    setStatus(error.message, true);
  } finally {
    stopProgress();
    createBtn.disabled = false;
  }
}

openZoomex.addEventListener("click", () => showView("zoomex", true));
openAlliance.addEventListener("click", () => showView("alliance-latin", true));
openAllianceBlack.addEventListener("click", () => showView("alliance-black", true));
backHome.addEventListener("click", () => showView("home", true));
window.addEventListener("hashchange", syncViewFromUrl);
personReference.addEventListener("change", updateReferenceFileName);
downloadLink.addEventListener("click", downloadThumbnail);

syncViewFromUrl();
createBtn.addEventListener("click", createThumbnail);
loadStatus();
