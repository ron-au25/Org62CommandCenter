# Org62 Command Center + /deals Slack app

> **Web dashboard?** See **[INSTALL.md](INSTALL.md)** for installing and
> connecting the Org62 Command Center (`web/server.py`) on your machine.
> The rest of this file covers the Slack `/deals` app.

---

## /deals — Slack app for my Org62 opportunities

A Socket Mode Slack app. Type `/deals` in Slack → get your open Org62
opportunities (where you're on the Opportunity Team). No public URL, no hosting;
runs on your Mac.

## How it reaches Org62

Web OAuth is blocked for the default CLI app in Org62. This app sidesteps that:
it reuses whatever session the Salesforce CLI already holds. So the one-time
`access-token` (sid) login is the prerequisite.

### 1. Authorize Org62 in the CLI (once, re-do when session expires)

Log into Org62 in your browser, copy the `sid` cookie value, then:

```bash
SFDX_ACCESS_TOKEN=<paste-sid> sf org login access-token \
  --alias org62 --instance-url https://org62.my.salesforce.com
```

Verify:

```bash
sf org display --target-org org62 --json
```

You should see `accessToken` and `instanceUrl`.

## Create the Slack app (once) — from the manifest

1. https://api.slack.com/apps → **Create New App** → **From a manifest**.
2. Pick your workspace → paste the contents of `slack-manifest.yaml` (switch
   the editor to YAML) → **Create**. This sets the `/deals` command, Socket
   Mode, and the `commands` + `chat:write` scopes in one shot.
3. **Basic Information → App-Level Tokens → Generate Token and Scopes**: add
   scope `connections:write`. Copy the `xapp-...` value → `SLACK_APP_TOKEN`.
4. **OAuth & Permissions → Install to Workspace** (admin approval may be
   required). Copy **Bot User OAuth Token** `xoxb-...` → `SLACK_BOT_TOKEN`.

## Run

```bash
cd slack-deals
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in the two Slack tokens
python app.py
```

Then in Slack: `/deals`.

## Notes / to confirm

- Assumes deal tagging is `OpportunityTeamMember`. Org62 may use
  `OpportunityContactRole` or a custom object — confirm against real data and
  adjust `my_open_deals()` in `salesforce.py`.
- Session tokens expire; re-run the `access-token` login when `/deals` starts
  returning auth errors. Swap to a JWT Connected App when you deploy this
  off your Mac.
