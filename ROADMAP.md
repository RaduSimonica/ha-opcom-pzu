# Roadmap

Planned improvements, not implemented yet. Each ships as a version of its own
(see [CONTRIBUTING.md](CONTRIBUTING.md): bump the manifest version and add a
changelog section in every pull request into `main`).

## 0. Split into two integrations

**OPCOM PZU** (this repository) keeps everything about prices. **Battery
Planner** ([RaduSimonica/battery-planner](https://github.com/RaduSimonica/battery-planner),
new) takes price series, a battery, and optionally solar and consumption, and
plans when to charge and discharge. They are joined only by the
[price series contract](docs/price-series.md), so Battery Planner also works with
other price sources and with fixed prices.

Order of work:

1. Write the contract (`docs/price-series.md`, done).
2. Battery Planner 0.1.0: the planner, battery options and sensors, their tests
   and the battery dashboard example. **Done.** Its own roadmap holds the
   battery items (battery share, countdown sensors, energy and state-of-charge
   sensors, solar and consumption) and the fixed/time-of-use price sources.
3. OPCOM PZU 0.3.0: the battery code and entities removed, with a migration
   note and automatic clean-up. **Done.**
4. The items below, each in the repository named in its title.


## 2. Developer name on the device

Show "By Radu Simonica" instead of "By OPCOM".

- `DeviceInfo.manufacturer` in `entity.py`: `"OPCOM"` becomes `"Radu Simonica"`.
- Existing installs pick it up on the next start (the device registry entry is
  updated when the entities are set up). Keep the model as "PZU day-ahead market".
- The data source stays credited to OPCOM in the README.

## 3. [OPCOM PZU] One price chart instead of two

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

## 5. [OPCOM PZU] Pre-filled formulas for common Romanian suppliers

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

## 7. Price card (the battery card is in the Battery Planner roadmap)

apexcharts-card works but is not the nicest to look at, and each example needs
a long block of YAML. A card made for this integration would look better and be
one line to add. (The battery card belongs to Battery Planner.)

- **Price card**: the merged price chart of item 3 (spot, buy, sell, now
  marker, cheap and expensive windows shaded, the planned charge and discharge
  windows marked), instead of an apexcharts configuration.
- Delivery: a single JavaScript file in the repository, registered as a
  dashboard resource by the integration so HACS users do not add it by hand
  (or documented as one step if that is not possible). Cards take the entity
  from their configuration and read the existing attributes, so no new backend
  work is needed beyond item 3. Needs a visual editor for the card
  options, a preview in the card picker, and light and dark theme checks.
- Decide first whether it is one card with options or two, and whether to build
  on Lit (as Home Assistant does) or plain web components. Keep the apexcharts
  examples as the fallback.

