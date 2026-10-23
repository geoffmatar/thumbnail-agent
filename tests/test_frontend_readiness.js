// Offline browser-flow checks. Run with: node tests/test_frontend_readiness.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    const classes = new Set();
    this.classList = {
      toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
      contains: (name) => classes.has(name),
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
    };
  }
  addEventListener() {}
  setAttribute() {}
  removeAttribute() {}
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  focus() {}
  querySelectorAll(selector) {
    const tags = selector.toUpperCase().split(/,\s*/);
    return this.children.flatMap((child) => [
      ...(tags.includes(child.tagName) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
}

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "public/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "public/index.html"), "utf8");

function studio() {
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  element("#zoomexView").hidden = true;
  const requests = [];
  let respond;
  const context = vm.createContext({
    document: {
      querySelector: element,
      createElement: (tag) => new Element(tag),
      body: new Element("body"),
      title: "Thumbnail Agent",
    },
    window: {
      location: { hash: "", pathname: "/", search: "" },
      history: { pushState() {} },
      addEventListener() {},
      setInterval() { return 1; },
      clearInterval() {},
    },
    URL: { createObjectURL: (file) => `blob:${file.name}`, revokeObjectURL() {} },
    FormData: class { constructor() { this.fields = []; } append(...field) { this.fields.push(field); } },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return respond(url, options);
    },
  });
  vm.runInContext(source, context);
  return {
    context, element, requests,
    state: (slug = "zoomex") => context.getClientState(slug),
    respondWith(callback) { respond = callback; },
    select(slug) {
      vm.runInContext(`currentClient = ${JSON.stringify(slug)}; hydrateClientState(currentClient);`, context);
    },
  };
}

const response = (payload) => ({ ok: true, json: async () => payload });
const readyStatus = (changes = {}) => ({
  image_provider: "higgsfield", vision_provider: "gemini",
  image_provider_configured: true, vision_provider_configured: true,
  generation_configured: true, openai_configured: false,
  design_ready: true, font_ready: true,
  client: { design_label: "Brand design", font_label: "Brand font" },
  ...changes,
});

async function checkStatus(changes, ready, message) {
  const app = studio();
  app.respondWith(() => response(readyStatus(changes)));
  await app.context.loadStatus();
  assert.equal(app.state().setupReady, ready);
  assert.equal(app.element("#createBtn").disabled, !ready);
  assert.equal(app.element("#statusLine").classList.contains("sr-only"), ready);
  app.context.renderProgress();
  assert.equal(app.element("#progressLabel").textContent, "Add your title and script.");
  if (message) assert.match(app.state().statusMessage, message);
  return app;
}

(async () => {
  await checkStatus({}, true, /Higgsfield images \+ Gemini review/);
  await checkStatus({ image_provider: "openai", vision_provider: "openai" }, true, /OpenAI images \+ OpenAI review/);
  await checkStatus({ image_provider_configured: false, openai_configured: true }, false, /Higgsfield image generation not configured/);
  await checkStatus({ vision_provider_configured: false, openai_configured: true }, false, /Gemini vision review not configured/);
  await checkStatus({ image_provider_configured: false, vision_provider_configured: false }, false, /Higgsfield image generation and Gemini vision review/);
  await checkStatus({ generation_configured: false }, false, /generation setup is incomplete/);
  await checkStatus({ generation_configured: undefined, image_provider_configured: undefined, vision_provider_configured: undefined, openai_configured: true }, false, /not configured/);
  await checkStatus({ design_ready: false }, false, /brand design missing/);
  await checkStatus({ font_ready: false }, false, /title font missing/);

  const failed = studio();
  failed.respondWith(() => { throw new Error("Network unavailable"); });
  await failed.context.loadStatus();
  assert.equal(failed.element("#createBtn").disabled, true);
  assert.equal(failed.element("#statusLine").classList.contains("sr-only"), false);
  assert.match(failed.state().statusMessage, /Could not check server setup.*Network unavailable/);
  await failed.context.createThumbnail();
  assert.equal(failed.requests.length, 1, "Unconfigured creation must not POST or hide its setup error");

  const race = studio();
  const resolve = [];
  race.respondWith(() => new Promise((done) => resolve.push(done)));
  const first = race.context.loadStatus();
  const second = race.context.loadStatus();
  resolve[1](response(readyStatus()));
  await second;
  resolve[0](response(readyStatus({ vision_provider_configured: false })));
  await first;
  assert.equal(race.state().setupReady, true, "An older status response cannot replace newer readiness");

  const brands = studio();
  const pending = [];
  brands.respondWith(() => new Promise((done) => pending.push(done)));
  const zoomex = brands.context.loadStatus("zoomex");
  brands.select("alliance-latin");
  const alliance = brands.context.loadStatus("alliance-latin");
  pending[0](response(readyStatus()));
  await zoomex;
  assert.equal(brands.element("#createBtn").disabled, true, "Another brand's readiness cannot enable the current studio");
  pending[1](response(readyStatus({ vision_provider_configured: false })));
  await alliance;
  assert.match(brands.element("#statusLine").textContent, /Gemini vision review/);
  brands.select("zoomex");
  assert.equal(brands.element("#createBtn").disabled, false);
  assert.equal(brands.element("#statusLine").classList.contains("sr-only"), true);

  const job = await checkStatus({}, true);
  for (let index = 0; index < 4; index += 1) job.context.setReferenceFile("zoomex", index, { name: `person-${index}.png` });
  job.element("#titleInput").value = "Test title";
  job.element("#scriptText").value = "Test script";
  const result = {
    used_ai: true, person_reference_used: true, person_reference_count: 4,
    reference_strategy: "multi_image_edit", reference_preservation: "identity_guided",
    reference_appearance_checked: true, reference_appearance_passed: true,
  };
  job.respondWith(() => response({ status_url: "/mock-job" }));
  let finish;
  job.context.waitForJob = () => new Promise((done) => { finish = done; });
  const creation = job.context.createThumbnail();
  await new Promise((done) => setImmediate(done));
  assert.equal(job.element("#createBtn").disabled, true);
  assert(job.element("#referenceSlots").querySelectorAll("input, button").every((control) => control.disabled));
  const post = job.requests.find((request) => request.url === "/api/create");
  assert.deepEqual(post.options.body.fields.filter(([name]) => name.startsWith("person_reference")).map(([name]) => name), ["person_reference", "person_reference_2", "person_reference_3", "person_reference_4"]);
  finish(result);
  await creation;
  assert.equal(job.state().result, result, "Missing gaze verification must not hide an appearance-passing result");
  assert.equal(job.element("#createBtn").disabled, false);
  assert.equal(job.state().personReferences.length, 4);
  assert.throws(() => job.context.verifyReferenceResult({ ...result, reference_appearance_passed: false }, 4), /face and hair checks/);
  assert.throws(() => job.context.verifyReferenceResult({ ...result, thumbnails: [result, { ...result, reference_appearance_checked: false }] }, 4), /face and hair checks/);

  for (const status of ["running", "done"]) {
    const progress = await checkStatus({}, true);
    const message = status === "running" ? "Generating the scene..." : "Your thumbnail is finished.";
    progress.state().running = status === "running";
    progress.state().progress = { status, progress: status === "done" ? 100 : 34, message, elapsed_seconds: 5 };
    progress.respondWith(() => response(readyStatus({ vision_provider_configured: false })));
    await progress.context.loadStatus();
    progress.context.renderProgress();
    assert.equal(progress.element("#progressLabel").textContent, message, "Setup checks must preserve running and completed progress");
    assert.equal(progress.state().progress.status, status);
    assert.equal(progress.element("#createBtn").disabled, true);
  }

  assert.match(html, /id="progressLabel">Add your title and script\./);
  assert.match(html, /id="createBtn"[^>]*\bdisabled\b/);
  assert.match(html, /\/app\.js\?v=20261023-provider-readiness-1/);
  assert(!source.includes("OPENAI_API_KEY"), "The browser must not infer provider readiness from the OpenAI key");
  console.log("Frontend readiness checks passed: provider matrix, visible setup errors, stale/brand responses, locked uploads, four-file submission, and appearance guards.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
