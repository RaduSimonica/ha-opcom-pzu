# Roadmap

Planned improvements, not implemented yet. Each ships as a version of its own
(see [CONTRIBUTING.md](CONTRIBUTING.md): bump the manifest version and add a
changelog section in every pull request into `main`).

## 1. Battery share: how much of the battery to use

Users choose a percentage of the battery the planner may work with, e.g. 50%.

- New battery option `battery_usable_pct` (1 to 100, default 100), in the
  battery page of the options flow.
- Meaning: the planner only uses that share of the capacity. The rest is a
  **reserve at the bottom of the battery that is never touched** (backup power,
  self-consumption). At 50% of a 10 kWh battery the planner works with 5 kWh.
- With a state-of-charge sensor, only the energy *above* the reserve counts as
  available: `available = clamp(stored - reserve, 0, usable)`. A battery at or
  below the reserve has nothing to sell. The SoC sensor still reports the whole
  battery.
- Plan attributes: keep `capacity_kwh` as the share the planner may use and add
  `reserve_kwh`.
- Tests: usable share caps the plan's capacity; SoC below the reserve gives
  zero available energy; with 100% nothing changes. Add the field to the
  English and Romanian translations and the README.

## 2. Developer name on the device

Show "By Radu Simonica" instead of "By OPCOM".

- `DeviceInfo.manufacturer` in `entity.py`: `"OPCOM"` becomes `"Radu Simonica"`.
- Existing installs pick it up on the next start (the device registry entry is
  updated when the entities are set up). Keep the model as "PZU day-ahead market".
- The data source stays credited to OPCOM in the README.

## 3. One price chart instead of two

The "today" and "upcoming" example cards become a single `prices.yaml`.

- One card showing the recent past and everything ahead: a rolling window
  (for example `span: {start: hour, offset: -6h}` with `graph_span: 36h`), with
  the *now* marker. Users who prefer the whole day can use `start: day` and
  `graph_span: 48h`.
- Data: the series entity is `sensor.opcom_pzu_upcoming_prices`, which changes
  every 15 minutes and when tomorrow is published, so the card refreshes by
  itself. Its `data_generator` merges it with the intervals already past from
  `sensor.opcom_pzu_prices_today` (read through `hass.states`), keyed by start
  time and sorted.
- Remove `today.yaml` and `upcoming.yaml`; keep `battery-plan.yaml`. Update the
  README table, the entity-ID note and the changelog.
- Check the generator against real integration output (as done for 0.2.0), and
  update the cards on the test dashboard.

## 4. Countdown sensors: time to the cheapest price

Timer sensors that count down in minutes.

- **Time to cheapest price:** minutes until the cheapest *buy* price among the
  upcoming intervals starts. **Time to best sell price:** the same for the
  highest *sell* price. Both use the user's own buy and sell prices.
- Window: from the current interval to a look-ahead horizon, default 24 hours
  (option `timer_horizon_hours`, 1 to 48, in the general options page). If
  tomorrow is not published yet, the window ends with today's data. Ties pick
  the earliest interval. `0` when the best interval is the current one.
- State in minutes (`device_class: duration`, unit `min`), rounded up so it
  reads 1 until the interval starts. Attributes: `at` (start, local time),
  `ends_at`, `price`.
- The state must tick every minute, not only on the 15-minute tick: the
  entities register their own minute timer (`async_track_utc_time_change` with
  `second=1`) instead of making every sensor update each minute.
- Tests: expected minutes computed independently from the fixture CSV, and the
  value dropping by one after a minute passes. Add translations and a README
  entry; a dashboard example is optional.
## 5. Pre-filled formulas for common Romanian suppliers

Let users pick their supplier's plan in the buy and sell pages and fill the
formula fields for them (they stay editable). Prices are per kWh, with VAT
where it applies to the buy price.

| Plan | Buy | Sell |
|---|---|---|
| E.ON Dinamic Pro | `(pzu + 0.10 + 0.464) * 1.21` | `pzu` |
| PPC MaxControl | `(max(min(pzu, 0.999), 0) + 0.097 + 0.00363 + 0.464) * 1.21` | `max(pzu, 0)` |
| Yellowgrid | `(min(0.75*pzu + 0.1375, 0.55) + 0.464) * 1.21` | `max(0.125 + 0.75*pzu, 0.5)` |

- A "Plan" selector at the top of the buy and sell pages (with "Custom" as the
  default) that fills the formula on selection; the options flow must keep what
  the user typed when they choose Custom.
- Keep the plans in one table (code) so adding a supplier is one line, and list
  them in the README with the date the figures were taken, as suppliers change
  their rates. Tests: every plan's formulas compile and give the expected buy
  and sell price for a sample spot price (including a negative one).

## 6. Energy and state-of-charge forecast sensors

What the plan will do, in energy rather than money. Built from the same plan
as the battery gain sensors (today from the real state of charge, tomorrow from
empty), so they stay consistent with it.

- **Energy to charge** and **energy to export** per plan: kWh drawn from the
  grid in the cheap windows and delivered to the grid in the expensive ones
  (today: the part still ahead; tomorrow: the whole day). Attributes: the
  windows they come from, with each window's kWh and average price.
- **Projected state of charge**: the battery level at the end of the day if the
  plan is followed, as kWh and as a percentage of capacity (the percentage
  needs the capacity, so it follows the capacity sensor when one is set).
- **State of charge curve** as an attribute (a point per interval, from now to
  the end of the plan) so a chart or card can draw it. Also expose the peak
  level and when it is reached.
- The planner already walks the battery level interval by interval, so it only
  has to return that curve (`Plan.soc`) next to the slots. Tests: the curve
  starts at the real level, never leaves 0..capacity, ends at start + bought
  (net of efficiency) - sold, and the energy sensors equal the sums over the
  schedule windows. Translations and README.

## 7. Custom dashboard card(s)

apexcharts-card works but is not the nicest to look at, and each example needs
a long block of YAML. A card made for this integration would look better and be
one line to add.

- **Battery card**: a battery shape showing the current level (from the SoC
  sensor) and the projected level at the end of the day, with what the plan does
  now (charging, exporting, idle), the energy to charge and export (item 6) and
  the next change. Colours follow the theme.
- **Price card**: the merged price chart of item 3 (spot, buy, sell, now
  marker, cheap and expensive windows shaded, the planned charge and discharge
  windows marked), instead of an apexcharts configuration.
- Delivery: a single JavaScript file in the repository, registered as a
  dashboard resource by the integration so HACS users do not add it by hand
  (or documented as one step if that is not possible). Cards take the entity
  from their configuration and read the existing attributes, so no new backend
  work is needed beyond items 3 and 6. Needs a visual editor for the card
  options, a preview in the card picker, and light and dark theme checks.
- Decide first whether it is one card with options or two, and whether to build
  on Lit (as Home Assistant does) or plain web components. Keep the apexcharts
  examples as the fallback.
