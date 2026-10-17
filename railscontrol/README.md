# Smart Rails

Automated rail network control for Minecraft (Fabric 26.3, server-side).

Turns a plain vanilla railway into a transit network: station signs pick
destinations, junctions route automatically, and the minecart drives itself.

## Concept

**Station sign** (regular wooden sign, registered with `/rails station add <name>`):

```
┌──────────────┐
│ Main Station │  <- line 1: station name (white, bold) = destination marker
│              │  <- line 2: empty
│ Destination: │  <- line 3
│ North Harbor │  <- line 4: selection (yellow=picked, green=confirmed, gray=none)
└──────────────┘
  [Button]  [Button]  <- one button on each neighbouring block
```

Usage:
- **Left button**: previous destination · **Right button**: next destination
  (only actually reachable stations are offered)
- **Right-click the sign**: confirm the destination → a minecart spawns on the
  platform track; boarding starts the trip
- **Shift + right-click the sign**: normal sign editing (admins)
- Shift + button press: normal redstone pulse (for tinkerers)

Once the rider boards, the cart departs automatically. Junctions are taken
actively: the router bends the cart onto the correct branch before vanilla
physics decides. On arrival the cart brakes by itself and dissolves.

## Junctions & routing

The network is not maintained as a list — the mod **walks the real rail
blocks** (topology scan) and detects branching cells automatically. Every
other station reachable by a real rail path is offered as a destination. The
router picks the shortest path per trip and steers the cart onto the planned
branch at every recognized junction.

Manual junction markers (`/rails junction add`, look at the switch rail)
supplement the automatic detection — useful where geometry is ambiguous or
for decorative crossings.

## High speed

Carts reach elevated speeds only by traversing **powered rails**: each powered
rail passed raises the governed speed by a fixed slice until the configured
maximum is reached (default: 32 powered rails → 34 m/s). Special cells tame
the ride:

- an **unpowered booster** drops the cart back to vanilla speed instantly,
- **detector rails, activator rails and curves** force vanilla pace shortly
  before and on the cell itself, restoring the charged speed afterwards,
- **slopes** are treated like hazards (configurable),
- plain rails hold full speed for 20 cells (configurable), then bleed
  1 m/s per rail down to the vanilla floor.

Because vanilla clamps cart motion internally, the mod transports the cart
kinematically along flat straight stretches and hands control back to vanilla
physics wherever the cap is welcome.

## Commands

| Command | Description |
| --- | --- |
| `/rails station add <name>` | Look at the sign → registers the station (buttons + rail nearby required) |
| `/rails station addcargo <name>` | Registers a **freight station** (🛒 tag on the sign) |
| `/rails station remove <name>` | Removes the registration, clears the sign |
| `/rails station list` | All stations with their current destination |
| `/rails junction add` / `remove` / `list` | Manual junction markers |
| `/rails depart <station>` | Force a departure (testing) |

Mutations require operator level 2; selecting/confirming at the sign is open
to everyone.

Persistence: `<world>/railscontrol/network.json` (stations incl. selected
destination, junction markers). Loaded on server start.

## Reboard window

If a player disembarks mid-trip, the cart parks and waits `reboardSeconds`
(default 30) for them to board again, then continues the trip. If nobody
reboards in time, the cart dismantles itself.

## Configuration

`config/railscontrol.json` (created with defaults on first run):

```json
{
  "lang": "de",                  // player-facing text language: de | en
  "reboardSeconds": 30,          // reboard window after a mid-trip disembark
  "highSpeed": {
    "enabled": true,
    "maxSpeedMs": 34.0,          // top governed speed, metres/second
    "poweredRailsToMax": 32,     // powered rails needed to reach the maximum
    "holdRails": 20,             // plain-rail cells keeping full speed
    "decayMsPerRail": 1.0,       // speed bleed per plain-rail cell
    "brakeOnSlopes": true,       // treat ascents/descents as hazards
    "vanillaSpeedMs": 8.0,       // the floor everything relaxes to
    "lookaheadCells": 6          // cells scanned ahead for upcoming hazards
  }
}
```

Note: powered rails count as chargers only when they actually carry redstone
power (a redstone block placed directly beneath a rail works reliably).

## Limitations (v0.1)

- One simultaneous trip per station; multiple carts at the same platform
  share the route.
- Vertical shafts / sloped junction cells are simplified (standard builds
  work).
- Head-on encounters on single-track networks are avoided by scheduling, not
  signalling — bidirectional double-track networks are a future topic.

## Planned

- Web-side network generator (blueprint/export of network.json, schematic
  output on the companion website).

## Requirements

- Minecraft 26.3, Fabric Loader ≥ 0.19.5, Fabric API
- Server-side only installation (clients need nothing)

## License

[MIT](LICENSE)
