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
import re
import sys

from flask import Flask, jsonify, request, send_from_directory

# salesforce.py lives one level up, in FieldServiceCommandCenter/
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
                "serviceAE": None,
                "coreSE": None,
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


# Verified against Org62: Event.SE_Task_Type__c is an exact-literal picklist,
# all 34 org values covered here. (related, facing) per Ron's official Activity
# Type definitions doc; "Not Available" and "Consumption Event" aren't in that
# doc — Not Available defaults to neither (unclassified placeholder), Consumption
# Event confirmed by Ron to match the other Consumption types (related+facing).
# facing is always a subset of related in this map (no facing=Yes/related=No rows).
ACTIVITY_TYPE_MAP = {
    "Account Planning": (True, False),
    "Admin": (False, False),
    "Asset Creation": (False, False),
    "Business Value Assessment": (True, True),
    "BVS - Business Case": (True, True),
    "BVS - Proposal": (True, True),
    "BVS - Value Hypothesis": (True, False),
    "Consumption Estimation": (True, True),
    "Consumption Event": (True, True),
    "Consumption - Partner Support": (True, True),
    "Consumption - POC": (True, True),
    "Consumption - Workshop": (True, True),
    "Customer Discovery": (True, True),
    "Customer Presentation": (True, True),
    "Dry Run": (True, False),
    "Localization": (False, False),
    "Marketing Support": (True, True),
    "Mentorship": (False, False),
    "Not Available": (False, False),
    "Partner Support": (True, True),
    "Personal Development": (False, False),
    "POC": (True, True),
    "Post Sale Adoption Support": (True, True),
    "Post Sale Consumption Activation": (True, True),
    "Post Sale Technical Product Support": (True, True),
    "PTO": (False, False),
    "Red Account Support": (True, True),
    "RFx": (True, False),
    "Sales Enablement": (False, False),
    "Solution Creation": (True, False),
    "Travel": (True, False),
    "V2MOM Initiatives": (False, False),
    "Wellness": (False, False),
    "Workshop": (True, True),
}

# Fiscal quarter (FY = Feb 1 - Jan 31) by calendar month number.
FQ_BY_MONTH = {2: 1, 3: 1, 4: 1, 5: 2, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 4, 12: 4, 1: 4}


def _activity_bucket(se_task_type):
    """3-way mutually-exclusive bucket for the chart/category cards, from the
    independent (related, facing) flags: facing always wins (it's a subset of
    related), then related-only, then neither."""
    related, facing = ACTIVITY_TYPE_MAP.get(se_task_type, (False, False))
    if facing:
        return "facing"
    if related:
        return "related"
    return "noncustomer"


