# Known For API

Internal REST API at **https://api.getknownfor.com** for Known For's
deterministic data-pipeline work: scorecards, piece enumeration, scrape
parsing, content stage transitions.

Replaces the prior franken-stack of MCP bridges, ngrok tunnels, pm2
processes, and Cowork custom connectors. Cowork sessions hit this API via
`web_fetch`, Claude Code sessions hit it via `curl` / `requests`, and cron
jobs hit it for periodic refreshes.

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

pytest
uvicorn app.main:app --reload --port 8000

curl http://localhost:8000/health
```

---

## Environment variables

| Var                  | Required | Purpose                                                                        |
| -------------------- | -------- | ------------------------------------------------------------------------------ |
| `KF_API_KEY`         | yes      | Bearer token clients must present in `Authorization` header.                   |
| `NOTION_API_TOKEN`   | yes      | Notion integration secret (`secret_…`).                                        |
| `KF_DATA_DIR`        | no       | Where scrape files are persisted. Default `./data`. Render uses `/var/data`.   |
| `NOTION_API_VERSION` | no       | Notion-Version header value. Default `2022-06-28`.                             |
| `KF_CLIENTS_PATH`    | no       | Path to `clients.json`. Default = repo root.                                   |

Generate the API key once with `openssl rand -hex 32`. Store in Render's env
var dashboard. Rotate by generating a new value and updating the env var;
deployed instances pick it up on next restart.

---

## Endpoint reference

### `GET /health`

Unauthenticated. `{"status": "ok", "version": "..."}`.

### `GET /`

Unauthenticated. Service identity.

### `POST /v1/scorecards/{client_slug}/{author_slug}`

Bearer-auth required. Generates an author scorecard following the 9-stage
workflow originally specified in `docs/skill-source/SKILL.md`.

Request body (all fields optional):

```json
{
  "start_date":   "2026-04-02",
  "end_date":     "2026-05-02",
  "scrape_paste": "All activity..."
}
```

- `start_date` defaults to `today - 30d`.
- `end_date` defaults to today.
- `scrape_paste` is the raw "All activity" page paste from LinkedIn.
  - **Mode A** (paste provided): the API saves the raw paste, runs
    `app/lib/parse_linkedin.py` to produce a `<author-slug>-posts-<today>.md`
    file in the author's storage dir, then continues from there.
  - **Mode B** (no paste): uses the most recent `*-posts-*.md` already on
    disk for this client/author. Returns 422 if none exists.

Pipeline stages (mirrors the original `author-scorecard` skill):

1. Verify bearer token; resolve `client_slug` against `clients.json`.
2. Stage 1 — get scrape file (Mode A parses the paste; Mode B reads disk).
3. Stage 3 — run `app/lib/parse_scrape.py` to produce structured JSON.
4. Stage 4 — query the client's Resources DB for sessions
   (`Type = Signal File AND Author = <slug-or-display>`).
5. Stage 5 — query the client's Content DB for pieces (filter by Author),
   then assemble each piece's body via `/v1/blocks/{id}/children`.
6. Stage 6 — call `app/lib/match_pieces.match()` in-process.
7. Stage 7 — run `app/lib/aggregate.py` with all the JSON inputs.
8. Stage 8 — create a Scorecard page in the client's Resources DB.

Response shape:

```json
{
  "request_id": "8e7…",
  "scorecard": {
    "meta":   { "author_name": "...", "client_name": "...", ... },
    "header": { "diagnosis_label": "Healthy|Watch|Trouble", "top_issue": "...",
                "leveraged_action": "..." },
    "stage1": { "sessions_held_in_range": 0, "last_session_date": null, ... },
    "stage2": { "pieces_delivered_in_range": 0, "piece_titles_in_range": [] },
    "stage3": { "pct_delivered_published": null, "stalled_pieces": [], ... },
    "stage4": { "kf_matched_posts_in_range": 0, "avg_reactions": null, ... },
    "match_diagnostics": { "total_pieces": 0, "matched": 0, "unmatched": 0,
                           "confidence_breakdown": {...} },
    "data_gaps": ["..."]
  },
  "notion_url": "https://www.notion.so/..."
}
```

Status codes:

| Code | When                                                                       |
| ---- | -------------------------------------------------------------------------- |
| 200  | Success                                                                    |
| 401  | Missing or invalid bearer token                                            |
| 404  | Unknown `client_slug`                                                      |
| 422  | Unparseable paste, or no paste AND no prior scrape on disk                 |
| 500  | Subprocess failure inside `parse_scrape.py` or `aggregate.py` (logged)     |
| 502  | Notion API call failed (body includes `notion_request_id`)                 |
| 503  | Server is missing required env vars                                        |

Example call:

```bash
curl -X POST https://api.getknownfor.com/v1/scorecards/bain/chuck-whitten \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2026-04-02", "end_date": "2026-05-02"}'
```

---

## `clients.json`

Maps `client_slug` to the client's Notion DB IDs. There is **no per-author
config**: any author slug is accepted in the URL and resolved at query time
against the configured DBs. Property names per DB are looked up against
`schema_hints.*_field_candidates`, taking the first candidate that exists.

```json
{
  "schema_hints": {
    "deliverable_title_field_candidates":   ["Name", "Title"],
    "deliverable_delivery_field_candidates": ["Delivery", "DRAFT Date", "Date"],
    "deliverable_author_field_candidates":   ["Author"],
    "deliverable_status_field_candidates":   ["Status"],
    "session_type_value":                    "Signal File",
    "session_date_field_candidates":         ["Session Date", "Date", "createdTime"]
  },
  "clients": {
    "bain": {
      "display_name":     "Bain & Co",
      "resources_db_id":  "...",
      "content_db_id":    "...",
      "author_convention":"display"
    }
  }
}
```

The author filter sent to Notion ORs across `select`, `multi_select`,
`rich_text`, and `title` shapes against both the slug (`chuck-whitten`) and
its title-cased display form (`Chuck Whitten`) — so it tolerates the
slug-vs-display variation observed in the wild (e.g. Bain Resources uses
slugs while Bain Content uses display names).

---

## Deployment (Render)

`render.yaml` is checked into the repo root.

1. Create a new Blueprint in Render and point it at this repo.
2. Render reads `render.yaml`, provisions a starter web service + 1 GB
   persistent disk mounted at `/var/data`.
3. Set the two `sync: false` env vars in Render's dashboard:
   - `KF_API_KEY` (`openssl rand -hex 32`)
   - `NOTION_API_TOKEN` (Notion integration secret)
4. First deploy completes; verify `/health`.
5. Add custom domain `api.getknownfor.com` in Render dashboard → Settings →
   Custom Domains. Render shows a CNAME target.
6. In the registrar, add a CNAME for `api` pointing to the Render target.
   Wait for DNS + automatic SSL provisioning.
7. End-to-end test:

   ```bash
   curl -X POST https://api.getknownfor.com/v1/scorecards/bain/chuck-whitten \
     -H "Authorization: Bearer $KF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{}'
   ```

   First call needs a paste (Mode A). After that, Mode B works against the
   persisted scrape file.

---

## Adding a new endpoint

1. Add a new module under `app/routers/`.
2. Define an `APIRouter` with the auth dependency:

   ```python
   from fastapi import APIRouter, Depends
   from ..auth import require_bearer

   router = APIRouter(prefix="/v1", dependencies=[Depends(require_bearer)])
   ```

3. Add request/response models in `app/models.py`.
4. Register the router in `app/main.py`.
5. Add tests under `tests/`.
6. Update this README.

---

## Project layout

```
app/
  main.py               FastAPI app + exception handler
  config.py             env var loader
  auth.py               bearer token dependency
  models.py             pydantic request/response models
  clients.py            clients.json loader (schema_hints + per-client DB IDs)

  notion/               REUSABLE Notion primitives. Compose; don't copy.
    __init__.py           re-exports NotionClient, NotionAPIError, blocks,
                          filters, properties.
    client.py             thin REST client; auto-paginates list endpoints;
                          query_database_all, get_block_children_all,
                          retrieve_database, retrieve_page, create_page,
                          update_page.
    filters.py            and_/or_, select_equals, multi_select_contains,
                          status_equals, rich_text_equals/contains,
                          title_equals/contains, date_on_or_after/before,
                          date_between, checkbox_equals, AND domain helpers
                          author_any_shape, type_select_or_multi.
    properties.py         read_value, first_present, first_present_named,
                          read_first — handles every common Notion property
                          type and the schema-hint candidate-name pattern.
    blocks.py             authoring helpers (text_run, paragraph, heading_2,
                          heading_3, bullet, divider) + reading helpers
                          (block_to_text, assemble_body_text(client, page_id)).

  workflows/            One module per deterministic workflow.
    scorecard.py          Author scorecard. Composes app.notion + app.lib;
                          owns scorecard-specific things (storage paths,
                          subprocess of parse_linkedin/parse_scrape/aggregate,
                          Notion-row-to-pieces/sessions mapping, page layout).
                          Public entry: run(...) → (scorecard, notion_url, request_id).
                          Public error: ScorecardError(status, detail).

  lib/                  Vendored verbatim from the original author-scorecard
    parse_linkedin.py     skill. Driven via subprocess in workflows/scorecard;
    parse_scrape.py       match_pieces.match() is imported in-process.
    match_pieces.py
    aggregate.py

  routers/              Thin FastAPI handlers; validate input, call workflow,
    health.py             translate workflow errors → HTTP status codes.
    scorecards.py

