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


def _is_dead(stage):
    return (stage or "").startswith("Dead")


def _se_notes(o):
    return {
        "comments": o.get("SE_Comments__c"),
        "commentDate": o.get("SE_Comment_Update_Date__c"),
        "nextSteps": o.get("SE_Next_Steps__c"),
        "architect": o.get("Architect_Comments__c"),
        "techExec": o.get("Issues__c"),
    }


def flatten_fy(records):
    """OpportunityTeamMember rows (open + closed, this FY) → enriched deal dicts.

    Matches the baked-snapshot shape so live and snapshot render identically.
    products[]/activity[] need per-opp bulk queries, so the live single-query
    path leaves them empty; the MCP-baked snapshot carries them.
    """
    rows = []
    for r in records:
        o = r.get("Opportunity") or {}
        acct = o.get("Account") or {}
        owner = o.get("Owner") or {}
        rows.append(
            {
                "id": o.get("Id"),
                "name": o.get("Name"),
                "account": acct.get("Name"),
                "owner": owner.get("Name"),
                "se": None,
                "stage": o.get("StageName"),
                "category": o.get("ForecastCategoryName") or _stage_to_category(o.get("StageName")),
                "close": o.get("CloseDate"),
                "amount": o.get("Amount"),
                "isClosed": bool(o.get("IsClosed")),
                "isWon": bool(o.get("IsWon")),
                "isDead": _is_dead(o.get("StageName")),
                "lastActivity": o.get("LastActivityDate"),
                "products": [],
                "seNotes": _se_notes(o),
                "aeNotes": {"nextStep": o.get("NextStep") or o.get("Next_Steps__c"),
                            "description": o.get("Description")},
                "activity": [],
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
                "id": o.get("Id"),
                "name": o.get("Name"),
                "account": acct.get("Name"),
                "country": acct.get("BillingCountry"),
                "stage": (o.get("StageName") or "")[:2],
                "close": o.get("CloseDate"),
                "amount": o.get("Amount"),
                "owner": owner.get("Name"),
                "products": [],
                "seNotes": _se_notes(o),
                "aeNotes": {"nextStep": o.get("NextStep") or o.get("Next_Steps__c")},
                "lastActivity": o.get("LastActivityDate"),
            }
        )
    return rows


def _event_category(what_type, ev_type):
    """customer = related to an Opportunity; marketing = Campaign-related or a
    Marketing activity type; everything else = non-customer."""
    if what_type == "Opportunity":
        return "customer"
    if what_type == "Campaign" or "marketing" in (ev_type or "").lower():
        return "marketing"
    return "noncustomer"


