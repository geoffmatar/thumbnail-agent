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
2. Paste the script; optionally add visual instructions and a separate reference photo for each person.
3. The agent creates a natural scene from the script, using the uploaded people as identity references.
4. The fixed brand design is composited over the scene, and the title is drawn in that brand's font.
5. Review the result and download the PNG.

Templates and title fonts are bundled with the app.

Every brand prompts featured people with front-facing heads, visible eyes, and direct eye contact with the camera, with or without reference photos. Script and extra-prompt instructions cannot override this direction.

The final rendered thumbnail receives a vision check for face direction and visibility, without identity recognition. A gaze or visibility failure allows one regeneration, reusing the original references when supplied. An unavailable or malformed check stops creation; the studio shows only results whose checks are confirmed for every option.

## Person Reference Images

Upload one photo per person, up to four people. The studio starts with **Upload image 1** and reveals the next upload after each selection. Each reference shows its original thumbnail and filename, with Replace and Remove controls; click the thumbnail to enlarge it. References are remembered separately for each brand while the page stays open and are locked during that brand's generation.

All selected photos are supplied together, in order, to visual planning and the OpenAI Images edits request. Each person's facial proportions, hair, and distinguishing features guide their identity in the generated scene. The script and extra prompt can change the setting, body pose, expression, and clothing while heads and eyes remain directed toward the camera. There is no local cutout, pasted-photo composition, or intact-photo fallback.

The studio verifies that the server reports using every uploaded reference before showing a photo-based result. Exact likeness is not guaranteed by image generation; review all faces and hair before using the thumbnail. Uploading identical image content more than once is rejected; different photos may share a filename.

`OPENAI_IMAGE_MODEL` selects the model for both scene generation and reference-image edits and defaults to `gpt-image-1`. Edits use high input fidelity with `gpt-image-1` and `gpt-image-1.5`; `gpt-image-2` handles high input fidelity automatically. `OPENAI_IMAGE_QUALITY` defaults to `medium`. The existing server OpenAI key is used throughout; no additional key or reference-model setting is needed.

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
python3 thumbnail_agent.py render --client alliance-latin --script my-script.txt --title "TITLE HERE" --person-reference person.png --extra-prompt "Show this person arriving at a university campus, smiling in formal clothes."
```

To use an existing scene image instead of generating one, OpenAI must still be configured for the final camera-facing check. A supplied image is checked once:

```bash
python3 thumbnail_agent.py render --script my-script.txt --title "TITLE HERE" --subject-image test.png
```

## Offline Regression Tests

Run the reference-workflow regression tests with mocked API responses; this does not generate images or spend API credits:

```bash
python3 -B -m unittest discover -s tests -v
```
