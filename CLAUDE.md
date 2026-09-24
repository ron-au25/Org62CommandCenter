# Org62 Command Center — project instructions

## What this is
2-tab web app (Flask) over Org62 (Salesforce internal prod).
Data owner: Ron Shpilman — SE, Field Service, ANZ region.

## Org62 connection (the durable path)
- Alias: **org62**. Always pass `--target-org org62` explicitly (no reliance on default org).
- Auth = SF CLI browser SSO, stored encrypted in `~/.sfdx/`. NOT the `sid` access-token hack, NOT JWT.
- Login: `sf org login web --alias org62`
- If `OAUTH_APP_ACCESS_DENIED` or `OAUTH_AUTHORIZATION_BLOCKED` (cross-org): assign perm set
  **Salesforce CLI (OOTB) Connected App Access** (API `Salesforce_CLI_OOTB_Connected_App_Access`,
  Id `0PSed000000jM2nGAE`) to the Org62 user, then re-login.
- Verify: `sf org display --target-org org62` — org ID must start `00D000000000062`.
- App has no Bearer token (Org62 redacts `accessToken` in `sf org display`). `salesforce.py`
  shells `sf api request rest` per query — CLI supplies its own internal session. No code change to go live.

## Data model facts (verified against Org62 — do NOT re-derive or assume)
- My-deal tagging: **OpportunityTeamMember**, `TeamMemberRole = 'Solutions Engineer'`. NOT Deal_Contribution.
- SE specialist crediting: **Deal_Contribution__c**, picklist `Opportunity_Role__c` — two exact literal
  values: `'Service Cloud FSL Specialist'` and `'Service Cloud SE'`. OpportunityTeamMember has no such role.
- ANZ = `Account.BillingCountry IN ('AU','NZ','Australia','New Zealand')`. Opp region fields unreliable.
- "Field Service product" = OpportunityLineItem with `Product2.Name LIKE '%Field Service%'`.
- "Service Cloud product" = OpportunityLineItem with `Product2.Name LIKE '%Service Cloud%'` OR
  `LIKE '%Agentforce for Service%'` (18 catalog SKUs, all `...Add-on - <Edition>` variants — no bare SKU).
- Open pipeline stages of interest: `02%`, `03%`, `04%` (`StageName LIKE`). Exclude closed/dead.
- Fiscal year: Feb 1 – Jan 31. Current = FY27 (ends 2027-01-31). Use `CloseDate = THIS_FISCAL_YEAR`.
- SOQL: no field aliasing (only aggregates allow it). OpportunityTeamMember is huge/unindexed — never LIKE-scan it.
- Semi-join subqueries on OpportunityTeamMember must `SELECT OpportunityId` (the direct FK), not
  `Opportunity.Id` — a relationship-traversal SELECT in that position throws `MALFORMED_QUERY`
  ("cannot have more than one level of relationships").
- `OpportunityHistory` is fully queryable, no restrictions. Has `Amount`/`PrevAmount` but **no
  `PrevStageName`** — stage-change direction must be derived by diffing sequential rows per opp in code.
- `OpportunityLineItem.Quantity` / `TotalPrice` / `Product2Id` confirmed present (see `opportunity_products()`).
- Service AE: **`sfbase__OpportunityTeam__c`** (label "Opportunity Team", managed-package object, `aAU` id
  prefix — NOT `OpportunityTeamMember`). Field `TeamRoleLookup__c` (label "Selling Role") is a lookup to
  `Team_Role__c`, not a picklist — filter on `TeamRoleLookup__r.Name = 'Service Cloud AE'`. Opp link =
  `sfbase__Opportunity__c`; user = `sfbase__User__c`/`sfbase__User__r.Name`. Verified against record
  `aAUed00000jdvd2` (opp `006ed00000kBbt0AAC`, AE Clinton Alver).
- Core SE: **`Deal_Contribution__c`**, `Opportunity_Role__c = 'Core SE'` (exact literal, distinct from e.g.
  `'RCG Kahuna Core SE'`; 2.13M rows org-wide — a common real role). Opp link = `Opportunity__c`; SE =
  `SE_Name__c`/`SE_Name__r.Name` (same field `coverage_gaps()` already uses for contributor identity).
  `salesforce.py:core_ses_for_opps(opp_ids)` is generic (not scoped to "my" deals) — used both for the Open
  Pipe "Core SE" column (my_deals_fy rows) and the whitespace "aligned" match (anz_fsl_whitespace rows).

## Role-mode toggle (Settings gear, top right)
`ROLE_MODES` in `salesforce.py` — `"fsl"` (default) vs `"service_cloud"`, each mapping to a
SKU LIKE-clause + a `Deal_Contribution__c.Opportunity_Role__c` literal. Threaded via `mode=` into
`anz_fsl_whitespace()` and `coverage_gaps()` (the latter only uses the SKU clause — its contributor
check is an identity match on `SE_Name__c`, not a role check). Server reads `?role_mode=` on
`/api/whitespace` and `/api/activity`, clamped to the two valid values.

## Settings persistence (file-backed, not localStorage)
`GET/POST /api/settings` reads/writes `web/settings_local.json` (gitignored — machine-local prefs,
not shared data): `{roleMode, showMovements, targets:{relatedHrs, facingHrs}, alignedSEs}`. Frontend
`boot()` awaits `GET /api/settings` before the first `fetchAll()`, and every setting change (role
toggle, movements checkbox, target inputs, aligned-SE list) fires an async `POST` to persist
immediately. Survives an app restart or a different browser, unlike the old localStorage-only
approach. `alignedSEs` is a raw comma-separated string (not an array) — matching is exact-name,
case-insensitive, done client-side in `alignedList()`/`isAligned()`.

