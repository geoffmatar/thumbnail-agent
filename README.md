# ZOOMEX Thumbnail Agent

This local app creates 9:16 vertical thumbnails using the fixed ZOOMEX design in:

```text
assets/zoomex-design.png
```

Normal flow:

1. Enter your title.
2. Paste the script.
3. The agent generates the background image from the script.
4. The fixed ZOOMEX design is always composited on top.
5. The app repaints the two title bars and draws your title in Blinker Bold.

There is no template upload step anymore.
There is no font upload step anymore. The title font is fixed at `assets/Blinker-Bold.ttf`.

## Subject Placement

The generated background is prompted so the important subject details land in the center square between the ZOOMEX logo and title bars. Faces, heads, products, vehicles, or the key action should be centered there, while the rest of the scene can extend behind the full thumbnail.

## Setup

Add your OpenAI API key in the app, or put it in `.env`:

```bash
OPENAI_API_KEY="your_api_key_here"
OPENAI_MODEL="gpt-5.5"
```

Start the app:

```bash
python3 thumbnail_agent.py serve
```

Open:

```text
http://127.0.0.1:8787
```

## Internal Company Link

For internal company use, host this folder as a private web app and set these server environment variables:

```bash
OPENAI_API_KEY="your_server_key"
APP_PASSWORD="a_shared_company_password"
OPENAI_MODEL="gpt-5.5"
```

When `APP_PASSWORD` is set, the browser asks for a username and password before anyone can use the app. The username can be anything; the password must match `APP_PASSWORD`.

The hosted app should use:

```bash
python3 thumbnail_agent.py serve --host 0.0.0.0 --port $PORT
```

Generated thumbnails are saved on the server filesystem. On many simple hosts, those files may disappear after a redeploy or restart, so download the PNG after creating it.

## Title Line Breaks

The title auto-splits into two bars. To force the split, use `|`:

```text
THE KID WHO | REFUSED TO QUIT
```

## Command Line

```bash
python3 thumbnail_agent.py render --script my-script.txt --title "THE KID WHO | REFUSED TO QUIT"
```

For local testing without using image generation:

```bash
python3 thumbnail_agent.py render --script my-script.txt --title "TITLE HERE" --subject-image test.png
```
