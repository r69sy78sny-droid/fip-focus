#!/usr/bin/env python3
"""Autorisation Spotify (PKCE, sans secret). Usage : python3 auth.py <CLIENT_ID>
Ouvre le navigateur, récupère le code, crée la playlist si besoin, écrit .env.local."""
import base64, hashlib, http.server, json, os, secrets, sys, urllib.parse, urllib.request, webbrowser
CID = sys.argv[1]; RED = "http://127.0.0.1:8888/callback"
SCOPE = "playlist-modify-public playlist-modify-private"
ver = secrets.token_urlsafe(64)
chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(8)
url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(dict(
    client_id=CID, response_type="code", redirect_uri=RED, scope=SCOPE,
    code_challenge_method="S256", code_challenge=chal, state=state))
got = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got.update({k: v[0] for k, v in q.items()})
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write("<h2>C'est bon, tu peux fermer cet onglet.</h2>".encode())
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 8888), H)
print("Ouverture du navigateur… si rien ne s'ouvre :\n", url)
webbrowser.open(url)
while "code" not in got and "error" not in got: srv.handle_request()
assert got.get("state") == state and "code" in got, got
def post(u, d, h=None):
    r = urllib.request.Request(u, data=urllib.parse.urlencode(d).encode() if not h else json.dumps(d).encode(),
        headers=h or {"Content-Type": "application/x-www-form-urlencoded"}); return json.load(urllib.request.urlopen(r))
tok = post("https://accounts.spotify.com/api/token", dict(grant_type="authorization_code", code=got["code"],
    redirect_uri=RED, client_id=CID, code_verifier=ver))
A = {"Authorization": "Bearer " + tok["access_token"], "Content-Type": "application/json"}
import time
def retry(fn, n=5):
    for i in range(n):
        try: return fn()
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read()[:200]); time.sleep(3)
    raise SystemExit("échec")
me = None
body = dict(name="FIP Focus (en live)", public=True,
    description="FIP en direct, filtré pour travailler sans être distrait : instrumental, calme, sans voix au premier plan. Mise à jour automatique.")
import urllib.error
def mk():
    try: return post("https://api.spotify.com/v1/me/playlists", body, A)
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            me = json.load(urllib.request.urlopen(urllib.request.Request("https://api.spotify.com/v1/me", headers=A)))
            return post(f"https://api.spotify.com/v1/users/{me['id']}/playlists", body, A)
        raise
pl = retry(mk)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local"), "w") as f:
    f.write(f"SPOTIFY_CLIENT_ID={CID}\nSPOTIFY_REFRESH_TOKEN={tok['refresh_token']}\nSPOTIFY_PLAYLIST_ID={pl['id']}\n")
os.chmod(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local"), 0o600)
print("OK — playlist :", pl["external_urls"]["spotify"])
