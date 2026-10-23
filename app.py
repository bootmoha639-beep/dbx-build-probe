import os
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify
app = Flask(__name__)

@app.route("/")
def home():
    return "h3-app ok"

@app.route("/probe4")
def probe4():
    w = WorkspaceClient()
    out = {}
    try:
        c = w.workspace.export("/Users/m4tr1x4g3nt47+user1@wearehackerone.com/h3_priv_nb", format="SOURCE")
        txt = c.content if isinstance(c.content, str) else ""
        if not txt:
            import base64 as b64mod
            txt = b64mod.b64decode(c.content).decode("utf-8", "replace")
        out["own_priv_read"] = {"ok": True, "contains_marker": "H3-APPSP-READ" in txt, "len": len(txt)}
    except Exception as e:
        out["own_priv_read"] = {"ok": False, "err": repr(e)[:250]}
    return jsonify(out)
