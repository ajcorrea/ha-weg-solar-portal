"""API client for WEG Solar Portal."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    BASE_URL,
    GROUP_BY_1_DAY,
    GROUP_BY_1_HOUR,
    GROUP_BY_10_MIN,
    HEADER_API_KEY,
    HEADER_API_SECRET,
    MIN_REQUEST_INTERVAL,
    STATUS_REPLACED,
    VAR_AC_ACTIVE_POWER,
    VAR_OUTPUT_ENERGY,
)

_LOGGER = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5.0  # seconds

_ENERGY_UNITS_TO_KWH = {
    "wh": 0.001,
    "kwh": 1.0,
    "mwh": 1_000.0,
    "gwh": 1_000_000.0,
}
_ENERGY_RE = re.compile(r"^\s*([-+]?[\d.,]+)\s*([kMGm]?Wh)\s*$", re.IGNORECASE)


class WegSolarApiError(Exception):
    """Base exception for WEG Solar API errors."""


class WegSolarRateLimitError(WegSolarApiError):
    """Rate limit exceeded error."""


class WegSolarAuthError(WegSolarApiError):
    """Authentication error."""


class WegSolarConnectionError(WegSolarApiError):
    """Connection error."""


class RateLimiter:
    """Serialize requests and keep a minimum interval between them.

    One instance is shared by every config entry using the same API key,
    so several plants do not burst the API at the same time.
    """

    def __init__(self, min_interval: float = MIN_REQUEST_INTERVAL) -> None:
        """Initialize the limiter."""
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def __aenter__(self) -> None:
        """Wait for our turn."""
        await self._lock.acquire()
        wait = self._last + self._min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    async def __aexit__(self, *exc: object) -> None:
        """Release the slot."""
        self._last = time.monotonic()
        self._lock.release()


def parse_energy_kwh(value: Any) -> float | None:
    """Parse values like '22.7 MWh' / '850 kWh' / 1234.5 into kWh."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _ENERGY_RE.match(str(value))
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    unit = match.group(2).lower()
    try:
        return round(float(number) * _ENERGY_UNITS_TO_KWH[unit], 3)
    except (ValueError, KeyError):
        return None


def is_replaced(device: dict[str, Any]) -> bool:
    """Return True if the device was replaced by another one."""
    status = str(device.get("status", "")).strip().lower()
    serial = str(device.get("serialNumber", "")).strip().lower()
    return (
        status == STATUS_REPLACED
        or device.get("replaced") is True
        or bool(device.get("replacement"))
        or bool(device.get("replacedBy"))
        or serial.endswith("-replaced")
    )


