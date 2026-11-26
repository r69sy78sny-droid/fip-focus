#!/usr/bin/env python3
"""FIP Focus : recopie sur Spotify les titres de FIP adaptés au travail (filtre strict).
Stdlib uniquement. État dans state.json (cache GitHub Actions)."""
import base64, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.environ.get("STATE_PATH", os.path.join(HERE, "state.json"))
CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
MAX_TRACKS = 2000
DRY = "--dry" in sys.argv

# --- Critères « strict » -------------------------------------------------
CRIT = dict(
    instrumentalness_min=0.35,  # pas de chant au premier plan
    speechiness_max=0.10,       # ni rap, ni voix parlée
    energy_max=0.65,
    loudness_min=-30.0,         # pas de morceau quasi inaudible / ambiance vide
    loudness_max=-6.0,          # pas de morceau écrasé / agressif
    tempo_min=0, tempo_max=999,   # tempo estimé trop peu fiable (doublements)
    liveness_max=0.85,          # liveness peu fiable en acoustique/ambient
    valence_min=0.0,            # valence basse = ambient/classique, pas un défaut
    dur_min_s=120, dur_max_s=720,
)

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def http(url, method="GET", data=None, headers=None, form=False, retries=3):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "fip-focus/1.0 (personal playlist filter)")
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    for i in range(retries):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(int(e.headers.get("Retry-After", "2")) + 1, 20)); continue
            if e.code >= 500 and i < retries - 1:
                time.sleep(2); continue
            raise RuntimeError(f"{method} {url} -> {e.code} {e.read()[:300]!r}")
        except urllib.error.URLError:
            if i < retries - 1: time.sleep(2); continue
            raise

def load_state():
    try:
        with open(STATE_PATH) as f: return json.load(f)
    except Exception: return {"seen": {}}

def save_state(st):
    # ne garder que ~800 décisions récentes
    items = sorted(st["seen"].items(), key=lambda kv: kv[1].get("t", 0))[-800:]
    st["seen"] = dict(items)
    with open(STATE_PATH, "w") as f: json.dump(st, f, ensure_ascii=False)

def access_token(st):
    rt = st.get("refresh_token") or os.environ["SPOTIFY_REFRESH_TOKEN"]
    j = http("https://accounts.spotify.com/api/token", "POST",
             {"grant_type": "refresh_token", "refresh_token": rt, "client_id": CLIENT_ID}, form=True)
    if j.get("refresh_token"): st["refresh_token"] = j["refresh_token"]
    return j["access_token"]

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())

def fip_steps():
    d = http("https://api.radiofrance.fr/livemeta/pull/7")
    now = time.time()
    out = [s for s in d.get("steps", {}).values()
           if s.get("embedType") == "song" and s["start"] <= now and s.get("title")]
    return sorted(out, key=lambda s: s["start"])

def find_on_spotify(tok, step):
    H = {"Authorization": f"Bearer {tok}"}
    title = step["title"]; artist = (step.get("authors") or step.get("performers") or "").split(",")[0]
    album = step.get("titreAlbum") or ""
    best, best_score = None, 0
    for q in (f'track:"{title}" artist:"{artist}"', f"{title} {artist}"):
        try:
            j = http("https://api.spotify.com/v1/search?" + urllib.parse.urlencode(
                {"q": q, "type": "track", "limit": 10, "market": "FR"}), headers=H)
        except RuntimeError as e:
            log("recherche échouée:", e); continue
        for t in j.get("tracks", {}).get("items", []):
            ta = " ".join(a["name"] for a in t["artists"])
            sc = 0
            if norm(t["name"]) == norm(title): sc += 2
            elif norm(title) in norm(t["name"]) or norm(t["name"]) in norm(title): sc += 1
            if norm(artist) and norm(artist) in norm(ta): sc += 2
            if album and norm(album) == norm(t["album"]["name"]): sc += 1
            if sc > best_score: best, best_score = t, sc
        if best_score >= 4: break
    return best if best_score >= 3 else None

def audio_features(spotify_id):
    try:
        j = http(f"https://api.reccobeats.com/v1/audio-features?ids={spotify_id}", headers={"Accept": "application/json"})
        c = j.get("content") or []
        return c[0] if c else None
    except Exception as e:
        log("reccobeats:", e); return None

