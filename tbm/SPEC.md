# Tunnel Boring Machine (TBM) — Product Specification

Server-only Fabric mod for Minecraft 26.3. Players assemble a tunnel boring
machine purely out of vanilla blocks; the mod recognizes the multiblock,
runs it, and no client installation is required.

Reference builds: `Variante1–4.schem` (parsed 2026-10-15, see Anatomy below).

## Concept

A TBM is a player-built multiblock that slowly drives itself forward through
terrain, destroying blocks in its face plane, optionally lining the finished
tunnel behind it with material supplied in barrels.

### Recognition (rule-based, not blueprint-fixed)

Trigger: right-click on a **wall sign** whose first line is exactly `[TBM]`.

Derived invariants (all verified against the four schematics):

1. The sign is wall-mounted on an **iron block**; the block below that iron
   block is a **furnace** (the generator).
2. Mining direction = opposite of the sign's facing (pistons face the rock,
   the sign faces the crew side).
3. **Front face**: one plane in front of the wall plane, a connected set of
   pistons (`facing=<miningDir>`, `extended=false`). Any size and any
   silhouette (square, octagonal disc, circle approximation) is legal as
   long as the face plane contains no foreign blocks inside the piston
   bounding box.
4. **Backing wall**: every piston must have an iron block directly behind it.
   The iron wall is connected and contains the furnace.
5. **Placer ring** (optional, makes it a "drill + placer"): behind the wall,
   rear pistons (`facing=<reverse of miningDir>`) each paired with a
   **barrel** in the nearer plane. Rings may be partial — omitting
   barrels/pistons on a side simply omits lining there (e.g. no ceiling).
6. **Chest yard** (optional): chests (incl. double chests) standing on the
   iron floor behind the machine. Used for item collection when enabled.

Validation feedback is a single success message or the precise defect
(German by default, `config/tbm.json` `lang` switchable to English).

### Operation

- **Activation**: right-click the `[TBM]` sign. Success message confirms;
  errors explain exactly what is wrong.
- **Energy**: the embedded furnace acts as the generator. Usual fuels apply.
  The furnace's vanilla smelting UI is replaced (inside an active TBM) by a
  generator UI showing the fuel state. Implemented with vanilla menu types +
  custom titles so vanilla clients work unmodified.
- **Propulsion**: fuel-driven, fixed pace per block advanced
  (**independent of face area**). Out of fuel → machine stands still.
- **Mining**: the machine advances one block at a time. Blocks in the swept
  face layer are destroyed. **Default: no item drops whatsoever.**
  Config toggle (`collectDrops`, default `false`) routes mined items into
  the machine's chests instead of deleting them.
- **Lining**: rear pistons place the barrel's block type behind the machine
  as it advances (tunnel lining). A barrel holds exactly one item type
  (restricted inventory via intercepted, retitled vanilla container UI);
  where no barrels/pistons are built, no lining is placed.
- **Fluids** encountered in the swept layer are cleared like blocks.

### Configuration (`config/tbm.json`)

| Key | Default | Meaning |
|-----|---------|---------|
| `lang` | `"de"` | Message language (`de`/`en`) |
| `collectDrops` | `false` | `false`: mined blocks vanish. `true`: mined items go to the machine's chests |
| `advanceIntervalTicks` | `100` | Ticks per one-block advance |
| `maxFaceDimension` | `12` | Hard cap for face width/height (performance guard) |

### Persistence & lifecycle

Registered machines persist in `<world>/tbm/tbms.json` (anchor position,
dimension, direction, member block lists, energy state). Breaking any
member block, removing the sign, or altering the sign text deactivates the
machine (registered blocks become freely editable again). Active machine
blocks are protected: attacking one is denied with a pointer to
deactivation.

### Commands (English root)

- `/tbm list` — registered machines with status (fuel, position, direction)
- `/tbm deactivate` — deactivate the machine you are looking at / standing at

## Roadmap

- [x] Structure recognition + validation messages
- [x] Registry + persistence + protection
- [ ] Propulsion engine (translate multiblock, destroy swept layer)
- [ ] Generator fuel accounting (burn-time → energy pool)
- [ ] Placer lining + barrel material management
- [ ] Retitled vanilla UIs (generator furnace, 1-material barrel buffer)
- [ ] Chest logistics (collectDrops routing)
