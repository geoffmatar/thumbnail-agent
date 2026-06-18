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

async function readJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
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
  setStatus("Creating visual and thumbnail...");

  try {
    const response = await fetch("/api/create", { method: "POST", body: form });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Thumbnail creation failed.");

    preview.src = payload.thumbnail_url;
    preview.style.display = "block";
    resultTitle.textContent = payload.title;
    resultPrompt.textContent = payload.visual_prompt;
    downloadLink.href = payload.thumbnail_url;
    downloadLink.hidden = false;
    setStatus(payload.used_ai ? "Done. Saved as a new thumbnail." : "Done with local fallback.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    createBtn.disabled = false;
  }
}

saveKeyBtn.addEventListener("click", saveApiKey);
createBtn.addEventListener("click", createThumbnail);
loadStatus();
