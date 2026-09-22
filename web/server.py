"""Command Center web app — 2 tabs (My Deals / ANZ FSL White Space) over Org62.

Serves index.html and two JSON APIs. Each API tries a live Org62 query (via the
Salesforce CLI token in salesforce.Org62); if that fails — e.g. org62 not yet
authorized in the CLI — it falls back to the bundled snapshot JSON so the UI
still renders, and flags source="snapshot" + the error.
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


def flatten_deals(records):
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
    return send_from_directory(HERE, "index.html")


@app.route("/api/deals")
def api_deals():
    try:
        return jsonify(_payload(flatten_deals(org.my_open_deals()), "live"))
    except Exception as e:
        snap = _snapshot("snapshot_deals.json")
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
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
