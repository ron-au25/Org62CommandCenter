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

## Role-mode toggle (Settings gear, top right)
`ROLE_MODES` in `salesforce.py` — `"fsl"` (default) vs `"service_cloud"`, each mapping to a
SKU LIKE-clause + a `Deal_Contribution__c.Opportunity_Role__c` literal. Threaded via `mode=` into
`anz_fsl_whitespace()` and `coverage_gaps()` (the latter only uses the SKU clause — its contributor
check is an identity match on `SE_Name__c`, not a role check). Server reads `?role_mode=` on
`/api/whitespace` and `/api/activity`, clamped to the two valid values. Persisted client-side in
`localStorage` (`cc_settings`), not on the server.

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
