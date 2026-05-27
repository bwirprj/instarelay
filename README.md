# InstaRelay

InstaRelay is a personal Instagram post scheduler and posting gateway for a
single Instagram account. The app receives scheduled carousel posts from an
upstream system such as `homelessbot`, stores the job locally, uploads through
Instagram Web with Playwright, and sends status callbacks back to the upstream
system.

This repository intentionally contains clean source code only. Runtime files,
secrets, browser profiles, copied media, screenshots, SQLite databases, and
planning documents are excluded from git.

## Features

- FastAPI backend with SQLite storage.
- Basic Auth protected dashboard.
- API key management for upstream clients.
- Shared local media ingest from `homelessbot`.
- Scheduler with one-at-a-time worker execution.
- Playwright browser automation with persistent profile reuse.
- Image carousel posting.
- Caption and hashtag support.
- Retry lifecycle for recoverable failures.
- Cancel flow from dashboard or upstream API.
- Callback delivery to upstream systems.
- Telegram notification support.
- Realtime system logs on the dashboard.

## Architecture

```txt
homelessbot or another upstream client
  |
  | POST /api/v1/posts/schedule
  | Authorization: Bearer <api_key>
  v
FastAPI
  |
  | validate API key
  | validate idempotency
  | ingest media from shared local path
  v
SQLite + scheduler-owned uploads
  |
  | APScheduler checks due jobs every minute
  v
Sequential worker
  |
  | Playwright persistent browser profile
  v
Instagram Web
  |
  | publish success, retry, failure, cancel
  v
Telegram notification + upstream callback
```

## Runtime Assumptions

- One Instagram account only.
- One VPS or server node only.
- One browser upload job at a time.
- No Meta Graph API dependency.
- Media is image-only for the initial version.
- `homelessbot` and this scheduler may run on the same VPS.
- Scheduler copies or hardlinks media into its own `uploads/{post_id}` folder
  before the publish time.

## Project Structure

```txt
app/
  automation.py       Playwright Instagram publishing flow
  callbacks.py        Upstream callback delivery
  cli.py              Admin CLI helpers
  config.py           Environment-driven settings
  db.py               SQLite schema and queries
  main.py             FastAPI routes and dashboard
  media.py            Shared media path validation and ingest
  notification.py     Telegram notification helpers
  scheduler.py        APScheduler tick and worker lock
  schemas.py          Pydantic request schemas
  security.py         Basic Auth and API key auth
  timezone.py         WIB and UTC helpers
  worker.py           Publish, retry, fail, and callback orchestration

scripts/
  login_instagram.py  Manual first-login bootstrap script

templates/
  Dashboard pages

static/
  Dashboard CSS

systemd/
  instarelay.service
```

## Requirements

- Python 3.12 or newer.
- SQLite.
- Playwright Chromium.
- Linux server for production.
- Caddy, Nginx, or another reverse proxy for public access.
- Optional Telegram bot token for notifications.

## Local Development

Create a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

Create local runtime folders and environment:

```bash
cp .env.example .env
```

For local testing, edit `.env` and point paths to local folders. Example:

```txt
PROJECT_ROOT=/absolute/path/to/instarelay
DATABASE_PATH=/absolute/path/to/instarelay/db/scheduler.sqlite3
UPLOADS_DIR=/absolute/path/to/instarelay/uploads
SCREENSHOTS_DIR=/absolute/path/to/instarelay/screenshots
PROFILE_DIR=/absolute/path/to/instarelay/profile
HOMELESSBOT_MEDIA_ROOT=/absolute/path/to/homelessbot/public/generated
ADMIN_USERNAME=admin
ADMIN_PASSWORD=local-password
API_KEY_PEPPER=local-random-pepper
CALLBACK_SHARED_SECRET=local-random-secret
IG_USERNAME=your-instagram-username
IG_DRY_RUN=true
SCHEDULER_ENABLED=false
```

Run the app:

```bash
set -a
. ./.env
set +a
uvicorn app.main:app --reload
```

Open:

```txt
http://127.0.0.1:8000/
```

## Environment Variables

### Core Paths

