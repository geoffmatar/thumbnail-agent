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
```

Do not put `OPENAI_API_KEY` in the code or send it to teammates.

The reference workflow uses the existing server key for visual planning and scene generation. `OPENAI_IMAGE_MODEL` selects the same model for normal generation and reference-image edits and defaults to `gpt-image-1`. `OPENAI_IMAGE_QUALITY` defaults to `medium`. No additional key or reference-model setting is needed.

## Person Reference Workflow

Teammates can upload one separate photo per person, up to four references. Uploads appear progressively, with original previews, filenames, Replace, and Remove controls. Every selected photo is sent in order to OpenAI visual planning and the Images edits request. The model uses each person's face, hair, and distinguishing features as identity guidance while the scene, pose, expression, and clothing can follow the script. There is no cutout pipeline or pasted-photo fallback.

Reference edits use high input fidelity for `gpt-image-1` and `gpt-image-1.5`; `gpt-image-2` handles high input fidelity automatically. Install the packages in `requirements.txt`; no segmentation model or masking runtime is required.

The included `.python-version` selects Python 3.12 for native Render deployments, matching the Docker image.

The browser locks reference controls while that brand's job runs and checks the reported reference count and edit strategy before showing the result. Exact likeness is not guaranteed by image generation; teammates should review every face and hairstyle before use. Duplicate image content is rejected, while different photos with the same filename are valid.

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
