"""Explicit Open-Meteo geographic lookup over the shared bounded research client."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ACTIONS = {"city_to_coordinates", "city_to_timezone", "get_weather"}


def number(value, minimum, maximum):
    if (
        type(value) not in (int, float)
        or not minimum <= value <= maximum
        or not math.isfinite(value)
    ):
        raise ValueError("Geographic response contains invalid numeric data")
    return value


def text(value, maximum=200):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Geographic response contains invalid text")
    return value


def zone_details(name, now=None):
    name = text(name, 100)
    try:
        local = (now or datetime.now(UTC)).astimezone(ZoneInfo(name))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Geographic response contains an unknown timezone") from None
    return {
        "timezone": name,
        "local_time": local.isoformat(),
        "utc_offset_seconds": int(local.utcoffset().total_seconds()),
        "is_dst": bool(local.dst().total_seconds()),
    }


class StudioGeography:
    def __init__(self, research):
        self.research = research

    def request(self, base, parameters):
        url = base + "?" + urlencode(parameters)
        try:
            result = self.research.request("GET", url)
            if not isinstance(result, dict) or result.get("error"):
                raise ValueError
            # The injected contract client must satisfy the same finite-data bound.
            encoded = json.dumps(result, allow_nan=False)
            if len(encoded) > 512 * 1024:
                raise ValueError
        except Exception:
            raise ValueError("Geographic service failed; no other provider was used") from None
        return result, url

    def locations(self, args):
        city, country, limit = args.get("city"), args.get("country_code"), args.get("limit", 1)
        if (
            not isinstance(city, str)
            or not 2 <= len(city.strip()) <= 160
            or any(ord(c) < 32 for c in city)
        ):
            raise ValueError("Choose a city name from 2 to 160 characters")
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("Location limit must be from 1 to 5")
        if country is not None and (
            not isinstance(country, str) or not re.fullmatch("[A-Za-z]{2}", country)
        ):
            raise ValueError("Country code must contain two letters")
        params = {"name": city.strip(), "count": limit, "language": "en", "format": "json"}
        if country:
            params["countryCode"] = country.upper()
        response, url = self.request("https://geocoding-api.open-meteo.com/v1/search", params)
        raw = response.get("results", [])
        if not isinstance(raw, list) or len(raw) > 100:
            raise ValueError("Geographic service returned invalid locations")
        locations = []
        for row in raw[:limit]:
            if not isinstance(row, dict):
                raise ValueError("Geographic service returned invalid locations")
            locations.append(
                {
                    "name": text(row.get("name")),
                    "country_code": text(row.get("country_code"), 2),
                    "country": text(row.get("country", row.get("country_code"))),
                    "latitude": number(row.get("latitude"), -90, 90),
                    "longitude": number(row.get("longitude"), -180, 180),
                    **zone_details(row.get("timezone")),
                }
            )
        return locations, url

    @staticmethod
    def source(url, data):
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return {
            "url": url,
            "title": "Open-Meteo geographic information",
            "text": encoded[:4000],
            "retrieved_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }

    def execute(self, action, args, actor):
        if not self.research.config.get("geographic"):
            raise ValueError("Explicitly enable research.geographic in private runtime settings")
        allowed = {"city", "country_code", "limit"} | (
            {"forecast_days"} if action == "get_weather" else set()
        )
        if set(args) - allowed:
            raise ValueError("Unsupported geographic tool arguments")
        days = args.get("forecast_days", 1)
        if type(days) is not int or not 1 <= days <= 7:
            raise ValueError("Forecast days must be from 1 to 7")
        locations, url = self.locations(args)
        sources = [self.source(url, locations)]
        result = {"provider": "open-meteo", "locations": locations, "untrusted": True}
        if action == "get_weather":
            if not locations:
                raise ValueError("No matching city was found")
            selected = locations[0]
            response, forecast_url = self.request(
                "https://api.open-meteo.com/v1/forecast",
                {
                    "latitude": selected["latitude"],
                    "longitude": selected["longitude"],
                    "timezone": selected["timezone"],
                    "forecast_days": days,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                },
            )
            current, daily = response.get("current"), response.get("daily")
            fields = (
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
            )
            if not isinstance(current, dict) or not isinstance(daily, dict):
                raise ValueError("Weather service returned incomplete data")
            times = daily.get("time")
            if not isinstance(times, list) or len(times) != days:
                raise ValueError("Weather service returned an invalid forecast")
            for key in fields:
                if not isinstance(daily.get(key), list) or len(daily[key]) != days:
                    raise ValueError("Weather service returned an invalid forecast")
            weather = {
                "location": selected,
                "current": {"time": text(current.get("time"), 40)},
                "forecast": [],
            }
            for key in ("temperature_2m", "relative_humidity_2m", "weather_code", "wind_speed_10m"):
                weather["current"][key] = number(current.get(key), -10000, 10000)
            for index, instant in enumerate(times):
                weather["forecast"].append(
                    {
                        "date": text(instant, 40),
                        **{key: number(daily[key][index], -10000, 10000) for key in fields},
                    }
                )
            for key in ("current_units", "daily_units"):
                units = response.get(key)
                if not isinstance(units, dict):
                    raise ValueError("Weather service did not provide units")
                weather[key] = {
                    name: text(units.get(name), 40)
                    for name in (
                        (
                            "time",
                            "temperature_2m",
                            "relative_humidity_2m",
                            "weather_code",
                            "wind_speed_10m",
                        )
                        if key == "current_units"
                        else ("time", *fields)
                    )
                }
            result.update(weather)
            sources.append(self.source(forecast_url, weather))
        self.research.cite(sources, actor)
        result["sources"] = sources
        return result
