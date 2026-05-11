# Known For API

Authoritative reference for AI agents (Cowork, Claude Code, scheduled jobs) that need to read or write Known For Notion data deterministically.

**Base URL:** `https://api.getknownfor.com`

**Auth:** Bearer token. Every request to `/v1/*` must include:

```
Authorization: Bearer $KF_API_KEY
```

The bearer token is stored in Render's env vars on the server side and in Adam's password manager + `~/.zshrc` on the client side. Never log it, never paste it into chat, never commit it. If you need the value, look up `KF_API_KEY` in the operator's secret store; if you can't find it, ask the operator — do not guess.

**Live OpenAPI spec:** [`/openapi.json`](https://api.getknownfor.com/openapi.json) (machine-readable) and [`/docs`](https://api.getknownfor.com/docs) (interactive).

---

## When to use this API vs. other Notion tools

Pick the right tool for the job:

| Goal | Use |
| --- | --- |
| Enumerate every piece in a database matching a filter (no silent caps) | **This API** — `POST /v1/notion/databases/{id}/query` |
| Discover what filter values are valid for a database property | **This API** — `GET  /v1/notion/databases/{id}/schema` |
| Run the author scorecard workflow end-to-end | **This API** — `POST /v1/scorecards/{client}/{author}` |
| Look up a single Notion page Adam casually mentioned by name or URL | Cowork's official Notion connector (`notion-fetch`, etc.) |
| Free-form "what does Notion know about X?" exploration | Cowork's official Notion connector |
| **Deterministic** counting / matching / filtering / mutation | **This API**, never `notion-search` |

The official Notion connector's `notion-search` is **semantic, capped at ~25 results, and silently omits matches**. Never use it for counting, enumerating, or anything that requires completeness. That failure mode is exactly the reason this API exists.

---

## Endpoints

All `/v1/*` endpoints require bearer auth. Error shape on every failure:

```json
{ "detail": "human-readable message" }
```

…except Notion API errors, which surface as:

```json
{
  "error": "Notion API error",
  "message": "the message Notion returned",
  "notion_status": 400,
  "notion_request_id": "abc-…"
}
```

### `GET /health` — unauth

Liveness check.

```bash
curl https://api.getknownfor.com/health
# → {"status":"ok","version":"0.1.0"}
```

### `GET /` — unauth

Service identity. `{"service":"known-for-api","version":"..."}`.

---

### `GET /v1/notion/databases/{db_id}/schema`

Returns the database's property types **and** valid select option names. **Call this first** before building a filter — it's how you avoid 400 errors from sending an option name that doesn't exist.

```bash
curl -H "Authorization: Bearer $KF_API_KEY" \
  https://api.getknownfor.com/v1/notion/databases/$DB_ID/schema
```

Response:

```json
{
  "id": "24042d44-95b2-8034-83bb-d817d453311c",
  "title": "Bain Content",
  "properties": {
    "Name":    {"type": "title"},
    "Author":  {"type": "select", "options": ["chuck-whitten", "karen-harris", "..."]},
    "Type":    {"type": "select", "options": ["LinkedIn", "Newsletter", "..."]},
    "Status":  {"type": "status", "options": ["Draft", "Published", "..."]},
    "Delivery":{"type": "date"},
    "Notes":   {"type": "rich_text"}
  }
}
```

For select/multi_select/status properties, `options` is the list of names you can use as filter values. For other types, `options` is omitted.

---

### `POST /v1/notion/databases/{db_id}/query`

Query a database with a filter and/or sort. Auto-paginates up to `max_results` (default 1000, hard cap 10000). The `filter` and `sorts` fields pass through verbatim to Notion's API; build them per [Notion's filter docs](https://developers.notion.com/reference/post-database-query-filter).

Request body (all fields optional):

```json
{
  "filter": { "property": "Author", "select": {"equals": "chuck-whitten"} },
  "sorts":  [ { "property": "Delivery", "direction": "descending" } ],
  "max_results": 1000
}
```

Response:

```json
{
  "results": [ /* Notion page objects, as-is */ ],
  "count": 124,
  "truncated": false
}
```

`truncated: true` means we stopped at `max_results` and more rows may exist; bump `max_results` (up to 10000) or refine the filter.

Example — every Chuck Whitten piece, newest first:

```bash
curl -X POST https://api.getknownfor.com/v1/notion/databases/$BAIN_CONTENT_DB/query \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "filter": {"property": "Author", "select": {"equals": "chuck-whitten"}},
    "sorts":  [{"property": "Delivery", "direction": "descending"}]
  }'
```

---

### `GET /v1/notion/pages/{page_id}`

Returns the full Notion page object (properties payload, as-is).

```bash
curl -H "Authorization: Bearer $KF_API_KEY" \
  https://api.getknownfor.com/v1/notion/pages/$PAGE_ID
```

---

### `GET /v1/notion/pages/{page_id}/body`

Walks the page's paginated `block_children` and returns the concatenated plain text. Best-effort: returns `""` rather than 500 if a block subtree fails.

```bash
curl -H "Authorization: Bearer $KF_API_KEY" \
  https://api.getknownfor.com/v1/notion/pages/$PAGE_ID/body
# → {"page_id": "...", "plain_text": "...", "length": 1234}
```

---

### `POST /v1/notion/pages`

Create a Notion page. Body fields pass through to Notion verbatim.

```bash
curl -X POST https://api.getknownfor.com/v1/notion/pages \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "parent": {"database_id": "..."},
    "properties": {
      "Name": {"title": [{"type": "text", "text": {"content": "My new page"}}]},
      "Type": {"select": {"name": "LinkedIn"}}
    },
    "children": [
      { "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type":"text","text":{"content":"Hello."}}]} }
    ]
  }'
```

Returns the created page object (including its `id` and `url`).

---

### `PATCH /v1/notion/pages/{page_id}`

Update properties on an existing page, or archive it.

```bash
curl -X PATCH https://api.getknownfor.com/v1/notion/pages/$PAGE_ID \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"properties": {"Status": {"status": {"name": "Published"}}}}'

# Archive:
curl -X PATCH https://api.getknownfor.com/v1/notion/pages/$PAGE_ID \
  -H "Authorization: Bearer $KF_API_KEY" \
  -d '{"archived": true}'
```

---

### `POST /v1/scorecards/{client_slug}/{author_slug}`

Run the author scorecard workflow end-to-end. Parses a LinkedIn paste (or reuses the latest on disk), enumerates the client's content + resources DBs from Notion, matches pieces to scrape posts, computes Stage 1–4 metrics, writes a Scorecard page back to the client's Resources DB, and returns the full scorecard JSON.

Request body (all fields optional):

```json
{
  "start_date": "2026-04-11",
  "end_date":   "2026-05-11",
  "scrape_paste": "<raw LinkedIn All Activity paste>"
}
```

Defaults: `end_date = today`, `start_date = end_date - 30 days`. If `scrape_paste` is omitted, the most recent persisted scrape on Render's disk is reused.

Example:

```bash
# With a fresh paste
jq -Rs '{scrape_paste: .}' /tmp/chuck-raw.md | \
  curl -X POST https://api.getknownfor.com/v1/scorecards/bain/chuck-whitten \
    -H "Authorization: Bearer $KF_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary @-

# With a custom date range, reusing the last persisted scrape
curl -X POST https://api.getknownfor.com/v1/scorecards/bain/chuck-whitten \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2026-04-02", "end_date": "2026-05-02"}'
```

Response shape:

```json
{
  "request_id": "...",
  "scorecard": {
    "meta":   {"author_name":"...", "client_name":"...", "start_date":"...", "end_date":"..."},
    "header": {"diagnosis_label":"Healthy|Watch|Trouble","top_issue":"...","leveraged_action":"..."},
    "stage1": {"sessions_held_in_range": 0, "last_session_date": "...", "diagnosis": "..."},
    "stage2": {"pieces_delivered_in_range": 11, "piece_titles_in_range": ["..."]},
    "stage3": {"pct_delivered_published": 90.9, "stalled_pieces": [...], "diagnosis": "..."},
    "stage4": {"kf_matched_posts_in_range": 22, "avg_reactions": 45.5},
    "match_diagnostics": {"total_pieces": 124, "matched": 52, "confidence_breakdown": {...}},
    "data_gaps": ["..."]
  },
  "notion_url": "https://www.notion.so/..."
}
```

Known clients (slugs): `bain`, `myriad360`, `blockdaemon`, `zenbusiness`, `sfg`, `Proto`. Authors are not pre-registered — any `author_slug` is accepted; if no rows match, the API returns 404 with the list of valid Author option values in the relevant DB so you can correct the slug.

---

## Common workflows for AI agents

### Discover-then-query (do this every time)

When you don't already know a database's exact filter shape:

1. `GET /v1/notion/databases/{id}/schema` — learn the property types and valid option names.
2. Build a filter using only known-valid option names.
3. `POST /v1/notion/databases/{id}/query` — get the rows.
4. If you need each row's body, `GET /v1/notion/pages/{id}/body` per row.

### Count something deterministically

Don't loop over pages or use `notion-search`. Issue one `/query` with the appropriate filter and read `response.count`. If `truncated: true`, raise `max_results` or refine the filter and retry.

### Mutate a piece's status

```bash
# After a piece is published on LinkedIn:
curl -X PATCH https://api.getknownfor.com/v1/notion/pages/$PIECE_ID \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"properties": {"Status": {"status": {"name": "Published"}}}}'
```

Confirm by re-fetching:

```bash
curl -H "Authorization: Bearer $KF_API_KEY" \
  https://api.getknownfor.com/v1/notion/pages/$PIECE_ID | jq '.properties.Status'
```

---

## Failure modes

| Status | Meaning | Likely fix |
| --- | --- | --- |
| 200 | Success | — |
| 401 | Missing or wrong bearer token | Check `Authorization` header; rotate key if leaked |
| 404 | Unknown client_slug (scorecard), or no rows match the author filter | Verify slug against `clients.json` or call `/schema` to see valid author option names |
| 422 | Malformed request body (e.g. `scrape_paste` unparseable, missing required fields, date range inverted) | Read the `detail` string; fix the request |
| 502 | Upstream Notion API call failed | Inspect `notion_request_id` and `notion_status`; transient 5xx from Notion can be retried after a few seconds |
| 503 | Server is missing required env vars (e.g. `NOTION_API_TOKEN`) | Ping the operator; not a client-fixable error |
| 500 | Unexpected — check Render logs | Ping the operator with `request_id` |

---

## Stability and versioning

- The `/v1/*` prefix is the version namespace. Endpoints under `/v1/` will not change in a breaking way without a new version prefix.
- The OpenAPI spec at `/openapi.json` is the source of truth; consult it if anything here looks stale.
- The repo is at [`Known-For/api`](https://github.com/Known-For/api); this doc lives at the repo root as `API.md` and is updated whenever endpoints change.

## What this API does NOT do

- It does not replace Cowork's official Notion connector for casual single-page lookups, free-form exploration, or rich-text editing UX. Use the official connector for those.
- It does not host a generic LLM. It runs deterministic Python (parsers, filters, matchers) only.
- It does not have per-user keys yet — there is one `KF_API_KEY` shared across all callers. Per-client scoped keys are a future feature.
