# Serenity Blooms Email Studio — Cloud Run deployment

This application can run locally or on Cloud Run without changing the agent workflow.

## Storage layout

Use two buckets in the existing Google Cloud project:

1. **Public asset bucket** — images and logos used in email and preview.
   - `images/...`
   - `logos/...`
   - grant `allUsers` the `Storage Object Viewer` role.
2. **Private data bucket** — mutable application state.
   - `unsubscribes.json`
   - `gmail_token.json`
   - `drafts/<draft-id>.json`
   - do **not** make this bucket public.

Set `SERENITY_DATA_BUCKET` to the private bucket name. Locally, if that variable is absent, the app continues to use `data/unsubscribes.json` and `data/gmail_token.json`.

## Secrets

Create Secret Manager secrets for:

- `serenity-google-oauth-client-id`
- `serenity-google-oauth-client-secret`
- `serenity-app-session-secret`
- `serenity-unsubscribe-signing-secret`
- your Gemini/API credential secret(s), using the same variable names your current ADK setup expects.

`APP_SESSION_SECRET` and `UNSUBSCRIBE_SIGNING_SECRET` should be different random values. For example:

```bash
openssl rand -hex 32
```

## Required environment variables on Cloud Run

Non-secret values:

```text
EMAIL_PUBLIC_ASSET_BASE_URL=https://storage.googleapis.com/YOUR_PUBLIC_ASSET_BUCKET
SERENITY_DATA_BUCKET=YOUR_PRIVATE_DATA_BUCKET
```

After the first deployment also set:

```text
APP_BASE_URL=https://YOUR-SERVICE-URL.run.app
GOOGLE_OAUTH_REDIRECT_URI=https://YOUR-SERVICE-URL.run.app/auth/google/callback
```

The public asset base is also used by the generated draft/preview, so a hosted preview never depends on `/assets/...` URLs.

## Service account permissions

Give the Cloud Run service account access to the private data bucket only:

```bash
gcloud storage buckets add-iam-policy-binding gs://YOUR_PRIVATE_DATA_BUCKET \
  --member="serviceAccount:YOUR_SERVICE_ACCOUNT_EMAIL" \
  --role="roles/storage.objectAdmin"
```

It does not need write access to the public asset bucket just to render/send existing images.

For this small-business app, `--max-instances 1` is a sensible starting point. Drafts and mutable state are persisted to the private bucket, so a container restart does not lose them.

Give the service account Secret Manager access to the secrets used by the service (`roles/secretmanager.secretAccessor`).

## Build and deploy

From the project directory:

```bash
gcloud run deploy serenity-blooms-email-studio \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --max-instances 1 \
  --set-env-vars "EMAIL_PUBLIC_ASSET_BASE_URL=https://storage.googleapis.com/YOUR_PUBLIC_ASSET_BUCKET,SERENITY_DATA_BUCKET=YOUR_PRIVATE_DATA_BUCKET" \
  --set-secrets "GOOGLE_OAUTH_CLIENT_ID=serenity-google-oauth-client-id:latest,GOOGLE_OAUTH_CLIENT_SECRET=serenity-google-oauth-client-secret:latest,APP_SESSION_SECRET=serenity-app-session-secret:latest,UNSUBSCRIBE_SIGNING_SECRET=serenity-unsubscribe-signing-secret:latest"
```

Add the Gemini/API secret mapping required by your current application to the same `--set-secrets` argument.

After deployment, Cloud Run prints the HTTPS service URL. Add that URL to the OAuth client in Google Cloud as an **Authorized redirect URI**:

```text
https://YOUR-SERVICE-URL.run.app/auth/google/callback
```

Then update the Cloud Run service:

```bash
gcloud run services update serenity-blooms-email-studio \
  --region us-east1 \
  --update-env-vars "APP_BASE_URL=https://YOUR-SERVICE-URL.run.app,GOOGLE_OAUTH_REDIRECT_URI=https://YOUR-SERVICE-URL.run.app/auth/google/callback"
```

## OAuth testing

While the Google OAuth app remains in Testing mode, add both your Gmail address and your wife's Gmail address under **Google Auth Platform → Audience → Test users**.

After deployment:

1. Open the Cloud Run URL.
2. Connect Gmail.
3. Generate an email and confirm preview images come from Cloud Storage.
4. Send a message to an address you control.
5. Open its unsubscribe link and confirm the address is written to `unsubscribes.json` in the private data bucket.
6. Revalidate/send to that address and confirm it is suppressed.

## Security note

`--allow-unauthenticated` makes the web interface public. Because this application can send from a connected Gmail account, production use should add application-level access control before sharing the URL broadly. For a private family/business deployment, Cloud Run IAM authentication is another option, but it changes how users access the site.
