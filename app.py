import os, json
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify
app = Flask(__name__)

@app.route("/")
def home():
    return "env-probe ok"

@app.route("/env")
def env():
    out = {}
    # 1. All environment keys
    out["env_keys"] = sorted(os.environ.keys())
    # 2. Databricks-specific env values (not secret values)
    for k in ["DATABRICKS_CLIENT_ID", "DATABRICKS_HOST", "DATABRICKS_WORKSPACE_ID"]:
        out[k] = os.environ.get(k, "NOT_SET")
    # 3. Check if CLIENT_SECRET is present (don't print value)
    out["has_client_secret"] = bool(os.environ.get("DATABRICKS_CLIENT_SECRET"))
    # 4. What identity does the app run as?
    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        out["identity"] = {"user": me.user_name, "id": me.id}
    except Exception as e:
        out["identity_err"] = str(e)[:200]
    # 5. Can it access other users' data?
    try:
        out["ws_users"] = [o.path for o in w.workspace.list("/Users")][:20]
    except Exception as e:
        out["ws_users_err"] = str(e)[:150]
    # 6. Secret scopes accessible to the app SP
    try:
        out["secret_scopes"] = [s.name for s in w.secrets.list_scopes()]
    except Exception as e:
        out["scopes_err"] = str(e)[:150]
    # 7. Can it read secrets from any scope?
    try:
        # Try the h1_test scope (the workspace group scope)
        keys = [k.key for k in w.secrets.list_secrets("h1_test")]
        out["h1_test_keys"] = keys[:10]
        if keys:
            s = w.secrets.get_secret("h1_test", keys[0])
            out["h1_test_read"] = {"ok": True, "value_len": len(s.value) if s.value else 0}
    except Exception as e:
        out["h1_test_err"] = str(e)[:150]
    return jsonify(out)

@app.route("/obo")
def obo():
    # Try to get a token for a DIFFERENT principal using the app's OAuth credentials
    import requests
    out = {}
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    host = os.environ.get("DATABRICKS_HOST", "")
    if not all([client_id, client_secret, host]):
        return jsonify({"error": "missing credentials"})

    # Token exchange grant
    try:
        r = requests.post(f"{host}/oidc/v1/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": client_id,
            "client_secret": client_secret,
            "subject_token": client_secret,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"
        }, timeout=10)
        out["token_exchange"] = {"status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        out["token_exchange_err"] = str(e)[:150]

    # Client credentials grant (for the app itself)
    try:
        r = requests.post(f"{host}/oidc/v1/token", data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }, timeout=10)
        out["client_credentials"] = {"status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        out["cc_err"] = str(e)[:150]

    return jsonify(out)
