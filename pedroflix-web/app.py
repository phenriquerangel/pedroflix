import os
import requests
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string
from urllib.parse import quote

app = Flask(__name__)

RADARR_URL     = os.getenv("RADARR_URL",     "http://radarr.pedroflix.svc.cluster.local:7878")
SONARR_URL     = os.getenv("SONARR_URL",     "http://sonarr.pedroflix.svc.cluster.local:8989")
BAZARR_URL     = os.getenv("BAZARR_URL",     "http://bazarr.pedroflix.svc.cluster.local:6767")
PROWLARR_URL   = os.getenv("PROWLARR_URL",   "http://prowlarr.pedroflix.svc.cluster.local:9696")
QBITTORRENT_URL= os.getenv("QBITTORRENT_URL","http://qbittorrent.pedroflix.svc.cluster.local:8080")
JELLYFIN_URL   = os.getenv("JELLYFIN_URL",   "http://jellyfin.pedroflix.svc.cluster.local:8096")

RADARR_KEY   = os.getenv("RADARR_KEY",   "")
SONARR_KEY   = os.getenv("SONARR_KEY",   "")
BAZARR_KEY   = os.getenv("BAZARR_KEY",   "")
PROWLARR_KEY = os.getenv("PROWLARR_KEY", "")
JELLYFIN_KEY = os.getenv("JELLYFIN_KEY", "")
QBT_USER     = os.getenv("QBT_USER",    "admin")
QBT_PASS     = os.getenv("QBT_PASS",    "pedroflix")

RADARR_PROFILE_ID = 7
SONARR_PROFILE_ID = 1
RADARR_BLURAY = {"Bluray-720p", "Bluray-1080p"}
SONARR_BLURAY = {"HDTV-1080p", "Bluray-1080p", "Bluray-1080p Remux"}


def radarr(method, path, **kwargs):
    headers = {"X-Api-Key": RADARR_KEY, "Content-Type": "application/json"}
    return requests.request(method, f"{RADARR_URL}{path}", headers=headers, timeout=15, **kwargs)


def sonarr(method, path, **kwargs):
    headers = {"X-Api-Key": SONARR_KEY, "Content-Type": "application/json"}
    return requests.request(method, f"{SONARR_URL}{path}", headers=headers, timeout=15, **kwargs)


def bazarr_get(path):
    return requests.get(f"{BAZARR_URL}{path}", headers={"X-API-KEY": BAZARR_KEY}, timeout=15)


def bazarr_post(path, data):
    return requests.post(f"{BAZARR_URL}{path}", json=data, headers={"X-API-KEY": BAZARR_KEY}, timeout=15)


def qbt_request(method, path, **kwargs):
    s = requests.Session()
    s.post(f"{QBITTORRENT_URL}/api/v2/auth/login",
           data={"username": QBT_USER, "password": QBT_PASS}, timeout=10)
    return s.request(method, f"{QBITTORRENT_URL}{path}", timeout=15, **kwargs)


# ── SEARCH ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    type_ = request.args.get("type", "all")
    if len(q) < 2:
        return jsonify({"movies": [], "series": []})
    result = {"movies": [], "series": []}
    if type_ in ("all", "movie"):
        try:
            r = radarr("GET", f"/api/v3/movie/lookup?term={quote(q)}")
            if r.ok:
                for m in r.json()[:20]:
                    result["movies"].append({
                        "tmdbId":   m.get("tmdbId"),
                        "title":    m.get("title", ""),
                        "year":     m.get("year"),
                        "overview": m.get("overview", ""),
                        "poster":   m.get("remotePoster", ""),
                        "rating":   round((m.get("ratings") or {}).get("tmdb", {}).get("value", 0), 1),
                        "inLibrary":(m.get("id") or 0) > 0,
                        "genres":   (m.get("genres") or [])[:3],
                        "runtime":  m.get("runtime", 0),
                    })
        except Exception:
            pass
    if type_ in ("all", "series"):
        try:
            r = sonarr("GET", f"/api/v3/series/lookup?term={quote(q)}")
            if r.ok:
                for s in r.json()[:20]:
                    result["series"].append({
                        "tvdbId":   s.get("tvdbId"),
                        "title":    s.get("title", ""),
                        "year":     s.get("year"),
                        "overview": s.get("overview", ""),
                        "poster":   s.get("remotePoster", ""),
                        "rating":   round((s.get("ratings") or {}).get("value", 0), 1),
                        "inLibrary":(s.get("id") or 0) > 0,
                        "seasons":  len(s.get("seasons") or []),
                        "status":   s.get("status", ""),
                        "genres":   (s.get("genres") or [])[:3],
                    })
        except Exception:
            pass
    return jsonify(result)


@app.route("/api/add", methods=["POST"])
def add():
    data  = request.json or {}
    type_ = data.get("type")
    if type_ == "movie":
        try:
            profiles     = radarr("GET", "/api/v3/qualityprofile").json()
            root_folders = radarr("GET", "/api/v3/rootfolder").json()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        payload = {
            "tmdbId":           data["tmdbId"],
            "title":            data["title"],
            "year":             data.get("year", 0),
            "qualityProfileId": profiles[0]["id"] if profiles else 1,
            "rootFolderPath":   root_folders[0]["path"] if root_folders else "/movies",
            "monitored":        True,
            "addOptions":       {"searchForMovie": True},
        }
        r = radarr("POST", "/api/v3/movie", json=payload)
        return jsonify({"ok": r.ok}) if r.ok else jsonify({"ok": False, "error": r.text}), 400
    elif type_ == "series":
        try:
            profiles     = sonarr("GET", "/api/v3/qualityprofile").json()
            root_folders = sonarr("GET", "/api/v3/rootfolder").json()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        payload = {
            "tvdbId":           data["tvdbId"],
            "title":            data["title"],
            "year":             data.get("year", 0),
            "qualityProfileId": profiles[0]["id"] if profiles else 1,
            "rootFolderPath":   root_folders[0]["path"] if root_folders else "/tv",
            "monitored":        True,
            "seasonFolder":     True,
            "seasons":          [],
            "addOptions":       {"searchForMissingEpisodes": True},
        }
        r = sonarr("POST", "/api/v3/series", json=payload)
        return jsonify({"ok": r.ok}) if r.ok else jsonify({"ok": False, "error": r.text}), 400
    return jsonify({"ok": False, "error": "tipo inválido"}), 400


# ── QUEUE / HISTORY ──────────────────────────────────────────────────────────

@app.route("/api/queue")
def queue():
    items = []
    try:
        r = radarr("GET", "/api/v3/queue?pageSize=50&includeMovie=true")
        if r.ok:
            for rec in r.json().get("records", []):
                size     = rec.get("size") or 0
                sizeleft = rec.get("sizeleft") or 0
                progress = round((1 - sizeleft / size) * 100, 1) if size > 0 else 0
                movie    = rec.get("movie") or {}
                items.append({
                    "id":                  rec.get("id"),
                    "downloadId":          rec.get("downloadId", ""),
                    "type":                "movie",
                    "title":               movie.get("title", "?"),
                    "year":                movie.get("year"),
                    "status":              rec.get("status", ""),
                    "trackedDownloadState":rec.get("trackedDownloadState", ""),
                    "progress":            progress,
                    "sizeMB":              round(size / 1024 / 1024),
                    "timeleft":            rec.get("timeleft", ""),
                    "quality":             (rec.get("quality") or {}).get("quality", {}).get("name", ""),
                    "errorMessage":        rec.get("errorMessage", ""),
                })
    except Exception:
        pass
    try:
        r = sonarr("GET", "/api/v3/queue?pageSize=50&includeSeries=true&includeEpisode=true")
        if r.ok:
            for rec in r.json().get("records", []):
                size     = rec.get("size") or 0
                sizeleft = rec.get("sizeleft") or 0
                progress = round((1 - sizeleft / size) * 100, 1) if size > 0 else 0
                ep       = rec.get("episode") or {}
                series   = rec.get("series") or {}
                ep_info  = f"S{ep.get('seasonNumber',0):02d}E{ep.get('episodeNumber',0):02d}" if ep else ""
                items.append({
                    "id":                  rec.get("id"),
                    "downloadId":          rec.get("downloadId", ""),
                    "type":                "series",
                    "title":               series.get("title", "?"),
                    "episode":             ep_info,
                    "epTitle":             ep.get("title", "") if ep else "",
                    "year":                series.get("year"),
                    "status":              rec.get("status", ""),
                    "trackedDownloadState":rec.get("trackedDownloadState", ""),
                    "progress":            progress,
                    "sizeMB":              round(size / 1024 / 1024),
                    "timeleft":            rec.get("timeleft", ""),
                    "quality":             (rec.get("quality") or {}).get("quality", {}).get("name", ""),
                    "errorMessage":        rec.get("errorMessage", ""),
                })
    except Exception:
        pass
    return jsonify(items)


@app.route("/api/history")
def history():
    items             = []
    radarr_ids        = []
    sonarr_series_ids = set()
    try:
        r = radarr("GET", "/api/v3/history?pageSize=30&sortKey=date&sortDirection=descending&includeMovie=true")
        if r.ok:
            for rec in r.json().get("records", []):
                if rec.get("eventType") != "downloadFolderImported":
                    continue
                movie = rec.get("movie") or {}
                mid   = movie.get("id")
                items.append({
                    "type":             "movie",
                    "title":            movie.get("title", "?"),
                    "year":             movie.get("year"),
                    "date":             rec.get("date", ""),
                    "quality":          (rec.get("quality") or {}).get("quality", {}).get("name", ""),
                    "radarrId":         mid,
                    "subtitles":        [],
                    "missingSubtitles": [],
                })
                if mid:
                    radarr_ids.append(mid)
    except Exception:
        pass
    try:
        r = sonarr("GET", "/api/v3/history?pageSize=30&sortKey=date&sortDirection=descending&includeSeries=true&includeEpisode=true")
        if r.ok:
            for rec in r.json().get("records", []):
                if rec.get("eventType") != "downloadFolderImported":
                    continue
                series  = rec.get("series") or {}
                ep      = rec.get("episode") or {}
                sid     = series.get("id")
                eid     = ep.get("id") if ep else None
                ep_info = f"S{ep.get('seasonNumber',0):02d}E{ep.get('episodeNumber',0):02d}" if ep else ""
                items.append({
                    "type":             "series",
                    "title":            series.get("title", "?"),
                    "episode":          ep_info,
                    "epTitle":          ep.get("title", "") if ep else "",
                    "year":             series.get("year"),
                    "date":             rec.get("date", ""),
                    "quality":          (rec.get("quality") or {}).get("quality", {}).get("name", ""),
                    "sonarrSeriesId":   sid,
                    "sonarrEpisodeId":  eid,
                    "subtitles":        [],
                    "missingSubtitles": [],
                })
                if sid:
                    sonarr_series_ids.add(sid)
    except Exception:
        pass
    if BAZARR_KEY:
        try:
            if radarr_ids:
                qs = "&".join(f"radarrid[]={i}" for i in radarr_ids)
                r  = bazarr_get(f"/api/movies?{qs}&start=0&length=50")
                if r.ok:
                    bz = {m.get("radarrId"): m for m in r.json().get("data", [])}
                    for item in items:
                        if item["type"] == "movie" and item.get("radarrId") in bz:
                            item["subtitles"]        = bz[item["radarrId"]].get("subtitles") or []
                            item["missingSubtitles"] = bz[item["radarrId"]].get("missing_subtitles") or []
        except Exception:
            pass
        try:
            for sid in sonarr_series_ids:
                r = bazarr_get(f"/api/episodes?seriesid[]={sid}&start=0&length=200")
                if r.ok:
                    ep_map = {e.get("sonarrEpisodeId"): e for e in r.json().get("data", [])}
                    for item in items:
                        if item["type"] == "series" and item.get("sonarrSeriesId") == sid:
                            bze = ep_map.get(item.get("sonarrEpisodeId"))
                            if bze:
                                item["subtitles"]        = bze.get("subtitles") or []
                                item["missingSubtitles"] = bze.get("missing_subtitles") or []
        except Exception:
            pass
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return jsonify(items)


# ── LIBRARY ──────────────────────────────────────────────────────────────────

