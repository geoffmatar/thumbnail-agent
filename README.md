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
3. The agent creates the scene from the script, using a reference edit when a photo is attached.
4. The fixed brand design is composited on top, and the title is drawn in that brand's font.
5. Review the result and download the PNG.

Templates and title fonts are bundled with the app.

## Person Reference Images

Upload a photo when the thumbnail should feature the people shown in it. The reference workflow aims to preserve each person's facial structure and distinguishing features while allowing the script or extra prompt to change clothing, expression, pose, lighting, and scene. It creates a new scene rather than pasting the original photo into the template.

The actual uploaded image is supplied to both visual planning and the direct OpenAI Images edits request (`/v1/images/edits`). The edit request uses `input_fidelity="high"` for `gpt-image-1` and `gpt-image-1.5`; `gpt-image-2` handles high input fidelity automatically. A failed reference edit is reported as an error rather than falling back to generation without the photo.

Reference edits use `OPENAI_REFERENCE_IMAGE_MODEL` when set, otherwise `OPENAI_IMAGE_MODEL`, whose default is `gpt-image-1`. `OPENAI_IMAGE_QUALITY` controls image quality and defaults to `medium`. Keep using the existing OpenAI API key; the reference workflow does not require a separate key.

Supported reference-edit models are `gpt-image-1`, `gpt-image-1.5`, and `gpt-image-2`. `gpt-image-1-mini` is rejected for person references because it does not support high input fidelity.

Compare the result with the actual reference photo before using it. Generative edits can still alter a face, and an allowed change of expression naturally changes its appearance. High input fidelity improves consistency but does not guarantee identical faces; the app does not automatically verify identity.

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
# Optional override for person-reference edits:
# OPENAI_REFERENCE_IMAGE_MODEL="gpt-image-1.5"
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
python3 thumbnail_agent.py render --client alliance-latin --script my-script.txt --title "TITLE HERE" --person-reference person.png --extra-prompt "Keep the same people; change their outfits to formal wear."
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