def judge(f, dur_s):
    """Retourne (ok, raison)."""
    c = CRIT
    checks = [
        (f["instrumentalness"] >= c["instrumentalness_min"], f"instrumental {f['instrumentalness']:.2f}"),
        (f["speechiness"] <= c["speechiness_max"], f"parole {f['speechiness']:.2f}"),
        (f["energy"] <= c["energy_max"], f"énergie {f['energy']:.2f}"),
        (c["loudness_min"] <= f["loudness"] <= c["loudness_max"], f"volume {f['loudness']:.1f} dB"),
        (c["tempo_min"] <= f["tempo"] <= c["tempo_max"], f"tempo {f['tempo']:.0f}"),
        (f["liveness"] <= c["liveness_max"], f"live {f['liveness']:.2f}"),
        (f["valence"] >= c["valence_min"], f"valence {f['valence']:.2f}"),
        (c["dur_min_s"] <= dur_s <= c["dur_max_s"], f"durée {dur_s:.0f}s"),
    ]
    bad = [r for ok, r in checks if not ok]
    return (not bad), ("; ".join(bad) if bad else "ok")


# --- Secours 2 : AcousticBrainz (via MusicBrainz) ------------------------
_last_mb = [0.0]
def mb_get(url):
    wait = 1.1 - (time.time() - _last_mb[0])
    if wait > 0: time.sleep(wait)
    _last_mb[0] = time.time()
    return http(url)

def acousticbrainz(artist, title):
    """Retourne un dict de classifieurs haut niveau, ou None (base figée en 2022)."""
    try:
        q = f'recording:"{title}" AND artist:"{artist}"'
        j = mb_get("https://musicbrainz.org/ws/2/recording?" + urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 5}))
        for r in j.get("recordings", []):
            if r.get("score", 0) < 90: continue
            try:
                hl = http(f"https://acousticbrainz.org/api/v1/{r['id']}/high-level")["highlevel"]
                return {k: hl[k]["all"] for k in ("voice_instrumental", "mood_aggressive", "mood_party", "mood_relaxed") if k in hl}
            except Exception:
                continue
    except Exception as e:
        log("acousticbrainz:", e)
    return None

def judge_ab(a):
    inst = a.get("voice_instrumental", {}).get("instrumental", 0)
    c = [(inst >= 0.65, f"instrumental {inst:.2f}"),
         (a.get("mood_aggressive", {}).get("not_aggressive", 1) >= 0.7, "agressif"),
         ]  # mood_party écarté : peu fiable (Brian Eno classé « festif »)
    bad = [r for ok, r in c if not ok]
    return (not bad), ("AB " + ("; ".join(bad) if bad else "ok"))

# --- Secours 3 : tags Last.fm (clé LASTFM_API_KEY facultative) -----------
OK_TAGS = {"instrumental", "ambient", "classical", "piano", "neoclassical", "post-classical", "downtempo",
           "chillout", "chill", "jazz", "cool jazz", "bossa nova", "lounge", "electronica", "minimal",
           "soundtrack", "modern classical", "drone", "new age", "easy listening", "trip-hop"}
BAD_TAGS = {"rap", "hip-hop", "hip hop", "metal", "punk", "hardcore", "spoken word", "chanson", "chanson francaise",
            "singer-songwriter", "pop", "rock", "indie rock", "hard rock", "reggae", "ska", "dance", "house", "techno"}

def lastfm_tags(artist, title):
    key = os.environ.get("LASTFM_API_KEY")
    if not key: return None
    try:
        j = http("https://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode(
            {"method": "track.getTopTags", "artist": artist, "track": title, "api_key": key, "format": "json", "autocorrect": 1}))
        tags = [t["name"].lower() for t in j.get("toptags", {}).get("tag", [])[:8]]
        if tags: return tags
        j = http("https://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode(
            {"method": "artist.getTopTags", "artist": artist, "api_key": key, "format": "json", "autocorrect": 1}))
        return [t["name"].lower() for t in j.get("toptags", {}).get("tag", [])[:8]] or None
    except Exception as e:
        log("lastfm:", e); return None

def judge_tags(tags):
    top = tags[:5]
    bad = [t for t in top if t in BAD_TAGS]
    good = [t for t in top if t in OK_TAGS]
    if bad: return False, "tags: " + ",".join(bad)
    if "instrumental" in top or len(good) >= 2: return True, "tags ok: " + ",".join(good)
    return False, "tags insuffisants: " + ",".join(top)

