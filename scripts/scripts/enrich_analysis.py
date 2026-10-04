#!/usr/bin/env python3
"""Attach provider-sourced soccer form and injury context; never infer missing data."""
import json, os, re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "picks.json"
BASE = "https://v3.football.api-sports.io/"
SOURCE = "https://www.api-football.com/"
DOCS = "https://api-sports.io/documentation/football/v3"

def api(path, params, key):
    req = Request(BASE + path + "?" + urlencode(params), headers={"x-apisports-key": key, "User-Agent": "PicksPro/1.3"})
    with urlopen(req, timeout=25) as response:
        return json.load(response).get("response", [])

def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

def same_team(a, b):
    a, b = norm(a), norm(b)
    return bool(a and b and (a == b or SequenceMatcher(None, a, b).ratio() >= .86))

def unavailable(status, message):
    return {"status": status, "provider": "API-Football", "source_url": SOURCE, "docs_url": DOCS, "message": message}

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    picks = data.get("picks", [])
    key = os.getenv("API_SPORTS_KEY")
    events = {}
    for p in picks:
        if not str(p.get("sport_key", "")).startswith("soccer_"): 
            p["analysis"] = unavailable("unsupported", "Esta fuente cubre fútbol; no hay informe verificado configurado para este deporte.")
            continue
        eid = (p.get("sport_key"), p.get("event"), p.get("commence_time"))
        events.setdefault(eid, p)
        p["analysis"] = unavailable("not_configured" if not key else "not_enriched", "Añade el secreto API_SPORTS_KEY para consultar lesiones y forma." if not key else "Evento fuera del lote consultado.")
    if key:
        schedules, forms = {}, {}
        for (sport, name, start), pick in list(events.items())[:4]:
            try:
                sides = name.split(" vs ", 1)
                if len(sides) != 2: continue
                date = (start or "")[:10]
                if date not in schedules: schedules[date] = api("fixtures", {"date": date, "timezone": "UTC"}, key)
                matches = []
                for f in schedules[date]:
                    teams = f.get("teams", {}); home = teams.get("home", {}).get("name", ""); away = teams.get("away", {}).get("name", "")
                    if same_team(home, sides[0]) and same_team(away, sides[1]): matches.append(f)
                if not matches:
                    result = unavailable("no_match", "No se encontró coincidencia de ambos equipos; no se asociaron datos.")
                else:
                    f = matches[0]; fid = f.get("fixture", {}).get("id"); recent_form = []
                    for side in ("home", "away"):
                        team = f.get("teams", {}).get(side, {}); tid = team.get("id")
                        if tid not in forms: forms[tid] = api("fixtures", {"team": tid, "last": 5}, key) if tid else []
                        seq = []
                        for past in forms[tid]:
                            if past.get("fixture", {}).get("status", {}).get("short") not in ("FT", "AET", "PEN"): continue
                            teams, goals = past.get("teams", {}), past.get("goals", {}); is_home = teams.get("home", {}).get("id") == tid
                            a, b = goals.get("home" if is_home else "away"), goals.get("away" if is_home else "home")
                            if isinstance(a, (int,float)) and isinstance(b, (int,float)): seq.append("W" if a>b else "D" if a==b else "L")
                        recent_form.append({"team": team.get("name"), "record": "".join(seq[:5]), "games": len(seq[:5])})
                    raw = api("injuries", {"fixture": fid}, key) if fid else []
                    injuries = [{"player": i.get("player",{}).get("name"), "team": i.get("team",{}).get("name"), "status": i.get("player",{}).get("type"), "reason": i.get("player",{}).get("reason")} for i in raw]
                    result = {"status":"available", "provider":"API-Football", "source_url":SOURCE, "docs_url":DOCS, "checked_at":datetime.now(timezone.utc).isoformat(), "team_form":recent_form, "injuries":injuries, "injuries_note":"La fuente no reporta bajas para este partido." if not injuries else None}
                for p in picks:
                    if (p.get("sport_key"), p.get("event"), p.get("commence_time")) == (sport, name, start): p["analysis"] = result
            except Exception as error:
                msg = "Error del proveedor; verifica la clave, cobertura y cuota."
                for p in picks:
                    if (p.get("sport_key"), p.get("event"), p.get("commence_time")) == (sport, name, start): p["analysis"] = unavailable("source_error", msg)
                print("API-Football error:", type(error).__name__)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
