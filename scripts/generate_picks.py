#!/usr/bin/env python3
"""Transparent market-consensus analysis; not an independent prediction model."""
import json, os, statistics, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "picks.json"
BASE = "https://api.the-odds-api.com/v4/"
LAST_REQUEST = 0.0

class APIError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(f"The Odds API HTTP {code}: {message}")

def api(path, params, key):
    global LAST_REQUEST
    url = BASE + path + "?" + urlencode({**params, "apiKey": key})
    for attempt in range(4):
        delay = max(0, 2 - (time.monotonic() - LAST_REQUEST))
        if delay:
            time.sleep(delay)
        LAST_REQUEST = time.monotonic()
        try:
            with urlopen(Request(url, headers={"User-Agent": "PicksPro/1.2"}), timeout=35) as response:
                print("The Odds API credits: remaining=" + response.headers.get("x-requests-remaining", "unknown") + ", used=" + response.headers.get("x-requests-used", "unknown"))
                return json.load(response)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
                message = str(payload.get("message", payload.get("error", body)))
            except (ValueError, AttributeError):
                message = body or str(error.reason)
            message = " ".join(message.split())[:300]
            if error.code == 429 and attempt < 3:
                retry = error.headers.get("Retry-After")
                try:
                    wait = min(30, max(2, float(retry))) if retry else 2 ** (attempt + 2)
                except ValueError:
                    wait = 2 ** (attempt + 2)
                print(f"HTTP 429; retrying in {wait:g}s")
                time.sleep(wait)
                continue
            raise APIError(error.code, message) from None

def write(data):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=OUT.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        name = f.name
    os.replace(name, OUT)

def empty(status, message):
    write({"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "status": status,
           "message": message, "events_analyzed": 0, "best_edge": None, "sports": [], "picks": [],
           "method": "Consenso sin margen; no es modelo independiente."})

def limited(error):
    empty("rate_limited", "The Odds API respondió HTTP 429 tras reintentos. Puede ser un límite temporal o de créditos; revisa el uso de la cuenta. No se inventaron datos.")
    print(error)

def main():
    key = os.getenv("ODDS_API_KEY")
    if not key:
        empty("needs_configuration", "Falta ODDS_API_KEY; no se inventaron datos.")
        return
    try:
        sports = [s for s in api("sports", {"all": "false"}, key) if s.get("active")]
    except APIError as error:
        if error.code == 429:
            limited(error)
            return
        raise

    now = datetime.now(timezone.utc)
    picks, events_seen = [], set()
    regions = os.getenv("ODDS_REGIONS", "us")
    markets = os.getenv("ODDS_MARKETS", "h2h,spreads,totals")
    for sport in sports:
        try:
            events = api("sports/" + sport["key"] + "/odds", {
                "regions": regions, "markets": markets,
                "oddsFormat": "decimal", "dateFormat": "iso"
            }, key)
        except APIError as error:
            if error.code in (404, 422):
                print(f"Skipping unavailable market for {sport['key']}: {error.message}")
                continue
            if error.code == 429:
                limited(error)
                return
            raise

        for event in events:
            start = event.get("commence_time")
            if start:
                try:
                    if datetime.fromisoformat(start.replace("Z", "+00:00")) < now:
                        continue
                except ValueError:
                    pass
            events_seen.add(event.get("id"))
            groups = {}
            for book in event.get("bookmakers", []):
                for market in book.get("markets", []):
                    key_market = market["key"]
                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price")
                        if not isinstance(price, (int, float)) or price <= 1:
                            continue
                        point = round(float(outcome["point"]), 3) if outcome.get("point") is not None else None
                        # Spread sides have opposite signed points; pair them by the same absolute line.
                        group_line = abs(point) if key_market == "spreads" and point is not None else point
                        groups.setdefault((key_market, group_line), {}).setdefault(book.get("key", ""), []).append(outcome)

            for (market, _line), books in groups.items():
                fair, best = {}, {}
                for book_name, outcomes in books.items():
                    probs = [1 / float(o["price"]) for o in outcomes]
                    total = sum(probs)
                    if total <= 0:
                        continue
                    for outcome, probability in zip(outcomes, probs):
                        selection = (outcome.get("name", ""), outcome.get("point"))
                        fair.setdefault(selection, []).append(probability / total)
                        if selection not in best or outcome["price"] > best[selection]["price"]:
                            best[selection] = {"price": outcome["price"], "book": book_name}

                for selection, offer in best.items():
                    values = fair.get(selection, [])
                    if not values:
                        continue
                    estimated = sum(values) / len(values)
                    odds = float(offer["price"])
                    implied = 1 / odds
                    edge = estimated - implied
                    variance = statistics.pstdev(values) if len(values) > 1 else 0.25
                    confidence = min(1, len(values) / 5) * max(0.2, 1 - 2 * variance)
                    kelly = max(0, ((odds - 1) * estimated - (1 - estimated)) / (odds - 1))
                    value = edge >= 0.03 and confidence >= 0.55 and len(values) >= 2
                    point = selection[1]
                    picks.append({
                        "event": event.get("home_team", "") + " vs " + event.get("away_team", ""),
                        "sport_key": sport["key"], "sport": sport.get("title", sport["key"]),
                        "commence_time": start, "market": market,
                        "selection": selection[0] + (f" ({point:+g})" if point is not None else ""),
                        "bookmaker": offer["book"], "odds": odds,
                        "implied_probability": implied, "estimated_probability": estimated,
                        "edge": edge, "confidence": confidence,
                        "stake_units": min(1, kelly * 25) if value else 0,
                        "decision": "VALUE" if value else "NO BET", "books_count": len(values),
                        "estimation": "market_consensus_no_vig"
                    })

    # Keep every available event/market, chronologically, instead of truncating at 500 outcomes.
    picks.sort(key=lambda p: (p.get("commence_time") or "", p["sport"], p["event"], p["market"], p["selection"]))
    write({
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "status": "ok",
        "message": "Probabilidad estimada = consenso sin margen; no es predicción independiente.",
        "events_analyzed": len(events_seen), "best_edge": max((p["edge"] for p in picks), default=None),
        "sports": [{"key": s["key"], "title": s.get("title", s["key"])} for s in sports],
        "picks": picks,
        "method": "NO BET bajo 3% edge, 55% confianza o menos de 2 casas. Stake = cuarto Kelly, máximo 1 unidad."
    })
    print(f"Events: {len(events_seen)}; markets and selections: {len(picks)}")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        empty("error", str(error))
        raise
