"""Thin Org62 client.

Runs SOQL against Org62 through the Salesforce CLI. Org62 REDACTS the access
token in `sf org display` output, so we can't grab a Bearer token and call REST
ourselves. Instead we let the CLI make the REST call with its own (internal,
unredacted) session via `sf api request rest`, which needs no token reveal.
Requires a CLI login for the alias: `sf org login web --alias org62`.
"""
import json
import subprocess
import urllib.parse

API_VERSION = "v64.0"

# Max opp ids per IN (...) clause. Keeps the query-string GET URL well under
# platform limits even for a heavy user; results are merged across batches.
_ID_BATCH = 200


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

# Verified against Org62 (00D000000000062EAA):
# - Deal_Contribution__c.Opportunity_Role__c has exact literal values
#   'Service Cloud FSL Specialist' and 'Service Cloud SE' (distinct values).
# - Product2.Name LIKE '%Service Cloud%' -> 567 catalog rows / 3.24M
#   OpportunityLineItem rows; LIKE '%Agentforce for Service%' -> 18 catalog
#   rows (all "...Add-on - <Edition>" variants) / 32,995 line items. Both
#   real/active, not catalog-only.
ROLE_MODES = {
    "fsl": {
        "label": "FSL",
        "sku_clause": "Product2.Name LIKE '%Field Service%'",
        "role": "Service Cloud FSL Specialist",
    },
    "service_cloud": {
        "label": "Service Cloud",
        "sku_clause": "(Product2.Name LIKE '%Service Cloud%' OR Product2.Name LIKE '%Agentforce for Service%')",
        "role": "Service Cloud SE",
    },
}


