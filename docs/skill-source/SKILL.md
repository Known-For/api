---
name: author-scorecard
description: Generate a per-author engagement health scorecard from a LinkedIn paste or an existing scrape file. Computes session cadence, deliverable volume, publishing rate, and platform performance for a date range, then writes a Notion page in the client Resources DB with Type Scorecard. Use when the operator asks to score an author, audit author health, run a scorecard, or check engagement drift. Triggers on direct prompts like score Chuck Whitten or scorecard for chuck-whitten, and also on raw LinkedIn Activity pastes that contain Feed post number markers. Operator-facing only.
---

# Author Scorecard

## What this skill does

Produces an engagement health scorecard for one author. Diagnosis is `Healthy / Watch / Trouble`. Used to spot drift before clients churn and ground renewal conversations.

The scorecard lands as a new page in the client's Resources DB with `Type: Scorecard`. Operator-facing only — never client-facing.

## Trigger modes

**Mode A — Paste mode (most common).** The operator pastes raw LinkedIn Activity content into chat and asks for a scorecard. Detect by: paste is >3000 characters AND contains multiple `Feed post number` markers OR multiple `View [Name] graphic link` patterns. Run Stage 0–9 of the workflow, where Stage 1 saves the paste and runs the parser before continuing.

**Mode B — Direct mode.** Operator types something like "scorecard for chuck-whitten" or "score Chuck Whitten" with no paste. Skip Stage 1's parser run; use the latest existing scrape file in the author's `training/` directory.

If both an author name and a paste are present, Mode A wins.

## Required inputs

- **Author name or slug.** If only a paste is given, run the paste's text through the doubled-name extraction logic (or just call `parse_linkedin.py` which does this) to detect the author.
- **Date range** (optional). Default trailing 30 days. Override with `start_date YYYY-MM-DD` and `end_date YYYY-MM-DD`.

## Hard constraints

**🛑 BLOCKING PRECONDITION — read this before anything else.**

This skill REQUIRES the local Notion bridge tool `notion_query_database` to be loaded in the session. It is the ONLY acceptable way to enumerate pieces and sessions from Notion DBs. **The official Notion connector's `notion-search` is BANNED for Stages 4–5** because it's semantic, top-25-capped, and silently misses pieces — that's the exact failure mode this skill exists to avoid.

Before doing ANY other work in this skill (before Stage 0, before reading any files, before parsing anything):

```
1. Call ToolSearch with query: "notion_query_database notion_retrieve_block_children notion_retrieve_page"
2. If those three tools appear in the results → load them, proceed to Stage 0.
3. If they DO NOT appear → the bridge is down or the custom connector isn't loaded. STOP. Tell the operator:
   "The Notion bridge tools (notion_query_database et al) aren't loaded in this session.
    Check `pm2 status` on your machine — is `notion-bridge` online? If not: pm2 restart notion-bridge.
    Also confirm the custom connector at <ngrok-url>/mcp shows in Cowork → Settings → Connectors.
    See tools/notion-bridge/README.md for full troubleshooting. Halting until this is fixed."
4. DO NOT FALL BACK TO `notion-search` UNDER ANY CIRCUMSTANCE. There is no degraded mode. Either the bridge is up and you proceed, or you halt. Falling back to semantic search produces silent data corruption.
```

**Other hard constraints:**

- **No fabricated metrics.** Missing data → `Data unavailable: [reason]`. Never estimate.
- **Stalled list shown inline, not just a count.** The matcher is approximate; the operator needs to eyeball the actual titles to sanity-check.
- **Scorecard lands in client Resources, not KFOS.** KFOS Resources is for development artifacts only.
- **Author resolution must be unambiguous.** Slugs can collide across clients (`david-crawford` is in both Bain and Myriad360). Always resolve client before continuing.
- **Notion writes must verify.** After creating the scorecard page, fetch it back and confirm the write.
- **Workspace folder must be mounted.** This skill needs read access to `/Users/adamrich/Library/Mobile Documents/com~apple~CloudDocs/known_for/clients/` to find scrape files. If the folder isn't mounted in the session, request it via `request_cowork_directory` BEFORE Stage 0 — don't try to do Stage 1 without it.

## Files in this skill

```
skills/author-scorecard/
├── SKILL.md                   (this file)
├── clients.json               per-client DB IDs
└── lib/
    ├── parse_linkedin.py      raw clipboard paste → structured posts file
    ├── parse_scrape.py        structured posts file → posts JSON
    ├── match_pieces.py        pieces JSON + posts → match JSON
    └── aggregate.py           all the above → scorecard JSON
```