@app.route("/api/library/series")
def library_series():
    try:
        r = sonarr("GET", "/api/v3/series")
        if not r.ok:
            return jsonify([])
        result = []
        for s in r.json():
            seasons = []
            for season in sorted(s.get("seasons", []), key=lambda x: x.get("seasonNumber", 0)):
                sn = season.get("seasonNumber", 0)
                if sn == 0:
                    continue
                stats  = season.get("statistics") or {}
                total  = stats.get("totalEpisodeCount", 0)
                has    = stats.get("episodeFileCount", 0)
                status = "full" if (total > 0 and has >= total) else "partial" if has > 0 else "empty"
                seasons.append({"number": sn, "monitored": season.get("monitored", False),
                                "total": total, "hasFiles": has, "status": status})
            stats_s = s.get("statistics") or {}
            result.append({"id": s.get("id"), "title": s.get("title", "?"), "year": s.get("year"),
                           "status": s.get("status", ""), "seasons": seasons,
                           "sizeOnDiskMB": round(stats_s.get("sizeOnDisk", 0) / 1024 / 1024)})
        result.sort(key=lambda x: x["title"].lower())
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/library/movies")
def library_movies():
    try:
        r = radarr("GET", "/api/v3/movie")
        if not r.ok:
            return jsonify([])
        result = []
        for m in r.json():
            mf = m.get("movieFile") or {}
            result.append({
                "id":       m.get("id"),
                "title":    m.get("title", "?"),
                "year":     m.get("year"),
                "hasFile":  m.get("hasFile", False),
                "monitored":m.get("monitored", True),
                "quality":  (mf.get("quality") or {}).get("quality", {}).get("name", "") if m.get("hasFile") else "",
                "sizeMB":   round(mf.get("size", 0) / 1024 / 1024) if m.get("hasFile") else 0,
                "fileId":   mf.get("id") if m.get("hasFile") else None,
            })
        result.sort(key=lambda x: x["title"].lower())
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/action/season-search", methods=["POST"])
def season_search():
    data      = request.json or {}
    series_id = data.get("seriesId")
    season_n  = data.get("seasonNumber")
    if series_id is None or season_n is None:
        return jsonify({"ok": False, "error": "seriesId e seasonNumber obrigatórios"}), 400
    try:
        r = sonarr("GET", f"/api/v3/series/{series_id}")
        if not r.ok:
            return jsonify({"ok": False, "error": "Série não encontrada"}), 404
        series_obj = r.json()
        series_obj["monitored"] = True
        for season in series_obj.get("seasons", []):
            if season.get("seasonNumber") == season_n:
                season["monitored"] = True
                break
        sonarr("PUT", f"/api/v3/series/{series_id}", json=series_obj)
        r2 = sonarr("POST", "/api/v3/command",
                    json={"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_n})
        return jsonify({"ok": r2.ok, "error": "" if r2.ok else r2.text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/action/movie-search", methods=["POST"])
def movie_search():
    data     = request.json or {}
    movie_id = data.get("movieId")
    if not movie_id:
        return jsonify({"ok": False, "error": "movieId obrigatório"}), 400
    try:
        r = radarr("GET", f"/api/v3/movie/{movie_id}")
        if r.ok:
            movie_obj = r.json()
            if not movie_obj.get("monitored"):
                movie_obj["monitored"] = True
                radarr("PUT", f"/api/v3/movie/{movie_id}", json=movie_obj)
        r2 = radarr("POST", "/api/v3/command", json={"name": "MoviesSearch", "movieIds": [movie_id]})
        return jsonify({"ok": r2.ok, "error": "" if r2.ok else r2.text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── HEALTH ───────────────────────────────────────────────────────────────────

@app.route("/api/health")
def health_check():
    def chk_radarr():
        try:
            r = radarr("GET", "/api/v3/health")
            issues = [i.get("message","") for i in (r.json() if r.ok else []) if i.get("type") == "error"]
            return {"name": "Radarr", "ok": r.ok, "issues": issues}
        except Exception as e:
            return {"name": "Radarr", "ok": False, "issues": [str(e)]}

    def chk_sonarr():
        try:
            r = sonarr("GET", "/api/v3/health")
            issues = [i.get("message","") for i in (r.json() if r.ok else []) if i.get("type") == "error"]
            return {"name": "Sonarr", "ok": r.ok, "issues": issues}
        except Exception as e:
            return {"name": "Sonarr", "ok": False, "issues": [str(e)]}

    def chk_prowlarr():
        try:
            r = requests.get(f"{PROWLARR_URL}/", timeout=5)
            return {"name": "Prowlarr", "ok": r.status_code < 500, "issues": []}
        except Exception as e:
            return {"name": "Prowlarr", "ok": False, "issues": [str(e)]}

    def chk_bazarr():
        try:
            r = bazarr_get("/api/system/status")
            return {"name": "Bazarr", "ok": r.ok, "issues": []}
        except Exception as e:
            return {"name": "Bazarr", "ok": False, "issues": [str(e)]}

    def chk_qbt():
        try:
            r = qbt_request("GET", "/api/v2/app/version")
            version = r.text.strip() if r.ok else ""
            return {"name": "qBittorrent", "ok": r.ok, "issues": [], "version": version}
        except Exception as e:
            return {"name": "qBittorrent", "ok": False, "issues": [str(e)]}

    def chk_jellyfin():
        try:
            r = requests.get(f"{JELLYFIN_URL}/health", timeout=5)
            return {"name": "Jellyfin", "ok": r.status_code == 200, "issues": []}
        except Exception as e:
            return {"name": "Jellyfin", "ok": False, "issues": [str(e)]}

    def chk_flare():
        try:
            FLARE = os.getenv("FLARESOLVERR_URL", "http://flaresolverr.pedroflix.svc.cluster.local:8191")
            r = requests.get(f"{FLARE}/", timeout=5)
            return {"name": "FlareSolverr", "ok": r.status_code < 500, "issues": []}
        except Exception as e:
            return {"name": "FlareSolverr", "ok": False, "issues": [str(e)]}

    with ThreadPoolExecutor(max_workers=7) as ex:
        fns = [chk_radarr, chk_sonarr, chk_prowlarr, chk_bazarr, chk_qbt, chk_jellyfin, chk_flare]
        services = list(ex.map(lambda f: f(), fns))

    disk = []
    try:
        r = radarr("GET", "/api/v3/diskspace")
        if r.ok:
            for d in r.json():
                total = d.get("totalSpace", 1)
                free  = d.get("freeSpace", 0)
                disk.append({
                    "path":   d.get("path", ""),
                    "freeGB": round(free  / 1024**3, 1),
                    "totalGB":round(total / 1024**3, 1),
                    "pct":    round((1 - free / max(total, 1)) * 100),
                })
    except Exception:
        pass

    return jsonify({"services": services, "disk": disk})


# ── QUALITY PROFILE TOGGLE ────────────────────────────────────────────────────

@app.route("/api/quality", methods=["GET"])
def quality_get():
    try:
        r = radarr("GET", f"/api/v3/qualityprofile/{RADARR_PROFILE_ID}")
        if not r.ok:
            return jsonify({"blurayEnabled": False})
        profile = r.json()
        bluray_on = any(
            item.get("quality", {}).get("name") in RADARR_BLURAY and item.get("allowed")
            for item in profile.get("items", [])
        )
        return jsonify({"blurayEnabled": bluray_on})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quality", methods=["PUT"])
def quality_put():
    enable = (request.json or {}).get("enableBluray", False)
    try:
        # Radarr
        r = radarr("GET", f"/api/v3/qualityprofile/{RADARR_PROFILE_ID}")
        p = r.json()
        for item in p.get("items", []):
            if item.get("quality", {}).get("name") in RADARR_BLURAY:
                item["allowed"] = enable
        p["cutoff"] = 7 if enable else 1001
        radarr("PUT", f"/api/v3/qualityprofile/{RADARR_PROFILE_ID}", json=p)

        # Sonarr
        r = sonarr("GET", f"/api/v3/qualityprofile/{SONARR_PROFILE_ID}")
        p = r.json()
        for item in p.get("items", []):
            if item.get("quality", {}).get("name") in SONARR_BLURAY:
                item["allowed"] = enable
        p["cutoff"] = 7 if enable else 1002
        sonarr("PUT", f"/api/v3/qualityprofile/{SONARR_PROFILE_ID}", json=p)

        return jsonify({"ok": True, "blurayEnabled": enable})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── CALENDAR ─────────────────────────────────────────────────────────────────

@app.route("/api/calendar")
def calendar():
    try:
        start = datetime.utcnow().strftime("%Y-%m-%d")
        end   = (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d")
        r     = sonarr("GET", f"/api/v3/calendar?start={start}&end={end}&unmonitored=false&includeSeries=true")
        if not r.ok:
            return jsonify([])
        items = []
        for ep in r.json():
            series = ep.get("series") or {}
            items.append({
                "seriesTitle":   series.get("title", ep.get("seriesTitle", "?")),
                "episodeTitle":  ep.get("title", ""),
                "seasonNumber":  ep.get("seasonNumber", 0),
                "episodeNumber": ep.get("episodeNumber", 0),
                "airDate":       ep.get("airDate", ""),
                "hasFile":       ep.get("hasFile", False),
                "monitored":     ep.get("monitored", True),
            })
        items.sort(key=lambda x: (x["airDate"], x["seriesTitle"]))
        return jsonify(items)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── QBITTORRENT ───────────────────────────────────────────────────────────────

@app.route("/api/qbt/torrents")
def qbt_torrents():
    try:
        r = qbt_request("GET", "/api/v2/torrents/info?sort=added_on&reverse=true")
        if not r.ok:
            return jsonify({"torrents": [], "dlSpeed": 0, "upSpeed": 0})
        torrents = []
        for t in r.json():
            torrents.append({
                "hash":     t.get("hash", ""),
                "name":     t.get("name", ""),
                "state":    t.get("state", ""),
                "progress": round(t.get("progress", 0) * 100, 1),
                "sizeMB":   round(t.get("size", 0) / 1024 / 1024),
                "dlspeed":  t.get("dlspeed", 0),
                "upspeed":  t.get("upspeed", 0),
                "eta":      t.get("eta", 0),
                "seeds":    t.get("num_seeds", 0),
                "category": t.get("category", ""),
            })
        info_r = qbt_request("GET", "/api/v2/transfer/info")
        info   = info_r.json() if info_r.ok else {}
        return jsonify({
            "torrents": torrents,
            "dlSpeed":  info.get("dl_info_speed", 0),
            "upSpeed":  info.get("up_info_speed", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e), "torrents": [], "dlSpeed": 0, "upSpeed": 0}), 500


@app.route("/api/qbt/action", methods=["POST"])
def qbt_action():
    data   = request.json or {}
    action = data.get("action")
    hash_  = data.get("hash", "")
    try:
        if action == "pause":
            qbt_request("POST", "/api/v2/torrents/pause", data={"hashes": hash_})
        elif action == "resume":
            qbt_request("POST", "/api/v2/torrents/resume", data={"hashes": hash_})
        elif action == "delete":
            qbt_request("POST", "/api/v2/torrents/delete",
                        data={"hashes": hash_, "deleteFiles": "false"})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── SUBTITLES SEARCH ──────────────────────────────────────────────────────────

@app.route("/api/subtitles/search", methods=["POST"])
def subtitles_search():
    data      = request.json or {}
    item_type = data.get("type")
    item_id   = data.get("id")
    if not BAZARR_KEY:
        return jsonify({"ok": False, "error": "BAZARR_KEY não configurado"}), 400
    try:
        if item_type == "movie":
            bazarr_post("/api/movies", {"action": "search-missing", "radarrid": [item_id]})
        elif item_type == "episode":
            bazarr_post("/api/episodes", {"action": "search-missing", "sonarrEpisodeId": [item_id]})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── JELLYFIN ─────────────────────────────────────────────────────────────────

def jellyfin_get(path):
    if not JELLYFIN_KEY:
        return None
    try:
        return requests.get(f"{JELLYFIN_URL}{path}",
                            headers={"Authorization": f'MediaBrowser Token="{JELLYFIN_KEY}"'},
                            timeout=10)
    except Exception:
        return None


def jellyfin_post(path, data):
    if not JELLYFIN_KEY:
        return None
    try:
        return requests.post(f"{JELLYFIN_URL}{path}",
                             headers={"Authorization": f'MediaBrowser Token="{JELLYFIN_KEY}"',
                                      "Content-Type": "application/json"},
                             json=data, timeout=10)
    except Exception:
        return None


@app.route("/api/jellyfin/sessions")
def jellyfin_sessions():
    r = jellyfin_get("/Sessions?activeWithinSeconds=960")
    if not r or not r.ok:
        return jsonify([])
    sessions = []
    for s in r.json():
        np = s.get("NowPlayingItem")
        if not np:
            continue
        pos   = s.get("PlayState", {}).get("PositionTicks", 0)
        total = np.get("RunTimeTicks", 1) or 1
        sessions.append({
            "user":       s.get("UserName", "?"),
            "client":     s.get("Client", ""),
            "device":     s.get("DeviceName", ""),
            "title":      np.get("Name", ""),
            "series":     np.get("SeriesName", ""),
            "type":       np.get("Type", ""),
            "progressPct":round(pos / total * 100, 1),
            "posMin":     round(pos / 10_000_000 / 60),
            "totalMin":   round(total / 10_000_000 / 60),
            "isPaused":   s.get("PlayState", {}).get("IsPaused", False),
        })
    return jsonify(sessions)


def _jellyfin_user_id():
    r = jellyfin_get("/Users")
    if r and r.ok and r.json():
        return r.json()[0].get("Id", "")
    return ""


@app.route("/api/jellyfin/recent")
def jellyfin_recent():
    uid = _jellyfin_user_id()
    if not uid:
        return jsonify([])
    r = jellyfin_get(f"/Users/{uid}/Items/Latest?Limit=20&Fields=Overview,DateCreated&IncludeItemTypes=Movie,Episode&ImageTypeLimit=1")
    if not r or not r.ok:
        return jsonify([])
    items = []
    for item in r.json():
        items.append({
            "id":         item.get("Id", ""),
            "title":      item.get("Name", ""),
            "series":     item.get("SeriesName", ""),
            "type":       item.get("Type", ""),
            "year":       item.get("ProductionYear"),
            "overview":   (item.get("Overview") or "")[:150],
            "thumb":      f"{JELLYFIN_URL}/Items/{item.get('Id')}/Images/Primary?height=120&fillHeight=120" if item.get("Id") else "",
            "added":      item.get("DateCreated", ""),
        })
    return jsonify(items)


@app.route("/api/jellyfin/control", methods=["POST"])
def jellyfin_control():
    data      = request.json or {}
    session_id = data.get("sessionId")
    action    = data.get("action")  # pause / unpause / stop
    cmd_map   = {"pause": "Pause", "unpause": "Unpause", "stop": "Stop"}
    cmd       = cmd_map.get(action)
    if not cmd or not session_id:
        return jsonify({"ok": False}), 400
    r = jellyfin_post(f"/Sessions/{session_id}/Playing/{cmd}", {})
    return jsonify({"ok": r is not None and r.ok})


# ── PROWLARR ─────────────────────────────────────────────────────────────────

@app.route("/api/prowlarr/indexers")
def prowlarr_indexers():
    if not PROWLARR_KEY:
        return jsonify([])
    try:
        r = requests.get(f"{PROWLARR_URL}/api/v1/indexer",
                         headers={"X-Api-Key": PROWLARR_KEY}, timeout=10)
        if not r.ok:
            return jsonify([])
        result = []
        for idx in r.json():
            result.append({
                "id":       idx.get("id"),
                "name":     idx.get("name", "?"),
                "enabled":  idx.get("enable", False),
                "protocol": idx.get("protocol", ""),
                "privacy":  idx.get("privacy", ""),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── STORAGE / FILE MANAGEMENT ────────────────────────────────────────────────

@app.route("/api/library/storage")
def library_storage():
    movies, series = [], []
    try:
        r = radarr("GET", "/api/v3/movie")
        if r.ok:
            for m in r.json():
                if not m.get("hasFile"):
                    continue
                mf = m.get("movieFile") or {}
                movies.append({
                    "id":      m.get("id"),
                    "title":   m.get("title", "?"),
                    "year":    m.get("year"),
                    "quality": (mf.get("quality") or {}).get("quality", {}).get("name", ""),
                    "sizeMB":  round(mf.get("size", 0) / 1024 / 1024),
                    "fileId":  mf.get("id"),
                })
            movies.sort(key=lambda x: x["sizeMB"], reverse=True)
    except Exception:
        pass
    try:
        r = sonarr("GET", "/api/v3/series")
        if r.ok:
            for s in r.json():
                stats = s.get("statistics") or {}
                if not stats.get("sizeOnDisk"):
                    continue
                series.append({
                    "id":      s.get("id"),
                    "title":   s.get("title", "?"),
                    "year":    s.get("year"),
                    "sizeMB":  round(stats.get("sizeOnDisk", 0) / 1024 / 1024),
                    "episodes":stats.get("episodeFileCount", 0),
                })
            series.sort(key=lambda x: x["sizeMB"], reverse=True)
    except Exception:
        pass
    return jsonify({"movies": movies, "series": series})


@app.route("/api/library/movies/delete", methods=["POST"])
def delete_movie_file():
    data    = request.json or {}
    file_id = data.get("fileId")
    if not file_id:
        return jsonify({"ok": False, "error": "fileId obrigatório"}), 400
    r = radarr("DELETE", f"/api/v3/moviefile/{file_id}")
    return jsonify({"ok": r.ok, "error": "" if r.ok else r.text})


@app.route("/api/library/series/season-delete", methods=["POST"])
def delete_season_files():
    data      = request.json or {}
    series_id = data.get("seriesId")
    season_n  = data.get("seasonNumber")
    if series_id is None or season_n is None:
        return jsonify({"ok": False, "error": "seriesId e seasonNumber obrigatórios"}), 400
    try:
        r     = sonarr("GET", f"/api/v3/episodefile?seriesId={series_id}")
        files = [f["id"] for f in (r.json() if r.ok else []) if f.get("seasonNumber") == season_n]
        if not files:
            return jsonify({"ok": True, "deleted": 0})
        r2 = sonarr("DELETE", "/api/v3/episodefile/bulk", json={"episodeFileIds": files})
        return jsonify({"ok": r2.ok, "deleted": len(files)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── MAGNET ADD ────────────────────────────────────────────────────────────────

@app.route("/api/qbt/add", methods=["POST"])
def qbt_add():
    data   = request.json or {}
    magnet = data.get("magnet", "").strip()
    if not magnet.startswith("magnet:"):
        return jsonify({"ok": False, "error": "URL de magnet inválida"}), 400
    try:
        r = qbt_request("POST", "/api/v2/torrents/add", data={"urls": magnet})
        return jsonify({"ok": r.ok, "error": "" if r.ok else r.text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── SUBTITLES BULK ────────────────────────────────────────────────────────────

@app.route("/api/subtitles/search-all", methods=["POST"])
def subtitles_search_all():
    if not BAZARR_KEY:
        return jsonify({"ok": False, "error": "BAZARR_KEY não configurado"}), 400
    try:
        bazarr_post("/api/movies",   {"action": "search-missing"})
        bazarr_post("/api/episodes", {"action": "search-missing"})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pedroflix</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { background: #0d0d0f; color: #e4e4e7; font-family: system-ui, sans-serif; }

  /* Cards */
  .card { position:relative; border-radius:12px; overflow:hidden; background:#18181b;
          transition:transform .2s,box-shadow .2s; display:flex; flex-direction:column; }
  .card:hover { transform:translateY(-4px); box-shadow:0 12px 40px rgba(0,0,0,.6); }
  .poster-wrap { position:relative; aspect-ratio:2/3; background:#27272a; flex-shrink:0; }
  .poster-wrap img { width:100%; height:100%; object-fit:cover; display:block; }
  .no-poster { width:100%; height:100%; display:flex; align-items:center; justify-content:center;
               color:#52525b; font-size:3rem; }
  .badge-type { position:absolute; top:8px; left:8px; padding:2px 8px; border-radius:4px;
                font-size:11px; font-weight:600; letter-spacing:.5px; text-transform:uppercase; }
  .badge-movie  { background:#2563eb; color:#fff; }
  .badge-series { background:#7c3aed; color:#fff; }
  .badge-rating { position:absolute; top:8px; right:8px; background:rgba(0,0,0,.75);
                  border:1px solid rgba(255,255,255,.15); padding:2px 7px; border-radius:4px;
                  font-size:12px; font-weight:600; color:#fbbf24; }
  .card-body { padding:12px; flex:1; display:flex; flex-direction:column; gap:6px; }
  .card-title { font-weight:700; font-size:14px; line-height:1.3; color:#f4f4f5; }
  .card-meta  { font-size:12px; color:#71717a; }
  .card-overview { font-size:12px; color:#a1a1aa; line-height:1.5;
                   display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical;
                   overflow:hidden; flex:1; }
  .genres { display:flex; flex-wrap:wrap; gap:4px; margin-top:2px; }
  .genre-tag { background:#27272a; color:#a1a1aa; padding:2px 6px; border-radius:4px; font-size:11px; }
  .btn-add { margin-top:auto; padding:8px; border-radius:8px; font-size:13px; font-weight:600;
             cursor:pointer; transition:background .15s,opacity .15s; border:none; width:100%; }
  .btn-add.idle    { background:#0ea5e9; color:#fff; }
  .btn-add.idle:hover { background:#0284c7; }
  .btn-add.loading { background:#27272a; color:#71717a; cursor:default; }
  .btn-add.done    { background:#16a34a; color:#fff; cursor:default; }
  .btn-add.library { background:#27272a; color:#4ade80; cursor:default; }
  .btn-add.error   { background:#991b1b; color:#fca5a5; }
  .btn-wl { background:none; border:none; cursor:pointer; font-size:18px; line-height:1;
             padding:4px 6px; border-radius:6px; transition:background .15s; }
  .btn-wl:hover { background:#27272a; }

  /* Tabs */
  .tab-bar { display:flex; border-bottom:1px solid #27272a; margin-bottom:24px; overflow-x:auto; }
  .tab-bar::-webkit-scrollbar { height:3px; }
  .tab-bar::-webkit-scrollbar-thumb { background:#3f3f46; border-radius:2px; }
  .tab-btn { padding:10px 16px; font-size:14px; font-weight:600; border:none; background:none;
             color:#71717a; cursor:pointer; border-bottom:2px solid transparent;
             transition:color .15s,border-color .15s; white-space:nowrap; }
  .tab-btn.active { color:#0ea5e9; border-bottom-color:#0ea5e9; }
  .tab-btn:hover:not(.active) { color:#e4e4e7; }

  /* Pills */
  .pill { padding:6px 16px; border-radius:999px; font-size:14px; font-weight:500;
          cursor:pointer; border:1.5px solid transparent; transition:all .15s;
          background:#18181b; color:#a1a1aa; }
  .pill.active { background:#0ea5e9; color:#fff; border-color:#0ea5e9; }
  .pill:not(.active):hover { border-color:#3f3f46; color:#e4e4e7; }
  .search-input { background:#18181b; border:1.5px solid #3f3f46; border-radius:12px;
                  color:#f4f4f5; font-size:16px; padding:12px 16px 12px 44px; width:100%;
                  outline:none; transition:border-color .15s; }
  .search-input:focus { border-color:#0ea5e9; }
  .search-input::placeholder { color:#52525b; }
  .section-title { font-size:18px; font-weight:700; color:#e4e4e7;
                   border-left:3px solid #0ea5e9; padding-left:10px; margin-bottom:16px; }
  #grid { display:grid; gap:16px; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); }
  @media(min-width:640px) { #grid { grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); } }

  /* Download items */
  .dl-item { background:#18181b; border:1px solid #27272a; border-radius:12px;
              padding:14px 16px; margin-bottom:10px; }
  .dl-header { display:flex; align-items:flex-start; justify-content:space-between;
               gap:12px; margin-bottom:6px; }
  .dl-title { font-weight:700; font-size:14px; color:#f4f4f5; line-height:1.4; flex:1; }
  .dl-year  { font-weight:400; color:#71717a; }
  .dl-meta  { font-size:12px; color:#71717a; margin-bottom:8px; }
  .dl-footer { display:flex; align-items:center; gap:12px; margin-top:4px; font-size:12px; }
  .dl-pct  { color:#a1a1aa; font-weight:600; min-width:38px; }
  .dl-eta  { color:#71717a; }
  .dl-error { color:#fca5a5; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .dl-date { font-size:12px; color:#52525b; white-space:nowrap; flex-shrink:0; }
  .prog-bg   { background:#27272a; border-radius:999px; height:6px; overflow:hidden; margin:8px 0; }
  .prog-fill { height:100%; border-radius:999px; transition:width .6s ease; }
  .prog-downloading { background:linear-gradient(90deg,#0ea5e9,#38bdf8); }
  .prog-paused  { background:#f97316; }
  .prog-failed  { background:#ef4444; }
  .prog-done    { background:#22c55e; }
  .st-badge { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700;
              white-space:nowrap; flex-shrink:0; }
  .st-downloading { background:#1e3a5f; color:#7dd3fc; }
  .st-queued      { background:#27272a; color:#a1a1aa; }
  .st-importing   { background:#422006; color:#fdba74; }
  .st-warning     { background:#422006; color:#fdba74; }
  .st-failed      { background:#7f1d1d; color:#fca5a5; }
  .sub-row   { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-top:8px; }
  .sub-label { font-size:12px; color:#71717a; }
  .sub-badge { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
  .sub-ptbr    { background:#14532d; color:#86efac; }
  .sub-en      { background:#1e3a5f; color:#7dd3fc; }
  .sub-other   { background:#27272a; color:#a1a1aa; }
  .sub-missing { background:#7f1d1d; color:#fca5a5; }
  .btn-sub { background:#27272a; color:#a1a1aa; border:none; border-radius:6px;
             padding:3px 10px; font-size:11px; font-weight:600; cursor:pointer;
             transition:background .15s; flex-shrink:0; }
  .btn-sub:hover { background:#3f3f46; color:#e4e4e7; }
  .btn-sub.done { background:#14532d; color:#86efac; cursor:default; }
  .btn-sub.loading { background:#1e3a5f; color:#7dd3fc; cursor:default; }

  /* Biblioteca */
  .lib-subtabs { display:flex; align-items:center; gap:4px; margin-bottom:20px; }
  .lib-sub { padding:6px 16px; border-radius:8px; font-size:13px; font-weight:600;
             border:1.5px solid #27272a; background:none; color:#71717a; cursor:pointer;
             transition:all .15s; }
  .lib-sub.active { background:#27272a; color:#e4e4e7; border-color:#3f3f46; }
  .lib-sub:hover:not(.active) { border-color:#52525b; color:#a1a1aa; }
  .lib-filter { margin-left:auto; background:#18181b; border:1.5px solid #27272a;
                border-radius:8px; color:#f4f4f5; font-size:13px; padding:6px 12px;
                outline:none; width:180px; transition:border-color .15s; }
  .lib-filter:focus { border-color:#0ea5e9; }
  .lib-filter::placeholder { color:#52525b; }
  .series-row { background:#18181b; border:1px solid #27272a; border-radius:12px;
                padding:14px 16px; margin-bottom:8px; transition:border-color .15s; }
  .series-row:hover { border-color:#3f3f46; }
  .series-header { display:flex; align-items:baseline; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
  .series-title  { font-weight:700; font-size:15px; color:#f4f4f5; }
  .series-year   { font-size:13px; color:#52525b; }
  .series-status { font-size:11px; font-weight:600; padding:2px 7px; border-radius:4px; }
  .ss-continuing { background:#14532d; color:#86efac; }
  .ss-ended      { background:#27272a; color:#71717a; }
  .ss-upcoming   { background:#1e3a5f; color:#7dd3fc; }
  .seasons-row   { display:flex; flex-wrap:wrap; gap:6px; }
  .s-pill { display:inline-flex; align-items:center; gap:4px; padding:4px 10px; border-radius:6px;
            font-size:12px; font-weight:600; border:1.5px solid transparent; cursor:default;
            transition:all .15s; user-select:none; white-space:nowrap; }
  .s-full      { background:#14532d; color:#86efac; border-color:#166534; }
  .s-partial   { background:#422006; color:#fdba74; border-color:#7c2d12; cursor:pointer; }
  .s-empty     { background:transparent; color:#71717a; border-color:#3f3f46; cursor:pointer; }
  .s-partial:hover { background:#7c2d12; color:#fed7aa; }
  .s-empty:hover   { background:#27272a; color:#a1a1aa; border-color:#71717a; }
  .s-searching { background:#1e3a5f; color:#7dd3fc; border-color:#1e40af; cursor:default; }
  .s-done      { background:#14532d; color:#86efac; border-color:#166534; cursor:default; }
  .movie-row { display:flex; align-items:center; gap:12px; background:#18181b;
               border:1px solid #27272a; border-radius:10px; padding:10px 14px;
               margin-bottom:6px; transition:border-color .15s; }
  .movie-row:hover { border-color:#3f3f46; }
  .movie-info  { flex:1; min-width:0; }
  .movie-title { font-weight:600; font-size:14px; color:#f4f4f5;
                 overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .movie-meta  { font-size:12px; color:#52525b; margin-top:2px; }
  .m-has-file  { color:#4ade80; font-size:16px; flex-shrink:0; }
  .btn-search  { background:#0ea5e9; color:#fff; border:none; border-radius:6px;
                 padding:5px 12px; font-size:12px; font-weight:600; cursor:pointer;
                 transition:background .15s; flex-shrink:0; }
  .btn-search:hover   { background:#0284c7; }
  .btn-search.loading { background:#27272a; color:#71717a; cursor:default; }
  .btn-search.done    { background:#16a34a; color:#fff; cursor:default; }

  /* Health */
  .svc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); gap:12px; margin-bottom:24px; }
  .svc-card { background:#18181b; border:1px solid #27272a; border-radius:12px; padding:14px;
              display:flex; flex-direction:column; align-items:center; gap:8px; }
  .svc-dot  { width:14px; height:14px; border-radius:50%; flex-shrink:0; }
  .svc-ok   { background:#22c55e; box-shadow:0 0 8px #22c55e88; }
  .svc-warn { background:#f59e0b; box-shadow:0 0 8px #f59e0b88; }
  .svc-err  { background:#ef4444; box-shadow:0 0 8px #ef444488; }
  .svc-name { font-size:13px; font-weight:600; color:#e4e4e7; }
  .svc-issue { font-size:10px; color:#fca5a5; text-align:center; line-height:1.4; }
  .disk-bar-bg { background:#27272a; border-radius:999px; height:8px; overflow:hidden; }
  .disk-bar-fill { height:100%; border-radius:999px; }
  .disk-ok  { background:linear-gradient(90deg,#22c55e,#16a34a); }
  .disk-warn { background:linear-gradient(90deg,#f59e0b,#d97706); }
  .disk-crit { background:linear-gradient(90deg,#ef4444,#dc2626); }

  /* Quality toggle */
  .toggle-wrap { display:flex; align-items:center; gap:12px; }
  .toggle { position:relative; width:44px; height:24px; cursor:pointer; }
  .toggle input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:#27272a; border-radius:999px;
            transition:background .2s; }
  .slider:before { content:''; position:absolute; width:18px; height:18px; left:3px; top:3px;
                   background:white; border-radius:50%; transition:transform .2s; }
  input:checked + .slider { background:#0ea5e9; }
  input:checked + .slider:before { transform:translateX(20px); }

  /* Calendar */
  .cal-day { margin-bottom:20px; }
  .cal-day-header { font-size:13px; font-weight:700; color:#71717a; text-transform:uppercase;
                    letter-spacing:.8px; margin-bottom:8px; padding-bottom:6px;
                    border-bottom:1px solid #27272a; }
  .cal-day-header.today { color:#0ea5e9; }
  .cal-ep { background:#18181b; border:1px solid #27272a; border-radius:10px;
            padding:10px 14px; margin-bottom:6px; display:flex; align-items:center; gap:12px; }
  .cal-ep:hover { border-color:#3f3f46; }
  .cal-ep-info { flex:1; min-width:0; }
  .cal-ep-series { font-weight:600; font-size:14px; color:#f4f4f5; }
  .cal-ep-title  { font-size:12px; color:#71717a; margin-top:2px; overflow:hidden;
                   text-overflow:ellipsis; white-space:nowrap; }
  .cal-ep-num  { font-size:12px; font-weight:700; color:#a1a1aa; white-space:nowrap; flex-shrink:0; }
  .cal-ep-has  { color:#4ade80; font-size:14px; flex-shrink:0; }
  .cal-ep-miss { color:#71717a; font-size:14px; flex-shrink:0; }

  /* qBittorrent control */
  .qbt-header { display:flex; align-items:center; gap:16px; background:#18181b;
                border:1px solid #27272a; border-radius:12px; padding:12px 16px;
                margin-bottom:16px; flex-wrap:wrap; }
  .speed-badge { display:flex; align-items:center; gap:6px; font-size:14px; font-weight:600; }
  .spd-dl { color:#38bdf8; }
  .spd-up { color:#34d399; }
  .torrent-item { background:#18181b; border:1px solid #27272a; border-radius:12px;
                  padding:14px 16px; margin-bottom:8px; }
  .torrent-header { display:flex; align-items:flex-start; justify-content:space-between;
                    gap:12px; margin-bottom:6px; }
  .torrent-name  { font-weight:700; font-size:13px; color:#f4f4f5; line-height:1.4; flex:1;
                   overflow:hidden; display:-webkit-box; -webkit-line-clamp:2;
                   -webkit-box-orient:vertical; }
  .torrent-state { font-size:11px; font-weight:700; padding:2px 8px; border-radius:4px;
                   white-space:nowrap; flex-shrink:0; }
  .ts-downloading { background:#1e3a5f; color:#7dd3fc; }
  .ts-uploading   { background:#14532d; color:#86efac; }
  .ts-paused      { background:#3f3f46; color:#a1a1aa; }
  .ts-error       { background:#7f1d1d; color:#fca5a5; }
  .ts-queued      { background:#27272a; color:#71717a; }
  .ts-stalled     { background:#422006; color:#fdba74; }
  .torrent-meta { font-size:12px; color:#71717a; margin-bottom:8px; }
  .torrent-actions { display:flex; align-items:center; gap:8px; margin-top:10px; }
  .btn-tor { border:none; border-radius:6px; padding:5px 12px; font-size:12px; font-weight:600;
             cursor:pointer; transition:background .15s; }
  .btn-pause  { background:#27272a; color:#a1a1aa; }
  .btn-pause:hover { background:#3f3f46; color:#e4e4e7; }
  .btn-resume { background:#1e3a5f; color:#7dd3fc; }
  .btn-resume:hover { background:#1e40af; color:#bfdbfe; }
  .btn-del    { background:#7f1d1d; color:#fca5a5; }
  .btn-del:hover { background:#991b1b; color:#fecaca; }

  /* Watchlist */
  .wl-item { background:#18181b; border:1px solid #27272a; border-radius:10px;
             padding:10px 14px; margin-bottom:6px; display:flex; align-items:center; gap:12px; }
  .wl-info { flex:1; min-width:0; }
  .wl-title { font-weight:600; font-size:14px; color:#f4f4f5; overflow:hidden;
              text-overflow:ellipsis; white-space:nowrap; }
  .wl-meta  { font-size:12px; color:#52525b; margin-top:2px; }
  .btn-wl-remove { background:none; border:none; cursor:pointer; color:#71717a; font-size:16px;
                   padding:4px 6px; border-radius:6px; transition:color .15s; }
  .btn-wl-remove:hover { color:#fca5a5; }

  /* Shared */
  .empty-msg { text-align:center; color:#52525b; padding:32px 0; font-size:14px; }
  .refresh-btn { background:#27272a; color:#a1a1aa; border:none; border-radius:8px;
                 padding:6px 14px; font-size:13px; cursor:pointer; transition:background .15s; }
  .refresh-btn:hover { background:#3f3f46; color:#e4e4e7; }
  .spinner { border:3px solid #27272a; border-top-color:#0ea5e9; border-radius:50%;
             width:32px; height:32px; animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
           background:#18181b; border:1px solid #3f3f46; color:#e4e4e7;
           padding:10px 20px; border-radius:10px; font-size:14px;
           box-shadow:0 8px 32px rgba(0,0,0,.5); z-index:100;
           transition:opacity .3s; pointer-events:none; }
</style>
</head>
<body class="min-h-screen">
<div class="max-w-5xl mx-auto px-4 py-8">

  <div class="flex items-center gap-3 mb-6">
    <div class="text-3xl">🎬</div>
    <div>
      <h1 class="text-2xl font-bold text-white leading-none">Pedroflix</h1>
      <p class="text-zinc-500 text-sm">Gerenciamento de mídia</p>
    </div>
  </div>

  <div class="tab-bar">
    <button class="tab-btn active" data-tab="search"    onclick="switchTab('search')">🔍 Busca</button>
    <button class="tab-btn"        data-tab="downloads" onclick="switchTab('downloads')">⬇ Downloads</button>
    <button class="tab-btn"        data-tab="calendar"  onclick="switchTab('calendar')">📅 Calendário</button>
    <button class="tab-btn"        data-tab="control"   onclick="switchTab('control')">🎛 Controle</button>
    <button class="tab-btn"        data-tab="library"   onclick="switchTab('library')">📁 Biblioteca</button>
    <button class="tab-btn"        data-tab="jellyfin"  onclick="switchTab('jellyfin')">🎬 Jellyfin</button>
    <button class="tab-btn"        data-tab="health"    onclick="switchTab('health')">💚 Saúde</button>
  </div>

  <!-- ══ BUSCA ══ -->
  <div id="tab-search">
    <div class="relative mb-4">
      <svg class="absolute left-3.5 top-1/2 -translate-y-1/2 w-5 h-5 text-zinc-500 pointer-events-none"
           fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M21 21l-4.35-4.35M17 11A6 6 0 111 11a6 6 0 0116 0z"/>
      </svg>
      <input id="searchInput" class="search-input" type="text"
             placeholder="Buscar filmes ou séries..." autocomplete="off">
    </div>
    <div class="flex gap-2 mb-4">
      <button class="pill active" data-type="all">Todos</button>
      <button class="pill"        data-type="movie">Filmes</button>
      <button class="pill"        data-type="series">Séries</button>
      <button class="pill"        data-type="watchlist" style="margin-left:auto">♡ Watchlist</button>
    </div>
    <div id="results"></div>
    <div id="watchlist-panel" style="display:none">
      <div class="section-title">Watchlist</div>
      <div id="wl-list"></div>
    </div>
  </div>

  <!-- ══ DOWNLOADS ══ -->
  <div id="tab-downloads" style="display:none">
    <div class="flex items-center gap-3 mb-6" style="flex-wrap:wrap">
      <button id="btn-notif" onclick="requestNotifPermission()" style="background:#27272a;color:#a1a1aa;border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">🔔 Ativar notificações</button>
      <button onclick="searchAllSubs(this)" style="background:#27272a;color:#a1a1aa;border:none;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">🔍 Buscar todas as legendas</button>
    </div>
    <div class="flex items-center justify-between mb-4">
      <div class="section-title" style="margin-bottom:0">Em andamento</div>
      <button class="refresh-btn" onclick="loadQueue()">↻ Atualizar</button>
    </div>
    <div id="queue-list" class="mb-10">
      <div class="flex justify-center py-8"><div class="spinner"></div></div>
    </div>
    <div class="flex items-center justify-between mb-4">
      <div class="section-title" style="margin-bottom:0">Concluídos recentemente</div>
      <button class="refresh-btn" onclick="loadHistory()">↻ Atualizar</button>
    </div>
    <div id="history-list">
      <div class="flex justify-center py-8"><div class="spinner"></div></div>
    </div>
  </div>

  <!-- ══ CALENDÁRIO ══ -->
  <div id="tab-calendar" style="display:none">
    <div class="flex items-center justify-between mb-4">
      <div class="section-title" style="margin-bottom:0">Próximos 14 dias</div>
      <button class="refresh-btn" onclick="loadCalendar()">↻ Atualizar</button>
    </div>
    <div id="calendar-list">
      <div class="flex justify-center py-12"><div class="spinner"></div></div>
    </div>
  </div>

  <!-- ══ CONTROLE (qBittorrent) ══ -->
  <div id="tab-control" style="display:none">
    <div id="qbt-header" class="qbt-header">
      <span class="speed-badge spd-dl">↓ <span id="spd-dl">—</span></span>
      <span class="speed-badge spd-up">↑ <span id="spd-up">—</span></span>
      <span style="flex:1"></span>
      <button class="refresh-btn" onclick="loadQbt()">↻ Atualizar</button>
    </div>
    <div id="qbt-list">
      <div class="flex justify-center py-12"><div class="spinner"></div></div>
    </div>
    <!-- Magnet add -->
    <div style="margin-top:24px">
      <div class="section-title">Adicionar torrent</div>
      <div style="display:flex;gap:8px;align-items:stretch">
        <input id="magnet-input" type="text" class="search-input" style="font-size:13px;padding:10px 14px"
               placeholder="Cole o link magnet aqui (magnet:?xt=...)">
        <button onclick="addMagnet()" style="background:#0ea5e9;color:#fff;border:none;border-radius:10px;
                padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap">
          ↓ Enviar
        </button>
      </div>
    </div>
  </div>

  <!-- ══ JELLYFIN ══ -->
  <div id="tab-jellyfin" style="display:none">
    <div class="flex items-center justify-between mb-4">
      <div class="section-title" style="margin-bottom:0">Sessões ativas</div>
      <button class="refresh-btn" onclick="loadJellyfin()">↻ Atualizar</button>
    </div>
    <div id="jelly-sessions" class="mb-8">
      <div class="flex justify-center py-6"><div class="spinner"></div></div>
    </div>
    <div class="section-title">Adicionado recentemente</div>
    <div id="jelly-recent">
      <div class="flex justify-center py-6"><div class="spinner"></div></div>
    </div>
  </div>

  <!-- ══ BIBLIOTECA ══ -->
  <div id="tab-library" style="display:none">
    <div class="lib-subtabs">
      <button class="lib-sub active" data-lib="series"  onclick="switchLib('series')">Séries</button>
      <button class="lib-sub"        data-lib="movies"  onclick="switchLib('movies')">Filmes</button>
      <button class="lib-sub"        data-lib="storage" onclick="switchLib('storage')">💾 Storage</button>
      <input id="libFilter" class="lib-filter" type="text" placeholder="Filtrar..." oninput="applyLibFilter()">
    </div>
    <div id="lib-series">
      <div class="flex justify-center py-12"><div class="spinner"></div></div>
    </div>
    <div id="lib-movies" style="display:none">
      <div class="flex justify-center py-12"><div class="spinner"></div></div>
    </div>
    <div id="lib-storage" style="display:none">
      <div class="flex justify-center py-12"><div class="spinner"></div></div>
    </div>
  </div>

  <!-- ══ SAÚDE ══ -->
  <div id="tab-health" style="display:none">
    <div class="flex items-center justify-between mb-4">
      <div class="section-title" style="margin-bottom:0">Serviços</div>
      <button class="refresh-btn" onclick="loadHealth()">↻ Atualizar</button>
    </div>
    <div id="svc-grid" class="svc-grid mb-8">
      <div class="flex justify-center py-8" style="grid-column:1/-1"><div class="spinner"></div></div>
    </div>

    <div class="section-title">Disco</div>
    <div id="disk-info" class="mb-8">
      <div class="flex justify-center py-4"><div class="spinner"></div></div>
    </div>

    <div class="section-title">Indexadores (Prowlarr)</div>
    <div id="prowlarr-indexers" class="mb-8">
      <div class="flex justify-center py-4"><div class="spinner"></div></div>
    </div>

    <div class="section-title">Qualidade de download</div>
    <div class="dl-item" style="margin-bottom:0">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
        <div>
          <div style="font-weight:600;color:#f4f4f5;margin-bottom:4px">Modo de qualidade</div>
          <div id="quality-desc" style="font-size:13px;color:#71717a">Carregando...</div>
        </div>
        <label class="toggle" title="Ativar/desativar arquivos Bluray">
          <input type="checkbox" id="quality-toggle" onchange="toggleQuality(this)">
          <span class="slider"></span>
        </label>
      </div>
      <div style="margin-top:10px;font-size:12px;color:#52525b">
        <b style="color:#a1a1aa">WEB-only:</b> WEBDL-1080p / WEBRip-1080p (1–4 GB/filme) &nbsp;|&nbsp;
        <b style="color:#a1a1aa">Bluray:</b> inclui Bluray-1080p (5–20 GB/filme)
      </div>
    </div>
  </div>

</div>
<div id="toast" class="toast" style="opacity:0"></div>

<script>
// ── UTILS ──────────────────────────────────────────────────────
const LANG_MAP = {
  pb:'PT-BR',pob:'PT-BR',en:'EN',eng:'EN',pt:'PT',por:'PT',
  es:'ES',spa:'ES',fr:'FR',fra:'FR',de:'DE',deu:'DE',
  it:'IT',ita:'IT',ja:'JA',jpn:'JA',zh:'ZH',zho:'ZH',
};
const LANG_CLS = {'PT-BR':'sub-ptbr','EN':'sub-en'};

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function langName(sub) {
  const code = (sub.code2||sub.code3||'').toLowerCase();
  return LANG_MAP[code]||sub.name||code;
}
function fmtSpeed(bps) {
  if (bps < 1024)         return bps + ' B/s';
  if (bps < 1024*1024)    return (bps/1024).toFixed(0) + ' KB/s';
  return (bps/1024/1024).toFixed(1) + ' MB/s';
}
function fmtETA(t) {
  if (!t || t==='00:00:00') return '';
  const dot = t.split('.');
  if (dot.length===2) { const[h,m]=dot[1].split(':'); return `${dot[0]}d ${parseInt(h)}h`; }
  const[h,m]=t.split(':');
  if(parseInt(h)>0) return `${parseInt(h)}h ${m}min`;
  return `${parseInt(m)}min`;
}
function fmtDate(iso) {
  if(!iso) return '';
  return new Date(iso).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function showToast(msg, color='#0ea5e9') {
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.borderColor=color; t.style.opacity=1;
  setTimeout(()=>t.style.opacity=0, 2800);
}

// ── TAB SWITCHING ───────────────────────────────────────────────
let queueTimer, histTimer, qbtTimer;
const TABS = ['search','downloads','calendar','control','library','jellyfin','health'];

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
  TABS.forEach(t=>{
    const el=document.getElementById('tab-'+t);
    if(el) el.style.display=t===tab?'':'none';
  });
  clearInterval(queueTimer); clearInterval(histTimer); clearInterval(qbtTimer);

  if(tab==='downloads') {
    loadQueue(); loadHistory();
    queueTimer=setInterval(loadQueue,5000);
    histTimer=setInterval(loadHistory,30000);
    updateNotifButton();
  }
  if(tab==='calendar')  loadCalendar();
  if(tab==='control')   { loadQbt(); qbtTimer=setInterval(loadQbt,5000); }
  if(tab==='library')   { loadLibSeries(); loadLibMovies(); }
  if(tab==='jellyfin')  loadJellyfin();
  if(tab==='health')    { loadHealth(); loadQualityMode(); loadProwlarrIndexers(); }
}

// ── QUEUE ───────────────────────────────────────────────────────
async function loadQueue() {
  const el=document.getElementById('queue-list');
  try {
    const r=await fetch('/api/queue'), items=await r.json();
    checkNotifications(items);
    if(!items.length){el.innerHTML='<p class="empty-msg">Nenhum download em andamento.</p>';return;}
    el.innerHTML=items.map(renderQueueItem).join('');
  } catch(e){el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar fila.</p>';}
}
function statusInfo(item){
  const s=item.status||'',t=item.trackedDownloadState||'';
  if(s==='failed')          return{label:'Falhou',cls:'st-failed'};
  if(s==='warning')         return{label:'Aviso',cls:'st-warning'};
  if(t==='importPending')   return{label:'Importando',cls:'st-importing'};
  if(s==='paused')          return{label:'Pausado',cls:'st-warning'};
  if(s==='queued')          return{label:'Na fila',cls:'st-queued'};
  if(s==='downloading')     return{label:'Baixando',cls:'st-downloading'};
  return{label:s||'—',cls:'st-queued'};
}
function progClass(item){
  if(item.status==='failed') return 'prog-failed';
  if(item.status==='paused') return 'prog-paused';
  return 'prog-downloading';
}
function renderQueueItem(item){
  const si=statusInfo(item), eta=fmtETA(item.timeleft), pct=item.progress;
  const sub=item.type==='series'&&item.episode
    ?`${esc(item.episode)}${item.epTitle?' · '+esc(item.epTitle):''} — `:'';
  const meta=[item.quality,item.sizeMB?item.sizeMB+' MB':''].filter(Boolean).join(' · ');
  return`<div class="dl-item">
    <div class="dl-header">
      <div class="dl-title">${sub}<span style="color:#f4f4f5">${esc(item.title)}</span>${item.year?`<span class="dl-year"> (${item.year})</span>`:''}</div>
      <span class="st-badge ${si.cls}">${si.label}</span>
    </div>
    ${meta?`<div class="dl-meta">${meta}</div>`:''}
    <div class="prog-bg"><div class="prog-fill ${progClass(item)}" style="width:${pct}%"></div></div>
    <div class="dl-footer">
      <span class="dl-pct">${pct}%</span>
      ${eta?`<span class="dl-eta">ETA: ${eta}</span>`:''}
      ${item.errorMessage?`<span class="dl-error">${esc(item.errorMessage.slice(0,80))}</span>`:''}
    </div>
  </div>`;
}

// ── HISTORY ─────────────────────────────────────────────────────
async function loadHistory() {
  const el=document.getElementById('history-list');
  try {
    const r=await fetch('/api/history'), items=await r.json();
    if(!items.length){el.innerHTML='<p class="empty-msg">Nenhum item concluído ainda.</p>';return;}
    el.innerHTML=items.map(renderHistoryItem).join('');
  } catch(e){el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar histórico.</p>';}
}
function subBadges(subs,missing){
  let html='';
  for(const s of(subs||[])){const n=langName(s),c=LANG_CLS[n]||'sub-other';html+=`<span class="sub-badge ${c}">${esc(n)}</span>`;}
  for(const s of(missing||[])){const n=langName(s);html+=`<span class="sub-badge sub-missing">${esc(n)} ?</span>`;}
  return html||'<span class="sub-badge sub-missing">Sem legendas</span>';
}
function renderHistoryItem(item){
  const sub=item.type==='series'&&item.episode
    ?`${esc(item.episode)}${item.epTitle?' · '+esc(item.epTitle):''} — `:'';
  const subId=item.type==='movie'?item.radarrId:item.sonarrEpisodeId;
  return`<div class="dl-item">
    <div class="dl-header">
      <div class="dl-title">${sub}<span style="color:#f4f4f5">${esc(item.title)}</span>${item.year?`<span class="dl-year"> (${item.year})</span>`:''}</div>
      <span class="dl-date">${fmtDate(item.date)}</span>
    </div>
    ${item.quality?`<div class="dl-meta">${esc(item.quality)}</div>`:''}
    <div class="sub-row">
      <span class="sub-label">Legendas:</span>
      ${subBadges(item.subtitles,item.missingSubtitles)}
      ${subId?`<button class="btn-sub" onclick="searchSubs(this,'${item.type}',${subId})">🔍 Buscar legenda</button>`:''}
    </div>
  </div>`;
}
async function searchSubs(btn, type, id) {
  btn.className='btn-sub loading'; btn.textContent='Buscando…'; btn.disabled=true;
  try {
    const r=await fetch('/api/subtitles/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,id})});
    const d=await r.json();
    if(d.ok){btn.className='btn-sub done';btn.textContent='✓ Solicitado';showToast('Busca de legenda solicitada!');}
    else{btn.className='btn-sub';btn.textContent='🔍 Buscar legenda';btn.disabled=false;showToast('Erro: '+d.error,'#ef4444');}
  } catch{btn.className='btn-sub';btn.textContent='🔍 Buscar legenda';btn.disabled=false;}
}

// ── CALENDAR ────────────────────────────────────────────────────
async function loadCalendar() {
  const el=document.getElementById('calendar-list');
  try {
    const r=await fetch('/api/calendar'), items=await r.json();
    if(!items.length){el.innerHTML='<p class="empty-msg">Nenhum episódio próximo.</p>';return;}
    const today=new Date().toISOString().slice(0,10);
    const byDay={};
    for(const ep of items){
      const d=ep.airDate||'?';
      if(!byDay[d]) byDay[d]=[];
      byDay[d].push(ep);
    }
    let html='';
    for(const [day,eps] of Object.entries(byDay)){
      const isToday=day===today;
      const label=day==='?'?'Sem data':new Date(day+'T12:00:00').toLocaleDateString('pt-BR',{weekday:'long',day:'2-digit',month:'2-digit'});
      html+=`<div class="cal-day">
        <div class="cal-day-header${isToday?' today':''}">${isToday?'Hoje — ':''}${label}</div>
        ${eps.map(ep=>{
          const epCode=`S${String(ep.seasonNumber).padStart(2,'0')}E${String(ep.episodeNumber).padStart(2,'0')}`;
          return`<div class="cal-ep">
            <div class="cal-ep-info">
              <div class="cal-ep-series">${esc(ep.seriesTitle)}</div>
              ${ep.episodeTitle?`<div class="cal-ep-title">${esc(ep.episodeTitle)}</div>`:''}
            </div>
            <span class="cal-ep-num">${epCode}</span>
            <span class="${ep.hasFile?'cal-ep-has':'cal-ep-miss'}" title="${ep.hasFile?'Disponível':'Não baixado'}">${ep.hasFile?'✓':'○'}</span>
          </div>`;
        }).join('')}
      </div>`;
    }
    el.innerHTML=html||'<p class="empty-msg">Nenhum episódio próximo.</p>';
  } catch(e){el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar calendário.</p>';}
}

// ── QBITTORRENT CONTROL ─────────────────────────────────────────
async function loadQbt() {
  const el=document.getElementById('qbt-list');
  try {
    const r=await fetch('/api/qbt/torrents'), d=await r.json();
    document.getElementById('spd-dl').textContent=fmtSpeed(d.dlSpeed||0);
    document.getElementById('spd-up').textContent=fmtSpeed(d.upSpeed||0);
    if(!d.torrents||!d.torrents.length){el.innerHTML='<p class="empty-msg">Nenhum torrent ativo.</p>';return;}
    el.innerHTML=d.torrents.map(renderTorrent).join('');
  } catch(e){el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao conectar ao qBittorrent.</p>';}
}
function torrentStateInfo(state){
  if(['downloading','metaDL','checkingDL'].includes(state)) return{label:'Baixando',cls:'ts-downloading'};
  if(['uploading','checkingUP','forcedUP'].includes(state)) return{label:'Enviando',cls:'ts-uploading'};
  if(['pausedDL','pausedUP'].includes(state))               return{label:'Pausado',cls:'ts-paused'};
  if(['stalledDL','stalledUP'].includes(state))             return{label:'Parado',cls:'ts-stalled'};
  if(['error','missingFiles','unknown'].includes(state))    return{label:'Erro',cls:'ts-error'};
  if(['queuedDL','queuedUP'].includes(state))               return{label:'Na fila',cls:'ts-queued'};
  return{label:state,cls:'ts-queued'};
}
function renderTorrent(t){
  const si=torrentStateInfo(t.state);
  const isPaused=['pausedDL','pausedUP'].includes(t.state);
  const meta=[t.sizeMB?t.sizeMB+' MB':'',t.seeds?t.seeds+' seeds':'',t.category].filter(Boolean).join(' · ');
  const dlSpeed=t.dlspeed>1024?fmtSpeed(t.dlspeed):'';
  return`<div class="torrent-item" id="tor-${t.hash}">
    <div class="torrent-header">
      <div class="torrent-name">${esc(t.name)}</div>
      <span class="torrent-state ${si.cls}">${si.label}</span>
    </div>
    ${meta?`<div class="torrent-meta">${meta}${dlSpeed?' · ↓ '+dlSpeed:''}</div>`:''}
    <div class="prog-bg"><div class="prog-fill prog-downloading" style="width:${t.progress}%"></div></div>
    <div class="torrent-actions">
      <span style="font-size:13px;font-weight:600;color:#a1a1aa">${t.progress}%</span>
      <span style="flex:1"></span>
      ${isPaused
        ?`<button class="btn-tor btn-resume" onclick="qbtAction('${t.hash}','resume',this)">▶ Retomar</button>`
        :`<button class="btn-tor btn-pause"  onclick="qbtAction('${t.hash}','pause',this)">⏸ Pausar</button>`
      }
      <button class="btn-tor btn-del" onclick="qbtAction('${t.hash}','delete',this)">✕ Remover</button>
    </div>
  </div>`;
}
async function qbtAction(hash, action, btn) {
  if(action==='delete'&&!confirm('Remover este torrent da fila? (arquivos baixados são mantidos)')) return;
  btn.disabled=true; btn.textContent='…';
  try {
    await fetch('/api/qbt/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hash,action})});
    if(action==='delete'){const el=document.getElementById('tor-'+hash);if(el)el.remove();}
    else loadQbt();
  } catch{btn.disabled=false;}
}

// ── HEALTH ──────────────────────────────────────────────────────
async function loadHealth() {
  try {
    const r=await fetch('/api/health'), d=await r.json();
    const grid=document.getElementById('svc-grid');
    grid.innerHTML=d.services.map(s=>{
      const cls=!s.ok?'svc-err':(s.issues&&s.issues.length?'svc-warn':'svc-ok');
      const label=!s.ok?'Off':(s.issues&&s.issues.length?'Aviso':'OK');
      const issues=s.issues&&s.issues.length?`<div class="svc-issue">${esc(s.issues[0].slice(0,60))}</div>`:'';
      const version=s.version?`<div style="font-size:10px;color:#52525b">${esc(s.version)}</div>`:'';
      return`<div class="svc-card"><div class="svc-dot ${cls}"></div><div class="svc-name">${esc(s.name)}</div>
        <div style="font-size:11px;color:${!s.ok?'#fca5a5':s.issues&&s.issues.length?'#fbbf24':'#86efac'}">${label}</div>
        ${issues}${version}</div>`;
    }).join('');

    const diskEl=document.getElementById('disk-info');
    if(!d.disk||!d.disk.length){diskEl.innerHTML='<p style="color:#52525b;font-size:13px">Sem dados de disco.</p>';return;}
    diskEl.innerHTML=d.disk.map(dk=>{
      const cls=dk.pct>90?'disk-crit':dk.pct>75?'disk-warn':'disk-ok';
      return`<div style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px">
          <span style="color:#a1a1aa;font-weight:600">${esc(dk.path)}</span>
          <span style="color:#71717a">${dk.freeGB} GB livres de ${dk.totalGB} GB</span>
        </div>
        <div class="disk-bar-bg"><div class="disk-bar-fill ${cls}" style="width:${dk.pct}%"></div></div>
      </div>`;
    }).join('');
  } catch(e){document.getElementById('svc-grid').innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar status.</p>';}
}

// ── QUALITY TOGGLE ──────────────────────────────────────────────
async function loadQualityMode() {
  try {
    const r=await fetch('/api/quality'), d=await r.json();
    const tog=document.getElementById('quality-toggle');
    const desc=document.getElementById('quality-desc');
    tog.checked=d.blurayEnabled||false;
    desc.textContent=d.blurayEnabled?'Modo Bluray ativo — inclui arquivos de alta qualidade (5–20 GB)':'Modo WEB-only ativo — apenas WEBDL/WEBRip 1080p (1–4 GB)';
  } catch{}
}
async function toggleQuality(cb) {
  const desc=document.getElementById('quality-desc');
  desc.textContent='Atualizando...';
  try {
    const r=await fetch('/api/quality',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enableBluray:cb.checked})});
    const d=await r.json();
    if(d.ok){
      desc.textContent=d.blurayEnabled?'Modo Bluray ativo — inclui arquivos de alta qualidade (5–20 GB)':'Modo WEB-only ativo — apenas WEBDL/WEBRip 1080p (1–4 GB)';
      showToast(d.blurayEnabled?'Bluray ativado no Radarr e Sonarr':'Modo WEB-only ativado');
    } else {
      cb.checked=!cb.checked;
      desc.textContent='Erro ao atualizar perfil';
      showToast('Erro: '+d.error,'#ef4444');
    }
  } catch{cb.checked=!cb.checked;desc.textContent='Erro de conexão';}
}

// ── BIBLIOTECA ──────────────────────────────────────────────────
let libSeriesData=[], libMoviesData=[], libActiveTab='series';

let libStorageData={movies:[],series:[]};

function switchLib(lib) {
  libActiveTab=lib;
  document.querySelectorAll('.lib-sub').forEach(b=>b.classList.toggle('active',b.dataset.lib===lib));
  document.getElementById('lib-series').style.display=lib==='series'?'':'none';
  document.getElementById('lib-movies').style.display=lib==='movies'?'':'none';
  document.getElementById('lib-storage').style.display=lib==='storage'?'':'none';
  if(lib==='storage') loadLibStorage();
  else applyLibFilter();
}
async function loadLibSeries() {
  const el=document.getElementById('lib-series');
  el.innerHTML='<div class="flex justify-center py-12"><div class="spinner"></div></div>';
  try{const r=await fetch('/api/library/series');libSeriesData=await r.json();renderLibSeries(libSeriesData);}
  catch{el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar séries.</p>';}
}
async function loadLibMovies() {
  const el=document.getElementById('lib-movies');
  el.innerHTML='<div class="flex justify-center py-12"><div class="spinner"></div></div>';
  try{const r=await fetch('/api/library/movies');libMoviesData=await r.json();renderLibMovies(libMoviesData);}
  catch{el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar filmes.</p>';}
}
async function loadLibStorage() {
  const el=document.getElementById('lib-storage');
  el.innerHTML='<div class="flex justify-center py-12"><div class="spinner"></div></div>';
  try{
    const r=await fetch('/api/library/storage');
    libStorageData=await r.json();
    renderStorage(libStorageData);
  }catch{el.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao carregar storage.</p>';}
}
function fmtSize(mb){
  if(mb>=1024) return (mb/1024).toFixed(1)+' GB';
  return mb+' MB';
}
function renderStorage(d){
  const el=document.getElementById('lib-storage');
  const totalMovies=(d.movies||[]).reduce((s,m)=>s+m.sizeMB,0);
  const totalSeries=(d.series||[]).reduce((s,x)=>s+x.sizeMB,0);
  const totalAll=totalMovies+totalSeries;
  let html=`<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">
    <div class="dl-item" style="flex:1;min-width:180px;margin-bottom:0">
      <div style="color:#71717a;font-size:12px;margin-bottom:4px">Total geral</div>
      <div style="font-size:22px;font-weight:700;color:#f4f4f5">${fmtSize(totalAll)}</div>
    </div>
    <div class="dl-item" style="flex:1;min-width:180px;margin-bottom:0">
      <div style="color:#71717a;font-size:12px;margin-bottom:4px">Filmes (${(d.movies||[]).length})</div>
      <div style="font-size:22px;font-weight:700;color:#7dd3fc">${fmtSize(totalMovies)}</div>
    </div>
    <div class="dl-item" style="flex:1;min-width:180px;margin-bottom:0">
      <div style="color:#71717a;font-size:12px;margin-bottom:4px">Séries (${(d.series||[]).length})</div>
      <div style="font-size:22px;font-weight:700;color:#86efac">${fmtSize(totalSeries)}</div>
    </div>
  </div>`;
  if((d.movies||[]).length){
    html+=`<div class="section-title" style="font-size:15px">Filmes por tamanho</div>`;
    html+=(d.movies||[]).map(m=>`
    <div class="movie-row">
      <div class="movie-info">
        <div class="movie-title">${esc(m.title)}</div>
        <div class="movie-meta">${[m.year,m.quality].filter(Boolean).join(' · ')}</div>
      </div>
      <span style="font-size:13px;font-weight:700;color:#a1a1aa;white-space:nowrap">${fmtSize(m.sizeMB)}</span>
      <button class="btn-search" style="background:#7f1d1d;color:#fca5a5;font-size:11px"
        onclick="deleteMovieFile(this,${m.id},${m.fileId},'${esc(m.title)}')">🗑 Deletar</button>
    </div>`).join('');
  }
  if((d.series||[]).length){
    html+=`<div class="section-title" style="font-size:15px;margin-top:24px">Séries por tamanho</div>`;
    html+=(d.series||[]).map(s=>`
    <div class="movie-row">
      <div class="movie-info">
        <div class="movie-title">${esc(s.title)}</div>
        <div class="movie-meta">${s.year||''} · ${s.episodes} episódios</div>
      </div>
      <span style="font-size:13px;font-weight:700;color:#a1a1aa;white-space:nowrap">${fmtSize(s.sizeMB)}</span>
    </div>`).join('');
  }
  el.innerHTML=html||'<p class="empty-msg">Nenhum arquivo encontrado.</p>';
}
async function deleteMovieFile(btn, movieId, fileId, title) {
  if(!confirm(`Deletar o arquivo de "${title}"?\nO filme permanece no Radarr para re-download futuro.`)) return;
  btn.disabled=true; btn.textContent='…';
  try{
    const r=await fetch('/api/library/movies/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({movieId,fileId})});
    const d=await r.json();
    if(d.ok){btn.closest('.movie-row').remove();showToast(`"${title}" deletado do disco`);}
    else{btn.disabled=false;btn.textContent='🗑 Deletar';showToast('Erro: '+d.error,'#ef4444');}
  }catch{btn.disabled=false;btn.textContent='🗑 Deletar';}
}
function applyLibFilter(){
  const q=(document.getElementById('libFilter')?.value||'').toLowerCase().trim();
  if(libActiveTab==='series') renderLibSeries(q?libSeriesData.filter(s=>s.title.toLowerCase().includes(q)):libSeriesData);
  else if(libActiveTab==='movies') renderLibMovies(q?libMoviesData.filter(m=>m.title.toLowerCase().includes(q)):libMoviesData);
}
function renderLibSeries(list){
  const el=document.getElementById('lib-series');
  el.innerHTML=list.length?list.map(s=>seriesRow(s)).join(''):'<p class="empty-msg">Nenhuma série na biblioteca.</p>';
}
function renderLibMovies(list){
  const el=document.getElementById('lib-movies');
  el.innerHTML=list.length?list.map(m=>movieRow(m)).join(''):'<p class="empty-msg">Nenhum filme na biblioteca.</p>';
}
function seriesStatusLabel(status){
  if(status==='continuing') return'<span class="series-status ss-continuing">Em andamento</span>';
  if(status==='ended')      return'<span class="series-status ss-ended">Encerrada</span>';
  if(status==='upcoming')   return'<span class="series-status ss-upcoming">Em breve</span>';
  return'';
}
function seasonPill(season, seriesId, seriesTitle){
  const sn=season.number,label=`S${String(sn).padStart(2,'0')}`,tip=`${season.hasFiles}/${season.total} ep`;
  if(season.status==='full') return`<span class="s-pill s-full" title="${tip}">${label} ✓</span>`;
  if(season.status==='partial') return`<span class="s-pill s-partial" title="Parcial · ${tip} · clique para buscar restantes" onclick="searchSeason(this,${seriesId},${sn},'${esc(seriesTitle)}')">${label} ${season.hasFiles}/${season.total}</span>`;
  return`<span class="s-pill s-empty" title="Sem arquivo · clique para baixar" onclick="searchSeason(this,${seriesId},${sn},'${esc(seriesTitle)}')">${label} ↓</span>`;
}
function seriesRow(s){
  const pills=s.seasons.map(season=>seasonPill(season,s.id,s.title)).join('');
  return`<div class="series-row">
    <div class="series-header">
      <span class="series-title">${esc(s.title)}</span>
      ${s.year?`<span class="series-year">${s.year}</span>`:''}
      ${seriesStatusLabel(s.status)}
    </div>
    <div class="seasons-row">${pills||'<span style="color:#52525b;font-size:12px">Sem temporadas</span>'}</div>
  </div>`;
}
function movieRow(m){
  const meta=[m.year,m.quality,m.sizeMB?m.sizeMB+' MB':''].filter(Boolean).join(' · ');
  const right=m.hasFile?`<span class="m-has-file" title="Arquivo disponível">✓</span>`
    :`<button class="btn-search" onclick="searchMovie(this,${m.id},'${esc(m.title)}')">↓ Baixar</button>`;
  return`<div class="movie-row">
    <div class="movie-info"><div class="movie-title">${esc(m.title)}</div>${meta?`<div class="movie-meta">${meta}</div>`:''}</div>
    ${right}
  </div>`;
}
async function searchSeason(pill, seriesId, seasonNum, seriesTitle) {
  if(pill.classList.contains('s-searching')||pill.classList.contains('s-done')) return;
  const orig=pill.outerHTML;
  pill.className='s-pill s-searching'; pill.textContent=`S${String(seasonNum).padStart(2,'0')} …`; pill.onclick=null;
  try {
    const r=await fetch('/api/action/season-search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seriesId,seasonNumber:seasonNum})});
    const d=await r.json();
    if(d.ok){pill.className='s-pill s-done';pill.textContent=`S${String(seasonNum).padStart(2,'0')} ✓`;showToast(`Buscando T${seasonNum} de "${seriesTitle}"…`);}
    else{pill.outerHTML=orig;showToast(`Erro: ${d.error}`,'#ef4444');}
  } catch{pill.outerHTML=orig;}
}
async function searchMovie(btn, movieId, title) {
  if(btn.disabled) return;
  btn.disabled=true; btn.className='btn-search loading'; btn.textContent='…';
  try {
    const r=await fetch('/api/action/movie-search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({movieId})});
    const d=await r.json();
    if(d.ok){btn.className='btn-search done';btn.textContent='✓ Buscando';showToast(`Buscando "${title}"…`);}
    else{btn.disabled=false;btn.className='btn-search';btn.textContent='↓ Baixar';showToast(`Erro: ${d.error}`,'#ef4444');}
  } catch{btn.disabled=false;btn.className='btn-search';btn.textContent='↓ Baixar';}
}

// ── WATCHLIST (localStorage) ────────────────────────────────────
function wlKey(item){ return `${item.type}-${item.tmdbId||item.tvdbId}`; }
function wlLoad(){ try{return JSON.parse(localStorage.getItem('pedroflix_wl')||'[]');}catch{return[];} }
function wlSave(list){ localStorage.setItem('pedroflix_wl', JSON.stringify(list)); }
function wlAdd(item){
  const list=wlLoad();
  if(!list.find(x=>wlKey(x)===wlKey(item))){list.unshift(item);wlSave(list);}
}
function wlRemove(key){
  wlSave(wlLoad().filter(x=>wlKey(x)!==key));
  renderWatchlist();
}
function renderWatchlist(){
  const el=document.getElementById('wl-list');
  const list=wlLoad();
  if(!list.length){el.innerHTML='<p class="empty-msg">Watchlist vazia. ♡ para salvar itens da busca.</p>';return;}
  el.innerHTML=list.map(item=>{
    const meta=[item.year,item.type==='movie'?'Filme':'Série'].filter(Boolean).join(' · ');
    const key=wlKey(item);
    return`<div class="wl-item">
      ${item.poster?`<img src="${item.poster}" style="width:36px;height:54px;object-fit:cover;border-radius:4px" onerror="this.style.display='none'">`:''}
      <div class="wl-info"><div class="wl-title">${esc(item.title)}</div><div class="wl-meta">${meta}</div></div>
      <button class="btn-search" onclick="addFromWatchlist('${key}',this)" style="font-size:12px">+ Adicionar</button>
      <button class="btn-wl-remove" onclick="wlRemove('${key}')" title="Remover da watchlist">✕</button>
    </div>`;
  }).join('');
}
async function addFromWatchlist(key, btn) {
  const list=wlLoad(), item=list.find(x=>wlKey(x)===key);
  if(!item) return;
  btn.disabled=true; btn.textContent='…'; btn.className='btn-search loading';
  const body=item.type==='movie'
    ?{type:'movie',tmdbId:item.tmdbId,title:item.title,year:item.year}
    :{type:'series',tvdbId:item.tvdbId,title:item.title,year:item.year};
  try {
    const r=await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){btn.textContent='✓ Adicionado';btn.className='btn-search done';wlRemove(key);showToast(`"${item.title}" adicionado!`);}
    else{btn.disabled=false;btn.textContent='+ Adicionar';btn.className='btn-search';showToast('Erro: '+d.error,'#ef4444');}
  } catch{btn.disabled=false;btn.textContent='+ Adicionar';btn.className='btn-search';}
}

// ── SEARCH ──────────────────────────────────────────────────────
let currentType='all', debounceTimer;
document.querySelectorAll('.pill').forEach(p=>{
  p.addEventListener('click',()=>{
    document.querySelectorAll('.pill').forEach(x=>x.classList.remove('active'));
    p.classList.add('active');
    currentType=p.dataset.type;
    if(currentType==='watchlist'){
      document.getElementById('results').innerHTML='';
      document.getElementById('watchlist-panel').style.display='';
      renderWatchlist();
    } else {
      document.getElementById('watchlist-panel').style.display='none';
      doSearch();
    }
  });
});
document.getElementById('searchInput').addEventListener('input',()=>{
  clearTimeout(debounceTimer);
  debounceTimer=setTimeout(doSearch,350);
});
async function doSearch(){
  if(currentType==='watchlist') return;
  const q=document.getElementById('searchInput').value.trim();
  const results=document.getElementById('results');
  if(q.length<2){results.innerHTML='';return;}
  results.innerHTML='<div class="flex justify-center py-16"><div class="spinner"></div></div>';
  try {
    const res=await fetch(`/api/search?q=${encodeURIComponent(q)}&type=${currentType}`);
    const data=await res.json();
    renderResults(data);
  } catch{results.innerHTML='<p class="text-center text-zinc-500 py-16">Erro ao buscar.</p>';}
}
function renderResults(data){
  const results=document.getElementById('results');
  const movies=data.movies||[],series=data.series||[];
  if(!movies.length&&!series.length){results.innerHTML='<p class="text-center text-zinc-500 py-16">Nenhum resultado encontrado.</p>';return;}
  let html='';
  if(currentType==='all'){
    if(movies.length) html+=section('Filmes',movies,'movie');
    if(series.length) html+=section('Séries',series,'series');
  } else if(currentType==='movie'){html=grid(movies,'movie');}
  else{html=grid(series,'series');}
  results.innerHTML=html;
}
function section(title,items,type){return`<div class="mb-10"><div class="section-title">${title}</div>${grid(items,type)}</div>`;}
function grid(items,type){return`<div id="grid">${items.map(i=>card(i,type)).join('')}</div>`;}
function card(item,type){
  const isMovie=type==='movie';
  const poster=item.poster
    ?`<img src="${item.poster}" alt="${esc(item.title)}" loading="lazy" onerror="this.parentNode.innerHTML='<div class=no-poster>🎬</div>'">`
    :`<div class="no-poster">🎬</div>`;
  const badgeCls=isMovie?'badge-movie':'badge-series';
  const meta=isMovie
    ?`${item.year||''}${item.runtime?' · '+item.runtime+' min':''}`
    :`${item.year||''}${item.seasons?' · '+item.seasons+' temp.':''}${item.status==='ended'?' · Encerrada':''}`;
  const genres=(item.genres||[]).map(g=>`<span class="genre-tag">${esc(g)}</span>`).join('');
  let btnState,btnText,btnDisabled;
  if(item.inLibrary){btnState='library';btnText='✓ Na biblioteca';btnDisabled=true;}
  else{btnState='idle';btnText='+ Adicionar';btnDisabled=false;}
  const idAttr=isMovie?`data-tmdb="${item.tmdbId}"`:`data-tvdb="${item.tvdbId}"`;
  const wlData=JSON.stringify({type,title:item.title,year:item.year,poster:item.poster,tmdbId:item.tmdbId,tvdbId:item.tvdbId});
  return`<div class="card">
    <div class="poster-wrap">
      ${poster}
      <span class="badge-type ${badgeCls}">${isMovie?'Filme':'Série'}</span>
      ${item.rating?`<span class="badge-rating">★ ${item.rating}</span>`:''}
    </div>
    <div class="card-body">
      <div class="card-title">${esc(item.title)}</div>
      <div class="card-meta">${esc(meta)}</div>
      ${genres?`<div class="genres">${genres}</div>`:''}
      ${item.overview?`<div class="card-overview">${esc(item.overview)}</div>`:''}
      <div style="display:flex;gap:4px;margin-top:auto">
        <button class="btn-add ${btnState}" style="flex:1"
          ${idAttr} data-type="${type}" data-title="${esc(item.title)}" data-year="${item.year||0}"
          ${btnDisabled?'disabled':''} onclick="addItem(this)">${btnText}</button>
        ${!item.inLibrary?`<button class="btn-wl" title="Salvar na watchlist" onclick="wlAdd(${esc(wlData)});showToast('Salvo na watchlist ♡')">♡</button>`:''}
      </div>
    </div>
  </div>`;
}
// ── JELLYFIN ────────────────────────────────────────────────────
async function loadJellyfin() {
  const sessEl=document.getElementById('jelly-sessions');
  const recEl=document.getElementById('jelly-recent');
  try {
    const [sr,rr]=await Promise.all([fetch('/api/jellyfin/sessions'),fetch('/api/jellyfin/recent')]);
    const sessions=await sr.json(), recent=await rr.json();
    if(!sessions.length){sessEl.innerHTML='<p class="empty-msg">Nenhuma sessão ativa no momento.</p>';}
    else{sessEl.innerHTML=sessions.map(s=>{
      const title=s.series?`${esc(s.series)} — ${esc(s.title)}`:esc(s.title);
      const type=s.type==='Episode'?'Série':'Filme';
      return`<div class="dl-item">
        <div class="dl-header">
          <div class="dl-title">${title}</div>
          <span class="st-badge ${s.isPaused?'st-warning':'st-downloading'}">${s.isPaused?'Pausado':'Assistindo'}</span>
        </div>
        <div class="dl-meta">${esc(s.user)} · ${esc(s.device)} · ${type}</div>
        <div class="prog-bg"><div class="prog-fill prog-downloading" style="width:${s.progressPct}%"></div></div>
        <div class="dl-footer">
          <span class="dl-pct">${s.progressPct}%</span>
          <span class="dl-eta">${s.posMin}min / ${s.totalMin}min</span>
        </div>
      </div>`;
    }).join('');}
    if(!recent.length){recEl.innerHTML='<p class="empty-msg">Nada adicionado recentemente.</p>';}
    else{recEl.innerHTML=`<div style="display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))">
      ${recent.map(item=>{
        const label=item.series?`${esc(item.series)} — ${esc(item.title)}`:esc(item.title);
        return`<div class="dl-item" style="margin-bottom:0;display:flex;gap:12px;align-items:flex-start">
          ${item.thumb?`<img src="${item.thumb}" style="width:48px;height:72px;object-fit:cover;border-radius:6px;flex-shrink:0" onerror="this.style.display='none'">`:''}
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:13px;color:#f4f4f5;margin-bottom:3px">${label}</div>
            <div style="font-size:11px;color:#71717a">${item.year||''} · ${item.type==='Episode'?'Episódio':'Filme'}</div>
            ${item.overview?`<div style="font-size:11px;color:#52525b;margin-top:4px;line-height:1.4">${esc(item.overview)}</div>`:''}
          </div>
        </div>`;
      }).join('')}
    </div>`;}
  } catch(e){
    sessEl.innerHTML='<p class="empty-msg" style="color:#fca5a5">Erro ao conectar ao Jellyfin.</p>';
    recEl.innerHTML='';
  }
}

// ── PROWLARR INDEXERS ────────────────────────────────────────────
async function loadProwlarrIndexers() {
  const el=document.getElementById('prowlarr-indexers');
  try{
    const r=await fetch('/api/prowlarr/indexers'), d=await r.json();
    if(!d.length){el.innerHTML='<p style="color:#52525b;font-size:13px">Sem indexadores configurados.</p>';return;}
    el.innerHTML=`<div style="display:flex;flex-wrap:wrap;gap:8px">
      ${d.map(idx=>`<div style="background:#18181b;border:1px solid ${idx.enabled?'#166534':'#3f3f46'};border-radius:8px;padding:7px 12px;display:flex;align-items:center;gap:8px">
        <div style="width:8px;height:8px;border-radius:50%;background:${idx.enabled?'#22c55e':'#52525b'}"></div>
        <span style="font-size:13px;font-weight:600;color:${idx.enabled?'#f4f4f5':'#71717a'}">${esc(idx.name)}</span>
        <span style="font-size:10px;color:#52525b">${esc(idx.protocol)}</span>
      </div>`).join('')}
    </div>`;
  }catch{el.innerHTML='<p style="color:#52525b;font-size:13px">Não foi possível carregar indexadores.</p>';}
}

// ── NOTIFICATIONS ────────────────────────────────────────────────
let _knownQueueIds=new Set();
function updateNotifButton(){
  const btn=document.getElementById('btn-notif');
  if(!btn) return;
  const perm=Notification?.permission||'default';
  if(perm==='granted'){btn.textContent='🔔 Notificações ativas';btn.style.color='#86efac';}
  else if(perm==='denied'){btn.textContent='🔕 Notificações bloqueadas';btn.style.color='#fca5a5';}
}
async function requestNotifPermission(){
  if(!('Notification' in window)){showToast('Notificações não suportadas','#ef4444');return;}
  const p=await Notification.requestPermission();
  updateNotifButton();
  if(p==='granted') showToast('Notificações ativadas!');
}
function checkNotifications(newItems){
  if(Notification?.permission!=='granted') return;
  const newIds=new Set(newItems.map(i=>i.id||i.downloadId));
  if(_knownQueueIds.size===0){_knownQueueIds=newIds;return;}
  for(const id of _knownQueueIds){
    if(!newIds.has(id)){
      new Notification('Pedroflix — Download concluído!',{body:'Um item terminou de baixar.',icon:'/favicon.ico'});
      break;
    }
  }
  _knownQueueIds=newIds;
}

// ── MAGNET ADD ───────────────────────────────────────────────────
async function addMagnet(){
  const inp=document.getElementById('magnet-input');
  const magnet=(inp?.value||'').trim();
  if(!magnet.startsWith('magnet:')){showToast('Cole um link magnet válido (magnet:...)','#ef4444');return;}
  try{
    const r=await fetch('/api/qbt/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({magnet})});
    const d=await r.json();
    if(d.ok){inp.value='';showToast('Torrent adicionado ao qBittorrent!');loadQbt();}
    else showToast('Erro: '+d.error,'#ef4444');
  }catch{showToast('Erro ao conectar','#ef4444');}
}

// ── SUBTITLES BULK ────────────────────────────────────────────────
async function searchAllSubs(btn){
  btn.disabled=true; btn.textContent='Buscando...';
  try{
    const r=await fetch('/api/subtitles/search-all',{method:'POST'});
    const d=await r.json();
    if(d.ok){showToast('Busca de legendas iniciada para toda a biblioteca!');btn.textContent='✓ Solicitado';}
    else{showToast('Erro: '+d.error,'#ef4444');btn.disabled=false;btn.textContent='🔍 Buscar todas as legendas';}
  }catch{btn.disabled=false;btn.textContent='🔍 Buscar todas as legendas';}
}

async function addItem(btn){
  if(btn.disabled) return;
  btn.disabled=true; btn.className='btn-add loading'; btn.textContent='Adicionando...';
  const type=btn.dataset.type,title=btn.dataset.title,year=parseInt(btn.dataset.year)||0;
  const body=type==='movie'
    ?{type,tmdbId:parseInt(btn.dataset.tmdb),title,year}
    :{type,tvdbId:parseInt(btn.dataset.tvdb),title,year};
  try {
    const res=await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await res.json();
    if(data.ok){btn.className='btn-add done';btn.textContent='✓ Adicionado!';showToast(`"${title}" adicionado!`);}
    else{btn.disabled=false;btn.className='btn-add error';btn.textContent='Erro — tentar novamente';showToast(`Erro: ${data.error}`,'#ef4444');setTimeout(()=>{btn.className='btn-add idle';btn.textContent='+ Adicionar';btn.disabled=false;},3000);}
  } catch{btn.disabled=false;btn.className='btn-add error';btn.textContent='Erro — tentar novamente';setTimeout(()=>{btn.className='btn-add idle';btn.textContent='+ Adicionar';btn.disabled=false;},3000);}
}
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
