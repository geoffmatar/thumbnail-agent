const createBtn = document.querySelector("#createBtn");
const statusLine = document.querySelector("#statusLine");
const keySetup = document.querySelector("#keySetup");
const apiKeyInput = document.querySelector("#apiKeyInput");
const saveKeyBtn = document.querySelector("#saveKeyBtn");
const titleInput = document.querySelector("#titleInput");
const scriptText = document.querySelector("#scriptText");
const preview = document.querySelector("#preview");
const resultTitle = document.querySelector("#resultTitle");
const resultPrompt = document.querySelector("#resultPrompt");
const downloadLink = document.querySelector("#downloadLink");
const progressPanel = document.querySelector("#progressPanel");
const progressLabel = document.querySelector("#progressLabel");
const progressPercent = document.querySelector("#progressPercent");
const progressFill = document.querySelector("#progressFill");
const progressMeta = document.querySelector("#progressMeta");

let progressStartedAt = 0;
let progressTimer = null;
let latestProgress = null;

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

function estimateRemainingText(elapsedSeconds, progress) {
  if (progress >= 100) return "complete";
  if (progress < 10) return "estimating time";
  if (progress < 34) return "about 1-2 min left";
  if (progress < 82) {
    if (elapsedSeconds > 150) return "still generating";
    return "about 45-90s left";
  }
  if (progress < 96) return "about 10-20s left";
  return "almost done";
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
    progressMeta.textContent = `Elapsed ${formatDuration(elapsed)} | ${estimateRemainingText(elapsed, roundedProgress)}`;
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

async function loadStatus() {
  try {
    const status = await readJson("/api/status");
    const keyText = status.openai_configured ? "OpenAI ready" : "OpenAI key missing";
    const designText = status.design_ready ? "ZOOMEX design ready" : "design missing";
    const fontText = status.font_ready ? "Blinker ready" : "font missing";
    keySetup.hidden = status.openai_configured || !status.allow_browser_key_setup;
    setStatus(`${keyText} | ${designText} | ${fontText} | model: ${status.model}`);
    if (!status.openai_configured && !status.allow_browser_key_setup) {
      setStatus("Server OpenAI key missing. Ask the app admin to set OPENAI_API_KEY.", true);
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function saveApiKey() {
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) {
    setStatus("Paste your OpenAI API key first.", true);
    apiKeyInput.focus();
    return;
  }

  saveKeyBtn.disabled = true;
  setStatus("Saving key locally...");

  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ openai_api_key: apiKey }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not save the key.");
    apiKeyInput.value = "";
    await loadStatus();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    saveKeyBtn.disabled = false;
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
    resultTitle.textContent = result.title;
    resultPrompt.textContent = result.visual_prompt;
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

saveKeyBtn.addEventListener("click", saveApiKey);
createBtn.addEventListener("click", createThumbnail);
loadStatus();