All Python files are stdlib-only.

**Locating the lib directory at runtime.** The bundled scripts live in `lib/` next to this `SKILL.md`. To find them in either a deployed plugin or the iCloud working copy, use:

```bash
SKILL_DIR=$(find "$HOME" "/var/folders" -name "SKILL.md" -path "*author-scorecard*" -print -quit 2>/dev/null | xargs dirname)
```

Use `$SKILL_DIR/lib/<script>.py` for all subsequent invocations.

## Workflow

### Stage −1 — Preconditions (do this FIRST, no exceptions)

1. **Load bridge tools.** Call `ToolSearch` with `"select:notion_query_database,notion_retrieve_block_children,notion_retrieve_page,notion_retrieve_database"`. If any of those three (excluding retrieve_database which is a bonus) fail to load, halt per the BLOCKING PRECONDITION above.
2. **Smoke-test the bridge.** Call `notion_retrieve_bot_user` with random_string="precondition_check". Should return `{"object":"user","name":"KFOS Web App",...}`. If it returns 401 or fails, the bridge token is wrong or the bridge process is down. Halt.
3. **Confirm workspace mount.** Verify `clients/` directory is reachable via Read or Bash. If not, call `request_cowork_directory` to get it mounted.

Only after all three pass, proceed to Stage 0.

### Stage 0 — Resolve inputs

1. **Mode detect.** Look at the operator's most recent message. If it has a chunky raw-text paste with `Feed post number` markers or `View [...] graphic link` patterns, this is Mode A. Otherwise Mode B.
2. **Author resolution.**
   - Mode A: extract the author name from the paste using `parse_linkedin.py`'s `extract_author_name` function, or just run the parser and read the file it produces. Convert to slug.
   - Mode B: take the author argument from the message. If display name, lowercase + hyphenate.
3. **Client resolution.** Enumerate `clients/<client>/authors/<slug>/` paths via Bash. If zero hits, ask. If multiple (slug collision), ask which client. Cache the path.
4. **Date range.** Default `end_date = today`, `start_date = today - 30 days`.
5. **Load `clients.json`.** Pick up the client's Resources DB ID and Content DB ID for the write target and queries.
6. **Probe the Author select option for this client.** Before Stage 4, run a single `notion_query_database` against `content_db_id` with NO filter, `page_size: 1`, to see one real page's `properties.Author.select.name`. That tells you whether the client uses slug (`ross-buhrdorf`) or display (`Ross Buhrdorf`) — clients.json `_verified` notes are unreliable when set to `false`. Use the actual observed value as the filter for Stages 4–5.

### Stage 1 — Get the scrape file

**Mode A** — Save the paste to `clients/<client>/authors/<slug>/training/linkedin-scrape-raw-<today>.md`, then run:

```bash
python3 SKILL_DIR/lib/parse_linkedin.py <raw-path>
```

The parser produces `<slug>-posts-<today>.md` next to the raw file. That's the latest scrape going forward.

**Mode B** — List existing scrapes in the author's `training/` directory:
```bash
ls clients/<client>/authors/<slug>/training/<slug>-posts-*.md 2>/dev/null
```
Filter to filenames matching `<slug>-posts-(\d{4}-\d{2}-\d{2})\.md`. Latest = most recent date. Baseline = earliest. If zero scrapes exist, halt with a clear message.

### Stage 2 — Staleness check (Mode B only)

If `end_date > latest_scrape_date`, ask the operator (use `AskUserQuestion`):

> Latest scrape for [Author] is dated [date]. Date range ends [date]. Run a fresh scrape before generating?

If they decline, proceed but flag in data gaps that the scrape pre-dates the range end. (In Mode A this never triggers because the scrape was just generated.)

### Stage 3 — Parse scrape to JSON

```bash
python3 SKILL_DIR/lib/parse_scrape.py <latest-scrape-path> > /tmp/scrape.json
python3 SKILL_DIR/lib/parse_scrape.py <baseline-scrape-path> > /tmp/baseline.json
```

If only one scrape exists (no baseline yet — first time for this author), pass the same path twice. Aggregator handles this and disables Stage 4 baseline comparison.

### Stage 4 — Pull sessions from client Resources DB

Use `notion_query_database` (from the local Notion bridge — see `tools/notion-bridge/README.md`) with the client's `resources_db_id` from `clients.json`. Filter by `Type = Signal File` AND `Author = <slug>`:

