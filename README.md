# Known For API

Internal REST API at **https://api.getknownfor.com** for Known For's
deterministic data-pipeline work: scorecards, piece enumeration, scrape
parsing, content stage transitions.

Replaces the prior franken-stack of MCP bridges, ngrok tunnels, pm2 processes,
and Cowork custom connectors. Cowork sessions hit this API via `web_fetch`,
Claude Code sessions hit it via `curl` / `requests`, and cron jobs hit it for
periodic refreshes.

---

## Quick start (local)

```bash
git clone https://github.com/Known-For/api.git known-for-api
cd known-for-api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# edit .env: set KF_API_KEY (openssl rand -hex 32) and NOTION_API_TOKEN

# fill in Notion DB IDs in clients.json (content_db_id, resources_db_id)

pytest
uvicorn app.main:app --reload --port 8000

curl http://localhost:8000/health
```

---

## Environment variables

| Var                  | Required | Purpose                                                       |
| -------------------- | -------- | ------------------------------------------------------------- |
| `KF_API_KEY`         | yes      | Bearer token clients must present in `Authorization` header.  |
| `NOTION_API_TOKEN`   | yes      | Notion integration secret (`secret_…`).                       |
| `KF_DATA_DIR`        | no       | Where scrape files are persisted. Default `./data`. Render uses `/var/data`. |
| `NOTION_API_VERSION` | no       | Notion-Version header value. Default `2022-06-28`.            |
| `KF_CLIENTS_PATH`    | no       | Path to `clients.json`. Default = repo root.                  |

Generate the API key once with:

```bash
openssl rand -hex 32
```

Store it in Render's env var dashboard. Rotate by generating a new value and
updating the env var; deployed instances will pick it up on next restart.

---

## Endpoint reference

### `GET /health`

Unauthenticated. Returns `{"status": "ok", "version": "..."}`.

### `GET /`

Unauthenticated. Returns service identity.

### `POST /v1/scorecards/{client_slug}/{author_slug}`

Bearer-auth required. Generates an author scorecard for the given client.

Request body (all fields optional):

```json
{
  "start_date": "2026-04-02",
  "end_date":   "2026-05-02",
  "scrape_paste": "All activity..."
}
```

- `start_date` defaults to `today - 30d`.
- `end_date` defaults to today.
- `scrape_paste` is the raw "All activity" page paste from LinkedIn. If
  omitted, the API uses the most recent saved scrape for this client/author
  from `KF_DATA_DIR`. If no prior scrape exists, returns 422.

Behavior:

1. Verify bearer token.
2. Resolve `client_slug` and `author_slug` from `clients.json`.
3. Parse paste (if provided) and persist to `{KF_DATA_DIR}/scrapes/{client}/{author}/`.
4. Query Notion content DB for all pieces by this author.
5. Pull each piece's body via `GET /v1/blocks/{page_id}/children`.
6. Query Notion resources DB for sessions (Type=Signal File).
7. Match pieces ↔ scrape posts (URL exact, then fuzzy text).
8. Aggregate scorecard JSON.
9. Create a Notion page in the resources DB with Type=Scorecard.
10. Return scorecard + Notion page URL.

Response shape:

```json
{
  "request_id": "8e7…",
  "scorecard": {
    "client": "bain",
    "author": {"slug": "chuck-whitten", "name": "Chuck Whitten"},
    "date_range": {"start": "2026-04-02", "end": "2026-05-02"},
    "totals": {"pieces": 13, "pieces_matched": 11, "pieces_unmatched": 2,
               "scrape_posts": 18, "sessions": 4},
    "stages": {"Published": 9, "Draft": 4},
    "top_pieces": [...],
    "pieces": [...],
    "sessions": [...]
  },
  "notion_url": "https://www.notion.so/..."
}
```

Status codes:

| Code | When                                                          |
| ---- | ------------------------------------------------------------- |
| 200  | Success                                                       |
| 401  | Missing or invalid bearer token                               |
| 404  | Unknown client_slug or author_slug                            |
| 422  | Malformed scrape paste, or no scrape provided AND none on disk|
| 502  | Notion API call failed (body includes `notion_request_id`)    |
| 503  | Server is missing required env vars or `clients.json` config  |
| 500  | Unexpected error (logged to Render)                           |

