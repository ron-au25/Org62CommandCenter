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

## 2. Clone and set up

```bash
git clone https://github.com/ron-au25/org62-command-center.git
cd org62-command-center            # (folder name may differ from repo name)

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional environment settings (defaults work out of the box):

```bash
cp .env.example .env               # only needed for the Slack /deals app
```

- `ORG62_ALIAS` — CLI alias for your Org62 auth (default `org62`).
- `PORT` — web port (default `5057`).

---

## 3. Connect Org62 (once per machine)

The app reads whatever session the Salesforce CLI already holds — it calls
`sf org display --json` and talks to the REST API. So the only setup is a CLI
login under the alias `org62`.

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

then re-run `sf org login web --alias org62`.

---

## 4. Run

```bash
source .venv/bin/activate          # if not already active
PORT=5057 python web/server.py
```

Open <http://127.0.0.1:5057>.

> On macOS, port **5000** is taken by AirPlay Receiver (returns 403) — use 5057
> (the default here) or any other free port.

Leave the process running while you use the dashboard. `Ctrl-C` to stop.

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
- SE specialist crediting: `Deal_Contribution__c.Opportunity_Role__c = 'Service Cloud FSL Specialist'`.
- ANZ = `Account.BillingCountry IN ('AU','NZ','Australia','New Zealand')`.
- "Field Service product" = `OpportunityLineItem` with `Product2.Name LIKE '%Field Service%'`.
- Fiscal year = Feb 1 – Jan 31 (`CloseDate = THIS_FISCAL_YEAR`).

These are Org62-specific. If another org uses different objects/roles, adjust
the SOQL in `salesforce.py`.
