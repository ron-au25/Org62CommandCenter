"""Org62 Command Center — local dashboard over Org62 (read-only).

Built to the "SE Command Centre" design guide (dark KPI band + tabbed UX),
wired to Org62 through the Salesforce CLI session (salesforce.Org62 →
`sf org display` token → REST v62.0). Every API tries a live query and falls
back to bundled snapshot JSON when the CLI has no live org62 auth, flagging
source="snapshot" so the UI shows a badge instead of breaking.

Read-only by design: SELECT SOQL only, no write-back. Binds 127.0.0.1.
"""
import json
import os
import sys

from flask import Flask, jsonify, send_from_directory

# salesforce.py lives one level up, in slack-deals/
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from salesforce import Org62  # noqa: E402

app = Flask(__name__)
org = Org62(alias=os.environ.get("ORG62_ALIAS", "org62"))


def _num(v):
    return v if isinstance(v, (int, float)) else 0


def _stage_to_category(stage):
    """Snapshot-only fallback: derive a forecast bucket from stage prefix.

    Live data uses the real Opportunity.ForecastCategoryName; this only runs
    for snapshot rows that predate that field.
    """
    s = (stage or "")[:2]
    if s in ("06", "07") or "won" in (stage or "").lower():
        return "Closed"
    return {"05": "Commit", "04": "Best Case", "03": "Pipeline", "02": "Pipeline",
            "01": "Pipeline"}.get(s, "Pipeline")


def flatten_deals(records):
    """OpportunityTeamMember rows → flat deal dicts (open-only view)."""
    rows = []
    for r in records:
        o = r.get("Opportunity") or {}
        rows.append(
            {
                "name": o.get("Name"),
                "stage": o.get("StageName"),
                "close": o.get("CloseDate"),
                "amount": o.get("Amount"),
                "role": r.get("TeamMemberRole"),
                "lastActivity": o.get("LastActivityDate"),
            }
        )
    return rows


def flatten_fy(records):
    """OpportunityTeamMember rows (open + closed, this FY) → deal dicts."""
    rows = []
    for r in records:
        o = r.get("Opportunity") or {}
        acct = o.get("Account") or {}
        owner = o.get("Owner") or {}
        rows.append(
            {
                "name": o.get("Name"),
                "account": acct.get("Name"),
                "stage": o.get("StageName"),
                "category": o.get("ForecastCategoryName") or _stage_to_category(o.get("StageName")),
                "close": o.get("CloseDate"),
                "amount": o.get("Amount"),
                "nextStep": o.get("NextStep"),
                "lastActivity": o.get("LastActivityDate"),
                "isClosed": bool(o.get("IsClosed")),
                "isWon": bool(o.get("IsWon")),
                "owner": owner.get("Name"),
            }
        )
    return rows


def flatten_whitespace(records):
    rows = []
    for o in records:
        acct = o.get("Account") or {}
        owner = o.get("Owner") or {}
        rows.append(
            {
                "name": o.get("Name"),
                "account": acct.get("Name"),
                "country": acct.get("BillingCountry"),
                "stage": (o.get("StageName") or "")[:2],
                "close": o.get("CloseDate"),
                "amount": o.get("Amount"),
                "owner": owner.get("Name"),
            }
        )
    return rows


def _snapshot(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def _payload(rows, source):
    return {
        "source": source,
        "count": len(rows),
        "total": sum(_num(r.get("amount")) for r in rows),
        "rows": rows,
    }


@app.route("/")
def index():
    return send_from_directory(HERE, "command.html")


@app.route("/api/deals")
def api_deals():
    try:
        return jsonify(_payload(flatten_deals(org.my_open_deals()), "live"))
    except Exception as e:
        snap = _snapshot("snapshot_deals.json")
        snap["error"] = str(e)
        return jsonify(snap)


@app.route("/api/forecast")
def api_forecast():
    """My deals (open + closed) closing this FY — powers KPIs, Forecast, Coach."""
    try:
        return jsonify(_payload(flatten_fy(org.my_deals_fy()), "live"))
    except Exception as e:
        snap = _snapshot("snapshot_forecast.json")
        snap["error"] = str(e)
        return jsonify(snap)


@app.route("/api/whitespace")
def api_whitespace():
    try:
        return jsonify(_payload(flatten_whitespace(org.anz_fsl_whitespace()), "live"))
    except Exception as e:
        snap = _snapshot("snapshot_whitespace.json")
        snap["error"] = str(e)
        return jsonify(snap)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    # Bind loopback only — no app-level auth by design (see CLAUDE.md / SRD).
    app.run(host="127.0.0.1", port=port, debug=False)
