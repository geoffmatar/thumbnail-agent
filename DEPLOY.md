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

The photo workflow uses the existing server key for planning and background generation. `OPENAI_IMAGE_MODEL` defaults to `gpt-image-1`, and `OPENAI_IMAGE_QUALITY` defaults to `medium`. No additional key or reference-edit model setting is needed.

## Person Reference Workflow

With a person photo attached, OpenAI generates the background scene. The server creates a local cutout using source photo colors and detail, then composites it into the selected brand template. Faces, hair, expressions, pose, and clothing are not generated again; only proportional scaling and positioning are applied to fit the image opening.

Local background removal uses bundled `assets/u2netp.onnx`, NumPy, and ONNX Runtime. Install all dependencies from `requirements.txt` and keep the model in the deployment; no extra masking API key or runtime model download is needed. Before an automatic cutout is accepted, OpenAI checks the source photo and candidate mask for complete coverage of every person's face, head, hair, and body. This coverage check does not identify people and runs once before thumbnail variants are generated. The photo can also be sent to OpenAI for visual planning.

The included `.python-version` selects Python 3.12 for native Render deployments, matching the Docker image.

Transparent PNG cutouts retain their supplied transparency. If automatic isolation or the coverage check is uncertain, the intact original photo is used and the completion message explains why. Script and extra-prompt requests apply to the generated background. Teammates should review complete people, hair, and placement before using the thumbnail.

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
