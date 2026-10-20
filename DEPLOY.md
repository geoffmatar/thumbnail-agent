# Deploying The Internal Thumbnail Agent

This app is intended to be deployed as an internal web app.

## What Teammates Get

Your team gets one URL. When they open it, they can choose an agent, enter a title and script, and optionally upload a person reference image to generate thumbnails.

## Required Server Secrets

Set these environment variables in your hosting provider:

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.5
OPENAI_IMAGE_MODEL=gpt-image-2.5-sunburst-2026-09-08
OPENAI_IMAGE_QUALITY=medium
OPENAI_PERSON_IMAGE_MODEL=gpt-image-2.5-sunburst-2026-09-08
OPENAI_PERSON_IMAGE_QUALITY=high
OPENAI_PERSON_REVIEW_MODEL=gpt-6-astra
```

Do not put `OPENAI_API_KEY` in the code or send it to teammates.

All planning, image generation, and checks use the existing server key. Scenes without person references use `OPENAI_IMAGE_MODEL` (default `gpt-image-2.5-sunburst-2026-09-08`) and `OPENAI_IMAGE_QUALITY` (default `medium`). Reference-image edits use independent settings: `OPENAI_PERSON_IMAGE_MODEL` defaults to the same pinned Sunburst model and `OPENAI_PERSON_IMAGE_QUALITY` defaults to `high`. Existing general-model overrides remain supported and do not change the reference defaults. No additional API key is needed.

High-quality reference edits and the dedicated appearance-review call may increase generation time and API cost. GPT Image 2-family models request a 1152×2048 scene, matching the final 9:16 composition; the downloadable branded PNG remains 1080×1920.

Across every brand, with or without reference photos, the prompts require front-facing heads, visible eyes, and direct camera eye contact for all featured people. Script and extra-prompt directions cannot override head or gaze direction.

The server audits each final rendered thumbnail for gaze and face visibility. When references are supplied, a passing gaze check is followed by the visible-feature review described below. Gaze and appearance failures share one retry: at most two generation attempts per thumbnail, always reusing the original references when supplied. Missing, malformed, or unavailable checks stop the job without another generation. The browser requires successful checks for every returned option. The CLI `--subject-image` path still requires configured OpenAI for its final camera-facing check and validates the supplied image once.

## Person Reference Workflow

Teammates can upload one separate photo per person, up to four references. Uploads appear progressively, with original previews, filenames, Replace, and Remove controls. Every selected photo is sent in order to OpenAI visual planning and the Images edits request. The model uses each person's facial proportions, hair, and distinguishing features as identity guidance while the scene, body pose, expression, and clothing can follow the script. Heads and eyes remain directed toward the camera. There is no cutout pipeline or pasted-photo fallback.

If the person-image model is explicitly overridden to `gpt-image-1` or `gpt-image-1.5`, reference edits use high input fidelity; 2-family models handle high input fidelity automatically. Install the packages in `requirements.txt`; no segmentation model or masking runtime is required.

The included `.python-version` selects Python 3.12 for native Render deployments, matching the Docker image.

The appearance review uses `OPENAI_PERSON_REVIEW_MODEL` (default `gpt-6-astra`), independently of `OPENAI_MODEL` for visual planning and gaze checks. It compares each original reference against the final thumbnail for face shape, eyes and brows, nose and mouth, hair, glasses, and sufficient clarity. Optional jewelry may change. All required visible-feature checks must pass for every reference. This reviews edit quality without identifying anyone or proving an exact identity match; it can miss changes. Exact likeness is not guaranteed by image generation, so teammates should review every face and hairstyle before use.

The browser locks reference controls while that brand's job runs and requires the reported reference count, edit strategy, and checked/passed appearance status for the overall result and every option before display. Duplicate image content is rejected, while different photos with the same filename are valid.

## Recommended: Render

1. Put this `thumbnail-agent` folder in a private GitHub repository.
2. Create a new Render web service from that repository.
3. Use these settings:

```text
Runtime: Python
Build command: python3 -m pip install -r requirements.txt
Start command: python3 thumbnail_agent.py serve --host 0.0.0.0 --port $PORT
```

4. Add the environment variables above.
5. Deploy.
6. Send your team the Render URL.

This folder also includes `render.yaml` if you prefer Render Blueprint setup.

## Docker Option

The included `Dockerfile` works on hosts that accept Docker apps:

```bash
docker build -t zoomex-thumbnail-agent .
docker run -p 8787:8787 \
  -e OPENAI_API_KEY="sk-..." \
  -e PORT=8787 \
  zoomex-thumbnail-agent
```

Then open:

```text
http://localhost:8787
```

## Important Security Notes

- Use a private repository.
- Do not enable browser API-key setup on the hosted app.
- Monitor OpenAI usage, because anyone with the link can spend API credits.
- Generated PNGs should be downloaded after creation; simple hosts may not keep files forever after restarts.