## Open Pipe: Core SE / Service AE columns + drawer
`viewOpen()`'s table and `openDetail()`'s drawer both show **Core SE** (`row.coreSE`) and **Service
AE** (`row.serviceAE`) alongside the existing Core AE (`row.owner` = `Opportunity.Owner.Name`).
Populated in `/api/forecast` by two id-scoped enrichment queries against `my_deals_fy()`'s opp ids —
`org.my_service_aes()` (Service AE) and `org.core_ses_for_opps()` (Core SE), both wrapped in a
try/except so a query failure degrades to blank columns rather than failing the whole tab. Stage
column is shortened to its leading numeric prefix via `stageShort()` (e.g. `"02 - Determining..."` →
`"02"`); non-numeric Dead stages render unchanged (no leading digits to strip).

## Whitespace alignment (New Business / ANZ White Space tab)
Settings holds a comma-separated `alignedSEs` list of **Service AE** names (not Core SE — confirmed
by Ron; his alignment is to specific Service Cloud AEs, e.g. "David Hull, Clinton Alver, Aloysious
Ryan, Nergis Kandemir, Brett McKenzie"). `/api/whitespace` enriches each row with both `coreSE` and
`serviceAE` (same two queries as Open Pipe, scoped to the whitespace opp ids instead); the frontend's
`isAligned(r)` matches `r.serviceAE` (case-insensitive, trimmed) against the parsed list —
`alignedList()` splits/trims/lowercases `state.alignedSEs`. An exact-name mismatch (e.g. a nickname
or typo) silently drops that person from matches — no error, since the query still runs fine — so
treat a suspiciously-low or zero aligned-match count as a name-typo signal, not "no data," same
lesson as the earlier Service-AE object-mismatch bug.
Surfaced three ways: a "N Aligned ($X)" second meta line on the "New Business (ANZ)" KPI card
(`renderKPIs()`), a "Show only aligned (N)" filter toggle on the whitespace tab (`state.showAlignedOnly`,
not persisted — same pattern as Closed Pipe's "Show dead" toggle), and a left-border highlight
(`tr.aligned`) on matching rows even when the filter is off.

## Quarterly activity + targets (Activity Log tab)
`Event.SE_Task_Type__c` (verified exact-literal picklist, all 34 org values) drives classification
via `server.ACTIVITY_TYPE_MAP`: each value maps to independent `(related, facing)` booleans, taken
from Ron's official Activity Type definitions doc (not derived from `What`/`Type` — those aren't
part of the real definition). **facing is always a subset of related** in the final map (no
facing=True/related=False rows) — two disputes between that doc's prose and its Yes/No columns were
resolved in Ron's favor toward the columns: `BVS - Value Hypothesis` = related-only (not facing);
`BVS - Business Case` = both (not neither). Two picklist values aren't in that doc: `Not Available`
defaults to neither (unclassified placeholder); `Consumption Event` was confirmed by Ron to match
the other Consumption types (both).

`_activity_bucket()` collapses those two booleans into one mutually-exclusive display bucket for
the chart/cards — `facing` > `related` (non-facing) > `noncustomer` — since facing time is always
also related time. `FQ_BY_MONTH` maps calendar month to fiscal quarter 1-4 (FY = Feb 1 - Jan 31).
`flatten_activity()` returns `currentQuarter`, `currentQuarterMinutes/Events`, `avgQuarterlyMinutes`,
`currentQuarterVsAvgPct`, `byQuarter` (one entry per quarter, `facing/related/noncustomer` bucket
minutes), and `currentQuarterProgress` (`elapsedDays`/`totalDays`/`pct` — how far through the
current fiscal quarter today is).

Ron's quarterly targets (default 250h Customer Related / 125h Customer Facing, editable in
Settings, persisted per above) are compared in `viewActivity()`'s "Quarterly targets" card. Customer
Related target = the full related superset (`byQuarter.facing + byQuarter.related`, since facing is
included in related); Customer Facing target = `byQuarter.facing` alone. Each gets a progress bar
with a pro-rata marker (`currentQuarterProgress.pct`) showing where hours *should* be if pacing
evenly through the quarter, plus the previous quarter's same metric for comparison.

## Whitespace definition
ANZ opps, open, stage 02/03/04, has in-scope product (FS or Service Cloud, per role mode),
`CloseDate >= TODAY AND = THIS_FISCAL_YEAR`, and NOT IN Deal_Contribution__c where role matches
the mode. Dead opps excluded.

## Pipeline movements bar (Settings gear → show/hide)
`/api/movements` → `salesforce.my_pipeline_movements()` (raw OpportunityHistory + recent
SE_Comment_Update_Date__c rows for my open opps) → `server.flatten_movements()` groups everything
by opportunity (one card per opp, not per history row) into a net amount move + latest stage move +
comment flag, last 30 days, capped at 40. No snapshot fallback exists yet — a live-query failure
just returns an empty ticker, not a crash.

## Per-opp product line items (detail drawer)
Live rows never bulk-carry `products[]` (would mean an OpportunityLineItem query per row on every
tab load — too expensive). Fetched lazily instead: `/api/opportunity/<id>/products` →
`org.opportunity_products()`, called only when the drawer opens for that opp.

## Run
- One-shot: `./run.sh` (creates venv, triggers CLI login if needed, starts server). See `INSTALL.md`.
- Manual: `PORT=5057 ./.venv/bin/python web/server.py` (port 5000 = macOS AirPlay, returns 403 — avoid).
- Live query fails gracefully to bundled snapshot JSON; badge shows `snapshot` vs `live`.

## House rules
- No assumptions. Verify against Org62 (schema/values) before changing a query.
- MCP auth via `/mcp`, not Bash flows.
