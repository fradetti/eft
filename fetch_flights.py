import json
import os
import sys
from datetime import datetime, timezone

import requests

FLIGHTS = ["EK78", "EK705", "EK708", "EK77"]
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flights.json")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"


def fetch_flight(flight_number, date_str):
    """Fetch flight status from AeroDataBox API."""
    url = f"https://{RAPIDAPI_HOST}/flights/number/{flight_number}/{date_str}"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }
    params = {"withAircraftImage": "false", "withLocation": "false"}

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_flight_data(raw_flights, flight_number, date_str):
    """Parse API response into a single best entry per flight/date.

    The API often returns multiple results (previous day's flight, multiple legs).
    We pick the best one: prefer matching scheduled date, then actual data, then
    most informative status.
    """
    candidates = []
    for flight in raw_flights:
        departure = flight.get("departure", {})
        arrival = flight.get("arrival", {})

        scheduled = departure.get("scheduledTime", {})
        actual = departure.get("actualTime") or departure.get("runwayTime") or {}

        scheduled_utc = scheduled.get("utc", "")
        actual_utc = actual.get("utc", "") if isinstance(actual, dict) else ""

        delay_minutes = None
        if scheduled_utc and actual_utc:
            try:
                fmt = "%Y-%m-%d %H:%MZ"
                sched_dt = datetime.strptime(scheduled_utc, fmt).replace(tzinfo=timezone.utc)
                actual_dt = datetime.strptime(actual_utc, fmt).replace(tzinfo=timezone.utc)
                delay_minutes = int((actual_dt - sched_dt).total_seconds() / 60)
            except (ValueError, TypeError):
                delay_minutes = None

        scheduled_local = scheduled.get("local", "")
        actual_local = ""
        if isinstance(actual, dict):
            actual_local = actual.get("local", "")

        status = flight.get("status", "Unknown")

        origin_code = departure.get("airport", {}).get("iata", "")
        dest_code = arrival.get("airport", {}).get("iata", "")

        # Score for picking the best candidate
        score = 0
        # Prefer entries whose scheduled departure matches the query date
        if scheduled_local.startswith(date_str):
            score += 10
        # Prefer entries with actual departure data
        if actual_local:
            score += 5
        # Prefer entries with known status
        if status not in ("Unknown", ""):
            score += 2
        # Prefer entries with a destination
        if dest_code:
            score += 1

        candidates.append((score, {
            "date": date_str,
            "flight": flight_number,
            "scheduled_departure": scheduled_local,
            "actual_departure": actual_local,
            "delay_minutes": delay_minutes,
            "status": status,
            "origin": origin_code,
            "destination": dest_code,
        }))

    if not candidates:
        return []

    # Return only the best candidate
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [candidates[0][1]]


def load_existing_data():
    """Load existing flights.json data."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_data(data):
    """Save data to flights.json."""
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    if not RAPIDAPI_KEY:
        print("ERROR: RAPIDAPI_KEY environment variable not set.")
        sys.exit(1)

    if len(sys.argv) > 1:
        today = sys.argv[1]
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Fetching flights for {today}...")

    existing = load_existing_data()

    # Build set of already-fetched (date, flight) pairs to avoid duplicates
    existing_keys = {(r["date"], r["flight"]) for r in existing}

    new_entries = []
    for flight_num in FLIGHTS:
        if (today, flight_num) in existing_keys:
            print(f"  {flight_num} on {today} already fetched, skipping.")
            continue

        print(f"  Fetching {flight_num}...")
        try:
            raw = fetch_flight(flight_num, today)
            entries = parse_flight_data(raw, flight_num, today)
            new_entries.extend(entries)
            print(f"    Got {len(entries)} result(s).")
        except requests.exceptions.HTTPError as e:
            print(f"    HTTP error for {flight_num}: {e}")
        except Exception as e:
            print(f"    Error for {flight_num}: {e}")

    if new_entries:
        existing.extend(new_entries)
        # Sort by date descending, then flight number
        existing.sort(key=lambda x: (x["date"], x["flight"]), reverse=True)
        save_data(existing)
        print(f"Added {len(new_entries)} new entries. Total: {len(existing)}.")
    else:
        print("No new entries to add.")


if __name__ == "__main__":
    main()
