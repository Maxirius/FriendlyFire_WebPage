"""Board catalogue: how the 54 leaderboards derive from raw vanilla stats.

Replicates the normalization the StatsWebUI mod performed, so the
website's data shape stays identical when the source flips from the
mod's HTTP API to the MCStats-to-SQL MariaDB tables.

All formulas operate on the flat stat map written by MCStats-to-SQL:
``{"custom:minecraft:jump": 42, "mined:minecraft:stone": 100, ...}``.
"""
from __future__ import annotations

KM_PER_CM = 1.0 / 100_000.0

# Vanilla hostile mobs (kill boards + hostile-kills sum).
HOSTILE_MOBS = {
    "minecraft:zombie", "minecraft:skeleton", "minecraft:creeper",
    "minecraft:enderman", "minecraft:blaze", "minecraft:wither_skeleton",
    "minecraft:witch", "minecraft:spider", "minecraft:cave_spider",
    "minecraft:slime", "minecraft:magma_cube", "minecraft:silverfish",
    "minecraft:guardian", "minecraft:shulker", "minecraft:husk",
    "minecraft:stray", "minecraft:phantom", "minecraft:drowned",
    "minecraft:pillager", "minecraft:vindicator", "minecraft:evoker",
    "minecraft:ravager", "minecraft:vex", "minecraft:piglin",
    "minecraft:piglin_brute", "minecraft:hoglin", "minecraft:zoglin",
    "minecraft:zombified_piglin", "minecraft:ghast", "minecraft:hoglin",
    "minecraft:breeze", "minecraft:bogged", "minecraft:creaking",
}

# Passive / farmable mobs (farm-kills sum).
FARM_MOBS = {
    "minecraft:cow", "minecraft:pig", "minecraft:sheep", "minecraft:chicken",
    "minecraft:rabbit", "minecraft:horse", "minecraft:donkey", "minecraft:mule",
    "minecraft:goat", "minecraft:fox", "minecraft:wolf", "minecraft:cat",
    "minecraft:ocelot", "minecraft:bee", "minecraft:villager",
    "minecraft:iron_golem", "minecraft:snow_golem", "minecraft:mooshroom",
    "minecraft:llama", "minecraft:trader_llama", "minecraft:panda",
    "minecraft:polar_bear", "minecraft:turtle", "minecraft:axolotl",
    "minecraft:frog", "minecraft:camel", "minecraft:sniffer",
    "minecraft:armadillo", "minecraft:happy_ghast",
}

BOSS_MOBS = {
    "minecraft:ender_dragon", "minecraft:wither", "minecraft:warden",
    "minecraft:elder_guardian",
}

ORE_BLOCKS = {
    "minecraft:coal_ore", "minecraft:deepslate_coal_ore",
    "minecraft:iron_ore", "minecraft:deepslate_iron_ore",
    "minecraft:copper_ore", "minecraft:deepslate_copper_ore",
    "minecraft:gold_ore", "minecraft:deepslate_gold_ore",
    "minecraft:nether_gold_ore",
    "minecraft:redstone_ore", "minecraft:deepslate_redstone_ore",
    "minecraft:lapis_ore", "minecraft:deepslate_lapis_ore",
    "minecraft:diamond_ore", "minecraft:deepslate_diamond_ore",
    "minecraft:emerald_ore", "minecraft:deepslate_emerald_ore",
    "minecraft:nether_quartz_ore",
}

DIAMOND_ORES = {"minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"}
IRON_ORES = {"minecraft:iron_ore", "minecraft:deepslate_iron_ore"}


def _sum(stats: dict, prefix: str, ids: set[str] | None = None) -> int:
    total = 0
    for k, v in stats.items():
        if not k.startswith(prefix):
            continue
        if ids is not None:
            item = k[len(prefix):]
            if item not in ids:
                continue
        total += v
    return total


def _cm_km(value_cm: int) -> float:
    return round(value_cm * KM_PER_CM, 2)


def _fmt_km(value_cm: int) -> str:
    return f"{_cm_km(value_cm):.2f} km"


def _fmt_count(v: int) -> str:
    return str(int(v))


def _fmt_hours(ticks: int) -> str:
    return f"{round(ticks / 72_000.0, 2):.2f} h"   # 20 ticks/s, 3600s/h