def flatten_activity(records):
    """Event rows → categorized distribution, alerts, and account breakdown.

    Uses the polymorphic `What` (TYPEOF) so Opportunity-related events carry the
    account, amount, close date and stage. Time split by category per fiscal
    quarter/overall; current-quarter total vs average FY quarter;
    dead-opportunity alerts in the last 7 days; per-account time spent over the
    last 14 days.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    end = now.strftime("%Y-%m-%dT%H:%M:%S")
    c7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
    c14 = (now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00")
    current_q = FQ_BY_MONTH[now.month]

    # FY = Feb 1 - Jan 31; quarter bounds for "how far through this quarter
    # are we", used by the frontend to plot a pro-rata target marker.
    fy_start_year = now.year if now.month >= 2 else now.year - 1
    q_bounds = {
        1: ((fy_start_year, 2, 1), (fy_start_year, 4, 30)),
        2: ((fy_start_year, 5, 1), (fy_start_year, 7, 31)),
        3: ((fy_start_year, 8, 1), (fy_start_year, 10, 31)),
        4: ((fy_start_year, 11, 1), (fy_start_year + 1, 1, 31)),
    }
    (sy, sm, sd), (ey, em, ed) = q_bounds[current_q]
    q_start = datetime(sy, sm, sd, tzinfo=timezone.utc)
    q_end = datetime(ey, em, ed, tzinfo=timezone.utc)
    total_days = (q_end - q_start).days + 1
    elapsed_days = min(total_days, max(1, (now - q_start).days + 1))
    quarter_progress = {
        "elapsedDays": elapsed_days,
        "totalDays": total_days,
        "pct": round(elapsed_days / total_days * 100),
    }

    by_quarter, by_cat, recent = {}, {}, []
    alerts = {}  # opp whatId -> aggregate of dead-deal activity (last 7d)
    l14 = {}  # account -> {minutes, events, opps{name->{...}}}
    total_min = cur_q_min = cur_q_n = 0
    for e in records:
        what = e.get("What") or {}
        mins = e.get("DurationInMinutes") or 0
        dt = e.get("ActivityDateTime") or ""
        cat = _activity_bucket(e.get("SE_Task_Type__c"))
        acct = (what.get("Account") or {}).get("Name")
        opp = what.get("Name")
        stage = what.get("StageName")
        dead = bool(stage and stage.startswith("Dead"))
        amt, close = what.get("Amount"), what.get("CloseDate")
        total_min += mins
        mon = int(dt[5:7]) if len(dt) >= 7 else None
        q = FQ_BY_MONTH.get(mon)
        if q == current_q:
            cur_q_min += mins
            cur_q_n += 1
        c = by_cat.setdefault(cat, {"category": cat, "count": 0, "minutes": 0})
        c["count"] += 1
        c["minutes"] += mins
        if q:
            m = by_quarter.setdefault(q, {"quarter": q, "count": 0, "minutes": 0,
                                          "facing": 0, "related": 0, "noncustomer": 0})
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
        if cat in ("facing", "related") and c14 <= dt <= end:
            a = l14.setdefault(acct or "—", {"account": acct or "—", "minutes": 0, "events": 0, "_opps": {}})
            a["minutes"] += mins
            a["events"] += 1
            o = a["_opps"].setdefault(opp, {"name": opp, "amount": amt, "close": close,
                                            "dead": dead, "whatId": e.get("WhatId"), "mins": 0})
            o["mins"] += mins
        recent.append({"subject": e.get("Subject"), "type": (e.get("RecordType") or {}).get("Name") or "—",
                       "date": dt, "mins": mins, "what": opp, "whatId": e.get("WhatId"),
                       "category": cat, "account": acct, "amount": amt, "close": close, "dead": dead})
    n_quarters = len(by_quarter) or 1
    avg_quarterly = round(total_min / n_quarters)
    last14 = []
    for a in l14.values():
        a["opps"] = sorted(a.pop("_opps").values(), key=lambda x: -x["mins"])
        last14.append(a)
    last14.sort(key=lambda x: -x["minutes"])
    return {
        "totalEvents": len(records),
        "totalMinutes": total_min,
        "currentQuarter": current_q,
        "currentQuarterMinutes": cur_q_min,
        "currentQuarterEvents": cur_q_n,
        "currentQuarterProgress": quarter_progress,
        "avgQuarterlyMinutes": avg_quarterly,
        "currentQuarterVsAvgPct": round((cur_q_min - avg_quarterly) / avg_quarterly * 100) if avg_quarterly else None,
        "byCategory": sorted(by_cat.values(), key=lambda x: -x["minutes"]),
        "byQuarter": sorted(by_quarter.values(), key=lambda x: x["quarter"]),
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


def _role_mode():
    m = request.args.get("role_mode", "fsl")
    return m if m in ("fsl", "service_cloud") else "fsl"


# User prefs (role mode, ticker toggle, quarterly targets) — file-backed so
# they survive a restart/different browser, not just one browser's localStorage.
SETTINGS_FILE = os.path.join(HERE, "settings_local.json")
DEFAULT_SETTINGS = {
    "roleMode": "fsl",
    "showMovements": True,
    "targets": {"relatedHrs": 250, "facingHrs": 125},
    "alignedSEs": "",
}


def _load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    out["targets"] = dict(DEFAULT_SETTINGS["targets"])
    out["targets"].update((data.get("targets") or {}))
    return out


def _save_settings(patch):
    current = _load_settings()
    if "roleMode" in patch and patch["roleMode"] in ("fsl", "service_cloud"):
        current["roleMode"] = patch["roleMode"]
    if "showMovements" in patch:
        current["showMovements"] = bool(patch["showMovements"])
    targets = patch.get("targets") or {}
    for key in ("relatedHrs", "facingHrs"):
        if key in targets:
            try:
                val = float(targets[key])
            except (TypeError, ValueError):
                continue
            if val >= 0:
                current["targets"][key] = val
    if "alignedSEs" in patch and isinstance(patch["alignedSEs"], str):
        current["alignedSEs"] = patch["alignedSEs"].strip()[:2000]
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return current


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(_load_settings())
    patch = request.get_json(silent=True) or {}
    return jsonify(_save_settings(patch))


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
        rows = flatten_fy(org.my_deals_fy())
        try:
            ae_by_opp = {}
            opp_ids = [row["id"] for row in rows if row.get("id")]
            for r in org.my_service_aes(opp_ids):
                ae_by_opp.setdefault(r.get("sfbase__Opportunity__c"), (r.get("sfbase__User__r") or {}).get("Name"))
            se_by_opp = {}
            for r in org.core_ses_for_opps(opp_ids):
                se_by_opp.setdefault(r.get("Opportunity__c"), (r.get("SE_Name__r") or {}).get("Name"))
            for row in rows:
                row["serviceAE"] = ae_by_opp.get(row.get("id"))
                row["coreSE"] = se_by_opp.get(row.get("id"))
        except Exception:
            pass  # Service AE / Core SE are enrichments, not core deal data — don't fail the whole tab for it.
        return jsonify(_payload(rows, "live"))
    except Exception as e:
        snap = _snapshot("snapshot_forecast.json")
        snap["error"] = str(e)
        return jsonify(snap)


@app.route("/api/whitespace")
def api_whitespace():
    mode = _role_mode()
    try:
        rows = flatten_whitespace(org.anz_fsl_whitespace(mode=mode))
        try:
            opp_ids = [row["id"] for row in rows if row.get("id")]
            se_by_opp = {}
            for r in org.core_ses_for_opps(opp_ids):
                se_by_opp.setdefault(r.get("Opportunity__c"), (r.get("SE_Name__r") or {}).get("Name"))
            ae_by_opp = {}
            for r in org.my_service_aes(opp_ids):
                ae_by_opp.setdefault(r.get("sfbase__Opportunity__c"), (r.get("sfbase__User__r") or {}).get("Name"))
            for row in rows:
                row["coreSE"] = se_by_opp.get(row.get("id"))
                row["serviceAE"] = ae_by_opp.get(row.get("id"))
        except Exception:
            pass  # Core SE / Service AE are enrichments for alignment filtering — don't fail the tab for it.
        payload = _payload(rows, "live")
        payload["roleMode"] = mode
        return jsonify(payload)
    except Exception as e:
        snap = _snapshot("snapshot_whitespace.json")
        snap["error"] = str(e)
        snap.setdefault("roleMode", "fsl")
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
    mode = _role_mode()
    try:
        payload = flatten_activity(org.my_events_fy())
        gaps = flatten_gaps(org.coverage_gaps(mode=mode))
        payload["coverageGaps"] = gaps
        payload["coverageGapTotal"] = sum(r.get("amount") or 0 for r in gaps)
        payload["source"] = "live"
        payload["roleMode"] = mode
        return jsonify(payload)
    except Exception as e:
        snap = _snapshot("snapshot_activity.json")
        snap["error"] = str(e)
        snap.setdefault("roleMode", "fsl")
        return jsonify(snap)


def flatten_movements(data, days=30):
    """OpportunityHistory + comment-date rows -> one card per opportunity.

    No PrevStageName on OpportunityHistory, so stage-change direction is
    derived by diffing each opp's rows in CreatedDate order (data["history"]
    is pre-sorted OpportunityId ASC, CreatedDate ASC). Multiple changes of the
    same type on the same opp within the window collapse into a single net
    amount move / latest stage move, rather than one card per row.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    end = now.strftime("%Y-%m-%dT%H:%M:%S")
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")

    opps = {}
    last_stage = {}
    for h in data.get("history", []):
        opp = h.get("Opportunity") or {}
        acct = opp.get("Account") or {}
        oid = h.get("OpportunityId")
        date = h.get("CreatedDate") or ""
        amount, prev_amount = h.get("Amount"), h.get("PrevAmount")
        stage = h.get("StageName")
        rec = opps.setdefault(oid, {"opp": opp.get("Name"), "account": acct.get("Name"),
                                     "amount_events": [], "stage_events": [], "comment_date": None})

        if prev_amount is not None and amount is not None and amount != prev_amount and cutoff <= date <= end:
            rec["amount_events"].append({"old": prev_amount, "new": amount, "date": date})

        prev_stage = last_stage.get(oid)
        if prev_stage is not None and stage and stage != prev_stage and cutoff <= date <= end:
            new_dead = stage.startswith("Dead") or "Lost" in stage
            old_pre, new_pre = prev_stage[:2], stage[:2]
            if new_dead:
                direction = "down"
            elif "Won" in stage:
                direction = "up"
            elif new_pre.isdigit() and old_pre.isdigit():
                direction = "up" if new_pre > old_pre else "down" if new_pre < old_pre else "flat"
            else:
                direction = "flat"
            rec["stage_events"].append({"old": prev_stage, "new": stage, "direction": direction, "date": date})
        if stage:
            last_stage[oid] = stage

    for c in data.get("comments", []):
        date = c.get("SE_Comment_Update_Date__c") or ""
        if not (cutoff <= date <= end):
            continue
        acct = c.get("Account") or {}
        rec = opps.setdefault(c.get("Id"), {"opp": c.get("Name"), "account": acct.get("Name"),
                                             "amount_events": [], "stage_events": [], "comment_date": None})
        rec["comment_date"] = date

    items = []
    for rec in opps.values():
        amount_events, stage_events = rec["amount_events"], rec["stage_events"]
        if not amount_events and not stage_events and not rec["comment_date"]:
            continue
        amount_block = None
        if amount_events:
            old, new = amount_events[0]["old"], amount_events[-1]["new"]
            delta = new - old
            amount_block = {"old": old, "new": new, "delta": delta,
                             "direction": "up" if delta > 0 else "down" if delta < 0 else "flat"}
        stage_block = None
        if stage_events:
            last = stage_events[-1]
            stage_block = {"old": last["old"], "new": last["new"], "direction": last["direction"]}
        dates = [e["date"] for e in amount_events] + [e["date"] for e in stage_events]
        if rec["comment_date"]:
            dates.append(rec["comment_date"])
        items.append({
            "opp": rec["opp"], "account": rec["account"], "date": max(dates) if dates else "",
            "amount": amount_block, "stage": stage_block, "comment": bool(rec["comment_date"]),
        })

    items.sort(key=lambda x: x["date"], reverse=True)
    return {"source": "live", "items": items[:40]}


@app.route("/api/movements")
def api_movements():
    try:
        return jsonify(flatten_movements(org.my_pipeline_movements()))
    except Exception as e:
        return jsonify({"source": "snapshot", "items": [], "error": str(e)})


_SF_ID_RE = re.compile(r"^[a-zA-Z0-9]{15,18}$")


@app.route("/api/opportunity/<opp_id>/products")
def api_opportunity_products(opp_id):
    """Lazy per-opp line items — fetched when the detail drawer opens."""
    if not _SF_ID_RE.match(opp_id):
        return jsonify({"error": "invalid id", "rows": []}), 400
    try:
        rows = [{"name": (r.get("Product2") or {}).get("Name"), "qty": r.get("Quantity"),
                 "total": r.get("TotalPrice")} for r in org.opportunity_products(opp_id)]
        return jsonify({"source": "live", "rows": rows})
    except Exception as e:
        return jsonify({"source": "error", "rows": [], "error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    # Bind loopback only — no app-level auth by design (see CLAUDE.md / SRD).
    app.run(host="127.0.0.1", port=port, debug=False)
