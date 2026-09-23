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
- SE specialist crediting: **Deal_Contribution__c**, picklist `Opportunity_Role__c`
  (value `'Service Cloud FSL Specialist'`). OpportunityTeamMember has no such role.
- ANZ = `Account.BillingCountry IN ('AU','NZ','Australia','New Zealand')`. Opp region fields unreliable.
- "Field Service product" = OpportunityLineItem with `Product2.Name LIKE '%Field Service%'`.
- Open pipeline stages of interest: `02%`, `03%`, `04%` (`StageName LIKE`). Exclude closed/dead.
- Fiscal year: Feb 1 – Jan 31. Current = FY27 (ends 2027-01-31). Use `CloseDate = THIS_FISCAL_YEAR`.
- SOQL: no field aliasing (only aggregates allow it). OpportunityTeamMember is huge/unindexed — never LIKE-scan it.

## Whitespace definition
ANZ opps, open, stage 02/03/04, has FS product, `CloseDate >= TODAY AND = THIS_FISCAL_YEAR`,
and NOT IN Deal_Contribution__c where role = 'Service Cloud FSL Specialist'. Dead opps excluded.

## Run
- Web: `PORT=5057 ./.venv/bin/python web/server.py` (port 5000 = macOS AirPlay, returns 403 — avoid).
- Live query fails gracefully to bundled snapshot JSON; badge shows `snapshot` vs `live`.

## House rules
- No assumptions. Verify against Org62 (schema/values) before changing a query.
- MCP auth via `/mcp`, not Bash flows.
