# Changelog

Every pull request into `main` bumps the version in
`custom_components/opcom_pzu/manifest.json` and adds a section here. Merging
publishes a GitHub release with this section as its notes, which is what HACS
shows as the version. Versions follow [semantic versioning](https://semver.org):
`0.x.0` for new features, `0.x.y` for fixes.

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
