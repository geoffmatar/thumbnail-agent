# Thumbnail Agent

This app creates 1080×1920 vertical thumbnails with fixed brand templates and title fonts.

| Agent | Fixed template | Title font | Thumbnails per creation |
| --- | --- | --- | --- |
| Zoomex | `assets/zoomex-design.png` | Blinker Bold | 2 |
| Alliance Latin | `assets/alliance-latin-design.png` | Poppins Bold | 1 |
| Alliance Black | `assets/alliance-black-design.png` | Poppins Bold | 1 |
| Alliance LGBTQ | `assets/alliance-lgbtq-design.png` | Poppins Bold | 1 |
| Leverage Edu | `assets/leverage-edu-design.png` | Gagalin Regular | 1 |
| Siyata | `assets/siyata-design.png` | Helvetica Neue Arabic Bold | 2 |

The other home-page cards are marked Coming soon.

Normal flow:

1. Choose an agent and enter your title.
2. Paste the script; optionally add visual instructions and a person reference image.
3. The agent creates the background from the script. If a photo is attached, its original people are composited into the thumbnail.
4. The app layers the background, brand design, and optional photo, then draws the title in that brand's font.
5. Review the result and download the PNG.

Templates and title fonts are bundled with the app.

## Person Reference Images

Upload a photo when the thumbnail should feature its original people. The app generates a background from the script, then composites the uploaded photo into the thumbnail. Faces, hair, expressions, pose, and clothing come from the original photo and are kept unchanged.

Opaque photos stay complete, including their original background. Transparent PNG cutouts keep their supplied transparency. For people-only placement, upload a transparent PNG cutout. The photo can still be sent to OpenAI for visual planning.

The photo may be proportionally scaled and repositioned to fit the selected brand's image opening. The script and extra prompt control the generated background; requests to change the uploaded people's expression, hairstyle, pose, or clothes do not apply to those people. Review the finished thumbnail for placement, visibility, and supplied transparency.

Photo selection is locked while that brand's thumbnail is being created. The studio hides a photo-based result if the server does not confirm original-photo composition.

Background generation uses `OPENAI_IMAGE_MODEL`, whose default is `gpt-image-1`. `OPENAI_IMAGE_QUALITY` defaults to `medium`. The photo composition uses the existing OpenAI key for planning and background generation; no additional key or reference-edit model setting is needed.

## Subject Placement

The generated background is prompted so the important subject details land in the center square between the selected brand's logo and title bars. Faces, heads, products, vehicles, or the key action should be centered there, while the rest of the scene can extend behind the full thumbnail.

Leverage Edu uses its own image opening: important subject details belong at x=400–1040, y=460–970, to the right of its logo and above the lower headline panel. Its template artwork was extracted from the supplied testimonial PSD without the sample headline; the headline and turquoise highlight are rendered dynamically.

## Setup

Put your OpenAI API key in `.env` for local use:

```bash
OPENAI_API_KEY="your_api_key_here"
OPENAI_MODEL="gpt-5.5"
OPENAI_IMAGE_MODEL="gpt-image-1"
OPENAI_IMAGE_QUALITY="medium"
```

Install the dependencies and start the app:

```bash
python3 -m pip install -r requirements.txt
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
OPENAI_MODEL="gpt-5.5"
OPENAI_IMAGE_MODEL="gpt-image-1"
OPENAI_IMAGE_QUALITY="medium"
```

The hosted link opens directly for teammates. Anyone with the hosted link can use it.

The hosted app should use:

```bash
python3 thumbnail_agent.py serve --host 0.0.0.0 --port $PORT
```

Generated thumbnails are saved on the server filesystem. On many simple hosts, those files may disappear after a redeploy or restart, so download the PNG after creating it.

## Title Line Breaks

Most agents auto-split the title into two lines. To force the split, use `|`:

```text
THE KID WHO | REFUSED TO QUIT
```

Leverage Edu uses three lines, with the final line highlighted in turquoise:

```text
MY JOURNEY TO | UNIVERSITY OF | SALFORD
```

It also splits a title automatically when no separators are supplied. With two parts, such as `MY JOURNEY TO UNIVERSITY OF | SALFORD`, the first part wraps across the top two lines and the second part stays in the highlighted third line.

## Command Line

```bash
python3 thumbnail_agent.py render --script my-script.txt --title "THE KID WHO | REFUSED TO QUIT"
```

To use a person reference with a specific agent:

```bash
python3 thumbnail_agent.py render --client alliance-latin --script my-script.txt --title "TITLE HERE" --person-reference person.png --extra-prompt "Set the background at a university campus."
```

For local testing without using image generation:

```bash
python3 thumbnail_agent.py render --script my-script.txt --title "TITLE HERE" --subject-image test.png
```

## Offline Regression Tests

Run the reference-workflow regression tests with mocked API responses; this does not generate images or spend API credits:

```bash
python3 -B -m unittest discover -s tests -v
```
