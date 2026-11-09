# Price series contract

How OPCOM PZU hands prices to other integrations, in particular
[Battery Planner](https://github.com/RaduSimonica/battery-planner). Any
integration, template sensor or script that exposes the same shape can feed the
planner, so it works with other price sources and with fixed prices too.

## Shape

A sensor entity whose attribute `prices` is a list of intervals, oldest first:

```yaml
unit: RON/kWh            # currency per kWh, for display
prices:
  - start: "2026-11-09T12:00:00+02:00"
    end:   "2026-11-09T12:15:00+02:00"
    buy:   1.2287        # what you pay to import one kWh
    sell:  0.9100        # what you receive for exporting one kWh
    spot:  0.9100        # optional, the market price; not used for planning
```

Rules:

- `start` and `end` are ISO 8601 timestamps with a UTC offset. Intervals are
  half open, `[start, end)`, and may be any length (15, 30 or 60 minutes);
  all intervals of one series should have the same length.
- Prices are per kWh, in the currency in `unit`, and **already include**
  everything the user pays or receives (tariff, taxes, VAT). They may be zero
  or negative.
- The series covers the current interval and everything known after it. Past
  intervals may be present and are ignored. Gaps are allowed; the planner only
  plans over what is published.
- Unknown extra keys are ignored, so the format can grow.
- The sensor state is not part of the contract (OPCOM PZU uses the number of
  intervals). Consumers read the attribute and update when it changes.

## OPCOM PZU

The *Upcoming prices* sensor (`upcoming_prices`, a diagnostic entity; its entity
ID can carry the device's area, e.g. `sensor.server_opcom_pzu_upcoming_prices`)
follows this contract: the rest of today, and tomorrow once OPCOM has published
it (about 13:45 Romanian time), refreshed every 15 minutes. The *buy* and *sell*
values come from the formulas set under *Configure*.

## Other sources

A source that does not use this shape needs a small adapter in the consumer, or
a template sensor that builds the attribute. Example for fixed prices:
a time-of-use tariff is the same list with one entry per period.
