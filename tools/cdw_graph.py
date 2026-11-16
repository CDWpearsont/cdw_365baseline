"""Shared Microsoft Graph helpers for the GitHub Actions automation.

Standard library only, so workflows need no pip install.

Two ways to get an app-only Graph token:
  * token_client_secret()  - the existing read-only backup app (CLIENT_ID / CLIENT_SECRET)
  * token_federated()      - the deploy app, via GitHub OIDC (no stored secret)
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com"
SCOPE = "https://graph.microsoft.com/.default"


def _http(method, url, headers=None, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def _token_request(tenant, form):
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = urllib.parse.urlencode(form).encode()
    try:
        _, body = _http("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, data)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token request failed ({e.code}): {e.read().decode(errors='replace')}")
    return body["access_token"]


def token_client_secret(tenant, client_id, client_secret):
    return _token_request(tenant, {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": SCOPE,
        "grant_type": "client_credentials",
    })


def jwt_claims(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def github_oidc_token(audience="api://AzureADTokenExchange"):
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise RuntimeError("No GitHub OIDC token available - the job needs 'permissions: id-token: write'")
    url += ("&" if "?" in url else "?") + "audience=" + urllib.parse.quote(audience)
    _, body = _http("GET", url, {"Authorization": f"bearer {bearer}"})
    return body["value"]


def token_federated(tenant, client_id):
    assertion = github_oidc_token()
    sub = jwt_claims(assertion).get("sub")
    try:
        token = _token_request(tenant, {
            "client_id": client_id,
            "scope": SCOPE,
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        })
        # Granted application permissions - not secret, and the first thing to
        # check when Graph answers 403.
        roles = sorted(jwt_claims(token).get("roles") or [])
        print(f"Signed in. Graph permissions in token: {', '.join(roles) or 'NONE - admin consent not granted'}")
        return token
    except RuntimeError as e:
        # The subject is not secret; printing it is the quickest way to fix a
        # federated credential whose Subject identifier doesn't match.
        print(f"::error::Federated sign-in failed. GitHub presented subject: {sub}")
        print("::error::The app registration's federated credential 'Subject identifier' must equal that value exactly.")
        raise e


class Graph:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, body=None):
        url = path if path.startswith("http") else f"{GRAPH}{path}"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        for attempt in range(6):
            try:
                return _http(method, url, headers, data)[1]
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt < 5:
                    wait = int(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                    print(f"  Graph {e.code}, retrying in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise GraphError(e.code, e.read().decode(errors="replace"), url) from None

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        return self.request("POST", path, body)

    def get_all(self, path):
        out, url = [], path
        while url:
            page = self.get(url)
            out.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
        return out


class GraphError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:500]
        super().__init__(f"Graph {status}: {msg}")
