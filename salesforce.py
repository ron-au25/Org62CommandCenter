"""Thin Org62 client.

Gets a live access token from the Salesforce CLI (whatever `sf` already has
authorized for the target alias) and runs SOQL over the REST API. No OAuth app
approval needed at runtime — it reuses the CLI session, so the `access-token`
(sid) login path works even though web OAuth is blocked in Org62.
"""
import json
import subprocess

import requests

API_VERSION = "v62.0"


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
        self._token = res["accessToken"]
        self._instance = res["instanceUrl"]
        self._username = res.get("username")
        return self._token, self._instance

    def token(self):
        if not self._token:
            self._auth()
        return self._token, self._instance

    # --- queries ----------------------------------------------------------
    def query(self, soql):
        token, instance = self.token()
        url = f"{instance}/services/data/{API_VERSION}/query"

        def _do():
            return requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": soql},
                timeout=30,
            )

        r = _do()
        if r.status_code == 401:  # stale session token — re-auth once
            self._token = None
            token, instance = self.token()
            r = _do()
        r.raise_for_status()
        return r.json()["records"]

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

    def anz_fsl_whitespace(self):
        """ANZ Field-Service opportunities with no FSL Specialist engaged.

        Verified against Org62:
        - ANZ = Account.BillingCountry in AU/NZ (Opp region fields unreliable).
        - "Field Service product" = an OpportunityLineItem whose Product2.Name
          contains "Field Service".
        - open + stages 02/03/04, closing between today and end of this FY.
        - whitespace = no Deal_Contribution__c with
          Opportunity_Role__c = 'Service Cloud FSL Specialist' (this is the
          real "deal contribution" object; OpportunityTeamMember has no such
          role in its picklist).
        """
        soql = (
            "SELECT Id, Name, StageName, CloseDate, Amount, "
            "Account.Name, Account.BillingCountry, Owner.Name "
            "FROM Opportunity "
            "WHERE IsClosed = false "
            "AND (StageName LIKE '02%' OR StageName LIKE '03%' OR StageName LIKE '04%') "
            "AND CloseDate >= TODAY AND CloseDate = THIS_FISCAL_YEAR "
            "AND Account.BillingCountry IN ('AU','NZ','Australia','New Zealand') "
            "AND Id IN (SELECT OpportunityId FROM OpportunityLineItem "
            "WHERE Product2.Name LIKE '%Field Service%') "
            "AND Id NOT IN (SELECT Opportunity__c FROM Deal_Contribution__c "
            "WHERE Opportunity_Role__c = 'Service Cloud FSL Specialist') "
            "ORDER BY Amount DESC NULLS LAST"
        )
        return self.query(soql)
