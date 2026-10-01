import http.server, json, glob
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ev = "NOT FOUND"
        hits = glob.glob("**/zzbuildprobe/build_evidence.py", recursive=True)
        hits += glob.glob("/opt/**/zzbuildprobe/build_evidence.py", recursive=True)
        hits += glob.glob("**/site-packages/zzbuildprobe/build_evidence.py", recursive=True)
        if hits:
            ns = {}
            exec(open(hits[0]).read(), ns)
            ev = json.dumps(ns.get("EVIDENCE", {}))
        body = ("APP BUILD EVIDENCE: " + ev).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
import os
http.server.HTTPServer(("0.0.0.0", int(os.environ.get("DATABRICKS_APP_PORT", "8080"))), H).serve_forever()