```json
{
  "database_id": "<resources_db_id from clients.json>",
  "filter": {
    "and": [
      {"property": "Type",   "select": {"equals": "Signal File"}},
      {"property": "Author", "select": {"equals": "<slug>"}}
    ]
  },
  "page_size": 100
}
```

If `has_more: true`, paginate via `start_cursor`. Each result has its full property set inline — no per-page fetch needed for sessions. Read `Session Date` (date property) for each. Build:

```json
[{"id": "...", "title": "...", "session_date": "YYYY-MM-DD"}, ...]
```

If `Session Date` is empty across pages, fall back to `created_time` (also in the response) and add a gap: "Stage 1: Session date approximated from createdTime."

**Schema notes.** The Bain Resources `Author` property uses lowercase-hyphenated slugs (e.g., `chuck-whitten`). Verify the slug matches the actual select-option value by checking one page's `properties.Author.select.name`. If the result count is 0 with a slug filter, try the display-name form (`Chuck Whitten`) — some clients use one convention, some the other. `clients.json` `_verified` notes are authoritative when present.

### Stage 5 — Pull deliverables from client Content DB

Same primitive — `notion_query_database` against the client's `content_db_id`, filtered by `Author = <slug>`. **Do NOT add a Delivery date filter at this stage** — the matcher needs visibility into pieces delivered before the window but published in window (false negatives if scoped too tight). Let the aggregator filter by date.

```json
{
  "database_id": "<content_db_id from clients.json>",
  "filter": {"property": "Author", "select": {"equals": "<slug>"}},
  "sorts": [{"property": "Delivery", "direction": "descending"}],
  "page_size": 100
}
```

Paginate to exhaustion (`has_more: false`).

Properties returned inline include: `Name` (title), `Delivery` (date), `Status` (status), `Notes` (rich text), `Author` (select), `Type` (select). For each piece, also pull the page body via `notion_retrieve_block_children` — that's the text the matcher greps against scrape posts.

Pieces with `Status = Killed` or `Status = Archived`: exclude from matching, but keep in the count for Stage 2 commentary if delivered in window.

Pieces with no body or fewer than 30 words after concatenation: mark `body_too_short`, exclude from matching, note in data gaps.

Build `pieces.json`:
```json
[{"id": "...", "title": "...", "delivered_date": "YYYY-MM-DD", "status": "...", "body": "..."}, ...]
```

**No coverage caveat needed in Stage 3 data gaps anymore** — `notion_query_database` returns the complete set, deterministic and paginated. If you find yourself reaching for `notion-search`, stop — that's the wrong tool for this stage.

### Stage 6 — Match

```bash
python3 SKILL_DIR/lib/match_pieces.py \
  --pieces-json /tmp/pieces.json \
  --scrape-json /tmp/scrape.json \
  > /tmp/match.json
```

Matcher auto-extracts distinctive phrases from each piece (entities, numbers, quoted text, distinctive ngrams) and greps them against scrape posts. Confidence is `high / medium / low`.

**No interactive confirmation.** That's the difference from `audit-author-edits`. Trade-off: false negatives surface as inflated stalled counts, which the operator eyeballs from the printed list.

### Stage 7 — Aggregate

```bash
python3 SKILL_DIR/lib/aggregate.py \
  --scrape-json   /tmp/scrape.json \
  --baseline-json /tmp/baseline.json \
  --match-json    /tmp/match.json \
  --sessions-json /tmp/sessions.json \
  --pieces-json   /tmp/pieces.json \
  --start-date    YYYY-MM-DD \
  --end-date      YYYY-MM-DD \
  --author-name   "Chuck Whitten" \
  --client-name   "Bain & Co" \
  > /tmp/scorecard.json
```

Produces structured JSON: header (diagnosis, top issue, leveraged action), stage1–stage4 metrics, match diagnostics, data gaps.

### Stage 8 — Write to client Resources

1. Verify the client's Resources DB has a `Scorecard` option on its `Type` select. (Should be true after the 2026-04-28 schema migration.) If not, halt and tell the operator to add the option in Notion before continuing.
2. Create a new page via `notion-create-pages`:
   - Parent: `<client>` Resources DB ID from `clients.json`
   - Properties:
     - `Name` = `<Author> Scorecard — <start_date> to <end_date>`
     - `Type` = `Scorecard`
   - Body: Markdown built from the scorecard JSON, sections in this order:

