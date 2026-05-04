# author-scorecard

V1 build of the Author Scorecard Generator. Operator-facing only.

## What's here

```
SKILL.md            instructions Claude follows when invoked
clients.json        per-client Notion DB IDs + schema hints
lib/parse_scrape.py scrape file → posts JSON
lib/match_pieces.py pieces JSON + posts → match JSON (auto-grep matcher)
lib/aggregate.py    full inputs → scorecard JSON with diagnosis + gaps
```

## Status (2026-04-28)

**Built and end-to-end tested on Chuck Whitten.** A live smoke-test scorecard is in KFOS Resources: ["Chuck Whitten Scorecard — 2026-03-29 to 2026-04-28 (smoke test)"](https://www.notion.so/35042d4495b281628c48cb0971897304).

What works:
- Filesystem author resolution from `clients/<client>/authors/<slug>/`.
- Scrape parsing (tested on Chuck, Konstantin, Ross — three different formats).
- Phrase matcher: 98% precision, ~70% recall in worst-case test (synthetic first-sentence rewrite). Topic-vocab false positives ("Agentic AI") filtered.
- Aggregator: stage 1–4 metrics, diagnosis thresholds per the brief, honest data gaps including scrape-staleness and small-sample callouts.
- Notion write to KFOS Resources with `Type: Scorecard` (option added one-time).

What's blocked:
- The official Notion MCP (`mcp__notion__*`) returned 401 unauthorized, which means filtered enumeration of `[Client] Resources` (Signal Files) and `[Client] Content` (deliverables) can't be done programmatically. The smoke test pulled 4 Chuck pieces by hand-fetched IDs to validate the pipeline.
- **Action for Adam**: re-auth the Notion connector (or use the `ca21` connector if it gains a query-data-source endpoint). Once that's in place, the SKILL workflow runs end-to-end against any active author.

## Notable design decisions

- **Per-client config over auto-detection.** Each client's Content DB has different field names (Bain: `Delivery`/`Name`; Myriad360: `DRAFT Date`/`Title`). The skill resolves field names at runtime from a candidate list rather than baking client-specific code paths.
- **Author convention varies by DB.** Bain Resources Author = slug (`chuck-whitten`); Bain Content Author = display name (`Chuck Whitten`). The skill converts between them.
- **No interactive match confirmation.** The `audit-author-edits` skill halts at Gate 1 to confirm each pair. The scorecard runs automatically — false positives surface as inflated stalled counts (which the operator can eyeball from the printed list), false negatives surface as inflated stalled counts likewise. Trade-off accepted for v1.
- **Bain Resources has no date property** — we use Notion's `createdTime` as session date. Flagged as a data gap when used.

## Where this lives

This folder is a self-contained skill in your iCloud workspace. To install as a Claude plugin skill:

1. Copy `skills/author-scorecard/` into the plugin location next to `audit-author-edits/`.
2. The SKILL.md frontmatter uses the standard skill format and will register automatically.

For now, Claude can invoke it from this location by reading `SKILL.md` directly.

## Out of scope for v1 (deferred to v2)

- Scheduled execution (end-of-quarter, all active authors).
- Cross-author trend reports.
- Reschedule / cancel tracking.
- Revision / approval activity (Notion engagement is unreliable; would need a different signal).
- Threshold-triggered alerts.
- Stage 5 anecdotal capture.
