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
| Resolve a client's Notion DB IDs without hardcoding them | **This API** — `GET  /v1/clients/{slug}` |
| Run the author scorecard workflow end-to-end | **This API** — `POST /v1/scorecards/{client}/{author}` |
| Read a page's block tree with stable block IDs | **This API** — `GET  /v1/notion/blocks/{id}/children` |
| Surgically edit a page (`old_str` → `new_str`) or append blocks | **This API** — `POST /v1/notion/pages/{id}/update_content` |
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

`PATCH /pages/{id}` updates **page-level properties** (Status, Author, etc.).
To edit the **content inside** a page — paragraphs, headings — use the block
endpoints or `update_content` below.

---

### `GET /v1/notion/blocks/{block_id}/children`

List a page's (or block's) child blocks with stable IDs and concatenated
plain text. Pages are blocks in Notion's data model, so `block_id` can be a
page ID. This is how you find *which block* contains the text you want to
edit before calling `update_content` or `PATCH /blocks/{id}`.

Query params: `recursive` (bool, default `false`), `page_size` (1–100,
default 100), `cursor` (opaque pagination token).

```bash
curl -H "Authorization: Bearer $KF_API_KEY" \
  "https://api.getknownfor.com/v1/notion/blocks/$PAGE_ID/children?recursive=true"
```

Response:

```json
{
  "results": [
    {
      "id": "block-uuid",
      "type": "paragraph",
      "has_children": false,
      "text": "concatenated plain text of the block's rich_text",
      "raw": { "...": "the full Notion block object" }
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

`recursive=false` returns one Notion page of direct children (honor
`next_cursor` / `has_more` to paginate). `recursive=true` walks the tree to
depth 2 and returns every descendant flattened, with no pagination. `text`
is `""` for block types that carry no rich text (images, dividers, etc.).

### `POST /v1/notion/blocks/{block_id}/children`

Append child blocks to a block or page. `children` (and optional `after`
anchor block ID) pass through verbatim to Notion.

```bash
curl -X POST https://api.getknownfor.com/v1/notion/blocks/$PAGE_ID/children \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "children": [
      { "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type":"text","text":{"content":"Appended."}}]} }
    ]
  }'
# → {"results": [<new block>, ...], "count": 1}
```

### `PATCH /v1/notion/blocks/{block_id}`

Update a single block's content. The body is the type-keyed content dict,
passed through verbatim to Notion.

```bash
curl -X PATCH https://api.getknownfor.com/v1/notion/blocks/$BLOCK_ID \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"paragraph": {"rich_text": [{"type":"text","text":{"content":"new text"}}]}}'
# → {"id": "...", "type": "paragraph", "text": "new text", "raw": {...}}
```

### `POST /v1/notion/pages/{page_id}/update_content`

High-level surgical edit. Give it a list of `operations` and it does the
list-blocks → match → patch → verify sequence server-side, so callers don't
have to orchestrate it.

Each operation is **either** a replace or an append:

```json
{
  "operations": [
    { "old_str": "exact text to find in one block", "new_str": "what it becomes" },
    { "append": { "children": [ <block>, ... ], "after_block_id": "optional-anchor" } }
  ],
  "dry_run": false
}
```

Server-side behavior:

1. Snapshots the page's block tree (depth 2).
2. **Validation pass:** every `old_str` op must match **exactly one** block.
   0 matches → `no_match`; >1 → `ambiguous_match`. Either fails the whole
   request **422 before anything is written**.
3. `dry_run: true` → returns the resolved plan without writing.
4. Otherwise executes: `PATCH` each replace, append each append op. If a
   write fails mid-execution, returns **502 with `applied` listing what
   already landed** — there is **no rollback**.
5. Re-fetches the replaced blocks to confirm the new text is present
   (`verified: true`).

```bash
curl -X POST https://api.getknownfor.com/v1/notion/pages/$PAGE_ID/update_content \
  -H "Authorization: Bearer $KF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "operations": [
      {"old_str": "the quick brown fox", "new_str": "the quick red fox"}
    ],
    "dry_run": true
  }'
```

Response:

```json
{
  "page_id": "...",
  "applied": [
    {"op_index": 0, "kind": "replace", "block_id": "...",
     "before_text": "the quick brown fox", "after_text": "the quick red fox"}
  ],
  "verified": false,
  "dry_run": true
}
```

**⚠️ v1 limitation — annotation loss.** A replace swaps the matched block's
*entire* `rich_text` array for a single plain-text run. Any inline bold,
italic, or link **inside that block** is lost. This is acceptable for Voice
Brief / Annotated Exemplars pages (near-plain paragraphs) but know it before
editing a heavily-formatted block. The replace is whole-block, not a true
inline splice.

**Recommended pattern:** always call with `dry_run: true` first, inspect the
`before_text` / `after_text`, then repeat with `dry_run: false`.

---

### `GET /v1/clients/{slug}`

Resolve a client's Notion DB IDs from server-side `clients.json` — one
source of truth, so callers never hardcode database IDs.

```bash
curl -H "Authorization: Bearer $KF_API_KEY" \
  https://api.getknownfor.com/v1/clients/bain
```

Response:

```json
{
  "slug": "bain",
  "display_name": "Bain & Co",
  "content_db_id": "24042d44-95b2-8034-83bb-d817d453311c",
  "resources_db_id": "2dc42d44-95b2-8058-b7ca-f37cbacae269",
  "author_property_verified": true
}
```

`author_property_verified` is `true` when the client's Notion schema has
been confirmed by direct fetch (vs. relying on runtime auto-detection).
Unknown slug → 404.

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

### Surgically edit text inside a Notion page

To change text *within* a page's body (not its properties):

1. `GET /v1/clients/{slug}` — resolve the target DB IDs if you don't have them.
2. `POST /v1/notion/databases/{resources_db_id}/query` — find the exact page
   (e.g. filter Type = Voice Brief, Author = the author).
3. `POST /v1/notion/pages/{page_id}/update_content` with `dry_run: true` and
   your `old_str` → `new_str` operations. Inspect the `before_text` /
   `after_text` in the response.
4. If the plan looks right, repeat with `dry_run: false`.
5. The response's `verified: true` confirms the new text re-fetched cleanly;
   you can also `GET /v1/notion/pages/{id}/body` to double-check.

If step 3/4 returns 422 `no_match` or `ambiguous_match`, **do not retry with
a looser match heuristic** — surface the failure. The `old_str` must be
specific enough to hit exactly one block.

---

## Failure modes

| Status | Meaning | Likely fix |
| --- | --- | --- |
| 200 | Success | — |
| 401 | Missing or wrong bearer token | Check `Authorization` header; rotate key if leaked |
| 404 | Unknown client_slug, unknown block/page ID, or no rows match the author filter | Verify slug via `/v1/clients/{slug}`, or call `/schema` to see valid option names |
| 422 | Malformed request body, OR an `update_content` op failed validation (`no_match`, `ambiguous_match`, `unsupported_block_type`, `malformed_op` — see the `error` field) | Read the structured `detail`; for match failures, make `old_str` more specific |
| 502 | Upstream Notion API call failed. For `update_content`, the `detail.applied` array lists writes that landed before the failure (no rollback) | Inspect `notion_request_id` and `notion_status`; transient 5xx from Notion can be retried after a few seconds |
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
