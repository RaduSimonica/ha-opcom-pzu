# OPCOM PZU for Home Assistant

Romanian day-ahead (PZU) electricity prices in Home Assistant: free, no account, no licence.

- **Real prices, not just spot:** set what you actually *pay* and *receive* (commission, taxes, VAT, or your own formula).
- **Feeds a battery planner:** the *Upcoming prices* sensor follows an open [price series format](docs/price-series.md), so [Battery Planner](https://github.com/RaduSimonica/battery-planner) can plan when to charge and discharge your battery from your own buy and sell prices.
- **Full history** as native Home Assistant statistics, back to 2015 if you want, plus a service to pull any date range.
- **Tomorrow's prices** as soon as OPCOM publishes them (about 13:45 Romanian time), with a full series for charts.
- Correct **CET market time** and **DST days** (92 / 100 intervals), 15-minute resolution (older days are hourly).

> **Beta.** This integration works, but it is young and the supplier plans are approximations. Please report what you find in the [issues](https://github.com/RaduSimonica/ha-opcom-pzu/issues) and attach the diagnostics file (*Settings → Devices & services → OPCOM PZU → three dots → Download diagnostics*).

## Install

HACS → Custom repositories → add this repository as *Integration* → install → restart. HACS shows the released version (see the [changelog](CHANGELOG.md)). Then *Settings → Devices & services → Add integration → OPCOM PZU*.

**Snapshots:** new things first appear as pre-releases (`vX.Y.Z-snapshot.N`). To try them, open the repository in HACS, menu → *Show beta versions*, then update. Stable releases are published once in a while from the snapshots that proved fine.

## Setup

You choose a currency (RON or EUR; fixed afterwards) and how many months of history to download.

> **About history.** Each past day is one request to OPCOM, sent one per second so their server is not overloaded. 12 months take about 10 minutes, the full archive (since 2015) over an hour. It runs in the background, resumes by itself after a restart, and reports progress in the **Logbook** and on the *History download progress* sensor.

## Your buy and sell price

*Configure → Buy price / Sell price.* Each page shows a live example using the current spot price. Pick your **supplier plan** to fill in the formula (see below), or keep *Custom*.

| | Simple fields | Formula (optional, replaces the fields) |
|---|---|---|
| **Sell** (what you receive) | `spot × (1 − commission%) − fixed fee` | e.g. `max(pzu*0.9 - 0.05, 0)` |
| **Buy** (what you pay) | `(spot × (1 + markup%) + fixed charges) × (1 + VAT%)` | e.g. `(pzu + 0.45) * 1.21` |

### Supplier plans

Choosing a plan fills the formula for you, and you can still edit it (it then becomes *Custom*). Figures as published on 2026-11-09; suppliers change their rates, so check yours.

| Plan | Buy | Sell |
|---|---|---|
| E.ON Dinamic Pro | `(pzu + 0.10 + 0.464) * 1.21` | `pzu` |
| PPC MaxControl | `(max(min(pzu, 0.999), 0) + 0.097 + 0.00363 + 0.464) * 1.21` | `max(pzu, 0)` |
| Yellowgrid | `(min(0.75*pzu + 0.1375, 0.55) + 0.464) * 1.21` | `max(0.125 + 0.75*pzu, 0.5)` |

Your plan missing or wrong? [Open an issue](https://github.com/RaduSimonica/ha-opcom-pzu/issues/new/choose) with its formula: adding one is a single line.

**Known limitations of the plans**

- **Regulated tariffs depend on your distribution operator.** The plans use the value for **Retele Electrice Romania**. If you are in another zone, edit the formula (see below).
- **PPC MaxControl.** The buy price follows PPC's offer: a fixed 0.097, plus the PZU price of each 15 minutes limited to 0 to 0.999, plus 0.00363. The offer does **not** state the price for energy you send to the grid (it refers to the rules for prosumers), so the sell price here (`max(pzu, 0)`) is an assumption: check your contract. It needs a smart meter that measures every 15 minutes; with an hourly meter your consumption is spread evenly over the hour.
- **Yellowgrid.** The buy formula is unconfirmed and may be wrong; the sell formula follows their advertised minimum of 0.50 with a share of the gain above it.
- **E.ON Dinamic Pro** assumes a supply component of 0.10, which is contract-specific.
- Suppliers change their rates, so treat the plans as a starting point and check them against your bill.

### Distribution zones: the regulated part of the price

Every plan adds the regulated tariffs, `0.464` in the formulas, before VAT. It depends on who operates the grid where you live (it is on your bill). These are PPC's figures from November 2026 and they can change at any time: transport 0.03645, system services 0.01272, green certificates 0.0740192, CfD 0.000144, cogeneration 0.0157 and excise 0.00768 are the same everywhere (together 0.14671), and the distribution tariff for low voltage differs:

| Distribution operator | Counties | Distribution | Regulated total to use |
|---|---|---|---|
| Retele Electrice Romania | București, Ilfov, Giurgiu, Călărași, Constanța, Ialomița, Tulcea, Arad, Caraș-Severin, Hunedoara, Timiș | 0.31739 | **0.464** |
| Distribuție Oltenia | Argeș, Dolj, Gorj, Mehedinți, Olt, Teleorman, Vâlcea | 0.33325 | **0.480** |
| Distribuție Energie Electrică Romania | Brașov, Alba, Sibiu, Mureș, Harghita, Covasna, Cluj, Maramureș, Satu Mare, Sălaj, Bihor, Bistrița-Năsăud, Prahova, Buzău, Dâmbovița, Brăila, Galați, Vrancea | 0.35534 | **0.502** |
| Delgaz Grid | Botoșani, Suceava, Neamț, Iași, Bacău, Vaslui | 0.38791 | **0.535** |

To change it, pick the plan and edit the formula, replacing `0.464` with your value. Examples for a house in Brașov (Distribuție Energie Electrică Romania, 0.502):

| Plan | Buy price formula for Brașov |
|---|---|
| E.ON Dinamic Pro | `(pzu + 0.10 + 0.502) * 1.21` |
| PPC MaxControl | `(max(min(pzu, 0.999), 0) + 0.097 + 0.00363 + 0.502) * 1.21` |
| Yellowgrid | `(min(0.75*pzu + 0.1375, 0.55) + 0.502) * 1.21` |

The sell formulas have no regulated part, so they stay as they are. If you are not sure of your operator, it is printed on your electricity bill.

### Formulas

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

A ready-to-paste card is in [`examples/dashboards/`](examples/dashboards) (it needs [apexcharts-card](https://github.com/RomRider/apexcharts-card) from HACS):

| File | Shows |
|---|---|
| `prices.yaml` | One chart: the last hours and every price ahead (today and tomorrow), spot as columns coloured by level, plus your buy and sell price, with a marker for now |

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