docs/skill-source/      The original SKILL.md and README.md, kept for
                        reference so the API behavior can be diffed against
                        the originating skill spec.

tests/                  pytest, no Notion network calls (responses lib mocks)
  fixtures/             chuck-whitten-posts-2026-05-02.md (verified ground truth)
  test_notion_primitives.py  filters / properties / blocks unit coverage
  test_lib_pipeline.py       subprocess pipeline against the Chuck fixture
  test_scorecard_endpoint.py end-to-end with mocked Notion

clients.json            per-client Notion DB IDs + schema_hints
render.yaml             Render web service + 1 GB disk
requirements.txt        runtime deps
requirements-dev.txt    + pytest, httpx, responses
```

### Adding a new workflow

A second deterministic workflow (e.g. piece enumeration, stage transitions,
scrape parsing as a standalone endpoint) should:

1. Live as a single module under `app/workflows/<name>.py`.
2. Import `NotionClient`, `filters`, `properties`, and `blocks` from
   `app.notion` — never reach for raw HTTP or rebuild filter dicts inline.
   If something is missing from `app.notion`, add it there.
3. Define a public `run(...)` function returning whatever the router needs,
   and a `<Name>Error(status, detail)` exception class so the router can
   map workflow errors to HTTP status codes uniformly.
4. Get its own thin router under `app/routers/`. The router validates the
   request shape, calls `workflow.run(...)`, and returns the response.

---

## Anti-goals

- No MCP server, MCP bridge, or stdio-to-HTTP adapter
- No Cowork plugin
- No pm2 / ngrok / supergateway machinery
- No connector that proxies Notion through this API to Cowork (Cowork can hit
  these REST endpoints directly via `web_fetch` when needed)
