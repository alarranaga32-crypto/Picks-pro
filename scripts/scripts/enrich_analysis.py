#!/usr/bin/env python3
"""Enrich soccer picks with provider-sourced form and injuries; never invent missing data."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# This file currently lives at scripts/scripts/enrich_analysis.py.
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "picks.json"
BASE = "https://v3.football.api-sports.io/"
SOURCE = "https://www.api-football.com/"
DOCS = "https://api-sports.io/documentation/football/v3"


def api(path, params, key):
    req = Request(
        BASE + path + "?" + urlencode(params),
        headers={"x-apisports-key": key, "User-Agent": "PicksPro/1.3"},
    )
    with urlopen(req, timeout=25) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError("API-Football returned an API error")
    return payload.get("response", [])


def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def same_team(a, b):
    # Exact normalized names only; fuzzy matches could attach another game's report.
    left, right = norm(a), norm(b)
    return bool(left and right and left == right)


def unavailable(status, message):
    return {
        "status": status,
        "provider": "API-Football",
        "source_url": SOURCE,
        "docs_url": DOCS,
        "message": message,
    }


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    picks = data.get("picks", [])
    key = os.getenv("API_SPORTS_KEY")
    events = {}

    for pick in picks:
        if not str(pick.get("sport_key", "")).startswith("soccer_"):
            pick["analysis"] = unavailable(
                "unsupported",
                "Esta fuente cubre fútbol; no hay informe verificado configurado para este deporte.",
            )
            continue
        event_id = (pick.get("sport_key"), pick.get("event"), pick.get("commence_time"))
        events.setdefault(event_id, pick)
        if not key:
            pick["analysis"] = unavailable(
                "not_configured",
                "Añade el secreto API_SPORTS_KEY para consultar lesiones y forma.",
            )
        else:
            pick["analysis"] = unavailable(
                "not_enriched",
                "La consulta diaria cubre hasta cuatro partidos de fútbol por límites de uso del proveedor.",
            )

    if key:
        schedules, forms = {}, {}
        for (sport, event, start), _pick in list(events.items())[:4]:
            try:
                sides = str(event or "").split(" vs ", 1)
                if len(sides) != 2:
                    continue
                date = str(start or "")[:10]
                if date not in schedules:
                    schedules[date] = api("fixtures", {"date": date, "timezone": "UTC"}, key)
                matches = []
                for fixture in schedules[date]:
                    teams = fixture.get("teams", {})
                    home = teams.get("home", {}).get("name", "")
                    away = teams.get("away", {}).get("name", "")
                    if same_team(home, sides[0]) and same_team(away, sides[1]):
                        matches.append(fixture)
                if len(matches) != 1:
                    result = unavailable(
                        "no_match",
                        "No hubo una coincidencia única de ambos equipos; no se asociaron datos.",
                    )
                else:
                    fixture = matches[0]
                    fixture_id = fixture.get("fixture", {}).get("id")
                    team_form = []
                    for side in ("home", "away"):
                        team = fixture.get("teams", {}).get(side, {})
                        team_id = team.get("id")
                        if team_id not in forms:
                            forms[team_id] = (
                                api("fixtures", {"team": team_id, "last": 5}, key)
                                if team_id else []
                            )
                        records = []
                        for past in forms[team_id]:
                            if past.get("fixture", {}).get("status", {}).get("short") not in {"FT", "AET", "PEN"}:
                                continue
                            teams = past.get("teams", {})
                            goals = past.get("goals", {})
                            is_home = teams.get("home", {}).get("id") == team_id
                            scored = goals.get("home" if is_home else "away")
                            conceded = goals.get("away" if is_home else "home")
                            if isinstance(scored, (int, float)) and isinstance(conceded, (int, float)):
                                records.append("W" if scored > conceded else "D" if scored == conceded else "L")
                        team_form.append({
                            "team": team.get("name"),
                            "record": "".join(records[:5]),
                            "games": len(records[:5]),
                        })
                    raw_injuries = api("injuries", {"fixture": fixture_id}, key) if fixture_id else []
                    injuries = [
                        {
                            "player": item.get("player", {}).get("name"),
                            "team": item.get("team", {}).get("name"),
                            "status": item.get("player", {}).get("type"),
                            "reason": item.get("player", {}).get("reason"),
                        }
                        for item in raw_injuries
                    ]
                    result = {
                        "status": "available",
                        "provider": "API-Football",
                        "source_url": SOURCE,
                        "docs_url": DOCS,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "team_form": team_form,
                        "injuries": injuries,
                        "injuries_note": "La fuente no reporta bajas para este partido en esta consulta." if not injuries else None,
                    }
                for pick in picks:
                    if (pick.get("sport_key"), pick.get("event"), pick.get("commence_time")) == (sport, event, start):
                        pick["analysis"] = result
            except Exception as error:
                result = unavailable(
                    "source_error",
                    "Error del proveedor; verifica la clave, la cobertura y el límite de consultas.",
                )
                print("API-Football error:", type(error).__name__)
                for pick in picks:
                    if (pick.get("sport_key"), pick.get("event"), pick.get("commence_time")) == (sport, event, start):
                        pick["analysis"] = result

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
