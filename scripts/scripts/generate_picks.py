#!/usr/bin/env python3
"""Market-consensus pick generator; this is a transparent baseline, not an independent model."""
import json, os, statistics, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "picks.json"
BASE = "https://api.the-odds-api.com/v4"
MARKETS = ("h2h", "spreads", "totals")
REGIONS = os.getenv("ODDS_REGIONS", "us,mx,uk,eu,au")


def api(path, params, key):
    url = f"{BASE}/{path}?{urlencode({**params, 'apiKey': key})}"
    req = Request(url, headers={"User-Agent": "PicksPro/1.0"})
    with urlopen(req, timeout=35) as response:
        return json.load(response)


def write(data):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=OUT.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        temp = f.name
    os.replace(temp, OUT)


def empty(status, message):
    write({"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
           "status": status, "message": message, "events_analyzed": 0,
           "best_edge": None, "sports": [], "picks": [],
           "method": "Consenso de cuotas sin margen; no es un modelo independiente."})


def main():
    key = os.getenv("ODDS_API_KEY")
    if not key:
        empty("needs_configuration", "Falta el secreto ODDS_API_KEY; no se generaron datos ficticios.")
        print("ODDS_API_KEY is not configured", file=sys.stderr)
        return 0
    try:
        sports = [s for s in api("sports", {"all": "false"}, key) if s.get("active")]
        picks, seen_events = [], set()
        for sport in sports:
            try:
                events = api(f"sports/{sport['key']}/odds", {
                    "regions": REGIONS, "markets": ",".join(MARKETS), "oddsFormat": "decimal",
                    "dateFormat": "iso", "bookmakers": ""}, key)
            except HTTPError as exc:
                if exc.code in (422, 404):
                    continue
                raise
            for event in events:
                seen_events.add(event.get("id"))
                groups = {}
                for book in event.get("bookmakers", []):
                    for market in book.get("markets", []):
                        for outcome in market.get("outcomes", []):
                            price = outcome.get("price")
                            if not isinstance(price, (int, float)) or price <= 1:
                                continue
                            point = outcome.get("point")
                            line = round(point, 3) if point is not None else None
                            group_key = (market.get("key"), line)
                            groups.setdefault(group_key, {}).setdefault(book.get("key", ""), []).append(outcome)
                for (market_key, line), books in groups.items():
                    fair_values, best = {}, {}
                    for book_key, outcomes in books.items():
                        probs = [1 / float(o["price"]) for o in outcomes]
                        total = sum(probs)
                        if not total:
                            continue
                        for outcome, prob in zip(outcomes, probs):
                            name, point = outcome.get("name", ""), outcome.get("point")
                            key_out = (name, point)
                            fair_values.setdefault(key_out, []).append(prob / total)
                            if key_out not in best or outcome["price"] > best[key_out]["price"]:
                                best[key_out] = {"price": outcome["price"], "book": book_key}
                    for outcome_key, offer in best.items():
                        vals = fair_values.get(outcome_key, [])
                        if not vals:
                            continue
                        estimated = sum(vals) / len(vals)
                        odds = float(offer["price"])
                        implied = 1 / odds
                        edge = estimated - implied
                        dispersion = statistics.pstdev(vals) if len(vals) > 1 else 0.25
                        confidence = min(1.0, len(vals) / 5) * max(0.2, 1 - dispersion * 2)
                        b = (odds - 1) * estimated - (1 - estimated)
                        kelly = max(0, b / (odds - 1)) if odds > 1 else 0
                        stake_units = min(1.0, kelly * 25) if edge >= .03 and confidence >= .55 else 0
                        is_value = edge >= .03 and confidence >= .55 and len(vals) >= 2
                        picks.append({
                            "event": f"{event.get('home_team', '')} vs {event.get('away_team', '')}",
                            "sport_key": sport["key"], "sport": sport.get("title", sport["key"]),
                            "commence_time": event.get("commence_time"), "market": market_key,
                            "selection": outcome_key[0] + (f" ({outcome_key[1]:+g})" if outcome_key[1] is not None else ""),
                            "bookmaker": offer["book"], "odds": odds,
                            "implied_probability": implied, "estimated_probability": estimated,
                            "edge": edge, "confidence": confidence, "stake_units": stake_units,
                            "decision": "VALUE" if is_value else "NO BET",
                            "books_count": len(vals), "estimation": "market_consensus_no_vig"})
        picks.sort(key=lambda p: (p["decision"] == "VALUE", p["edge"]), reverse=True)
        write({"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
               "status": "ok", "message": "Probabilidad estimada = promedio de consenso sin margen entre casas; no es una predicción independiente.",
               "events_analyzed": len(seen_events), "best_edge": max((p["edge"] for p in picks), default=None),
               "sports": [{"key": s["key"], "title": s.get("title", s["key"])} for s in sports],
               "picks": picks[:500], "method": "Consenso de cuotas sin margen; NO BET si edge < 3%, confianza < 55% o faltan casas."})
        print(f"Generated {len(picks)} candidates from {len(seen_events)} events")
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
        empty("error", f"No fue posible actualizar cuotas: {exc}")
        raise


if __name__ == "__main__":
    main()