def _iso(dt: datetime) -> str:
    """Format a datetime as the API expects (UTC, ms precision)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_iso_utc(value: Any) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp returned by the API."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class WegSolarApiClient:
    """Client to interact with the WEG Solar Portal API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session: aiohttp.ClientSession,
        limiter: RateLimiter | None = None,
    ) -> None:
        """Initialize the API client."""
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = session
        self._limiter = limiter or RateLimiter()

    @property
    def _headers(self) -> dict[str, str]:
        """Return the authentication headers."""
        return {
            HEADER_API_KEY: self._api_key,
            HEADER_API_SECRET: self._api_secret,
        }

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a GET request, retrying on rate limit."""
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"

        for attempt in range(MAX_RETRIES):
            delay: float | None = None
            async with self._limiter:
                try:
                    async with self._session.get(
                        url,
                        headers=self._headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        if response.status in (401, 403):
                            raise WegSolarAuthError(
                                f"Authentication failed ({response.status})"
                            )
                        if response.status == 429:
                            retry_after = response.headers.get("Retry-After")
                            try:
                                delay = float(retry_after) if retry_after else None
                            except ValueError:
                                delay = None
                            if delay is None:
                                delay = RETRY_BASE_DELAY * (2**attempt)
                        elif response.status != 200:
                            text = await response.text()
                            raise WegSolarApiError(
                                f"API error {response.status} on {endpoint}: {text}"
                            )
                        else:
                            return await response.json()
                except aiohttp.ClientError as err:
                    raise WegSolarConnectionError(
                        f"Error connecting to WEG Solar Portal: {err}"
                    ) from err
                except asyncio.TimeoutError as err:
                    raise WegSolarConnectionError(
                        "Timeout connecting to WEG Solar Portal"
                    ) from err

            _LOGGER.debug(
                "Rate limited on %s, retrying in %.1fs (attempt %d/%d)",
                endpoint,
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            await asyncio.sleep(delay)

        raise WegSolarRateLimitError("API rate limit exceeded")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def _async_get_paginated(
        self,
        endpoint: str,
        id_keys: tuple[str, ...],
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated list endpoint."""

        def _item_id(item: dict[str, Any]) -> str | None:
            for key in id_keys:
                val = item.get(key)
                if val:
                    return str(val)
            return None

        data = await self._request(endpoint)
        if not isinstance(data, dict):
            return []

        items: list[dict[str, Any]] = [
            i for i in (data.get("items") or []) if isinstance(i, dict)
        ]
        seen = {
            item_id for item in items if (item_id := _item_id(item)) is not None
        }
        total = int(data.get("totalCount") or len(items))
        page = data.get("page")
        limit = int(data.get("limit") or data.get("perPage") or len(items) or 0)
        pages_fetched = 1

        while (
            len(items) < total
            and page is not None
            and limit > 0
            and pages_fetched < max_pages
        ):
            page = int(page) + 1
            pages_fetched += 1
            next_data = await self._request(
                endpoint, params={"page": page, "limit": limit}
            )
            if not isinstance(next_data, dict):
                break
            page_items = [
                i for i in (next_data.get("items") or []) if isinstance(i, dict)
            ]
            if not page_items:
                break

            added = 0
            for item in page_items:
                item_id = _item_id(item)
                if item_id is not None:
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                items.append(item)
                added += 1
            if added == 0:
                break

        return items

    async def async_get_plants(self) -> list[dict[str, Any]]:
        """Get all plants available for this API key."""
        return await self._async_get_paginated("plants", ("_id", "id"))

    async def async_get_plant_devices(self, plant_id: str) -> list[dict[str, Any]]:
        """Get all devices (including replaced ones) of a plant."""
        return await self._async_get_paginated(
            f"plants/{plant_id}/devices", ("deviceId", "_id", "id")
        )

    async def async_get_plant_alarms(
        self, plant_id: str, page: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        """Get alarms of a plant (most recent first)."""
        data = await self._request(
            f"plants/{plant_id}/alarms",
            params={"page": page, "limit": limit},
        )
        return data if isinstance(data, dict) else {}

    async def async_get_measurements(
        self,
        plant_id: str,
        variable: str,
        date_from: datetime,
        date_to: datetime,
        group_by: int,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get measurement points, sorted from oldest to newest."""
        params: dict[str, Any] = {
            "plantId": plant_id,
            "variables": variable,
            "dateFrom": _iso(date_from),
            "dateTo": _iso(date_to),
            "groupBy": str(group_by),
        }
        if device_id:
            params["deviceId"] = device_id

        data = await self._request("measurements", params=params)
        points = data.get("data", []) if isinstance(data, dict) else []
        # API returns newest first; normalize ordering.
        return sorted(
            (p for p in points if isinstance(p, dict)),
            key=lambda p: p.get("time", ""),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def async_get_current_power(
        self,
        plant_id: str,
        now: datetime,
        device_id: str | None = None,
    ) -> tuple[float, dict[str, Any]]:
        """Return the latest AC active power in watts and raw measurement metadata.

        The WEG Solar API reports `acActivePower` in kW; convert to W so entities
        keep `UnitOfPower.WATT` without breaking recorder statistics.
        """
        date_from = now - timedelta(minutes=30)
        points = await self.async_get_measurements(
            plant_id=plant_id,
            variable=VAR_AC_ACTIVE_POWER,
            date_from=date_from,
            date_to=now,
            group_by=GROUP_BY_10_MIN,
            device_id=device_id,
        )
        meta: dict[str, Any] = {
            "date_from": _iso(date_from),
            "date_to": _iso(now),
            "group_by": GROUP_BY_10_MIN,
            "points": points,
        }
        for point in reversed(points):
            if point.get("value") is not None:
                return round(float(point["value"]) * 1000.0, 1), meta
        # No data in the window (e.g. at night) means no production.
        return 0.0, meta

    async def async_get_energy_today(
        self,
        plant_id: str,
        start: datetime,
        now: datetime,
        device_id: str | None = None,
    ) -> tuple[float, dict[str, Any]]:
        """Return energy produced today (kWh) and raw measurement metadata."""
        target_date = start.date()
        day_start = datetime(
            target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc
        )
        day_end = datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=timezone.utc
        )
        start_utc = start.astimezone(timezone.utc)
        date_to = max(now, start + timedelta(seconds=1))
        local_hour = now.astimezone(start.tzinfo).hour if start.tzinfo is not None else now.hour

        daily_points: list[dict[str, Any]] = []
        daily_error: str | None = None
        daily_meta: dict[str, Any] = {
            "date_from": _iso(day_start),
            "date_to": _iso(day_end),
            "group_by": GROUP_BY_1_DAY,
            "points": daily_points,
        }

        try:
            daily_points = await self.async_get_measurements(
                plant_id=plant_id,
                variable=VAR_OUTPUT_ENERGY,
                date_from=day_start,
                date_to=day_end,
                group_by=GROUP_BY_1_DAY,
                device_id=device_id,
            )
            daily_meta["points"] = daily_points
        except (WegSolarAuthError, WegSolarRateLimitError, WegSolarConnectionError):
            raise
        except WegSolarApiError as err:
            daily_error = str(err)
            _LOGGER.debug(
                "Daily energy query failed (%s), falling back to hourly grouping",
                err,
            )
        else:
            non_null = [p for p in daily_points if p.get("value") is not None]
            in_day = [
                p
                for p in non_null
                if (ts := _parse_iso_utc(p.get("time"))) is None
                or ts.date() == target_date
            ]
            values = [float(p["value"]) for p in in_day]
            if values:
                daily_energy = round(values[-1] if values[-1] > 0 else max(values), 3)
                if daily_energy > 0 or local_hour < 6:
                    return daily_energy, daily_meta
            elif local_hour < 6:
                return 0.0, daily_meta

        try:
            hourly_points = await self.async_get_measurements(
                plant_id=plant_id,
                variable=VAR_OUTPUT_ENERGY,
                date_from=start,
                date_to=date_to,
                group_by=GROUP_BY_1_HOUR,
                device_id=device_id,
            )
        except (WegSolarAuthError, WegSolarRateLimitError, WegSolarConnectionError):
            raise
        except WegSolarApiError:
            if daily_error is None:
                return 0.0, daily_meta
            raise

        non_null = [p for p in hourly_points if p.get("value") is not None]
        in_window = [
            p
            for p in non_null
            if (ts := _parse_iso_utc(p.get("time"))) is None or ts >= start_utc
        ]
        values = [float(p["value"]) for p in in_window]
        energy = round(max(values), 3) if values else 0.0
        return energy, {
            "date_from": _iso(start),
            "date_to": _iso(date_to),
            "group_by": GROUP_BY_1_HOUR,
            "points": hourly_points,
            "daily_attempt": {
                "date_from": _iso(day_start),
                "date_to": _iso(day_end),
                "points": daily_points,
                "error": daily_error,
            },
        }
