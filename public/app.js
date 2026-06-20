const homeView = document.querySelector("#homeView");
const zoomexView = document.querySelector("#zoomexView");
const openZoomex = document.querySelector("#openZoomex");
const backHome = document.querySelector("#backHome");
const createBtn = document.querySelector("#createBtn");
const statusLine = document.querySelector("#statusLine");
const titleInput = document.querySelector("#titleInput");
const scriptText = document.querySelector("#scriptText");
const personReference = document.querySelector("#personReference");
const referenceFileName = document.querySelector("#referenceFileName");
const preview = document.querySelector("#preview");
const downloadLink = document.querySelector("#downloadLink");
const progressPanel = document.querySelector("#progressPanel");
const progressLabel = document.querySelector("#progressLabel");
const progressPercent = document.querySelector("#progressPercent");
const progressFill = document.querySelector("#progressFill");
const progressMeta = document.querySelector("#progressMeta");

let progressStartedAt = 0;
let progressTimer = null;
let latestProgress = null;

function showView(view, updateUrl = false) {
  const isZoomex = view === "zoomex";
  homeView.hidden = isZoomex;
  zoomexView.hidden = !isZoomex;
  document.body.classList.toggle("home-mode", !isZoomex);
  document.body.classList.toggle("zoomex-mode", isZoomex);

  if (!updateUrl) return;
  if (isZoomex) {
    window.location.hash = "zoomex";
  } else {
    window.history.pushState("", document.title, window.location.pathname + window.location.search);
  }
}

function syncViewFromUrl() {
  showView(window.location.hash === "#zoomex" ? "zoomex" : "home");
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
    const status = await readJson("/api/status");
    const designText = status.design_ready ? "ZOOMEX design ready" : "design missing";
    const fontText = status.font_ready ? "Blinker ready" : "font missing";
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
    downloadLink.href = result.thumbnail_url;
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
backHome.addEventListener("click", () => showView("home", true));
window.addEventListener("hashchange", syncViewFromUrl);
personReference.addEventListener("change", updateReferenceFileName);

syncViewFromUrl();
createBtn.addEventListener("click", createThumbnail);
loadStatus();
