"""CDW 365 Baseline - runner broker.

Starts one Container Apps job execution per deployment and reports its result.

POST /api/runs        body: runner payload (tenantId, tokens, selected, config, branch)
                      -> 202 {"runId": "..."}
GET  /api/runs/{id}   -> {"status": "Running" | "Succeeded" | "Failed", "results": [...]}

Callers authenticate with the ID token the deployer site already holds (issued to the
site's multi-tenant app registration). The broker never sees anything it doesn't pass
straight to the runner: the payload is written to a blob the runner deletes on read.
"""
import datetime
import json
import logging
import os
import re
import uuid

import azure.functions as func
import jwt
import requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

SPA_CLIENT_ID = os.environ["SPA_CLIENT_ID"]
JOB_ID = os.environ["RUNNER_JOB_ID"]
IMAGE = os.environ["RUNNER_IMAGE"]
STORAGE_URL = os.environ["RUNNER_STORAGE_URL"]
RUNNER_CLIENT_ID = os.environ["RUNNER_IDENTITY_CLIENT_ID"]
MAX_RUNNING = int(os.environ.get("MAX_RUNNING", "5"))
ARM = "https://management.azure.com"
API = "api-version=2024-03-01"
WORKLOADS = {"exo", "scc", "spo", "teams", "mdca"}
RUN_ID = re.compile(r"^[0-9a-f]{32}$")

_credential = DefaultAzureCredential()
_jwks = jwt.PyJWKClient("https://login.microsoftonline.com/common/discovery/v2.0/keys", cache_keys=True)
_blobs = BlobServiceClient(STORAGE_URL, credential=_credential)


def _json(status, body):
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json")


def _caller(req):
    """Validated claims of the caller's ID token, or None."""
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        key = _jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=SPA_CLIENT_ID,
                            options={"require": ["exp", "iss", "tid", "aud"]})
    except jwt.PyJWTError as e:
        logging.warning("Rejected token: %s", e)
        return None
    if claims["iss"] != f"https://login.microsoftonline.com/{claims['tid']}/v2.0":
        return None
    return claims


def _arm(method, path, body=None):
    token = _credential.get_token(f"{ARM}/.default").token
    r = requests.request(method, f"{ARM}{path}", json=body, timeout=30,
                         headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json() if r.content else {}


def _running():
    executions = _arm("GET", f"{JOB_ID}/executions?{API}").get("value", [])
    return sum(1 for e in executions if e.get("properties", {}).get("status") in ("Running", "Processing"))


@app.route(route="runs", methods=["POST"])
def start_run(req: func.HttpRequest) -> func.HttpResponse:
    caller = _caller(req)
    if not caller:
        return _json(401, {"error": "Sign in to the deployer again."})
    try:
        payload = req.get_json()
    except ValueError:
        return _json(400, {"error": "Body must be JSON."})

    selected = payload.get("selected") or []
    if not selected or not all(s.get("workload") in WORKLOADS and s.get("path") for s in selected):
        return _json(400, {"error": "Nothing to run, or an unknown workload."})
    # Tokens must belong to the tenant the consultant is signed in to.
    if payload.get("tenantGuid") != caller["tid"]:
        return _json(403, {"error": "The payload is for a different tenant from your sign-in."})
    if _running() >= MAX_RUNNING:
        return _json(429, {"error": "The runner is busy. Try again in a few minutes."})

    run_id = uuid.uuid4().hex
    payload["runId"] = run_id
    _blobs.get_blob_client("payloads", f"{run_id}.json").upload_blob(json.dumps(payload), overwrite=True)
    owner = {"tid": caller["tid"], "oid": caller.get("oid"), "started": datetime.datetime.utcnow().isoformat() + "Z"}

    env = [
        {"name": "RUN_ID", "value": run_id},
        {"name": "RUNNER_STORAGE_URL", "value": STORAGE_URL},
        {"name": "AZURE_CLIENT_ID", "value": RUNNER_CLIENT_ID},
    ]
    execution = _arm("POST", f"{JOB_ID}/start?{API}", {
        "containers": [{"name": "runner", "image": IMAGE, "env": env,
                        "resources": {"cpu": 1.0, "memory": "2Gi"}}]})
    owner["execution"] = execution.get("name")
    _blobs.get_blob_client("results", f"{run_id}.owner.json").upload_blob(json.dumps(owner), overwrite=True)
    logging.info("Run %s started for tenant %s (%d policies)", run_id, caller["tid"], len(selected))
    return _json(202, {"runId": run_id})


@app.route(route="runs/{run_id}", methods=["GET"])
def get_run(req: func.HttpRequest) -> func.HttpResponse:
    caller = _caller(req)
    if not caller:
        return _json(401, {"error": "Sign in to the deployer again."})
    run_id = req.route_params.get("run_id", "")
    if not RUN_ID.match(run_id):
        return _json(400, {"error": "Bad run id."})
    try:
        owner = json.loads(_blobs.get_blob_client("results", f"{run_id}.owner.json").download_blob().readall())
    except Exception:
        return _json(404, {"error": "Run not found."})
    if owner["tid"] != caller["tid"]:
        return _json(404, {"error": "Run not found."})

    result_blob = _blobs.get_blob_client("results", f"{run_id}.json")
    if result_blob.exists():
        return _json(200, {"status": "Succeeded", "results": json.loads(result_blob.download_blob().readall())})

    status = "Running"
    if owner.get("execution"):
        try:
            status = _arm("GET", f"{JOB_ID}/executions/{owner['execution']}?{API}").get("properties", {}).get("status", status)
        except requests.HTTPError:
            pass
    if status in ("Succeeded", "Failed", "Stopped", "Degraded"):
        # Finished without writing results: the runner crashed before it could report.
        return _json(200, {"status": "Failed", "results": [], "error": f"Runner execution {status.lower()} without results."})
    return _json(200, {"status": "Running"})
