#!/usr/bin/env python3
"""Weather forecast in your terminal via the free Open-Meteo API. No API key.

Geocodes a city name, then prints current conditions and a daily forecast
table with WMO weather-code descriptions. Open-Meteo is free for
non-commercial use with no signup, which makes this the rare weather CLI
that works forever without babysitting a token.

Usage:
    python 18_weather_cli.py "Panama City"
    python 18_weather_cli.py Berlin --days 3
    python 18_weather_cli.py "Buenos Aires" --fahrenheit

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Severe thunderstorm",
}


def fetch_json(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full, headers={"User-Agent": "weather-cli/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f"Open-Meteo refused the request: HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"Network error talking to Open-Meteo: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        sys.exit(f"Network error talking to Open-Meteo: {exc}")
    except ValueError:
        sys.exit("Open-Meteo sent something that is not JSON (captive portal or proxy?).")


def geocode(city: str) -> dict:
    data = fetch_json(GEO_URL, {"name": city, "count": 1, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        sys.exit(f"City not found: '{city}'. Try adding the country, e.g. 'Colon Panama'.")
    return results[0]


def describe(code: int) -> str:
    return WMO_CODES.get(code, f"Code {code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal weather via Open-Meteo (no key).")
    parser.add_argument("city", help="city name, quotes for spaces")
    parser.add_argument("--days", type=int, default=7, choices=range(1, 17),
                        metavar="1-16", help="forecast days (default: 7)")
    parser.add_argument("--fahrenheit", action="store_true", help="use Fahrenheit")
    args = parser.parse_args()

    place = geocode(args.city)
    unit = "fahrenheit" if args.fahrenheit else "celsius"
    symbol = "F" if args.fahrenheit else "C"

    data = fetch_json(FORECAST_URL, {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "temperature_unit": unit,
        "timezone": "auto",
        "forecast_days": args.days,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    })

    location = f"{place['name']}, {place.get('country', '')}".rstrip(", ")
    current = data["current"]
    print(f"\n{location}  ({place['latitude']:.2f}, {place['longitude']:.2f})")
    print(f"Now: {current['temperature_2m']:.0f}{symbol}, "
          f"{describe(current['weather_code'])}, "
          f"humidity {current['relative_humidity_2m']}%, "
          f"wind {current['wind_speed_10m']:.0f} km/h\n")

    daily = data["daily"]
    print(f"  {'DATE':<12}{'MIN':>6}{'MAX':>6}{'RAIN':>7}  CONDITIONS")
    print(f"  {'-' * 12}{'-' * 6}{'-' * 6}{'-' * 7}  {'-' * 24}")
    for i, date in enumerate(daily["time"]):
        rain = daily["precipitation_probability_max"][i]
        rain_s = f"{rain}%" if rain is not None else "--"
        print(f"  {date:<12}"
              f"{daily['temperature_2m_min'][i]:>5.0f}{symbol[0]}"
              f"{daily['temperature_2m_max'][i]:>5.0f}{symbol[0]}"
              f"{rain_s:>7}  {describe(daily['weather_code'][i])}")
    print("\nData: open-meteo.com (free for non-commercial use)")


if __name__ == "__main__":
    main()
