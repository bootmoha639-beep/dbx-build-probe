
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
        try:
            out["ws_users"] = [o.path for o in w.workspace.list("/Users")][:30]
        except Exception as e:
            out["ws_users_err"] = str(e)[:150]
        try:
            out["catalogs"] = [c.name for c in w.catalogs.list()]
        except Exception as e:
            out["catalogs_err"] = str(e)[:150]
    except Exception as e:
        out["sdk_err"] = str(e)[:200]
    return jsonify(out)
