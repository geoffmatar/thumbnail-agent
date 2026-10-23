# Deploying the Thumbnail Agent

The migration keeps the same repository, existing Render service, URL, six brand templates, fonts and one-to-four-photo upload flow.

## Provider configuration

Set these values in the existing service's Environment settings:

```text
THUMBNAIL_IMAGE_PROVIDER=higgsfield
THUMBNAIL_VISION_PROVIDER=gemini
HF_API_KEY_ID=<Higgsfield API key ID>
HF_API_KEY_SECRET=<Higgsfield API key secret>
GEMINI_API_KEY=<Google Gemini API key>
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_REVIEW_MODEL=gemini-3.8-flash
```

Store the three credential values as server secrets. Keep them out of chat, browser code and source control. Local development reads the ignored `.env`; changing it does not update Render.

Higgsfield uses production `marketing-studio/image/sunburst` for scenes and reference edits: high quality, 2K, 9:16, `enhance_prompt=false`. All references are uploaded in order with signed upload URLs. Finished images are downloaded immediately and rendered as 1080×1920 branded PNGs.

Gemini handles planning, optional gaze checks and required visible-feature appearance review. The review model is independent of the planning model. With both new selectors set, no creation step calls OpenAI or falls back to its billing. Existing OpenAI environment values may remain for rollback.

### Billing and account access

Higgsfield's API has a separate prepaid dollar balance. Subscription/plugin credits and Unlimited allowances do not fund website API requests. Gemini uses a Google API project with its own billing and quotas. Confirm model access and available balance/quota in both accounts.

References: [Higgsfield API and billing](https://higgsfield.ai/creator-hub/help-center/integrations/what-is-the-higgsfield-api), [Sunburst parameters](https://open.higgsfield.ai/models/marketing-studio/image/sunburst/api-reference), [Gemini models](https://ai.google.dev/gemini-api/docs/models).

## Update the existing Render service

1. Sign in to the Render account/workspace that owns `thumbnail-agent-1.onrender.com`. An empty workspace cannot update it.
2. Open that existing web service's **Environment** page. Add the selectors, models and secrets above.
3. Keep its repository and existing build/start commands:
   - Build: `python3 -m pip install -r requirements.txt`
   - Start: `python3 thumbnail_agent.py serve --host 0.0.0.0 --port $PORT`
4. Deploy the migration commit after credentials and provider billing/access are ready.
5. Confirm `/api/status` reports Higgsfield images, Gemini vision, both configuration flags true, workflow 8, and valid brand assets.
6. Run one single-option brand job with a short script and individual reference photo(s). Confirm completion, appearance checks, and a downloaded 1080×1920 PNG. Visually compare every face and hairstyle before releasing the migration to all teams.

`render.yaml` describes the new provider configuration for Blueprint-managed services. Committing it does not apply environment changes to a separately configured existing service. `.python-version` and the Docker image select Python 3.12.

`/healthz` confirms only that the HTTP process responds. `/api/status` credential flags indicate nonempty configuration; they do not verify authentication, balance, successful generation or image quality. Missing configuration is detected before a request can spend on either provider.

## Reference and retry behavior

Original-reference ordering, required appearance checks and best-effort camera direction remain. Every visible-feature category must pass for every reference. Failed, blocked, unavailable or malformed appearance reviews cannot publish unchecked output. Head angle alone never rejects a thumbnail.

Appearance and gaze corrections share at most two intentional generation attempts per thumbnail, always from original photos. Zoomex and Siyata create two options, so a run can generate up to four images. A failed optional correction retains the previously selected appearance-passing result.

The Higgsfield client never automatically repeats a generation submission after an ambiguous receipt, timeout or network failure. Each submission uses an idempotency key; only safe status/download reads are retried. A timed-out request may still finish at Higgsfield: inspect provider request history before starting another run.

Automatic review evaluates visible editing quality, without identifying anyone or guaranteeing exact likeness. Inspect finished faces and hair before use.

## Rollback

Without selectors, existing installations retain the OpenAI workflow. To roll back explicitly, set both provider selectors to `openai`, supply a valid funded `OPENAI_API_KEY`, and redeploy. Existing `OPENAI_*` overrides remain supported. An invalid key or exhausted OpenAI organization budget must be resolved before rollback can generate.

Provider errors never change billing providers automatically.

## Offline checks

```bash
python3 -B -m unittest discover -s tests -v
node tests/test_frontend_readiness.js
```

These use synthetic images and mocked responses, spend no credits, and do not prove live access or real-person likeness.

## Hosting notes

Generated PNGs stay on the host filesystem and may disappear after restart/redeploy; download them promptly. Browser API-key setup stays disabled. Anyone with access to the app URL can request generation, so monitor both provider accounts.

Docker deployments use the included image and the same environment variables; pass secrets through the host's secret store or an ignored environment file.
