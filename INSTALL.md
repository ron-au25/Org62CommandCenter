# Org62 Command Center — installation guide

A local, read-only dashboard over your own Org62 opportunities, pipeline,
white-space and activity. Runs entirely on your machine, binds to loopback
(`127.0.0.1`) only, and issues **SELECT-only** SOQL — no write-back.

Each user connects with their **own** Org62 login and sees **their own** data.
No pipeline data ships in this repo (the `web/snapshot_*.json` files are
git-ignored as confidential), so a working live connection is required.

---

## 1. Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Python | 3.10+ | `python3 --version` |
| Git | any | `git --version` |
| Salesforce CLI (`sf`) | latest | `sf --version` |
| Org62 access | active user | you can log in at `https://org62.my.salesforce.com` |

Install the Salesforce CLI: <https://developer.salesforce.com/tools/salesforcecli>
(`brew install --cask sf` on macOS, or the platform installer).

---

## 2. Clone and run

```bash
git clone https://github.com/ron-au25/FieldServiceCommandCenter.git
cd FieldServiceCommandCenter
./run.sh
```

`run.sh` does everything: creates the venv and installs dependencies (first
run only), opens the browser to log in to Org62 if there's no CLI session yet
for the `org62` alias, then starts the server. Re-run it any time — it's a
no-op on setup once the venv and login exist, and just starts the server.

Open <http://127.0.0.1:5057>.

Leave the process running while you use the dashboard. `Ctrl-C` to stop.

Environment overrides (optional, both have working defaults):

- `ORG62_ALIAS` — CLI alias for your Org62 auth (default `org62`).
- `PORT` — web port (default `5057`; macOS AirPlay owns 5000, so 5057 is used
  instead).

```bash
PORT=5099 ./run.sh          # e.g. run on a different port
```

> If you copied this folder instead of cloning it fresh (e.g. moved it between
> drives) and it already had a `.venv`, `run.sh` detects a broken interpreter
> and rebuilds the venv automatically. No action needed.

---

## 3. Connect Org62 (once per machine — run.sh triggers this automatically)

`run.sh` calls `sf org login web --alias org62` for you the first time there's
no session. If you'd rather do it manually, or need to re-auth after a session
expires:

```bash
sf org login web --alias org62
```

A browser opens — log in to Org62 with your normal SSO. On success the CLI
stores an encrypted token in `~/.sfdx/`.

Verify:

```bash
sf org display --target-org org62
```

The **Org Id** must start with `00D000000000062` (that's Org62). You should
also see an `Access Token` and `Instance Url`.

### If login is blocked

`OAUTH_APP_ACCESS_DENIED` / `OAUTH_AUTHORIZATION_BLOCKED` means the Salesforce
CLI connected app is restricted for your user. Ask an Org62 admin to assign the
permission set:

> **Salesforce CLI (OOTB) Connected App Access**
> (API name `Salesforce_CLI_OOTB_Connected_App_Access`)

then re-run `./run.sh` (or `sf org login web --alias org62` directly).

---

## 4. Run manually (if you'd rather not use run.sh)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
PORT=5057 python web/server.py
```

---

## 5. How the data works

- Every API endpoint runs a **live** SOQL query against your Org62 session.
- If the live query fails (no CLI auth, expired token), the endpoint falls back
  to a bundled snapshot **only if one exists on your machine**. The snapshots
  are git-ignored confidential data and are **not** distributed, so without a
  live connection the views will be empty and flagged.
- The header badge shows the data source (`live` vs `snapshot`).

To go from empty to populated: complete step 3, confirm `sf org display` works,
then reload the page.

---

## 6. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Views empty / `source: snapshot` with an error | CLI auth missing or expired — re-run `sf org login web --alias org62`, verify with `sf org display`. |
| `OAUTH_APP_ACCESS_DENIED` on login | Get the perm set in §3 assigned, then re-login. |
| Port 403 / won't bind on 5000 | Use `PORT=5057` (macOS AirPlay owns 5000). |
| `ModuleNotFoundError` | Activate the venv (`source .venv/bin/activate`) and `pip install -r requirements.txt`. |
| Wrong org data | Confirm the alias: `sf org display --target-org org62` — Org Id starts `00D000000000062`. |

---

## 7. Security notes

- Read-only by design: SELECT SOQL only, no write-back.
- The server binds `127.0.0.1` only — not reachable from other machines.
- No credentials live in the repo. Auth is the CLI's encrypted token in
  `~/.sfdx/`; `.env` and all `web/snapshot_*.json` are git-ignored. Never commit
  pipeline data or tokens.

---

## Data model (verified against Org62)

The queries assume these Org62 facts (see `CLAUDE.md` for the full list):

- My-deal tagging: `OpportunityTeamMember`, `TeamMemberRole = 'Solutions Engineer'`.
- SE specialist crediting: `Deal_Contribution__c.Opportunity_Role__c` — `'Service Cloud FSL Specialist'`
  or `'Service Cloud SE'`, selected via the Settings (⚙) role-mode toggle in the header.
- ANZ = `Account.BillingCountry IN ('AU','NZ','Australia','New Zealand')`.
- "Field Service product" = `OpportunityLineItem` with `Product2.Name LIKE '%Field Service%'`;
  "Service Cloud product" = `LIKE '%Service Cloud%'` or `LIKE '%Agentforce for Service%'`.
- Fiscal year = Feb 1 – Jan 31 (`CloseDate = THIS_FISCAL_YEAR`).

The dashboard also has a rolling pipeline-movements ticker (recent amount/stage/comment changes on
your open pipe) and lazy per-opportunity product line items in the detail drawer — both toggled or
loaded live, no snapshot dependency for either. See `CLAUDE.md` for the query details.

These are Org62-specific. If another org uses different objects/roles, adjust
the SOQL in `salesforce.py`.
