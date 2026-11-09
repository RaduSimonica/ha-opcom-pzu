# Changelog

Every pull request into `main` bumps the version in
`custom_components/opcom_pzu/manifest.json` and adds a section here. Merging
publishes a GitHub release with this section as its notes, which is what HACS
shows as the version. Versions follow [semantic versioning](https://semver.org):
`0.x.0` for new features, `0.x.y` for fixes.

## [0.4.0] - 2026-11-09

### Added
- **Supplier plans.** The buy and sell pages have a *Supplier plan* selector (E.ON Dinamic Pro, PPC MaxControl, Yellowgrid) that fills the formula. You can edit the formula afterwards, and it becomes *Custom*.
- **One price chart** (`examples/dashboards/prices.yaml`) replaces the *today* and *upcoming* examples: the last hours and everything ahead, with spot, buy and sell price and a marker for now.

### Changed
- The device now shows *Radu Simonica* as the manufacturer instead of OPCOM. The data is still OPCOM's, credited in the README.

## [0.3.0] - 2026-11-09

### Changed (breaking)
- **The battery planner moved to its own integration, [Battery Planner](https://github.com/RaduSimonica/battery-planner).** OPCOM PZU now only handles prices. The battery sensors (net gain, gain per kWh, action, profitable today/tomorrow), the *Battery* settings page and the battery example card are gone from here. The old battery entities and settings are removed automatically on update. To keep planning your battery, install Battery Planner (0.1.0 or later), pick this integration's *Upcoming prices* sensor as its price source, and enter your battery settings there. Entity names change to `sensor.battery_planner_...`.

### Added
- The [price series format](docs/price-series.md) is now documented as a contract: the *Upcoming prices* sensor is the way other integrations read the buy and sell prices.

## [0.2.1] - 2026-11-09

### Fixed
- **Price statistics now keep up while Home Assistant runs.** New hours were only added to the long-term statistics after a restart; they are now added every 15 minutes.
- **Emptied fields in the options are really cleared.** Removing a formula or a fee in the buy/sell pages had no effect before.
- A formula that has no answer for some prices (division by zero, a square root of a negative price) no longer breaks every sensor: the simple fields are used for that price and a warning is logged.
- The battery plan can sell stored energy in the last interval of the day, and the interval in progress only counts the power still available in it.
- The history download no longer gets stuck on a day that OPCOM keeps answering with an HTTP error: the day is skipped for now and retried on the next run (it pauses only if several days in a row fail).
- A stray footer line after the price table no longer discards the whole day; prices written like `1,234.56` are read correctly.
- The *Last price download* sensor keeps its value after a restart.
- *Cheap price now* and *Expensive price now* are both off on a day with a flat price.
- `opcom_pzu.get_prices` lists the days it has no data for (`missing_days`).
- A failed setup no longer leaves a timer behind, and reloading no longer leaves HTTP sessions open.
- The example dashboard cards now explain that entity IDs can carry the device's area as a prefix (e.g. `sensor.server_opcom_pzu_upcoming_prices`), and what to do when a card says "Entity not available".

## [0.2.0] - 2026-11-09

### Added
- **Upcoming prices** sensor: every known interval from now on (the rest of today and tomorrow once published) with spot, buy and sell price, refreshed every 15 minutes. One entity is enough for a "prices from now" chart.
- Ready-to-paste dashboard cards in `examples/dashboards/` (need apexcharts-card): today's price profile, prices from now, and the battery plan (when to charge and discharge).

## [0.1.0] - 2026-11-08

First release.

### Added
- Romanian day-ahead (PZU) prices from OPCOM's public CSV export, in RON or EUR, with no account.
- Correct handling of CET market time, DST days (92 and 100 intervals) and the hourly files published before October 2025.
- Tomorrow's prices fetched from 13:15 local time (with random jitter), then every 15 minutes until published. Everything is cached on disk.
- Buy and sell price from simple fields (commission, fixed fee, markup, charges, VAT) or a safe arithmetic formula over `pzu` and `hour`.
- Battery arbitrage planner: capacity, **separate charge and discharge power**, round-trip efficiency and degradation cost. It can start from your own state-of-charge and capacity sensors. Provides net gain, gain per kWh, the charge/discharge schedule, a `charge`/`discharge`/`idle` sensor and profitable-today/tomorrow binary sensors.
- Daily lowest, highest and average prices, the full price series, and sensors for automations (tomorrow published, cheap now, expensive now).
- History as hourly long-term statistics (`opcom_pzu:spot_price`), a throttled and resumable download with progress in the Logbook, and the `refresh`, `backfill_history` and `get_prices` services.
- Config and options flow with a live price preview, in English and Romanian.

### Fixed
- OPCOM answers HTTP 403 to sessions that keep its `XSRF-TOKEN` cookies (after about 50 requests). The integration uses its own cookie-less session instead of Home Assistant's shared one.
