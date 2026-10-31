"""Local preview server that mimics this site's vercel.json (cleanUrls, redirects, headers).
Usage: python3 tools/serve.py [port]   (run from the site folder)"""
import http.server, json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "vercel.json")))

def rx(src):  # vercel path-to-regexp subset: literal paths and (.*) groups
    return re.compile("^" + src + "$")

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def send_head(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        for r in CFG.get("redirects", []):
            if rx(r["source"]).match(path):
                self.send_response(308 if r.get("permanent") else 307); self.send_header("Location", r["destination"]); self.end_headers(); return None
        if path.endswith(".html"):
            self.send_response(308); self.send_header("Location", path[:-5] if path != "/index.html" else "/"); self.end_headers(); return None
        if path != "/" and path.endswith("/"):
            self.send_response(308); self.send_header("Location", path.rstrip("/")); self.end_headers(); return None
        fs = os.path.join(ROOT, path.lstrip("/"))
        if path != "/" and not os.path.exists(fs) and os.path.exists(fs + ".html"):
            self.path = path + ".html"
        elif not os.path.exists(fs):
            self.path = "/404.html"; self._nf = True
        return super().send_head()
    def send_response(self, code, msg=None):
        if getattr(self, "_nf", False) and code == 200: code = 404
        super().send_response(code, msg)
    def end_headers(self):
        path = self.path.split("?", 1)[0]
        orig = getattr(self, "_orig", path)
        for h in CFG.get("headers", []):
            if rx(h["source"]).match(orig):
                for kv in h["headers"]: self.send_header(kv["key"], kv["value"])
        super().end_headers()
    def parse_request(self):
        ok = super().parse_request(); self._orig = self.path.split("?", 1)[0]; return ok
    def log_message(self, *a): pass

port = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
http.server.ThreadingHTTPServer.allow_reuse_address = True
print(f"Serving {ROOT} on http://localhost:{port}"); http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
