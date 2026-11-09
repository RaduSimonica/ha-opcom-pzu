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


## 1. Price card (the battery card is in the Battery Planner roadmap)

apexcharts-card works but is not the nicest to look at, and each example needs
a long block of YAML. A card made for this integration would look better and be
one line to add. (The battery card belongs to Battery Planner.)

- **Price card**: the merged price chart (`prices.yaml`, done) with spot, buy, sell, a now
  marker, the cheap and expensive windows shaded and the planned charge and discharge
  windows marked, instead of an apexcharts configuration.
- Delivery: a single JavaScript file in the repository, registered as a
  dashboard resource by the integration so HACS users do not add it by hand
  (or documented as one step if that is not possible). Cards take the entity
  from their configuration and read the existing attributes, so no new backend
  work is needed  Needs a visual editor for the card
  options, a preview in the card picker, and light and dark theme checks.
- Decide first whether it is one card with options or two, and whether to build
  on Lit (as Home Assistant does) or plain web components. Keep the apexcharts
  examples as the fallback.