Example call:

```bash
curl -X POST https://api.getknownfor.com/v1/scorecards/bain/chuck-whitten \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2026-04-02", "end_date": "2026-05-02"}'
```

---

## `clients.json`

Maps `client_slug` and `author_slug` to Notion database IDs and property
configuration. Ships with a `bain` / `chuck-whitten` entry but **the Notion
DB IDs must be filled in before the scorecard endpoint will work.**

```json
{
  "bain": {
    "name": "Bain & Company",
    "notion": {
      "content_db_id": "<32-char Notion DB ID>",
      "resources_db_id": "<32-char Notion DB ID>",
      "author_property": "Author",
      "type_property": "Type",
      "stage_property": "Stage",
      "signal_file_type_value": "Signal File",
      "scorecard_type_value": "Scorecard"
    },
    "authors": {
      "chuck-whitten": {
        "name": "Chuck Whitten",
        "notion_value": "Chuck Whitten"
      }
    }
  }
}
```

To add a new client or author, edit `clients.json`, commit, and let Render
auto-deploy.

---

## Deployment (Render)

`render.yaml` is checked into the repo root, so Render will pick it up
automatically when the repo is connected.

1. Create a new "Blueprint" in Render and point it at this repo.
2. Render reads `render.yaml`, provisions a starter web service + 1 GB
   persistent disk mounted at `/var/data`.
3. Set the two `sync: false` env vars in Render's dashboard:
   - `KF_API_KEY` (output of `openssl rand -hex 32`)
   - `NOTION_API_TOKEN` (Notion integration secret from
     `~/.config/known_for/notion_token` on Adam's Mac)
4. First deploy completes; verify `/health`.
5. Add custom domain `api.getknownfor.com` in Render dashboard → Settings →
   Custom Domains. Render shows a CNAME target.
6. In the domain registrar, add a CNAME for `api` pointing to the Render
   target. Wait for DNS + Render's automatic SSL provisioning.
7. End-to-end check:

   ```bash
   curl -X POST https://api.getknownfor.com/v1/scorecards/bain/chuck-whitten \
     -H "Authorization: Bearer $KF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{}'
   ```

---

## Adding a new endpoint

1. Add a new module under `app/routers/` (e.g. `app/routers/pieces.py`).
2. Define an `APIRouter` with the auth dependency:

   ```python
   from fastapi import APIRouter, Depends
   from ..auth import require_bearer

   router = APIRouter(prefix="/v1", dependencies=[Depends(require_bearer)])

   @router.get("/pieces/{client_slug}/{author_slug}")
   def list_pieces(...): ...
   ```

3. Define request/response Pydantic models in `app/models.py`.
4. Register the router in `app/main.py`:

   ```python
   from .routers import pieces
   app.include_router(pieces.router)
   ```

5. Add tests under `tests/` mirroring the file name.
6. Update this README's endpoint reference.

---

## Project layout

```
app/
  main.py            FastAPI app + exception handler
  config.py          env var loader
  auth.py            bearer token dependency
  models.py          pydantic request/response models
  notion.py          thin Notion REST client (no SDK, no MCP)
  clients.py         clients.json loader
  storage.py         scrape persistence on local/Render disk
  lib/
    parse_linkedin.py  raw LinkedIn paste -> structured posts
    parse_scrape.py    resolve relative dates + filter by range
    match_pieces.py    Notion pieces <-> scrape posts (URL + fuzzy)
    aggregate.py       final scorecard JSON
  routers/
    health.py
    scorecards.py     POST /v1/scorecards/{client}/{author}

tests/                pytest, no Notion network calls (responses lib mocks)
clients.json          client/author config (Notion DB IDs etc.)
render.yaml           Render web service + 1 GB disk
requirements.txt      runtime deps
requirements-dev.txt  + pytest, httpx, responses
```

---

## Anti-goals

- No MCP server, MCP bridge, or stdio-to-HTTP adapter
- No Cowork plugin
- No pm2 / ngrok / supergateway machinery
- No connector that proxies Notion through this API to Cowork (Cowork can hit
  these REST endpoints directly via `web_fetch` when needed)