```txt
PROJECT_ROOT=/home/ubuntu/instarelay
DATABASE_PATH=/home/ubuntu/instarelay/db/scheduler.sqlite3
UPLOADS_DIR=/home/ubuntu/instarelay/uploads
SCREENSHOTS_DIR=/home/ubuntu/instarelay/screenshots
PROFILE_DIR=/home/ubuntu/instarelay/profile
LOGS_DIR=/home/ubuntu/instarelay/logs
HOMELESSBOT_MEDIA_ROOT=/home/ubuntu/homelessbot/public/generated
```

### Auth And Secrets

```txt
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-this-password
API_KEY_PEPPER=change-this-random-pepper
CALLBACK_SHARED_SECRET=change-this-callback-secret
IG_USERNAME=your-instagram-username
```

`ADMIN_USERNAME` and `ADMIN_PASSWORD` protect the dashboard with Basic Auth.

`API_KEY_PEPPER` is used when hashing API keys. The app stores only hashes in
SQLite.

`CALLBACK_SHARED_SECRET` is sent to callback endpoints as:

```http
X-Instagram-Scheduler-Secret: <secret>
```

### Public URL And Routing

```txt
PUBLIC_BASE_URL=https://example.com/instarelay
ROOT_PATH=/instarelay
APP_TIMEZONE=Asia/Jakarta
```

Set `ROOT_PATH` when the app is mounted behind a reverse proxy path such as
`/instarelay`.

### Worker Flags

```txt
SCHEDULER_ENABLED=true
WORKER_ENABLED=true
IG_DRY_RUN=false
PLAYWRIGHT_HEADLESS=true
```

Use `IG_DRY_RUN=true` for safe local tests that should not publish to Instagram.

### Limits

```txt
MAX_MEDIA_BYTES=15728640
UPLOAD_TIMEOUT_SECONDS=120
STUCK_PROCESSING_MINUTES=30
```

### Telegram