```
**Author**: <Author>
**Client**: <Client>
**Date range**: <start> to <end>
**Generated**: <today>
**Diagnosis**: **<Healthy | Watch | Trouble>**
**Top issue**: <one line>
**Most leveraged action**: <one line>

---

## Stage 1 — Session quality
- Sessions held in range: <N>
- Last session: <YYYY-MM-DD> (<N> days ago)
- Diagnosis: <label>

<one-paragraph commentary tying the numbers to expected cadence (Standard = bi-weekly, Accelerated = weekly)>

## Stage 2 — Deliverable volume
- Pieces delivered in range: <N>

Titles in range:
- <title 1>
- <title 2>

## Stage 3 — Publishing rate
- Of pieces delivered in range: <X> of <Y> published (<%>)
- Avg days from delivery to publish (matched pairs, all time): <N>
- Stalled pieces (delivered >30 days ago, unmatched): <N>

**Stalled list:**
- <Title> — delivered <YYYY-MM-DD> (<N> days ago)

<commentary>

## Stage 4 — Platform performance
- KF-matched posts in range: <N>
- Avg reactions: <X> (baseline: <Y>, delta: <±%>)
- Avg comments: <X> (baseline: <Y>, delta: <±%>)
- Avg impressions: <Data unavailable: LinkedIn does not expose impressions in scrapes>

<commentary>

## Recommendations
- <action 1>
- <action 2>

## Data gaps
- <each gap>
- Match confidence: <high>/<medium>/<low>/<unmatched> out of <total> pieces
```

3. Fetch the created page back via `notion-fetch` to confirm the write landed.

### Stage 9 — Report to operator

One-line summary in chat:
> `<Author> scorecard: <Diagnosis>. <top issue>.` + Notion URL.

If gaps exist, mention briefly. Don't repeat the full scorecard body in chat.

## Notion tools

This skill uses TWO Notion connectors in parallel:

**1. Local bridge connector** (the `@suekou/mcp-notion-server` running via `tools/notion-bridge/`). This is the canonical source for enumeration. Tools live under whatever namespace Cowork assigned the custom connector — use `ToolSearch` with the bare tool name to find them. Required tools:
- `notion_query_database` — Stages 4 and 5 use this for filtered, paginated enumeration
- `notion_retrieve_block_children` — Stage 5 uses this to get piece bodies for matching
- `notion_retrieve_page` — fallback if a property is missing from a query response

If these tools don't appear in the session, the bridge isn't running. Check `pm2 status` on Adam's machine, or read `tools/notion-bridge/README.md`. Halt the skill — don't fall back to semantic search.

**2. Official Notion Connector** (`mcp__ca21e891-...__notion-*`, the one with the human-readable slug). Used for two narrow purposes only:
- `notion-create-pages` — Stage 8 writes the scorecard page (the bridge's `notion_create_page` works too, but the official one has friendlier markdown handling)
- `notion-update-page` — for in-place corrections after scorecard ships

Do NOT use `notion-search` from the official connector for Stages 4–5. It's semantic and capped — that's the failure mode the bridge fixes.

## Failure modes to avoid

- Picking the wrong author on slug collision (`david-crawford` in both Bain and Myriad360). Always resolve client at Stage 0.
- Falling back to `notion-search` for Stages 4–5 because the bridge tools "feel slow" or aren't immediately found. The bridge is the source of truth; semantic search misses pieces silently. If the bridge isn't loaded, halt and tell the operator.
- Running with no baseline (single scrape) and silently emitting Stage 4 deltas — the aggregator handles this; verify the "baseline unavailable" gap fired.
- Falling back to chat output if the Notion write fails. Halt and report — the operator wants the artifact, not a transcript.
- Confusing database IDs vs data source IDs. `notion_query_database` (suekou's tool, used in Stages 4–5) takes the **database ID** — the `*_db_id` field in clients.json. The data source ID (sometimes also in clients.json under separate fields) is for the newer `query_data_source` API endpoint, not what suekou exposes.
- For Mode A: writing the raw paste somewhere wrong (e.g., outputs/) instead of the author's `training/` directory. The path matters because the parser writes its output next to the raw, and the scorecard reads from the same place.
- Filtering pieces by Delivery date in Stage 5. Don't — the matcher needs the full piece corpus (or at least last 90 days) to handle the "delivered before window, published in window" case. Let the aggregator do the date filtering.

## What this skill does NOT do

- Does not run LinkedIn scrapes itself — the operator pastes the raw content; the parser turns it into structured form.
- Does not modify schema (Scorecard Type option must already exist on the target Resources DB).
- Does not loop across authors. One per invocation.
- Does not generate client-facing content.
- Does not track revisions, approvals, reschedules, or cancels — those signals aren't reliably available in Notion.