class Org62:
    def __init__(self, alias="org62"):
        self.alias = alias
        self._token = None
        self._instance = None
        self._username = None
        self._user_id = None

    # --- auth -------------------------------------------------------------
    def _display(self):
        out = subprocess.run(
            ["sf", "org", "display", "--target-org", self.alias, "--json"],
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            raise RuntimeError(f"`sf org display` failed: {out.stderr or out.stdout}")
        return json.loads(out.stdout)["result"]

    def _auth(self):
        res = self._display()
        self._instance = res["instanceUrl"]
        self._username = res.get("username")
        return self._instance

    def token(self):
        # Kept for callers that want the instance URL; the token itself is
        # redacted by Org62 and never used directly (see module docstring).
        if not self._instance:
            self._auth()
        return None, self._instance

    # --- queries ----------------------------------------------------------
    def _rest(self, path):
        """GET an Org62 REST path via the CLI (it supplies its own session)."""
        out = subprocess.run(
            ["sf", "api", "request", "rest", path, "--target-org", self.alias],
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            raise RuntimeError(f"`sf api request rest` failed: {out.stderr or out.stdout}")
        return json.loads(out.stdout)

    def query(self, soql):
        path = f"/services/data/{API_VERSION}/query?q={urllib.parse.quote(soql)}"
        records = []
        while True:
            data = self._rest(path)
            records.extend(data.get("records", []))
            nxt = data.get("nextRecordsUrl")
            if not nxt:
                return records
            path = nxt

    def my_user_id(self):
        if self._user_id:
            return self._user_id
        self.token()  # ensures self._username populated
        if not self._username:
            raise RuntimeError("No username on the CLI auth for this org.")
        recs = self.query(
            f"SELECT Id FROM User WHERE Username = '{self._username}' LIMIT 1"
        )
        if not recs:
            raise RuntimeError(f"No User found for {self._username}")
        self._user_id = recs[0]["Id"]
        return self._user_id

    def my_open_deals(self):
        """Open opportunities where I'm on the Opportunity Team.

        NOTE: assumes tagging lives on OpportunityTeamMember. Org62 may use a
        different object/role — confirm and adjust the FROM/WHERE once a token
        is available to test against.
        """
        uid = self.my_user_id()
        # Tagging confirmed against Org62: OpportunityTeamMember, role
        # "Solutions Engineer". NextStep is unpopulated in Org62, so it's
        # omitted; LastActivityDate drives the staleness flag instead.
        soql = (
            "SELECT Opportunity.Name, Opportunity.StageName, Opportunity.CloseDate, "
            "Opportunity.Amount, Opportunity.LastActivityDate, TeamMemberRole "
            "FROM OpportunityTeamMember "
            f"WHERE UserId = '{uid}' AND Opportunity.IsClosed = false "
            "ORDER BY Opportunity.CloseDate ASC"
        )
        return self.query(soql)

    def my_deals_fy(self):
        """All my deals (open + closed) closing THIS fiscal year.

        Same tagging as my_open_deals (OpportunityTeamMember, UserId indexed —
        do NOT LIKE-scan this object). Drops the IsClosed filter so Closed
        Won / Lost land in the Forecast + KPI views, and pulls the standard
        forecast/coach fields (ForecastCategoryName, NextStep, IsWon) — all
        standard Opportunity fields, no assumptions.
        """
        uid = self.my_user_id()
        soql = (
            "SELECT Opportunity.Id, Opportunity.Name, Opportunity.StageName, "
            "Opportunity.CloseDate, Opportunity.Amount, Opportunity.ForecastCategoryName, "
            "Opportunity.NextStep, Opportunity.LastActivityDate, Opportunity.IsClosed, "
            "Opportunity.IsWon, Opportunity.Account.Name, Opportunity.Owner.Name, "
            "Opportunity.SE_Comments__c, Opportunity.SE_Comment_Update_Date__c, "
            "Opportunity.SE_Next_Steps__c, Opportunity.Architect_Comments__c, "
            "Opportunity.Issues__c, Opportunity.Next_Steps__c, Opportunity.Description, "
            "TeamMemberRole "
            "FROM OpportunityTeamMember "
            f"WHERE UserId = '{uid}' AND Opportunity.CloseDate = THIS_FISCAL_YEAR "
            "ORDER BY Opportunity.CloseDate ASC"
        )
        return self.query(soql)

    def my_events_fy(self):
        """My Events this FY — powers the Activity Log tab + time-spent KPI.

        Event.OwnerId = me, ActivityDate in this FY. RecordType.Name drives the
        distribution (verified: 'Solutions Event', 'Sales Events').
        DurationInMinutes is the time-spent measure. SE_Task_Type__c (verified
        picklist, all 34 org values) drives the Customer Related / Customer
        Facing split in server.py's ACTIVITY_TYPE_MAP, per Ron's official
        Activity Type definitions doc.
        """
        uid = self.my_user_id()
        soql = (
            "SELECT Id, Subject, Type, SE_Task_Type__c, RecordType.Name, DurationInMinutes, "
            "ActivityDateTime, WhatId, "
            "TYPEOF What "
            "WHEN Opportunity THEN Name, Amount, CloseDate, StageName, Account.Name "
            "WHEN Campaign THEN Name "
            "ELSE Name END "
            "FROM Event "
            f"WHERE OwnerId = '{uid}' AND ActivityDate = THIS_FISCAL_YEAR "
            "ORDER BY ActivityDateTime DESC"
        )
        return self.query(soql)

    def coverage_gaps(self, mode="fsl"):
        """Business opps in accounts I'm actively working this FY that I'm NOT
        engaged on (not the Deal_Contribution contributor).

        Contributor = Deal_Contribution__c.SE_Name__c (label 'Contributor') —
        this is ME, regardless of role, so `mode` only swaps which product
        line counts as "in scope" (the FS/Service-Cloud SKU clause), not the
        contributor check itself.
        Active account = an account with an opp closing this FY where I'm the
        contributor. Only New Business / Add-On Business (skip Renewal / SOW /
        Success Plan / Transfer / Upgrade), and only in-scope-product opps.
        Open, closing this FY.
        """
        if mode not in ROLE_MODES:
            raise ValueError(f"Unknown role mode: {mode!r}")
        sku_clause = ROLE_MODES[mode]["sku_clause"]
        uid = self.my_user_id()
        acct_recs = self.query(
            "SELECT Opportunity__r.AccountId FROM Deal_Contribution__c "
            f"WHERE SE_Name__c = '{uid}' AND Opportunity_Close_Date__c = THIS_FISCAL_YEAR"
        )
        acct_ids = sorted({(r.get("Opportunity__r") or {}).get("AccountId")
                           for r in acct_recs if (r.get("Opportunity__r") or {}).get("AccountId")})
        if not acct_ids:
            return []
        ids = ",".join(f"'{a}'" for a in acct_ids)
        soql = (
            "SELECT Id, Name, Account.Name, AccountId, Amount, CloseDate, StageName, "
            "Type, Owner.Name FROM Opportunity "
            f"WHERE AccountId IN ({ids}) AND IsClosed = false "
            "AND Type IN ('New Business','Add-On Business') "
            "AND CloseDate = THIS_FISCAL_YEAR "
            f"AND Id IN (SELECT OpportunityId FROM OpportunityLineItem WHERE {sku_clause}) "
            f"AND Id NOT IN (SELECT Opportunity__c FROM Deal_Contribution__c WHERE SE_Name__c = '{uid}') "
            "ORDER BY Amount DESC NULLS LAST"
        )
        return self.query(soql)

    def anz_fsl_whitespace(self, mode="fsl"):
        """ANZ opportunities with no specialist of the given `mode` engaged.

        Verified against Org62:
        - ANZ = Account.BillingCountry in AU/NZ (Opp region fields unreliable).
        - open + stages 02/03/04, closing between today and end of this FY.
        - business only: Type in New Business / Add-On Business (skip
          Renewal / SOW / Success Plan / Transfer / Upgrade).
        - whitespace = no Deal_Contribution__c with Opportunity_Role__c
          matching the mode's role (this is the real "deal contribution"
          object; OpportunityTeamMember has no such role in its picklist).
        - `mode="fsl"` (default): Field Service product, role
          'Service Cloud FSL Specialist'. `mode="service_cloud"`: Service
          Cloud / Agentforce for Service product, role 'Service Cloud SE'.
        """
        if mode not in ROLE_MODES:
            raise ValueError(f"Unknown role mode: {mode!r}")
        sku_clause = ROLE_MODES[mode]["sku_clause"]
        role = ROLE_MODES[mode]["role"]
        soql = (
            "SELECT Id, Name, StageName, CloseDate, Amount, "
            "Account.Name, Account.BillingCountry, Owner.Name, NextStep, "
            "SE_Comments__c, SE_Comment_Update_Date__c, SE_Next_Steps__c, "
            "Architect_Comments__c, Issues__c, Next_Steps__c, LastActivityDate "
            "FROM Opportunity "
            "WHERE IsClosed = false "
            "AND (StageName LIKE '02%' OR StageName LIKE '03%' OR StageName LIKE '04%') "
            "AND CloseDate >= TODAY AND CloseDate = THIS_FISCAL_YEAR "
            "AND Type IN ('New Business','Add-On Business') "
            "AND Account.BillingCountry IN ('AU','NZ','Australia','New Zealand') "
            f"AND Id IN (SELECT OpportunityId FROM OpportunityLineItem WHERE {sku_clause}) "
            "AND Id NOT IN (SELECT Opportunity__c FROM Deal_Contribution__c "
            f"WHERE Opportunity_Role__c = '{role}') "
            "ORDER BY Amount DESC NULLS LAST"
        )
        return self.query(soql)

    def my_pipeline_movements(self):
        """Raw ingredients for the rolling pipeline-movements bar.

        Two raw queries; server.py does the diffing/shaping (matching the
        existing convention where flatten_activity() derives everything from
        raw my_events_fy() rows). history = OpportunityHistory rows for my
        open opps (verified queryable, standard fields incl. PrevAmount — no
        PrevStageName, so stage-change detection needs a sequential diff
        against the previous row per opp, done in server.py).
        comments = open opps with a recent SE_Comment_Update_Date__c.
        """
        uid = self.my_user_id()
        history = self.query(
            "SELECT OpportunityId, Amount, PrevAmount, StageName, CreatedDate, "
            "Opportunity.Name, Opportunity.Account.Id, Opportunity.Account.Name "
            "FROM OpportunityHistory "
            "WHERE OpportunityId IN (SELECT OpportunityId FROM OpportunityTeamMember "
            f"WHERE UserId = '{uid}' AND Opportunity.IsClosed = false) "
            "ORDER BY OpportunityId ASC, CreatedDate ASC"
        )
        comments = self.query(
            "SELECT Id, Name, Account.Name, SE_Comment_Update_Date__c FROM Opportunity "
            "WHERE IsClosed = false AND SE_Comment_Update_Date__c != null "
            "AND Id IN (SELECT OpportunityId FROM OpportunityTeamMember "
            f"WHERE UserId = '{uid}') "
            "ORDER BY SE_Comment_Update_Date__c DESC"
        )
        return {"history": history, "comments": comments}

    def my_service_aes(self, opp_ids):
        """Service Cloud AE per opportunity, for a given set of opp ids.

        NOT OpportunityTeamMember — "Selling Role" lives on the managed-package
        object sfbase__OpportunityTeam__c ("Opportunity Team"), field
        TeamRoleLookup__c (label "Selling Role"), a lookup to Team_Role__c, not
        a picklist. Verified against Org62 record aAUed00000jdvd2: Opportunity
        006ed00000kBbt0AAC, sfbase__User__r.Name = 'Clinton Alver',
        TeamRoleLookup__r.Name = 'Service Cloud AE' — exact match.
        Takes explicit opp_ids (from my_deals_fy()) to build a literal
        IN (...) clause rather than a semi-join.
        """
        out = []
        for batch in _chunks(opp_ids, _ID_BATCH):
            ids = ",".join(f"'{i}'" for i in batch)
            soql = (
                "SELECT sfbase__Opportunity__c, sfbase__User__r.Name "
                "FROM sfbase__OpportunityTeam__c "
                f"WHERE TeamRoleLookup__r.Name = 'Service Cloud AE' AND sfbase__Opportunity__c IN ({ids}) "
                "ORDER BY sfbase__Opportunity__c ASC, CreatedDate DESC"
            )
            out.extend(self.query(soql))
        return out

    def core_ses_for_opps(self, opp_ids):
        """Core SE per opportunity, for a given set of opp ids (any opps, not just mine).

        Deal_Contribution__c, Opportunity_Role__c = 'Core SE' (verified exact
        literal against Org62 — distinct from e.g. 'RCG Kahuna Core SE'; 2.13M
        rows org-wide, a common/real role, not a typo-adjacent decoy).
        SE_Name__c is the contributor lookup (same field used by
        coverage_gaps()). Literal IN (...) — same reasoning as my_service_aes.
        Used both for my_deals_fy() rows (Open Pipe "Core SE" column) and for
        anz_fsl_whitespace() rows (whitespace "aligned SE" matching), since the
        role isn't scoped to any one user.
        """
        out = []
        for batch in _chunks(opp_ids, _ID_BATCH):
            ids = ",".join(f"'{i}'" for i in batch)
            soql = (
                "SELECT Opportunity__c, SE_Name__r.Name FROM Deal_Contribution__c "
                f"WHERE Opportunity_Role__c = 'Core SE' AND Opportunity__c IN ({ids}) "
                "ORDER BY Opportunity__c ASC, CreatedDate DESC"
            )
            out.extend(self.query(soql))
        return out

    def changes_since(self, account_ids, since_iso):
        """Cheap poll check: any Opportunity under these accounts touched since `since_iso`?

        Single aggregate COUNT — LastModifiedDate bumps on stage/amount/comment-field
        edits AND on newly-created rows alike, so this one filter covers every alert
        type the alert bar cares about. Callers should only run the expensive detail
        queries (my_pipeline_movements / new_opps_in_my_accounts) when this is > 0.
        """
        if not account_ids:
            return 0
        out = 0
        for batch in _chunks(sorted(account_ids), _ID_BATCH):
            ids = ",".join(f"'{a}'" for a in batch)
            recs = self.query(
                "SELECT COUNT(Id) cnt FROM Opportunity "
                f"WHERE AccountId IN ({ids}) AND LastModifiedDate > {since_iso}"
            )
            out += recs[0]["cnt"] if recs else 0
        return out

    def new_opps_in_my_accounts(self, account_ids, known_opp_ids):
        """Opportunities under my accounts not already in a previously-seen id set.

        Used by the alert bar's "new opportunity created" alert type. Scoped to
        accounts from my currently-open opps (same set my_pipeline_movements() uses).
        """
        if not account_ids:
            return []
        out = []
        known = ",".join(f"'{i}'" for i in known_opp_ids) if known_opp_ids else None
        for batch in _chunks(sorted(account_ids), _ID_BATCH):
            ids = ",".join(f"'{a}'" for a in batch)
            soql = (
                "SELECT Id, Name, Account.Name, CreatedDate FROM Opportunity "
                f"WHERE AccountId IN ({ids})"
            )
            if known:
                soql += f" AND Id NOT IN ({known})"
            out.extend(self.query(soql))
        return out

    def opportunity_products(self, opp_id):
        """Line items for one opportunity — fetched lazily by the detail drawer."""
        soql = (
            "SELECT Product2.Name, Quantity, TotalPrice FROM OpportunityLineItem "
            f"WHERE OpportunityId = '{opp_id}'"
        )
        return self.query(soql)