```txt
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

If either value is empty, Telegram notifications are skipped.

## Dashboard

The dashboard is protected by Basic Auth and provides:

- Status overview.
- Post buckets:
  - `Schedule`: pending posts, sorted from nearest schedule to latest.
  - `Sent`: published or sent posts.
  - `Review`: failed, canceled, and retry-scheduled posts.
- Post detail page.
- Manual retry.
- Publish now.
- Cancel.
- Resend callback.
- API key creation and revocation.
- Realtime system logs.

## API Key Management

Create an API key from the dashboard or via CLI:

```bash
set -a
. ./.env
set +a
. .venv/bin/activate
python -m app.cli create-key --name homelessbot
```

The full API key is shown once. Store it in the upstream app environment.

API keys use this shape:

```txt
igs_live_<random_token>
```

Clients authenticate with:

```http
Authorization: Bearer <api_key>
```

## Scheduling API

### Create Scheduled Post

```http
POST /api/v1/posts/schedule
Authorization: Bearer <api_key>
Idempotency-Key: <optional-stable-key>
Content-Type: application/json
```

Payload:

```json
{
  "source": "homelessbot",
  "external_id": "draft_abc123",
  "idempotency_key": "homelessbot:draft_abc123:2026-05-21T19:00",
  "headline": "Post headline",
  "media_paths": [
    "draft_abc123/slide-1.png",
    "draft_abc123/slide-2.png"
  ],
  "image_urls": [
    "https://example.com/generated/draft_abc123/slide-1.png",
    "https://example.com/generated/draft_abc123/slide-2.png"
  ],
  "caption": "Caption utama untuk Instagram.",
  "hashtags": ["#tech", "#automation"],
  "post_date": "2026-05-21",
  "post_time": "19:00",
  "callback_url": "http://127.0.0.1:3005/api/instarelay/callback",
  "metadata": {
    "draft_id": "draft_abc123"
  }
}
```

Response:

```json
{
  "post_id": "post_01HXYZ",
  "external_id": "draft_abc123",
  "status": "pending",
  "duplicate": false,
  "scheduled_at_wib": "2026-05-21 19:00",
  "scheduled_at_utc": "2026-05-21T12:00:00Z",
  "media_count": 2
}
```

If the same idempotency key or `source + external_id` is sent again, the app
returns the existing post with `duplicate: true`.

### List Posts

```http
GET /api/v1/posts
Authorization: Bearer <api_key>
```

Optional query params:

```txt
status=pending
source=homelessbot
page=1
page_size=25
```

### Get Post Detail

```http
GET /api/v1/posts/{post_id}
Authorization: Bearer <api_key>
```

### Retry

```http
POST /api/v1/posts/{post_id}/retry
Authorization: Bearer <api_key>
```

Allowed for `failed` and `retry_scheduled`.

### Publish Now

```http
POST /api/v1/posts/{post_id}/publish-now
Authorization: Bearer <api_key>
```

Allowed for `pending` and `retry_scheduled`.

### Cancel By Post ID

```http
DELETE /api/v1/posts/{post_id}
Authorization: Bearer <api_key>
```

### Cancel By External ID

Used by `homelessbot` when an admin cancels from the upstream dashboard.

```http
POST /api/v1/posts/cancel
Authorization: Bearer <api_key>
Content-Type: application/json
```

Payload:

```json
{
  "source": "homelessbot",
  "external_id": "draft_abc123",
  "reason": "Canceled from homelessbot dashboard"
}
```

Response:

```json
{
  "post_id": "post_01HXYZ",
  "status": "canceled",
  "media_deleted": true
}
```

Cancel rules:

- `pending`, `retry_scheduled`, and `failed` can be canceled.
- `processing` returns `409 Conflict`.
- `published` returns `409 Conflict`.
- Scheduler-owned copied media in `uploads/{post_id}` is deleted.
- Original `homelessbot` generated media is not deleted.

## Callback Contract

When a post status changes meaningfully, the app sends a callback to
`callback_url` when provided.

Method:

```http
POST <callback_url>
X-Instagram-Scheduler-Secret: <CALLBACK_SHARED_SECRET>
Content-Type: application/json
```

Published payload example:

```json
{
  "event": "post.published",
  "status": "published",
  "post_id": "post_01HXYZ",
  "source": "homelessbot",
  "external_id": "draft_abc123",
  "headline": "Post headline",
  "scheduled_at_utc": "2026-05-21T12:00:00Z",
  "scheduled_at_wib": "2026-05-21 19:00",
  "published_at_utc": "2026-05-21T12:01:13Z",
  "published_at_wib": "2026-05-21 19:01",
  "next_retry_at_utc": null,
  "next_retry_at_wib": null,
  "retry_count": 0,
  "error_message": null,
  "screenshot_path": null,
  "media_deleted": true,
  "instagram_result": {
    "confirmed_by": "instagram_web_success_ui",
    "url": null
  }
}
```

Other callback events:

- `post.retry_scheduled`
- `post.failed`
- `post.canceled`

Callback retry policy:

- Timeout: 15 seconds.
- Retry attempts: up to 5.
- Backoff: 1 minute, 5 minutes, 15 minutes, 1 hour, 6 hours.
- Callback failure does not change Instagram publish status.

## Shared Media Ingest

The scheduler expects `media_paths` to be relative paths under:

```txt
HOMELESSBOT_MEDIA_ROOT
```

Example:

```txt
HOMELESSBOT_MEDIA_ROOT=/home/ubuntu/homelessbot/public/generated
media_paths=["draft_abc123/slide-1.png"]
resolved file=/home/ubuntu/homelessbot/public/generated/draft_abc123/slide-1.png
```

Security rules:

- Absolute paths are rejected.
- `..` path traversal is rejected.
- Symlink escapes outside `HOMELESSBOT_MEDIA_ROOT` are rejected.
- Missing files are rejected.
- Unsupported image files are rejected.
- Oversized files are rejected.

Ingest behavior:

- The source media is hardlinked into `uploads/{post_id}` when possible.
- If hardlink is unavailable, the source media is copied.
- The Playwright worker only reads scheduler-owned files from `uploads/{post_id}`.
- On successful publish, copied media is deleted.
- On cancel, copied media is deleted.
- Original upstream media remains owned by the upstream cleanup policy.

## Status Lifecycle

```txt
pending
  -> processing
  -> published

processing
  -> retry_scheduled
  -> failed

retry_scheduled
  -> pending

pending/retry_scheduled/failed
  -> canceled