def playlist_uris(tok, pid, limit=100):
    H = {"Authorization": f"Bearer {tok}"}
    for ep in ("items", "tracks"):
        try:
            j = http(f"https://api.spotify.com/v1/playlists/{pid}/{ep}?limit={limit}&fields=total,items(item(uri),track(uri))", headers=H)
            uris = []
            for it in j.get("items", []):
                t = it.get("item") or it.get("track")
                if t: uris.append(t["uri"])
            return uris, j.get("total", len(uris)), ep
        except RuntimeError as e:
            last = e
    raise last

def add_top(tok, pid, uri, ep):
    H = {"Authorization": f"Bearer {tok}"}
    for e in (ep, "items", "tracks"):
        try:
            return http(f"https://api.spotify.com/v1/playlists/{pid}/{e}", "POST", {"uris": [uri], "position": 0}, headers=H)
        except RuntimeError as err:
            last = err
    raise last

def trim(tok, pid, total, ep):
    if total <= MAX_TRACKS: return
    H = {"Authorization": f"Bearer {tok}"}
    extra = total - MAX_TRACKS
    j = http(f"https://api.spotify.com/v1/playlists/{pid}/{ep}?limit={min(extra,100)}&offset={MAX_TRACKS}&fields=items(item(uri),track(uri))", headers=H)
    uris = [(i.get("item") or i.get("track"))["uri"] for i in j.get("items", []) if (i.get("item") or i.get("track"))]
    if uris:
        http(f"https://api.spotify.com/v1/playlists/{pid}/{ep}", "DELETE", {"items": [{"uri": u} for u in uris]} if ep == "items" else {"tracks": [{"uri": u} for u in uris]}, headers=H)
        log(f"{len(uris)} anciens titres retirés")

def main():
    pid = os.environ["SPOTIFY_PLAYLIST_ID"]
    st = load_state()
    tok = access_token(st)
    steps = fip_steps()
    new = [s for s in steps if s["stepId"] not in st["seen"]]
    log(f"{len(steps)} titres FIP récents, {len(new)} nouveaux")
    if not new:
        save_state(st); return
    present, total, ep = playlist_uris(tok, pid)
    for s in new:
        label = f"{s.get('authors')} — {s['title']}"
        rec = {"t": s["start"], "label": label}
        tr = find_on_spotify(tok, s)
        if not tr:
            rec.update(ok=False, why="introuvable sur Spotify")
        else:
            dur = tr["duration_ms"] / 1000
            f = audio_features(tr["id"])
            artist = (s.get("authors") or s.get("performers") or "").split(",")[0]
            if f:
                ok, why = judge(f, dur)
            else:
                ok, why = False, "aucune donnée"
                dmin, dmax = CRIT["dur_min_s"], CRIT["dur_max_s"]
                if not (dmin <= dur <= dmax):
                    why = f"durée {dur:.0f}s"
                else:
                    ab = acousticbrainz(artist, s["title"])
                    if ab: ok, why = judge_ab(ab)
                    else:
                        tg = lastfm_tags(artist, s["title"])
                        if tg: ok, why = judge_tags(tg)
            rec.update(ok=ok, why=why)
            if ok and tr["uri"] in present: rec.update(ok=False, why="déjà dans la playlist")
            elif ok and not DRY:
                add_top(tok, pid, tr["uri"], ep); present.insert(0, tr["uri"]); total += 1
            rec["uri"] = tr["uri"]
        st["seen"][s["stepId"]] = rec
        log(("AJOUT " if rec["ok"] else "refus ") + label, "|", rec["why"])
    if not DRY: trim(tok, pid, total, ep)
    save_state(st)

if __name__ == "__main__":
    if "--loop" in sys.argv:
        # --loop <intervalle_s> <durée_s> : un passage toutes les <intervalle_s> secondes pendant <durée_s>
        i = sys.argv.index("--loop"); every, span = int(sys.argv[i+1]), int(sys.argv[i+2])
        end = time.time() + span
        while time.time() < end:
            t0 = time.time()
            try: main()
            except Exception as e: log("passage échoué:", e)
            time.sleep(max(5, every - (time.time() - t0)))
    else:
        main()