def flatten_activity(records):
    """Event rows → categorized distribution, alerts, and account breakdown.

    Uses the polymorphic `What` (TYPEOF) so Opportunity-related events carry the
    account, amount, close date and stage. Time split by category monthly/overall;
    last-30d total vs average FY month; dead-opportunity alerts in the last 7 days;
    per-account time spent over the last 14 days.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    end = now.strftime("%Y-%m-%dT%H:%M:%S")
    c7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
    c14 = (now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00")
    c30 = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")

    by_month, by_cat, recent = {}, {}, []
    alerts = {}  # opp whatId -> aggregate of dead-deal activity (last 7d)
    l14 = {}  # account -> {minutes, events, opps{name->{...}}}
    total_min = last30_min = last30_n = 0
    for e in records:
        what = e.get("What") or {}
        wtype = (what.get("attributes") or {}).get("type")
        mins = e.get("DurationInMinutes") or 0
        dt = e.get("ActivityDateTime") or ""
        cat = _event_category(wtype, e.get("Type"))
        acct = (what.get("Account") or {}).get("Name")
        opp = what.get("Name")
        stage = what.get("StageName")
        dead = bool(stage and stage.startswith("Dead"))
        amt, close = what.get("Amount"), what.get("CloseDate")
        total_min += mins
        if c30 <= dt <= end:
            last30_min += mins
            last30_n += 1
        c = by_cat.setdefault(cat, {"category": cat, "count": 0, "minutes": 0})
        c["count"] += 1
        c["minutes"] += mins
        mon = dt[:7]
        if mon:
            m = by_month.setdefault(mon, {"month": mon, "count": 0, "minutes": 0,
                                          "customer": 0, "marketing": 0, "noncustomer": 0})
            m["count"] += 1
            m["minutes"] += mins
            m[cat] += mins
        if dead and c7 <= dt <= end:
            wid = e.get("WhatId")
            g = alerts.setdefault(wid, {"whatId": wid, "opp": opp, "account": acct, "amount": amt,
                                        "close": close, "stage": stage, "events": 0, "minutes": 0,
                                        "latestDate": "", "latestSubject": None})
            g["events"] += 1
            g["minutes"] += mins
            if dt >= g["latestDate"]:
                g["latestDate"], g["latestSubject"] = dt, e.get("Subject")
        if cat == "customer" and c14 <= dt <= end:
            a = l14.setdefault(acct or "—", {"account": acct or "—", "minutes": 0, "events": 0, "_opps": {}})
            a["minutes"] += mins
            a["events"] += 1
            o = a["_opps"].setdefault(opp, {"name": opp, "amount": amt, "close": close,
                                            "dead": dead, "whatId": e.get("WhatId"), "mins": 0})
            o["mins"] += mins
        if len(recent) < 150:
            recent.append({"subject": e.get("Subject"), "type": (e.get("RecordType") or {}).get("Name") or "—",
                           "date": dt, "mins": mins, "what": opp, "whatId": e.get("WhatId"),
                           "category": cat, "account": acct, "amount": amt, "close": close, "dead": dead})
    n_months = len(by_month) or 1
    avg_monthly = round(total_min / n_months)
    last14 = []
    for a in l14.values():
        a["opps"] = sorted(a.pop("_opps").values(), key=lambda x: -x["mins"])
        last14.append(a)
    last14.sort(key=lambda x: -x["minutes"])
    return {
        "totalEvents": len(records),
        "totalMinutes": total_min,
        "last30Minutes": last30_min,
        "last30Events": last30_n,
        "avgMonthlyMinutes": avg_monthly,
        "last30VsAvgPct": round((last30_min - avg_monthly) / avg_monthly * 100) if avg_monthly else None,
        "byCategory": sorted(by_cat.values(), key=lambda x: -x["minutes"]),
        "byMonth": sorted(by_month.values(), key=lambda x: x["month"]),
        "alerts": sorted(alerts.values(), key=lambda x: -x["minutes"]),
        "last14ByAccount": last14,
        "recent": recent,
    }


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


def flatten_gaps(records):
    """Coverage-gap opps → flat rows (business opps in my active accounts I'm not on)."""
    rows = []
    for o in records:
        acct = o.get("Account") or {}
        owner = o.get("Owner") or {}
        rows.append({"id": o.get("Id"), "name": o.get("Name"), "account": acct.get("Name"),
                     "amount": o.get("Amount"), "close": o.get("CloseDate"),
                     "stage": o.get("StageName"), "type": o.get("Type"), "owner": owner.get("Name")})
    rows.sort(key=lambda r: -(r.get("amount") or 0))
    return rows


@app.route("/api/activity")
def api_activity():
    """My Events this FY — Activity Log tab (time, alerts, coverage gaps)."""
    try:
        payload = flatten_activity(org.my_events_fy())
        gaps = flatten_gaps(org.coverage_gaps())
        payload["coverageGaps"] = gaps
        payload["coverageGapTotal"] = sum(r.get("amount") or 0 for r in gaps)
        payload["source"] = "live"
        return jsonify(payload)
    except Exception as e:
        snap = _snapshot("snapshot_activity.json")
        snap["error"] = str(e)
        return jsonify(snap)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    # Bind loopback only — no app-level auth by design (see CLAUDE.md / SRD).
    app.run(host="127.0.0.1", port=port, debug=False)