```

## Retry Policy

Automatic retry delays:

```txt
Retry 1: 5 minutes
Retry 2: 15 minutes
Retry 3: 30 minutes
Max retry: 3
```

Recoverable failures include selector misses and upload timeouts.

Terminal failures include expired Instagram session or login checkpoint.

## First Instagram Login

The worker uses a persistent browser profile stored in `PROFILE_DIR`. You must
log in once manually on the server.

One practical VPS approach:

```bash
Xvfb :99 -screen 0 1365x900x24 >/tmp/ig-xvfb.log 2>&1 &
DISPLAY=:99 fluxbox >/tmp/ig-fluxbox.log 2>&1 &
x11vnc -display :99 -localhost -forever -shared -passwd <temporary-password> -rfbport 5901
```

From your local machine:

```bash
ssh -L 5901:127.0.0.1:5901 <server>
```

Connect with a VNC client to:

```txt
vnc://127.0.0.1:5901
```

Then run:

```bash
cd /home/ubuntu/instarelay
set -a
. ./.env
set +a
. .venv/bin/activate
DISPLAY=:99 python scripts/login_instagram.py
```

Log in to Instagram, complete Google login and any 2FA or checkpoint, then
press Enter in the script terminal. The profile is saved in `PROFILE_DIR`.

Stop the temporary VNC processes after login.

## Production Deployment With systemd

Install dependencies:

```bash
cd /home/ubuntu/instarelay
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install --with-deps chromium
playwright install chromium
```

Create `.env` from `.env.example`:

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

Install systemd service:

```bash
sudo cp systemd/instarelay.service /etc/systemd/system/instarelay.service
sudo systemctl daemon-reload
sudo systemctl enable instarelay.service
sudo systemctl restart instarelay.service
sudo systemctl status instarelay.service
```

The provided service runs:

```txt
127.0.0.1:8008
```

## Reverse Proxy Example With Caddy

Example when mounting this app at `/instarelay` while another app uses the
root path:

```caddyfile
example.com {
    encode zstd gzip

    handle_path /instarelay* {
        reverse_proxy 127.0.0.1:8008
    }

    reverse_proxy 127.0.0.1:3005
}
```

Set:

```txt
ROOT_PATH=/instarelay
PUBLIC_BASE_URL=https://example.com/instarelay
```

## Homelessbot Integration

Recommended upstream environment variables:

```txt
INSTARELAY_BASE_URL=https://example.com/instarelay
INSTARELAY_API_KEY=igs_live_xxxxxxxxx
INSTARELAY_CALLBACK_SECRET=xxxxxxxxx
INSTARELAY_MEDIA_MODE=shared_path
```

Recommended upstream callback endpoint:

```http
POST /api/instarelay/callback
X-Instagram-Scheduler-Secret: <secret>
```

The callback endpoint should:

- Verify `X-Instagram-Scheduler-Secret`.
- Match by `external_id` or scheduler `post_id`.
- Store callback payload for debugging.
- Update local draft status:
  - `published` -> posted or sent.
  - `failed` -> failed.
  - `retry_scheduled` -> scheduled with warning.
  - `canceled` -> canceled or back to editable draft.

## Useful Commands

Health:

```bash
curl http://127.0.0.1:8008/health
```

Create API key:

```bash
python -m app.cli create-key --name homelessbot
```

View logs:

```bash
sudo journalctl -u instarelay.service -f
```

Restart:

```bash
sudo systemctl restart instarelay.service
```

Manual scheduler tick:

```bash
curl -X POST http://127.0.0.1:8008/api/v1/admin/tick \
  -H "Authorization: Bearer <api_key>"
```

## Operational Notes

- Keep `IG_DRY_RUN=false` only when you are ready for real Instagram uploads.
- Never commit `.env`, browser profile, SQLite database, copied uploads, or
  screenshots.
- If Instagram asks for login again, rerun `scripts/login_instagram.py`.
- Instagram UI changes can break selectors. Use screenshots and logs to debug.
- Keep only one worker instance active for this personal-use architecture.

## Repository Hygiene

Ignored by git:

- `.env` and local secret files.
- SQLite DB files.
- Browser profile.
- Copied uploads.
- Screenshots.
- Logs.
- Local backups and storage folders.
- `BRD.md`.

This keeps the GitHub repository as clean source code only.
