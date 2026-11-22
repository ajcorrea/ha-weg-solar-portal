# WEG Solar Portal - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ajcorrea/ha-weg-solar-portal)](https://github.com/ajcorrea/ha-weg-solar-portal/releases)
[![Validate integration](https://github.com/ajcorrea/ha-weg-solar-portal/actions/workflows/validate.yaml/badge.svg)](https://github.com/ajcorrea/ha-weg-solar-portal/actions/workflows/validate.yaml)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ajcorrea&repository=ha-weg-solar-portal&category=integration)

Custom integration for [Home Assistant](https://www.home-assistant.io/) to monitor solar plants through the [WEG Solar Portal](https://solarportal.weg.net/) API.

## Features

- **One Hub per plant**: each plant is configured as its own entry (`hub`), containing a Service device with plant-level totals/alarms and the active inverters linked inside it.
- **Per-inverter data**: active power (W, converted from kW returned by the API), energy today (kWh, via daily grouping), lifetime energy, status, online connectivity, alarms (24h & last alarm), serial number, and model.
- **Per-plant data**: active power (W), energy today (kWh), lifetime energy, inverters online, alarms (24h), and last alarm. Plant power and today's energy are summed directly from the active inverters when available.
- **Replaced inverters are ignored**: devices marked as replaced (`status: "replaced"`, `replaced: true`, non-empty `replacement` / `replacedBy`, or serial number ending in `-replaced`) are excluded from inverter device creation and removed on reload. Their historical `outputEnergy` still counts toward the plant's lifetime total, and they are listed in the `replaced_inverters` attribute of the *Inverters online* sensor.
- **Automatic discovery & on-demand scan**: after you add the first plant, other plants linked to the same API key are discovered automatically on startup and every **1 hour** (with full API pagination). Each plant also provides a **Scan for plants and inverters** configuration button to trigger discovery and an immediate refresh of inverters on demand.
- **Rate limit handling**: requests that share an API key are serialized, and 429 responses are retried with backoff.
- **Diagnostics & Brand assets**: includes local light/dark brand icons and logos (`brand/`), full `hassfest` + HACS GitHub Actions validation, and diagnostics export (credentials redacted) with raw devices, alarms, and measurements.
- Translations: English and Portuguese (pt-BR).

## Prerequisites

1. Log in to the WEG Solar Portal and open **Gestão de API**.
2. Click **Adicionar chave de API**, then select the plants.
3. Copy the **API Key** and **API Secret**. The secret is shown only once.

## Installation

### Home Assistant Marketplace / HACS (Recommended)

Click the **Open repository in HACS** badge above, or add `https://github.com/ajcorrea/ha-weg-solar-portal` as a custom repository (category **Integration**) in HACS / Home Assistant Marketplace, install **WEG Solar Portal**, and restart Home Assistant.

### Manual

Copy `custom_components/weg_solar` into `config/custom_components/` and restart Home Assistant. Requires Home Assistant 2025.3 or later.

## Configuration

**Settings → Devices & Services → Add Integration → WEG Solar Portal**. Enter the key and secret, then choose a plant. Accept the other plants from the *Discovered* section.

> Upgrading from 1.x: the existing entry is migrated to the first plant, and the other plants are offered as discovered.

## Entities

### Plant (Service Device)
| Entity | Description |
|---|---|
| Power | Current AC active power (W) |
| Energy today | Energy produced since local midnight (kWh) |
| Lifetime energy | Sum of all inverters, including replaced ones (kWh) |
| Inverters online | Online count; attributes list active and replaced inverters |
| Alarms (24h) | Alarms in the last 24 h; attributes show total and recent alarms |
| Last alarm | Timestamp of the most recent alarm, with code, severity and description |
| Alarm (binary) | ON if any alarm occurred in the last 24 h |
| All inverters online (binary) | ON when every active inverter is online |
| Scan for plants and inverters (button, Config) | Immediately checks for new plants and refreshes this plant's devices |

### Inverter
| Entity | Description |
|---|---|
| Power | Current AC active power (W) |
| Energy today | Energy produced today (kWh) |
| Lifetime energy | Parsed from the portal (for example "14.9 MWh" → 14900 kWh) |
| Status | Raw status from the portal |
| Online (binary) | Connectivity |
| Alarm (binary) / Alarms (24h) / Last alarm | Alarms of this inverter |
| Serial number / Model | Diagnostic |

## API endpoints used

| Endpoint | Frequency |
|---|---|
| `GET /plants` | Setup, hourly discovery, and scan button (paginated) |
| `GET /plants/{id}/devices` | Every 5 min (paginated) |
| `GET /measurements` (`acActivePower` with 10 min grouping, `outputEnergy` with daily grouping) | Every 5 min, per active inverter (plant totals derived from inverters) |
| `GET /plants/{id}/alarms` | Every 15 min |

## Disclaimer

This integration is not affiliated with or endorsed by WEG S.A.
