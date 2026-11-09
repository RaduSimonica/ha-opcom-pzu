# OPCOM PZU for Home Assistant

Romanian day-ahead (PZU) electricity prices in Home Assistant: free, no account, no licence.

- **Real prices, not just spot:** set what you actually *pay* and *receive* (commission, taxes, VAT, or your own formula).
- **Feeds a battery planner:** the *Upcoming prices* sensor follows an open [price series format](docs/price-series.md), so [Battery Planner](https://github.com/RaduSimonica/battery-planner) can plan when to charge and discharge your battery from your own buy and sell prices.
- **Full history** as native Home Assistant statistics, back to 2015 if you want, plus a service to pull any date range.
- **Tomorrow's prices** as soon as OPCOM publishes them (about 13:45 Romanian time), with a full series for charts.
- Correct **CET market time** and **DST days** (92 / 100 intervals), 15-minute resolution (older days are hourly).

## Install

HACS → Custom repositories → add this repository as *Integration* → install → restart. HACS shows the released version (see the [changelog](CHANGELOG.md)). Then *Settings → Devices & services → Add integration → OPCOM PZU*.

## Setup

You choose a currency (RON or EUR; fixed afterwards) and how many months of history to download.

> **About history.** Each past day is one request to OPCOM, sent one per second so their server is not overloaded. 12 months take about 10 minutes, the full archive (since 2015) over an hour. It runs in the background, resumes by itself after a restart, and reports progress in the **Logbook** and on the *History download progress* sensor.

## Your buy and sell price

*Configure → Buy price / Sell price.* Each page shows a live example using the current spot price.

| | Simple fields | Formula (optional, replaces the fields) |
|---|---|---|
| **Sell** (what you receive) | `spot × (1 − commission%) − fixed fee` | e.g. `max(pzu*0.9 - 0.05, 0)` |
| **Buy** (what you pay) | `(spot × (1 + markup%) + fixed charges) × (1 + VAT%)` | e.g. `(pzu + 0.45) * 1.21` |

Formulas are plain arithmetic over `pzu` (spot, currency/kWh) and `hour` (local hour, for time-of-use tariffs), with `+ - * / ** %`, `min`, `max`, `abs`, `round`, `floor`, `ceil`. Nothing else runs, and mistakes are caught when you save.

## Battery planning

Planning when to charge and discharge a battery is done by the separate [**Battery Planner**](https://github.com/RaduSimonica/battery-planner) integration, which works with any price source. Install it, and when it asks for the price source pick this integration's *Upcoming prices* sensor. It uses the buy and sell prices you set above.

**Coming from 0.2.x?** The battery sensors and settings used to live here. 0.3.0 removes them (the old battery entities and options are cleaned up automatically), so install Battery Planner and enter your battery settings there. Its entities are named `sensor.battery_planner_...`.

## Other entities

Spot, buy and sell price now and next interval; average, lowest and highest per day (spot enabled by default; buy/sell variants are available but disabled); *Upcoming prices* (every interval from now on, rolling, in the [price series format](docs/price-series.md)) and *Prices today/tomorrow*, all with the full series in attributes; *Tomorrow's prices published*; *Cheap/Expensive price now* (threshold configurable); last download time.

## Services

| Service | Purpose |
|---|---|
| `opcom_pzu.refresh` | Fetch missing prices now |
| `opcom_pzu.backfill_history` | Download history (optionally `months`) |
| `opcom_pzu.get_prices` | Return spot/buy/sell for `start_date`..`end_date` (response data, max. 366 days) |

## Graphs

History is stored as the long-term statistic `opcom_pzu:spot_price` (hourly mean/min/max), so it works in the built-in *Statistics graph* card:

```yaml
type: statistics-graph
entities: [opcom_pzu:spot_price]
stat_types: [mean, min, max]
period: day
```

## Dashboards

Ready-to-paste cards are in [`examples/dashboards/`](examples/dashboards) (they need [apexcharts-card](https://github.com/RomRider/apexcharts-card) from HACS):

| File | Shows |
|---|---|
| `today.yaml` | Today's spot price as columns coloured by level, plus your buy price |
| `upcoming.yaml` | Every price from now on (today and tomorrow), spot, buy and sell |

Paste the YAML into a card's code editor. **Entity IDs:** the cards use `sensor.opcom_pzu_...`, but Home Assistant adds the device's area to the ID of entities created while the device is in an area (e.g. `sensor.server_opcom_pzu_upcoming_prices`). If a card says *Entity not available*, copy the real ID from *Settings → Devices & services → OPCOM PZU*. The `prices` attributes (`start`, `end`, `spot`, `buy`, `sell`) also work with any other chart card or a template.

## When are prices fetched?

OPCOM's auction closes at 13:00 Romanian time and results appear roughly 13:45. The integration first tries at **13:15** (plus a few random minutes so installs do not all hit the server together), then every **15 minutes** until tomorrow's file is published. Downloaded prices are cached on disk, so an OPCOM outage or a restart never loses data.

## Notes

- Prices are OPCOM's published market closing prices (PIP). Your bill can differ; that is what the formulas are for.
- Not affiliated with OPCOM.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md): each pull request into `main` bumps the version and gets a release.

```
uv venv && uv pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```
