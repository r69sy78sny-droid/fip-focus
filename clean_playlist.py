#!/usr/bin/env python3
"""Nettoie la playlist FIP Focus : passe chaque titre au filtre (ReccoBeats) et retire les refusés.
Usage : python3 clean_playlist.py          -> simulation (rien n'est retiré)
        python3 clean_playlist.py --apply  -> retire les refusés"""
import os, sys, re, time
import fip_focus as F
APPLY = "--apply" in sys.argv
pid = os.environ["SPOTIFY_PLAYLIST_ID"]
st = {}; tok = F.access_token(st); H = {"Authorization": f"Bearer {tok}"}

tracks, off, ep = [], 0, "items"
while True:
    try:
        j = F.http(f"https://api.spotify.com/v1/playlists/{pid}/{ep}?limit=50&offset={off}&fields=total,items(item(id,uri,name,duration_ms,artists(name)),track(id,uri,name,duration_ms,artists(name)))", headers=H)
    except RuntimeError:
        if ep == "items": ep = "tracks"; continue
        raise
    for it in j["items"]:
        t = it.get("item") or it.get("track")
        if t and t.get("id"): tracks.append(t)
    off += 50
    if off >= j["total"]: break
F.log(f"{len(tracks)} titres lus dans la playlist")

feats = {}
ids = [t["id"] for t in tracks]
for i in range(0, len(ids), 40):
    chunk = ids[i:i+40]
    try:
        j = F.http("https://api.reccobeats.com/v1/audio-features?ids=" + ",".join(chunk), headers={"Accept": "application/json"})
        for c in j.get("content", []):
            m = re.search(r"/track/([A-Za-z0-9]+)", c.get("href", ""))
            if m: feats[m.group(1)] = c
    except Exception as e:
        F.log("reccobeats lot échoué:", e)
    time.sleep(0.3)
F.log(f"{len(feats)} titres avec données audio")

keep, drop, nodata = [], [], []
for t in tracks:
    f = feats.get(t["id"])
    if not f: nodata.append(t); continue
    ok, why = F.judge(f, t["duration_ms"] / 1000)
    (keep if ok else drop).append((t, why))
F.log(f"gardés {len(keep)} | refusés {len(drop)} | sans données {len(nodata)}")
for t, _ in keep[:15]: print("  +", t["artists"][0]["name"], "—", t["name"])

if APPLY:
    rm = [t["uri"] for t, _ in drop] + [t["uri"] for t in nodata]
    key = "items" if ep == "items" else "tracks"
    for i in range(0, len(rm), 100):
        F.http(f"https://api.spotify.com/v1/playlists/{pid}/{ep}", "DELETE",
               {key: [{"uri": u} for u in rm[i:i+100]]}, headers=H)
    F.log(f"{len(rm)} titres retirés ; il reste {len(keep)} titres")
else:
    F.log("Simulation : relance avec --apply pour retirer les refusés.")
