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

Every brand aims to turn featured faces naturally toward the camera while protecting their facial features. Angled or looking-away reference photos are accepted; uploads do not need to face the camera.

An optional gaze check can guide one automatic correction, with at most two generation attempts per thumbnail shared with appearance corrections. Off-camera gaze or a missing, malformed, or unavailable gaze check never blocks a thumbnail. With reference photos, the app keeps the best result that passes the required appearance review, even if its gaze remains off-camera. It always reuses the original references for a correction.

## Person Reference Images

Upload one photo per person, up to four people. The studio starts with **Upload image 1** and reveals the next upload after each selection. Each reference shows its original thumbnail and filename, with Replace and Remove controls; click the thumbnail to enlarge it. References are remembered separately for each brand while the page stays open and are locked during that brand's generation.

All selected photos are supplied together, in order, to visual planning and the selected image provider's edit request. Each person's facial proportions, hair, and distinguishing features guide their appearance in the generated scene. The script and extra prompt can change the setting, body pose, expression, and clothing. Camera-facing head and eye direction is a best-effort target, with facial features taking priority over forced reorientation. There is no local cutout, pasted-photo composition, or intact-photo fallback.

An appearance review compares each reference photo with the final rendered thumbnail for visible face shape, eyes and brows, nose and mouth, hair, glasses, and sufficient clarity, regardless of gaze direction. With Gemini selected, `GEMINI_REVIEW_MODEL` defaults to `gemini-3.8-flash`, independently of `GEMINI_MODEL` (`gemini-3.5-flash-lite`) for planning and optional gaze checks. Optional jewelry may change. Changed facial features or an unavailable or malformed appearance review can still stop creation. This is a review of visible edit quality, without identifying anyone or proving an exact identity match. The check can miss changes, and exact likeness is not guaranteed by image generation; review all faces and hair before using the thumbnail.

The studio checks the server's reported reference count, edit strategy, and successful appearance review for every option before showing a photo-based result. Uploading identical image content more than once is rejected; different photos may share a filename.

With Higgsfield selected, both new scenes and reference edits use Marketing Studio Image 2.5 Sunburst (`marketing-studio/image/sunburst`) in direct mode: high quality, 2K, 9:16, and `enhance_prompt=false`. The app supplies all original reference photos in their numbered order. Higgsfield uploads remain outside the app's public asset routes. Completed images are downloaded immediately and composited to 1080×1920 branded PNGs. Submission failures never trigger an OpenAI fallback. An ambiguous or timed-out submission is not automatically resubmitted; inspect it in the provider's dashboard before starting another run.

Higgsfield's API balance is separate from its website subscription and plugin credits. Gemini planning/review use Google API billing and limits. The Higgsfield API does not provide the text/vision review model, so the two providers are required for this configuration. See [Higgsfield API billing](https://higgsfield.ai/creator-hub/help-center/integrations/what-is-the-higgsfield-api), [Sunburst API parameters](https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference), and [Gemini model documentation](https://ai.google.dev/gemini-api/docs/models).

## Subject Placement

The generated background is prompted so the important subject details land in the center square between the selected brand's logo and title bars. Faces, heads, products, vehicles, or the key action should be centered there, while the rest of the scene can extend behind the full thumbnail.

Leverage Edu uses its own image opening: important subject details belong at x=400–1040, y=460–970, to the right of its logo and above the lower headline panel. Its template artwork was extracted from the supplied testimonial PSD without the sample headline; the headline and turquoise highlight are rendered dynamically.

## Setup

Copy `.env.example` to the ignored `.env` and configure these settings locally or in the host's server environment. Keep real credentials out of chat, source control, browser code, and screenshots:

```bash
THUMBNAIL_IMAGE_PROVIDER=higgsfield
THUMBNAIL_VISION_PROVIDER=gemini
HF_API_KEY_ID=<server-only-key-id>
HF_API_KEY_SECRET=<server-only-key-secret>
GEMINI_API_KEY=<server-only-google-api-key>
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_REVIEW_MODEL=gemini-3.8-flash
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

For internal company use, host this folder as a private web app and set the same server environment variables as above. For the existing Render service, update its Environment settings; a local `.env` or a commit to `render.yaml` does not update an independently configured live service.

The hosted link opens directly for teammates. Anyone with the hosted link can use it.

The hosted app should use:

```bash
python3 thumbnail_agent.py serve --host 0.0.0.0 --port $PORT
```

Generated thumbnails are saved on the server filesystem. On many simple hosts, those files may disappear after a redeploy or restart, so download the PNG after creating it.

`/api/status` reports selected providers and whether each has nonempty credentials. This is configuration presence, not a live authentication, balance, or generation test. Both providers and the selected brand's assets must be configured before the browser enables Create. Missing Gemini credentials are detected before an image request can spend Higgsfield funds.

### Existing installations and rollback

When provider selectors are absent, existing installations retain the OpenAI workflow. Selecting `THUMBNAIL_IMAGE_PROVIDER=higgsfield` and `THUMBNAIL_VISION_PROVIDER=gemini` removes OpenAI API calls from the whole creation path, even when an old `OPENAI_API_KEY` remains in the host environment.

To roll back, set both selectors to `openai` and provide a valid funded `OPENAI_API_KEY`. Legacy options remain supported: `OPENAI_MODEL=gpt-5.5`, `OPENAI_IMAGE_MODEL=gpt-image-2.5-sunburst-2026-09-08`, `OPENAI_IMAGE_QUALITY=medium`, independent `OPENAI_PERSON_IMAGE_MODEL=gpt-image-2.5-sunburst-2026-09-08`, `OPENAI_PERSON_IMAGE_QUALITY=high`, and `OPENAI_PERSON_REVIEW_MODEL=gpt-6-astra`. GPT Image 2-family models use 1152×2048; explicit `gpt-image-1`/`gpt-image-1.5` reference overrides use high input fidelity. Rollback cannot bypass an exhausted OpenAI organization budget or an invalid key.

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

To use an existing scene image instead of generating one, supply `--subject-image`. Its optional gaze check does not block the result or require a camera-facing image:

```bash
python3 thumbnail_agent.py render --script my-script.txt --title "TITLE HERE" --subject-image test.png
```

## Offline Regression Tests

Run the reference-workflow regression tests with mocked API responses; this does not generate images or spend API credits:

```bash
python3 -B -m unittest discover -s tests -v
```
