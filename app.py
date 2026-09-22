"""Slack Socket Mode app: `/deals` lists my open Org62 opportunities."""
import os
from datetime import date, datetime

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from salesforce import Org62

load_dotenv()

STALE_DAYS = 90  # no activity for this long → flag it


def fmt_amount(a):
    return f"${a:,.0f}" if a else "—"


def days_since(iso):
    if not iso:
        return None
    try:
        return (date.today() - datetime.fromisoformat(iso).date()).days
    except ValueError:
        return None


def deals_blocks(records):
    if not records:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No open deals found where you're on the Opportunity Team.",
                },
            }
        ]
    total = sum((r.get("Opportunity") or {}).get("Amount") or 0 for r in records)
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Your open deals ({len(records)}) · {fmt_amount(total)}",
            },
        }
    ]
    for r in records:
        o = r.get("Opportunity") or {}
        stale = days_since(o.get("LastActivityDate"))
        flag = " :warning:" if stale is not None and stale >= STALE_DAYS else ""
        last = o.get("LastActivityDate") or "no activity"
        line = (
            f"*{o.get('Name', '(no name)')}*{flag}\n"
            f"{o.get('StageName', '—')} · close {o.get('CloseDate', '—')} · {fmt_amount(o.get('Amount'))}\n"
            f"Role: {r.get('TeamMemberRole', '—')} · Last activity: {last}"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line}})
        blocks.append({"type": "divider"})
    return blocks


def build_app():
    """Construct the Bolt app. Kept out of import time so the module (and
    deals_blocks) can be imported/tested without Slack credentials."""
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    org = Org62(alias=os.environ.get("ORG62_ALIAS", "org62"))

    @app.command("/deals")
    def deals(ack, respond, command):
        ack()
        try:
            records = org.my_open_deals()  # open only — IsClosed=false in SOQL
            respond(blocks=deals_blocks(records), response_type="ephemeral")
        except Exception as e:  # surface the error to the user, not just logs
            respond(f":warning: Couldn't fetch deals: {e}")

    return app


if __name__ == "__main__":
    SocketModeHandler(build_app(), os.environ["SLACK_APP_TOKEN"]).start()
