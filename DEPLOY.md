# Deploying The Internal ZOOMEX Thumbnail Agent

This app is intended to be deployed as a private internal web app.

## What Teammates Get

Your team gets one URL. When they open it, the browser asks for a password. After that, they can enter a title and script to generate thumbnails.

## Required Server Secrets

Set these environment variables in your hosting provider:

```bash
OPENAI_API_KEY=sk-...
APP_PASSWORD=choose-a-company-password
OPENAI_MODEL=gpt-5.5
```

Do not put `OPENAI_API_KEY` in the code or send it to teammates.

## Recommended: Render

1. Put this `thumbnail-agent` folder in a private GitHub repository.
2. Create a new Render web service from that repository.
3. Use these settings:

```text
Runtime: Python
Build command: pip install -r requirements.txt
Start command: python thumbnail_agent.py serve --host 0.0.0.0 --port $PORT
```

4. Add the environment variables above.
5. Deploy.
6. Send your team the Render URL and the `APP_PASSWORD`.

This folder also includes `render.yaml` if you prefer Render Blueprint setup.

## Docker Option

The included `Dockerfile` works on hosts that accept Docker apps:

```bash
docker build -t zoomex-thumbnail-agent .
docker run -p 8787:8787 \
  -e OPENAI_API_KEY="sk-..." \
  -e APP_PASSWORD="your-password" \
  -e PORT=8787 \
  zoomex-thumbnail-agent
```

Then open:

```text
http://localhost:8787
```

## Important Security Notes

- Keep the app behind `APP_PASSWORD`.
- Use a private repository.
- Do not enable browser API-key setup on the hosted app.
- Monitor OpenAI usage, because anyone with the password can spend API credits.
- Generated PNGs should be downloaded after creation; simple hosts may not keep files forever after restarts.
