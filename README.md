# Org62 Command Center

A local, read-only dashboard over Org62 (Salesforce internal prod) — forecast,
ANZ Field Service white space, coverage gaps, and activity, scoped to your own
opportunities. Five tabs: Open Pipe, Closed Pipe, New Business (Whitespace),
Coach (coverage gaps), Activity Log.

See **[USER_GUIDE.html](USER_GUIDE.html)** for a walkthrough of each tab.

Flask backend (`web/server.py` + `salesforce.py`) serving a vanilla-JS SPA
(`web/command.html`). Runs on your own machine, binds to loopback only, issues
SELECT-only SOQL — no write-back.

## Install / run

See **[INSTALL.md](INSTALL.md)** for prerequisites, clone/setup, connecting
your own Org62 login, and running the server.

## Data model and connection facts

See **[CLAUDE.md](CLAUDE.md)** for the verified Org62 data-model facts (deal
tagging, SE specialist crediting, whitespace definition, fiscal year) and the
durable CLI auth path this app relies on.
