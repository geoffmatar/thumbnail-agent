# Deploying The Internal Thumbnail Agent

This app is intended to be deployed as an internal web app.

## What Teammates Get

Your team gets one URL. When they open it, they can choose an agent, enter a title and script, and optionally upload a person reference image to generate thumbnails.

## Required Server Secrets

Set these environment variables in your hosting provider:

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.5
OPENAI_IMAGE_MODEL=gpt-image-1
OPENAI_IMAGE_QUALITY=medium
# Optional override for person-reference edits:
# OPENAI_REFERENCE_IMAGE_MODEL=gpt-image-1.5
```

Do not put `OPENAI_API_KEY` in the code or send it to teammates.

The reference workflow uses the existing server key. `OPENAI_REFERENCE_IMAGE_MODEL` falls back to `OPENAI_IMAGE_MODEL` when unset; the image-model default is `gpt-image-1`, and the quality default is `medium`.

## Person Reference Workflow

Reference photos are sent as image input to visual planning and directly to the OpenAI Images edits endpoint (`/v1/images/edits`). Supported reference-edit models are `gpt-image-1`, `gpt-image-1.5`, and `gpt-image-2`. Edits use high input fidelity (`input_fidelity="high"`) with `gpt-image-1` and `gpt-image-1.5`; `gpt-image-2` handles high input fidelity automatically. `gpt-image-1-mini` is rejected for person references because it does not support high input fidelity. Reference-edit failures are surfaced rather than retried as generation without the reference.

The workflow aims to retain every pictured person's facial identity while allowing clothing, expressions, pose, and scene changes. Teammates should compare the output with the actual reference photo before use. Identity consistency is not guaranteed, and the app does not automatically validate faces.

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
