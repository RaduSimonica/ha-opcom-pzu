# OPCOM PZU for Home Assistant

Romanian day-ahead (PZU) electricity prices in Home Assistant: free, no account, no licence.

- **Real prices, not just spot:** set what you actually *pay* and *receive* (commission, taxes, VAT, or your own formula).
- **Battery arbitrage planner:** is it worth buying cheap and selling dear? Accounts for round-trip efficiency, battery degradation, capacity and power. Shows the net gain and the gain per kWh.
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

## Battery planner

*Configure → Battery:* usable capacity, **maximum charge power and maximum discharge power** (they are often different, e.g. 0.3 kW in and 1.2 kW out), round-trip efficiency, degradation cost per kWh delivered. Optionally pick your own sensors:

- **State of charge** (%, Wh or kWh): today's plan then starts from the energy actually in the battery and covers the remaining intervals. It is re-planned every 15 minutes.
- **Capacity** (Wh or kWh): replaces the fixed capacity, e.g. when your inverter reports usable capacity.

Without a state-of-charge sensor the plan assumes an empty battery. Tomorrow's plan always assumes an empty battery at midnight, as nobody knows the level by then.

For one kWh bought at `b` and sold later at `s`:

```
net gain per kWh sold = s − degradation − b / efficiency
```

It is only worth cycling when that is positive. The planner finds the best schedule for the rest of the day (several cycles allowed, capacity and power respected), using *your* buy and sell prices. Energy already stored counts as free, since its cost is sunk.

| Entity | Meaning |
|---|---|
| Battery net gain today / tomorrow | Planned profit (today: remaining intervals); attributes hold the schedule (charge/discharge windows), energy bought and sold, cycles and the break-even sell price |
| Battery net gain per kWh today / tomorrow | Net gain per kWh sold |
| Battery action | `charge` / `discharge` / `idle` right now, for automations |
| Battery arbitrage profitable today / tomorrow | Binary sensors |

Not financial advice: results depend on your inputs, and tomorrow's plan assumes an empty battery.

## Other entities

Spot, buy and sell price now and next interval; average, lowest and highest per day (spot enabled by default; buy/sell variants are available but disabled); *Upcoming prices* (every interval from now on, rolling) and *Prices today/tomorrow*, all with the full series in attributes; *Tomorrow's prices published*; *Cheap/Expensive price now* (threshold configurable); last download time.

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
| `battery-plan.yaml` | When the planner charges and discharges, over your buy price |

Paste the YAML into a card's code editor. The `prices` attributes (`start`, `end`, `spot`, `buy`, `sell`) also work with any other chart card or a template.

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