def _fmt_hearts(half_hearts: int) -> str:
    return f"{half_hearts / 10.0:.1f} \u2665"


def _fmt_per_hour(kills: int, ticks: int) -> str:
    hours = ticks / 72_000.0
    rate = kills / hours if hours > 0 else 0.0
    return f"{rate:.1f}/h"


# ---------------------------------------------------------------------------
# Board definitions: id -> (category, unit, formula, display-formatter)
# ---------------------------------------------------------------------------

def _boards(stats: dict) -> dict[str, dict]:
    """Compute all board entries for one player's flat stat map."""
    play_ticks = stats.get("custom:minecraft:play_time", 0)
    out: dict[str, dict] = {}

    def put(bid, cat, unit, value, display):
        out[bid] = {"value": value, "display": display,
                    "unit": unit, "category": cat}

    put("advancements", "advancements", "count", 0, "0")  # filled by caller
    put("play-time", "life", "hours", round(play_ticks / 72_000.0, 2),
        _fmt_hours(play_ticks))
    put("animals-bred", "life", "count",
        stats.get("custom:minecraft:animals_bred", 0),
        _fmt_count(stats.get("custom:minecraft:animals_bred", 0)))
    fc = stats.get("custom:minecraft:fish_caught", 0)
    put("fish-caught", "life", "count", fc, _fmt_count(fc))
    vt = stats.get("custom:minecraft:traded_with_villagers", 0)
    put("villager-trades", "life", "count", vt, _fmt_count(vt))
    ie = stats.get("custom:minecraft:enchant_item", 0)
    put("items-enchanted", "life", "count", ie, _fmt_count(ie))
    sl = stats.get("custom:minecraft:sleep_in_bed", 0)
    put("sleeps", "life", "count", sl, _fmt_count(sl))
    jp = stats.get("custom:minecraft:jump", 0)
    put("jumps", "life", "count", jp, _fmt_count(jp))

    walk = stats.get("custom:minecraft:walk_one_cm", 0)
    sprint = stats.get("custom:minecraft:sprint_one_cm", 0)
    swim = stats.get("custom:minecraft:swim_one_cm", 0)
    boat = stats.get("custom:minecraft:boat_one_cm", 0)
    elytra = stats.get("custom:minecraft:aviate_one_cm", 0)
    horse = stats.get("custom:minecraft:horse_one_cm", 0)
    ghast = stats.get("custom:minecraft:happy_ghast_one_cm", 0)
    nautilus = stats.get("custom:minecraft:nautilus_one_cm", 0)
    cart = stats.get("custom:minecraft:minecart_one_cm", 0)
    strider = stats.get("custom:minecraft:strider_one_cm", 0)
    total = walk + sprint + swim + boat + elytra + horse + ghast \
        + nautilus + cart + strider \
        + stats.get("custom:minecraft:pig_one_cm", 0) \
        + stats.get("custom:minecraft:climb_one_cm", 0) \
        + stats.get("custom:minecraft:fall_one_cm", 0)

    put("distance-total", "exploration", "km", _cm_km(total), _fmt_km(total))
    ws = walk + sprint
    put("distance-walk-sprint", "exploration", "km", _cm_km(ws), _fmt_km(ws))
    put("distance-walk", "exploration", "km", _cm_km(walk), _fmt_km(walk))
    put("distance-sprint", "exploration", "km", _cm_km(sprint), _fmt_km(sprint))
    put("distance-swim", "exploration", "km", _cm_km(swim), _fmt_km(swim))
    put("distance-boat", "exploration", "km", _cm_km(boat), _fmt_km(boat))
    put("distance-elytra", "exploration", "km", _cm_km(elytra), _fmt_km(elytra))
    put("distance-horse", "exploration", "km", _cm_km(horse), _fmt_km(horse))
    put("distance-happy-ghast", "exploration", "km", _cm_km(ghast),
        _fmt_km(ghast))
    put("distance-nautilus", "exploration", "km", _cm_km(nautilus),
        _fmt_km(nautilus))
    put("distance-minecart", "exploration", "km", _cm_km(cart), _fmt_km(cart))
    put("distance-strider", "exploration", "km", _cm_km(strider),
        _fmt_km(strider))

    oc = (stats.get("custom:minecraft:open_chest", 0)
          + stats.get("custom:minecraft:open_barrel", 0)
          + stats.get("custom:minecraft:open_shulker_box", 0))
    put("chests-opened", "exploration", "count", oc, _fmt_count(oc))

    bm = _sum(stats, "mined:")
    put("blocks-mined", "mining", "blocks", bm, _fmt_count(bm))
    ores = _sum(stats, "mined:", ORE_BLOCKS)
    put("ores", "mining", "blocks", ores, _fmt_count(ores))
    dm = _sum(stats, "mined:", DIAMOND_ORES)
    put("diamonds-mined", "mining", "blocks", dm, _fmt_count(dm))
    im = _sum(stats, "mined:", IRON_ORES)
    put("iron-mined", "mining", "blocks", im, _fmt_count(im))
    ad = stats.get("mined:minecraft:ancient_debris", 0)
    put("ancient-debris", "mining", "blocks", ad, _fmt_count(ad))
    lm = _sum(stats, "mined:")  # refined below: only *_log / *_stem
    logs = sum(v for k, v in stats.items()
               if k.startswith("mined:")
               and (k.endswith("_log") or k.endswith("_stem")))
    put("logs-mined", "mining", "blocks", logs, _fmt_count(logs))
    sc = stats.get("mined:minecraft:sculk", 0)
    put("sculk-mined", "mining", "blocks", sc, _fmt_count(sc))
    tb = _sum(stats, "broken:")
    put("tools-broken", "mining", "count", tb, _fmt_count(tb))

    de = stats.get("custom:minecraft:deaths", 0)
    put("deaths", "deaths", "count", de, _fmt_count(de))
    for mob, bid in (("zombie", "killed-by-zombie"),
                     ("creeper", "killed-by-creeper"),
                     ("skeleton", "killed-by-skeleton"),
                     ("warden", "killed-by-warden")):
        v = stats.get(f"killed_by:minecraft:{mob}", 0)
        put(bid, "deaths", "count", v, _fmt_count(v))

    hk = sum(v for k, v in stats.items()
             if k.startswith("killed:")
             and k[len("killed:"):] in HOSTILE_MOBS)
    put("hostile-kills", "combat", "kills", hk, _fmt_count(hk))
    fk = sum(v for k, v in stats.items()
             if k.startswith("killed:")
             and k[len("killed:"):] in FARM_MOBS)
    put("farm-kills", "combat", "kills", fk, _fmt_count(fk))
    PLURAL = {"enderman": "endermen"}
    for mob in ("zombie", "skeleton", "creeper", "enderman", "blaze",
                "wither_skeleton"):
        v = stats.get(f"killed:minecraft:{mob}", 0)
        bid = PLURAL.get(mob) or (
            mob.replace("_", "-") + ("" if mob.endswith("s") else "s"))
        put(bid, "combat", "kills", v, _fmt_count(v))
    bo = sum(v for k, v in stats.items()
             if k.startswith("killed:")
             and k[len("killed:"):] in BOSS_MOBS)
    put("bosses", "combat", "kills", bo, _fmt_count(bo))
    ed = stats.get("killed:minecraft:ender_dragon", 0)
    wi = stats.get("killed:minecraft:wither", 0)
    wa = stats.get("killed:minecraft:warden", 0)
    eg = stats.get("killed:minecraft:elder_guardian", 0)
    put("ender-dragon", "combat", "kills", ed, _fmt_count(ed))
    put("wither", "combat", "kills", wi, _fmt_count(wi))
    put("warden", "combat", "kills", wa, _fmt_count(wa))
    put("elder-guardian", "combat", "kills", eg, _fmt_count(eg))
    rw = stats.get("custom:minecraft:raid_win", 0)
    put("raids-won", "combat", "count", rw, _fmt_count(rw))
    pk = stats.get("killed:minecraft:player", 0)
    put("player-kills", "combat", "kills", pk, _fmt_count(pk))
    dd = stats.get("custom:minecraft:damage_dealt", 0)
    put("damage-dealt", "combat", "hearts", dd / 10.0, _fmt_hearts(dd))
    kh = (hk + fk + pk)
    put("kills-per-hour", "combat", "kills_per_hour",
        round(kh / (play_ticks / 72_000.0), 1) if play_ticks else 0.0,
        _fmt_per_hour(kh, play_ticks))

    return out
