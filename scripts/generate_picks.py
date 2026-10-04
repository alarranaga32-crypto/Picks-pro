#!/usr/bin/env python3
"""Transparent market-consensus analysis. This is a baseline, not an independent prediction model."""
import json
import os
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "picks.json"
BASE_URL = "https://api.the-odds-api.com/v4/"
LAST_REQUEST_AT = 0.0


class APIError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"The Odds API HTTP {code}: {message}")


def api(path, params, key):
    global LAST_REQUEST_AT
    url = BASE_URL + path + "?" + urlencode({**params, "apiKey": key})
    for attempt in range(4):
        # Avoid burst rate limits; 429 responses are retried with backoff.
        wait = max(0.0, 2.0 - (time.monotonic() - LAST_REQUEST_AT))
        if wait:
            time.sleep(wait)
        LAST_REQUEST_AT = time.monotonic()
        try:
            with urlopen(Request(url, headers={"User-Agent": "PicksPro/1.1"}), timeout=35) as response:
                remaining = response.headers.get("x-requests-remaining", "unknown")
                used = response.headers.get("x-requests-used", "unknown")
                print(f"The Odds API credits: remaining={remaining}, used={used}")
                return json.load(response)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
                message = str(parsed.get("message", parsed.get("error", body)))
            except (ValueError, AttributeError):
                message = body or error.reason or "Request rejected"
            message = " ".join(message.split())[:300]
            if error.code == 429 and attempt < 3:
                retry_after = error.headers.get("Retry-After")
                try:
                    delay = min(30.0, max(2.0, float(retry_after))) if retry_after else 2.0 ** (attempt + 2)
                except ValueError:
                    delay = 2.0 ** (attempt + 2)
                print(f"The Odds API returned 429; retrying in {delay:g}s ({attempt + 1}/3).")
                time.sleep(delay)
                continue
            raise APIError(error.code, message) from None


def write(data):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=OUT.parent, delete=False) as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        temp_name = output.name
    os.replace(temp_name, OUT)


def empty(status, message):
    write({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "message": message,
        "events_analyzed": 0,
        "best_edge": None,
        "sports": [],
        "picks": [],
        "method": "Consenso sin margen; no es modelo independiente.",
    })


def report_api_error(error):
    if error.code == 429:
        status = "rate_limited"
        message = ("The Odds API mantuvo el HTTP 429 después de varios reintentos. "
                   "Puede ser límite temporal de velocidad o créditos agotados; revisa el uso de la cuenta. "
                   "No se inventaron datos.")
    else:
        status = "error"
        message = str(error)
    empty(status, message)
    print(message)


def main():
    key = os.getenv("ODDS_API_KEY")
    if not key:
        empty("needs_configuration", "Falta ODDS_API_KEY; no se inventaron datos.")
        return

    try:
        sports = [sport for sport in api("sports", {"all": "false"}, key) if sport.get("active")]
    except APIError as error:
        if error.code == 429:
            report_api_error(error)
            return
        raise

    picks = []
    events_seen = set()
    regions = os.getenv("ODDS_REGIONS", "us")
    markets = os.getenv("ODDS_MARKETS", "h2h,spreads,totals")
    for sport in sports:
        try:
            events = api(
                "sports/" + sport["key"] + "/odds",
                {"regions": regions, "markets": markets, "oddsFormat": "decimal", "dateFormat": "iso"},
                key,
            )
        except APIError as error:
            if error.code in (404, 422):
                print(f"Skipping unavailable market for {sport['key']}: {error.message}")
                continue
            if error.code == 429:
                report_api_error(error)
                return
            raise

        for event in events:
            events_seen.add(event.get("id"))
            groups = {}
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price")
                        if not isinstance(price, (int, float)) or price <= 1:
                            continue
                        line = round(outcome["point"], 3) if outcome.get("point") is not None else None
                        groups.setdefault((market["key"], line), {}).setdefault(
                            bookmaker.get("key", ""), []
                        ).append(outcome)

            for (market, line), books in groups.items():
                fair = {}
                best = {}
                for bookmaker, outcomes in books.items():
                    probabilities = [1 / float(outcome["price"]) for outcome in outcomes]
                    total = sum(probabilities)
                    if not total:
                        continue
                    for outcome, probability in zip(outcomes, probabilities):
                        selection = (outcome.get("name", ""), outcome.get("point"))
                        fair.setdefault(selection, []).append(probability / total)
                        if selection not in best or outcome["price"] > best[selection]["price"]:
                            best[selection] = {"price": outcome["price"], "book": bookmaker}

                for selection, offer in best.items():
                    values = fair.get(selection, [])
                    if not values:
                        continue
                    probability = sum(values) / len(values)
                    odds = float(offer["price"])
                    implied = 1 / odds
                    edge = probability - implied
                    spread = statistics.pstdev(values) if len(values) > 1 else 0.25
                    confidence = min(1, len(values) / 5) * max(0.2, 1 - 2 * spread)
                    kelly = max(0, ((odds - 1) * probability - (1 - probability)) / (odds - 1))
                    value = edge >= 0.03 and confidence >= 0.55 and len(values) >= 2
                    picks.append({
                        "event": event.get("home_team", "") + " vs " + event.get("away_team", ""),
                        "sport_key": sport["key"],
                        "sport": sport.get("title", sport["key"]),
                        "commence_time": event.get("commence_time"),
                        "market": market,
                        "selection": selection[0] + (f" ({selection[1]:+g})" if selection[1] is not None else ""),
                        "bookmaker": offer["book"],
                        "odds": odds,
                        "implied_probability": implied,
                        "estimated_probability": probability,
                        "edge": edge,
                        "confidence": confidence,
                        "stake_units": min(1, kelly * 25) if value else 0,
                        "decision": "VALUE" if value else "NO BET",
                        "books_count": len(values),
                        "estimation": "market_consensus_no_vig",
                    })

    picks.sort(key=lambda pick: (pick["decision"] == "VALUE", pick["edge"]), reverse=True)
    write({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "message": "Probabilidad estimada = consenso sin margen; no es una predicción independiente.",
        "events_analyzed": len(events_seen),
        "best_edge": max((pick["edge"] for pick in picks), default=None),
        "sports": [{"key": sport["key"], "title": sport.get("title", sport["key"])} for sport in sports],
        "picks": picks[:500],
        "method": "NO BET bajo 3% edge, 55% confianza o menos de 2 casas. Stake = cuarto Kelly, máximo 1 unidad.",
    })
    print(f"Events: {len(events_seen)}; candidates: {len(picks)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        empty("error", str(error))
        raise
