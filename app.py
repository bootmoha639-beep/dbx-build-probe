import http.server, json, glob, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ev = "NOT FOUND"
        for pat in ["**/zzbuildprobe/build_evidence.py", "/opt/**/zzbuildprobe/build_evidence.py",
                    "/app/**/zzbuildprobe/build_evidence.py", "/home/**/zzbuildprobe/build_evidence.py"]:
            try:
                hits = glob.glob(pat, recursive=True)
                if hits:
                    ns = {}
                    exec(open(hits[0]).read(), ns)
                    ev = json.dumps(ns.get("EVIDENCE", {}))
                    break
            except Exception:
                pass
        body = ("APP_BUILD_EVIDENCE: " + ev).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
http.server.HTTPServer(("0.0.0.0", int(os.environ.get("DATABRICKS_APP_PORT", "8080"))), H).serve_forever()
