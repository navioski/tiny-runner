"""
TINY RUNNER — a **procedurally generated** side-scrolling platformer written with Pyxel.

Controls:
    Arrow keys / A D   move
    Space / Z / Up     jump (hold to jump higher; press again in mid-air = double jump, against a wall = wall jump)
    C / J / SHIFT      dash (8-way, once per airtime; refilled by landing / crystal / spring)
    X / K              shoot
    Title screen ← → or 1 2 3   pick a difficulty (EASY / NORMAL / HARD)
    R                  restart with a new seed (keeps the difficulty)
    Q                  quit

Design notes:
    * Fixed 320x240 screen, 16 colors only — the same constraints as the GBC
    * All art is "baked" from ASCII art into the image bank at startup; no .pyxres assets needed
    * Physics is solved per frame: move X, resolve X, then move Y, resolve Y (separated axes, the standard platformer approach)
    * Game feel: coyote time, jump buffer, variable jump height, dash buffer, post-dash momentum
    * A few Celeste-isms: 8-way dash / mid-air dash crystal / spring / strawberry (optional collectible)
    * Three difficulties (EASY / NORMAL / HARD) that change hearts, dashes, trap density and crumble timers
    * Vertical space: the height target is a diagonal plus two sine waves (two deliberate climbs and dives),
      paired with 13-17-row wells, 12-18-row steep towers, a two-storey atrium and 10-14-row chasms
    * Sky changes with altitude: sunset in the valley -> night pressing in from the top of the screen -> starfield at the tower top
    * Levels are assembled from segments (features): each one ends on safe footing, and every pit width / step height stays within reach,
      so even a randomly generated level is always completable (verified by --sweep, which runs the bot over many seeds)
"""

import math
import os
import random
import sys

import pyxel

# ---------------------------------------------------------------- constants

TILE = 8
VIEW_W, VIEW_H = 320, 240

# Pyxel's fixed 16 colors (PICO-8 palette)
BLACK, NAVY, PURPLE, GREEN = 0, 1, 2, 3
BROWN, DGRAY, LGRAY, WHITE = 4, 5, 6, 7
RED, ORANGE, YELLOW, LIME = 8, 9, 10, 11
BLUE, LAVENDER, PINK, PEACH = 12, 13, 14, 15

# Letters used by the ASCII art -> palette entries.
# Backgrounds must stay '.': blt uses color 0 as the color key, so outlines use NAVY(1), not BLACK(0),
# otherwise the outline gets keyed out along with it and the sprite falls apart.
PAL = {
    "k": NAVY, "w": WHITE, "s": PEACH, "h": RED, "c": PURPLE,
    "u": BLUE,          # the player's blue hair
    "o": PURPLE,        # player outline: purple, so it reads separately from the navy dress
    "v": NAVY,          # the player's navy dress
    "b": BROWN, "g": LIME, "p": PINK, "P": PURPLE,
    "y": YELLOW, "Y": ORANGE, "r": RED, "d": DGRAY,
}

# physics parameters (pixels / frame at a fixed 60 fps)
GRAVITY = 0.34
MAX_FALL = 6.4
RUN_ACC = 0.42
RUN_MAX = 1.80
FRICTION = 0.55
JUMP_VEL = -5.35
JUMP_CUT = 0.45          # share of velocity kept when jump is released -> short hop
COYOTE = 6               # frames you can still jump after walking off a ledge
JUMP_BUFFER = 7          # frames of jump buffering before landing
STOMP_BOUNCE = -3.6

# ---- extra moves: double jump + wall jump
MAX_JUMPS = 2            # total jumps available after landing
DOUBLE_JUMP_VEL = JUMP_VEL * 0.92
WALL_JUMP_VX = 2.7       # horizontal speed pushing away from the wall
WALL_JUMP_VY = -4.85
WALL_LOCK = 8            # brief loss of horizontal control after a wall jump, or you snap straight back to the wall
WALL_SLIDE = 1.15        # terminal speed while sliding down a wall

# ---- dash: the core move borrowed from Celeste
DASH_SPEED = 4.7         # dash speed (2.6x run speed)
DASH_FRAMES = 8          # a dash lasts 8 frames
DASH_COOLDOWN = 3        # frames after a dash before the next one (short, so dashes chain easily)
DASH_UP_SCALE = 0.82     # up-dashes are slightly slower so one doesn't launch you clean off the screen
DASH_BUFFER = 5          # frames of dash buffering before landing
DASH_BOOST_FRAMES = 10   # frames of "above-run-speed" momentum kept after a dash
DASH_BOOST_KEEP = 0.55   # share of speed kept at the instant a dash ends

# ---- spring / dash crystal / strawberry
SPRING_VEL = -6.7        # spring launch speed (≈ 8 tiles high)
SPRING_ANIM = 10
CRYSTAL_RESPAWN = 150    # frames before an eaten dash crystal grows back

# ---- shooting
BULLET_SPEED = 4.4
BULLET_RANGE = 128       # range in pixels, roughly half a screen
SHOOT_COOLDOWN = 9       # frames between two shots
MAX_BULLETS = 3          # max bullets alive on screen at once

# ---- crumbling slabs (the exact timers are per-difficulty overrides in DIFFS)

# movement limits derived from the parameters above; the generator must respect both
JUMP_HEIGHT = JUMP_VEL ** 2 / (2 * GRAVITY)          # ≈ 42 px ≈ 5.2 tiles
JUMP_DISTANCE = RUN_MAX * (2 * abs(JUMP_VEL) / GRAVITY)  # ≈ 56 px ≈ 7 tiles

# level tiles
EMPTY, SOLID, PLATFORM, SPIKE, BREAK, CRUMBLE = 0, 1, 2, 3, 4, 5
DECOR = 6                # pure decoration (battlements): no collision, it just makes a wall read as a wall

GROUND_TOP = 22          # default ground row (the generator actually uses its own g)
GROUND_DEPTH = 5         # rows of fill under each floor — a castle in the sky needs slabs, not soil

# generation parameters
SEGMENTS = 34            # roughly how many segments per run
G_MIN, G_MAX = 8, 78        # the world is 84 rows, enough to climb from the bottom to the top
START_ROW = 66              # start: the foundation at the bottom of the world
TARGET_TOP = 14             # goal: climb all the way to around row 14 and plant the flag    # allowed band for ground height (smaller row = higher up)
LEVEL_H = 84

# Height waves: the target height for a run isn't a straight line but a "diagonal + two sine waves",
# so it deliberately dives twice on the way up, then climbs back — that's the skeleton of the vertical space.
WAVE_ROWS = 8.0
WAVE_TURNS = 2.0

# ---------------------------------------------------------------- difficulty
#
# Borrowing Celeste's "assist mode": difficulty doesn't change how the controls feel, it changes **forgiveness** and **content density**.
# Feel (jump/dash parameters) is identical across all three, so muscle memory carries over;
# what changes is hearts, air-dash count, trap frequency, pit width, crumble timers and moving-platform speed.
DIFFS = (
    dict(key="easy", label="EASY", rank=0, hearts=5, dashes=2,
         hazard=0.62, ramp=0.80, pit_hi=3, gap_hi=3,
         crumble=52, crumble_respawn=110, mover=0.75,
         wisp=0.35,
         blurb="5 HEARTS  2 DASHES  FEWER TRAPS"),
    dict(key="normal", label="NORMAL", rank=1, hearts=3, dashes=1,
         hazard=1.00, ramp=1.00, pit_hi=4, gap_hi=4,
         crumble=36, crumble_respawn=150, mover=1.00,
         wisp=0.75,
         blurb="3 HEARTS  1 DASH  THE INTENDED RUN"),
    dict(key="hard", label="HARD", rank=2, hearts=3, dashes=1,
         hazard=1.35, ramp=1.12, pit_hi=5, gap_hi=5,
         crumble=28, crumble_respawn=175, mover=1.28,
         wisp=1.05,
         blurb="3 HEARTS  FASTER TRAPS  NO MERCY"),
)
DEFAULT_DIFF = 1

# ---------------------------------------------------------------- ASCII art

ART = {
    # ---- player 10x14
    "player_idle": [
        "......u.........",
        ".....u..........",
        "...wwwwwwwww....",
        "..owwwwwwwwwwo..",
        "..ouuuuuuuuuuo..",
        ".ouuuuuuuuuuuuo.",
        ".ouuuuuuuuuuuuo.",
        ".ouusssssssssuo.",
        ".ouusvvssvvuuo..",
        ".ouusvwsswvuuo..",
        ".oupppssspppuuo.",
        "..ouusspsssuuo..",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvywwwwyvvvo.",
        ".ovvvwwuuwwvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvvvvvvvvvo.",
        "....owwowwo.....",
    ],
    "player_run1": [
        "......u.........",
        ".....u..........",
        "...wwwwwwwww....",
        "..owwwwwwwwwwo..",
        "..ouuuuuuuuuuo..",
        ".ouuuuuuuuuuuuo.",
        ".ouuuuuuuuuuuuo.",
        ".ouusssssssssuo.",
        ".ouusvvssvvuuo..",
        ".ouusvwsswvuuo..",
        ".oupppssspppuuo.",
        "..ouusspsssuuo..",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvywwwwyvvvo.",
        ".ovvvwwuuwwvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvvvvvvvvvo.",
        "...owwoowwo.....",
    ],
    "player_run2": [
        "......u.........",
        ".....u..........",
        "...wwwwwwwww....",
        "..owwwwwwwwwwo..",
        "..ouuuuuuuuuuo..",
        ".ouuuuuuuuuuuuo.",
        ".ouuuuuuuuuuuuo.",
        ".ouusssssssssuo.",
        ".ouusvvssvvuuo..",
        ".ouusvwsswvuuo..",
        ".oupppssspppuuo.",
        "..ouusspsssuuo..",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvywwwwyvvvo.",
        ".ovvvwwuuwwvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvvvvvvvvvo.",
        ".....owowo......",
    ],
    "player_jump": [
        "......u.........",
        ".....u..........",
        "...wwwwwwwww....",
        "..owwwwwwwwwwo..",
        "..ouuuuuuuuuuo..",
        ".ouuuuuuuuuuuuo.",
        ".ouuuuuuuuuuuuo.",
        ".ouusssssssssuo.",
        ".ouusvvssvvuuo..",
        ".ouusvwsswvuuo..",
        ".oupppssspppuuo.",
        "..ouusspsssuuo..",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvywwwwyvvvo.",
        ".ovvvwwuuwwvvvo.",
        ".ovvvvwwwwvvvvo.",
        ".ovvvvvvvvvvvvo.",
        "..oowwowwoo.....",
    ],
    # ---- slime 10x8
    "slime_a": [
        "...kkkk...",
        "..kPPPPk..",
        ".kPPPPPpk.",
        "kPPwPPwPpk",
        "kPPPPPPPPk",
        "kPpPPPPpPk",
        ".kPPPPPPk.",
        "..kkkkkk..",
    ],
    "slime_b": [
        "...kkkk...",
        "..kPPPPk..",
        ".kPPPPPpk.",
        "kPPwPPwPpk",
        "kPPPPPPPPk",
        "kkkkkkkkkk",
    ],
    # ---- ground spike 8x12: pokes 4px above the tile, white triangle on a red base
    "spike": [
        "........",
        "..w...w.",
        "..w...w.",
        "..w...w.",
        ".www.www",
        ".www.www",
        ".www.www",
        ".www.www",
        "wwwwwwww",
        "wwwwwwww",
        "rrrrrrrr",
        "rrrrrrrr",
    ],
    # ---- coin 8x8
    "coin_a": [
        "..kkkk..",
        ".kyyyyk.",
        "kyyYYyyk",
        "kyYYYYyk",
        "kyYYYYyk",
        "kyyYYyyk",
        ".kyyyyk.",
        "..kkkk..",
    ],
    "coin_b": [
        "...kk...",
        "..kyyk..",
        "..kYyk..",
        "..kYyk..",
        "..kYyk..",
        "..kyyk..",
        "...kk...",
    ],
    # ---- goal flag 8x18
    "flag": [
        "kk......",
        "kkyyyy..",
        "kkyyyyy.",
        "kkyyyyyy",
        "kkyyyyyk",
        "kkyyyy..",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
        "kk......",
    ],
    # ---- HUD heart 6x5
    "heart_empty": [
        ".k..k.",
        "kddddk",
        "kddddk",
        ".kddk.",
        "..kk..",
    ],
    "heart": [
        ".k..k.",
        "krkkrk",
        "krrrrk",
        ".krrk.",
        "..kk..",
    ],
    # ---- dash crystal 10x10 (Celeste-style mid-air refill)
    "crystal": [
        "....uu....",
        "...uuwu...",
        "..uuwuuu..",
        ".uuwuuuuu.",
        "uuwuuuuuuu",
        "uuuuuuuuuu",
        ".uuuuuuuu.",
        "..uuuuuu..",
        "...uuuu...",
        "....uu....",
    ],
    # ---- strawberry 8x8 (optional collectible)
    "berry": [
        "...gg...",
        "..gggg..",
        ".g.gg.g.",
        "..rrrr..",
        ".rrwrrr.",
        ".rrrrrr.",
        "..rrrr..",
        "...rr...",
    ],
    # ---- spring 8x6
    "spring": [
        "..yyyy..",
        ".yYYYYy.",
        "yyyyyyyy",
        ".dddddd.",
        "..dddd..",
        "..dddd..",
    ],
    # ---- hovering enemy (wisp) 10x8
    "wisp": [
        "...kkkk...",
        "..kPPPPk..",
        ".kPPwPPwk.",
        ".kPwPPwPk.",
        ".kPPPPPPk.",
        "..kPPPPk..",
        "...kPPk...",
        "....kk....",
    ],
    # ---- HUD dash pip 7x7
    "orb": [
        "...u...",
        "..uuu..",
        ".uuwuu.",
        "uuwwwuu",
        ".uuwuu.",
        "..uuu..",
        "...u...",
    ],
    "orb_empty": [
        "...d...",
        "..ddd..",
        ".ddddd.",
        "ddddddd",
        ".ddddd.",
        "..ddd..",
        "...d...",
    ],
}

SPR = {}


def bake_art():
    """Write the ASCII art into image bank 0, baking a left-right mirror (the _l suffix) as well."""
    img = pyxel.images[0]
    pos = {}
    x, y, row_h = 0, 0, 0

    def place(name, rows):
        nonlocal x, y, row_h
        w = max(len(r) for r in rows)
        h = len(rows)
        if x + w > 256:
            x, y = 0, y + row_h + 1
            row_h = 0
        for j, row in enumerate(rows):
            for i, ch in enumerate(row.ljust(w, ".")):
                if ch != ".":
                    img.pset(x + i, y + j, PAL[ch])
        pos[name] = (x, y, w, h)
        x += w + 1
        row_h = max(row_h, h)

    for name, rows in ART.items():
        place(name, rows)
        place(name + "_l", [r[::-1] for r in rows])
    SPR.update(pos)


def blit(name, x, y, flip=False):
    if flip:
        name += "_l"
    u, v, w, h = SPR[name]
    pyxel.blt(int(x), int(y), 0, u, v, w, h, 0)


# ---------------------------------------------------------------- sound effects


def make_sounds():
    # Note: this version of Pyxel only accepts the four octaves c0..b4; writing c5 raises immediately
    pyxel.sounds[0].set("c3e3", "p", "74", "n", 12)          # jump
    pyxel.sounds[1].set("e4g4", "s", "54", "f", 10)          # coin
    pyxel.sounds[2].set("g2c2", "p", "76", "f", 14)          # hurt
    pyxel.sounds[3].set("c3", "n", "54", "f", 8)             # stomp
    pyxel.sounds[4].set("c3e3g3c4", "p", "6545", "n", 9)     # clear
    pyxel.sounds[5].set("c4g3e3c3", "t", "6575", "f", 20)    # fail
    pyxel.sounds[6].set("g4c4", "n", "75", "f", 6)           # shoot
    pyxel.sounds[7].set("e4", "s", "43", "f", 5)             # hit
    pyxel.sounds[8].set("c4e4g4", "p", "543", "f", 6)        # dash
    pyxel.sounds[9].set("g3c4", "s", "65", "f", 9)           # spring
    pyxel.sounds[10].set("e4g4c4", "s", "543", "n", 8)       # strawberry
    pyxel.sounds[11].set("c4e4", "p", "65", "f", 4)          # crystal


# ---------------------------------------------------------------- level


class Level:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.tiles = [[EMPTY] * w for _ in range(h)]
        self.enemy_spawns = []
        self.wisp_spawns = []
        self.coin_spawns = []
        self.berry_spawns = []
        self.crystal_spawns = []
        self.spring_spawns = []
        self.player_spawn = (24, 160)
        self.flag_pos = (0, 0)
        self.pieces = []        # [(kind, x0, x1, ground_row)]
        self.crumble_state = {}  # (tx,ty) -> [phase, timer]; phase 0=shaking 1=gone
        self.mover_spawns = []   # initial parameters for the moving platforms
        self.top = []           # row of the topmost solid ground in each column (used for rendering)
        self.seed = 0
        self.name = ""
        self.mode = DIFFS[DEFAULT_DIFF]

    # --- construction primitives
    def rect(self, x, y, w, h, tile=SOLID):
        for ty in range(y, y + h):
            for tx in range(x, x + w):
                if 0 <= tx < self.w and 0 <= ty < self.h:
                    self.tiles[ty][tx] = tile

    def ground(self, x0, x1, top=GROUND_TOP, depth=None):
        d = GROUND_DEPTH if depth is None else depth
        self.rect(x0, top, x1 - x0, min(d, self.h - top), SOLID)

    def platform(self, x, y, w):
        self.rect(x, y, w, 1, PLATFORM)

    def spikes(self, x, w, y=None):
        self.rect(x, (y if y is not None else GROUND_TOP - 1), w, 1, SPIKE)

    def coin(self, tx, ty):
        self.coin_spawns.append((tx * TILE + 1, ty * TILE))

    def coin_row(self, tx, ty, n, step=1):
        for i in range(n):
            self.coin(tx + i * step, ty)

    def enemy(self, tx, ty=None):
        self.enemy_spawns.append((tx * TILE, (ty if ty is not None else GROUND_TOP - 1) * TILE))

    def wisp(self, tx, ty):
        self.wisp_spawns.append((tx * TILE, ty * TILE))

    def berry(self, tx, ty):
        self.berry_spawns.append((tx * TILE, ty * TILE))

    def crystal(self, tx, ty):
        self.crystal_spawns.append((tx * TILE, ty * TILE))

    def spring(self, tx, ty=None):
        self.spring_spawns.append(
            (tx * TILE, (ty if ty is not None else GROUND_TOP) * TILE))

    def flag(self, tx, top=None):
        self.flag_pos = (tx * TILE, (top if top is not None else GROUND_TOP) * TILE)

    # --- queries
    def tile(self, tx, ty):
        if 0 <= tx < self.w and 0 <= ty < self.h:
            return self.tiles[ty][tx]
        return EMPTY

    def is_solid(self, tx, ty):
        t = self.tile(tx, ty)
        return t == SOLID or t == BREAK or t == CRUMBLE

    def is_platform(self, tx, ty):
        return self.tile(tx, ty) == PLATFORM


# ---------------------------------------------------------------- generator


# Level "pieces". Each piece is responsible for three things:
#   1. Leave solid footing at both ends, so pieces can be joined back to back.
#   2. Keep every internal hop within reach — asserted on the spot with _hop(), so a bad piece blows up immediately.
#   3. Only touch the terrain of its own stretch and never depend on context, so pieces can be chained in any order.
#
# Assertions matter more than "looks passable": a broken piece makes --sweep raise instead of generating an unwinnable level.

MAX_HOP_X = 7          # tiles. A full jump measures 6.97 tiles, so 7 is the ceiling
MAX_HOP_Y = 4          # tiles. A single jump measures 4.2 tiles
MAX_HOP_Y2 = 6         # tiles. A double jump measures 6.9 tiles


def _hop(dx, dy=0, double=False):
    limit = MAX_HOP_Y2 if double else MAX_HOP_Y
    assert 1 <= dx <= MAX_HOP_X, f"horizontal hop too far: {dx} tiles"
    assert dy <= limit, f"hop too high: {dy} tiles ({'double' if double else 'single'} jump limit {limit})"


MAX_DASH_X = 12        # tiles. Measured, "full jump + air dash" clears 12 tiles (calibrated by calib3.py)


def _dash_hop(dx, dy=0):
    """Dash hop: farther than a normal jump, so any distance past jump range has to be covered by the dash.

    The assertions are just as tight here — if a generated gap can't be crossed even
    with a dash, --sweep blows up instead of handing the player an unwinnable level.
    """
    assert 1 <= dx <= MAX_DASH_X, f"dash hop too far: {dx} tiles (limit {MAX_DASH_X})"
    assert dy <= MAX_HOP_Y2, f"dash hop climbs too high: {dy} tiles"


def p_flat(L, x, g, rng, diff, hard):
    """A stretch of flat ground: room to breathe, with coins and (maybe) a slime thrown in."""
    w = rng.randint(5, 9)
    L.ground(x, x + w, top=g)
    if rng.random() < 0.6 and w >= 6:
        L.coin_row(x + 2, g - 2, min(3, w - 3))
    if rng.random() < 0.25 + 0.30 * diff and w >= 5:
        L.enemy(x + rng.randint(2, w - 2), g - 1)
    return x + w, g


def p_pit(L, x, g, rng, diff, hard):
    """A pit."""
    lead = 4
    L.ground(x, x + lead, top=g)
    w = rng.randint(3, L.mode["pit_hi"])
    _hop(w)
    for i in range(w):
        L.coin(x + lead + i, g - 4 - (1 if 0 < i < w - 1 else 0))
    if rng.random() < 0.30:                      # high-risk strawberry above the pit
        L.berry(x + lead + w // 2, g - 5)
    x += lead + w
    L.ground(x, x + 4, top=g)
    return x + 4, g


def p_steps(L, x, g, rng, diff, hard):
    """Step up or down 1-2 tiles; this is where the level's height changes."""
    L.ground(x, x + 3, top=g)
    step = rng.randint(1, 2)
    if rng.random() < 0.5 and g - step >= G_MIN:
        ng = g - step
    elif g + step <= G_MAX:
        ng = g + step
    else:
        ng = g - step
    w = rng.randint(5, 7)
    L.ground(x + 3, x + 3 + w, top=ng)
    L.coin_row(x + 4, ng - 2, 2)
    if rng.random() < 0.28 and w >= 5:
        L.enemy(x + rng.randint(4, 3 + w - 3), ng - 1)
    if rng.random() < 0.12:
        L.berry(x + 5, ng - 4)
    return x + 3 + w, ng


def p_spikes(L, x, g, rng, diff, hard):
    """Spikes on the ground; just jump over them."""
    lead = 5
    sw = rng.randint(2, 4 if hard else 3)
    w = lead + sw + 3
    L.ground(x, x + w, top=g)
    L.spikes(x + lead, sw, g - 1)
    _hop(sw)
    L.coin_row(x + lead, g - 3, sw)
    if rng.random() < 0.22 + 0.15 * diff:
        L.berry(x + lead + sw // 2, g - 5)
    return x + w, g


def p_ledge(L, x, g, rng, diff, hard):
    """Wide spike strip: you have to hop onto the ledge above, run across it, then drop back down."""
    lead = 4
    sw = rng.randint(5, 7)
    w = lead + sw + 4
    L.ground(x, x + w, top=g)
    L.spikes(x + lead, sw, g - 1)
    # The ledge must cover the whole spike strip plus a tile on each side — otherwise you drop off its right end onto the last spike
    pw = sw + 3
    px = x + lead - 1
    L.platform(px, g - 4, pw)
    _hop(1, 4)
    L.coin_row(px, g - 6, min(pw, 3))
    return x + w, g


def p_islands(L, x, g, rng, diff, hard):
    """Wide pit + floating islands: the main route leaves the ground for the first time; all footing is in the air."""
    lead = 4
    L.ground(x, x + lead, top=g)
    cur = x + lead              # the first "empty column"
    row = g - 1
    gap_hi = L.mode["gap_hi"]
    for _ in range(rng.randint(2, 3)):
        gap = rng.randint(3, gap_hi)
        _hop(gap, abs(row - (g - 1)))
        cur += gap
        L.platform(cur, row, 3)
        L.coin(cur + 1, row - 2)
        cur += 3                # the first column after the platform's right edge
        row = g - 1 - rng.choice((0, 1))
    gap = rng.randint(3, gap_hi)
    _hop(gap)
    if rng.random() < 0.45:
        L.berry(cur + gap // 2, g - 6)
    if rng.random() < 0.20 * L.mode["wisp"]:
        L.wisp(cur + gap // 2, g - 7)
    cur += gap
    L.ground(cur, cur + 4, top=g)
    return cur + 4, g


def p_bridge(L, x, g, rng, diff, hard):
    """Long pit + a row of narrow stumps: rhythm jumping. They all sit on the same row, so it's purely a test of distance."""
    lead = 4
    L.ground(x, x + lead, top=g)
    cur = x + lead
    row = g - 1
    gap_hi = L.mode["gap_hi"]
    for _ in range(rng.randint(2, 3)):
        gap = rng.randint(3, gap_hi)
        _hop(gap)
        cur += gap
        L.platform(cur, row, 3)
        L.coin(cur + 1, row - 2)
        cur += 3
    gap = rng.randint(3, gap_hi)
    _hop(gap)
    if rng.random() < 0.40:
        L.berry(cur + gap // 2, g - 5)
    cur += gap
    L.ground(cur, cur + 4, top=g)
    return cur + 4, g


def p_tower(L, x, g, rng, diff, hard):
    """Step tower: climb it tier by tier, grab the coins on top, then jump off the back."""
    lead = 3
    L.ground(x, x + lead, top=g)
    tiers = rng.randint(2, 3)
    bx = x + lead
    L.ground(bx, bx + tiers * 2, top=g)
    for i in range(tiers):
        h = 2 * (i + 1)
        L.rect(bx + i * 2, g - h, 2, h, SOLID)
        L.coin(bx + i * 2, g - h - 2)
        _hop(2, 2)
    top = bx + tiers * 2
    w = 5
    L.ground(top, top + w, top=g)
    L.coin(top + 1, g - 2)
    if rng.random() < 0.35 + 0.3 * diff:
        L.enemy(top + 3, g - 1)
    if rng.random() < 0.20:
        L.berry(top + 1, g - 5)
    return top + w, g


def p_tunnel(L, x, g, rng, diff, hard):
    """Tunnel: only 2 tiles of headroom, so you can't jump inside, and there are enemies in there too."""
    w = rng.randint(11, 15)
    L.ground(x, x + w, top=g)
    # 3 tiles (24px) of clearance: enough to hop up and stomp, but the ceiling is right there, so no showing off in here
    L.rect(x + 2, g - 4, w - 4, 1, SOLID)
    L.coin_row(x + 3, g - 2, 2)
    for _ in range(rng.randint(1, 2)):
        L.enemy(x + rng.randint(3, w - 3), g - 1)
    if rng.random() < 0.15:
        L.berry(x + w - 4, g - 2)
    return x + w, g


def p_gauntlet(L, x, g, rng, diff, hard):
    """A pack of enemies plus spikes at the exit."""
    w = rng.randint(12, 16)
    L.ground(x, x + w, top=g)
    for i in range(rng.randint(2, 3)):
        L.enemy(x + 3 + i * 4, g - 1)
    L.spikes(x + w - 5, 3, g - 1)
    _hop(3)
    L.coin_row(x + 3, g - 2, 3)
    if rng.random() < 0.35:
        L.berry(x + w - 4, g - 5)
    if rng.random() < 0.30 * L.mode["wisp"]:
        L.wisp(x + w - 4, g - 6)
    return x + w, g


def p_chimney(L, x, g, rng, diff, hard):
    """Wall-jump well: a 6-8 tile wall faces you, and wall jumps are the only way up.

    The ground rises once you're on top — this is the main source of the level's vertical relief.
    """
    lead = 3
    L.ground(x, x + lead, top=g)
    h = min(rng.randint(6, 9), g - G_MIN - 1)
    if h < 4:
        return p_flat(L, x, g, rng, diff, hard)
    wall_x = x + lead
    # Note: the ground is only a few rows thick, so the plateau alone can't provide an h-row wall;
    # the wall must be filled in explicitly from the plateau top down to the ground — otherwise the "well" is literally unclimbable.
    L.rect(wall_x, g - h, 1, h, SOLID)
    L.ground(wall_x, wall_x + 11, top=g - h)      # plateau on the right
    L.coin(wall_x + 1, g - h - 2)
    L.coin(wall_x + 2, g - h - 3)
    L.enemy(wall_x + 5, g - h - 1)
    if rng.random() < 0.30:
        L.berry(wall_x + 9, g - h - 4)
    _hop(6, 0, double=True)      # a single jump can't clear it; a wall jump is required
    return wall_x + 11, g - h


def p_canyon(L, x, g, rng, diff, hard):
    """Canyon: the ground drops 5-7 tiles overall, with a few ledges on the way down to break the fall."""
    lead = 3
    L.ground(x, x + lead, top=g)
    h = min(rng.randint(5, 7), G_MAX - g)
    if h < 3:
        return p_flat(L, x, g, rng, diff, hard)
    ng = g + h
    L.platform(x + lead + 1, g + 2, 3)
    L.coin(x + lead + 2, g)
    L.platform(x + lead + 5, g + 4 if h > 4 else g + 3, 3)
    L.coin(x + lead + 6, g + 2)
    L.ground(x + lead + 8, x + lead + 16, top=ng)
    if rng.random() < 0.30:
        L.berry(x + lead + 2, g - 2)
    return x + lead + 16, ng


def p_spire(L, x, g, rng, diff, hard):
    """Double-jump spire: 6-tile platform spacing, purely so you have to reach with the double jump (side route, for coins)."""
    lead = 4
    L.ground(x, x + lead + 14, top=g)
    cur = x + lead + 1
    row = g
    for _ in range(rng.randint(2, 3)):
        row -= 6
        L.platform(cur, row, 3)
        L.coin(cur + 1, row - 2)
        _hop(4, 6, double=True)
        cur += 4
    L.coin(cur, row - 3)
    if rng.random() < 0.65:
        L.berry(cur, row - 4)
        if rng.random() < 0.35 * L.mode["wisp"]:
            L.wisp(cur + 2, row - 5)
    return x + lead + 14, g


def c_plaza(L, x, g, rng, diff, hard):
    """Castle plaza: one wide platform, battlements at both ends, enemies and coins in between."""
    w = rng.randint(14, 20)
    L.ground(x, x + w, top=g, depth=6)
    for tx in (x + 1, x + 4, x + w - 4, x + w - 2):
        L.rect(tx, g - 1, 2, 1, SOLID)
    L.coin_row(x + 6, g - 2, 3)
    if rng.random() < 0.8:
        L.enemy(x + rng.randint(7, max(8, w - 6)), g - 1)
    if rng.random() < 0.30:
        L.berry(x + w - 3, g - 4)
    # Breakable bricks: each one drops a coin, which gives the gun a purpose beyond killing enemies
    if rng.random() < 0.55:
        bx = x + rng.randint(9, max(10, w - 5))
        for dx in range(2):
            for dy in range(2):
                if L.tile(bx + dx, g - 1 - dy) == EMPTY:
                    L.tiles[g - 1 - dy][bx + dx] = BREAK
    return x + w, g


def c_rampart(L, x, g, rng, diff, hard):
    """Rampart walk: a broken-up wall topped with crenellations that you have to jump along."""
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    cur = x + lead
    gap_hi = L.mode["gap_hi"]
    for _ in range(rng.randint(2, 3)):
        gap = rng.randint(3, gap_hi)
        _hop(gap)
        cur += gap
        seg = rng.randint(4, 6)
        L.ground(cur, cur + seg, top=g, depth=6)
        # The crenellations are **decoration** (DECOR), not solid tiles.
        # As 1-tile-high solids they raised both the take-off and the landing spot by a tile,
        # so you stepped down off them instead of jumping off the wall — and the 5-tile gap on hard turned into a coin flip.
        # Now they're just a low wall in the art, and collision is left entirely to the ground.
        for tx in range(cur + 1, cur + seg - 2, 3):
            if L.tile(tx, g - 1) == EMPTY:
                L.tiles[g - 1][tx] = DECOR
        L.coin(cur + 1, g - 3)
        if rng.random() < 0.25:
            L.berry(cur + seg // 2, g - 4)
        cur += seg
    return cur, g


def c_shaft(L, x, g, rng, diff, hard):
    """Shaft: walls on both sides with staggered footholds between them; you climb it with wall jumps."""
    lead = 3
    L.ground(x, x + lead, top=g)
    h = min(rng.randint(8, 12), g - G_MIN - 2)
    if h < 6:
        return c_plaza(L, x, g, rng, diff, hard)
    left = x + lead
    right = left + 5
    top = g - h
    L.rect(left, top, 1, h, SOLID)
    L.rect(right, top, 1, h, SOLID)
    side = 1
    for i in range(3):
        row = g - 3 - i * 3
        if row <= top + 1:
            break
        tx = left + 1 if side > 0 else right - 2
        L.rect(tx, row, 2, 1, SOLID)
        L.coin(tx, row - 2)
        side = -side
    L.ground(right, right + 10, top=top, depth=6)
    _hop(6, 0, double=True)
    if rng.random() < 0.35:
        L.berry(left + 2, g - 6)
    return right + 10, top


def c_stair(L, x, g, rng, diff, hard):
    """Outdoor staircase: 2 tiles per step, straight up."""
    L.ground(x, x + 3, top=g, depth=6)
    steps = rng.randint(3, 5)
    cur = x + 3
    row = g
    for _ in range(steps):
        row = max(G_MIN + 1, row - 2)
        L.rect(cur, row, 3, 2, SOLID)
        L.coin(cur + 1, row - 3)
        cur += 3
    L.ground(cur, cur + 8, top=row, depth=6)
    if rng.random() < 0.30:
        L.berry(cur + 4, row - 4)
    return cur + 8, row


def c_drop(L, x, g, rng, diff, hard):
    """Drop a storey: fall 6-10 rows down onto the platform below."""
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    ng = min(G_MAX, g + rng.randint(6, 10))
    cur = x + lead + 3
    L.ground(cur, cur + 12, top=ng, depth=6)
    L.coin(cur + 2, ng - 2)
    L.coin(cur + 4, ng - 3)
    if rng.random() < 0.5:
        L.enemy(cur + 8, ng - 1)
    if rng.random() < 0.25:
        L.berry(cur + 6, ng - 4)
    return cur + 12, ng


def p_crumble(L, x, g, rng, diff, hard):
    """Crumbling bridge: it breaks 0.47-0.87 s after you step on it (faster on higher difficulties), so you have to run straight across.

    It grows back after 1.8-2.9 s, so a fall doesn't end the attempt (though there's nothing below, so falling off is death).
    """
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    span = rng.randint(5, 8)
    end = x + lead + span
    L.ground(x + lead, end, top=g, depth=2)
    for tx in range(x + lead, end):
        L.tiles[g][tx] = CRUMBLE
        L.tiles[g + 1][tx] = CRUMBLE
        if tx % 2 == 0:
            L.coin(tx, g - 4)
    if rng.random() < 0.35:
        L.berry(x + lead + span // 2, g - 5)
    L.ground(end, end + 6, top=g, depth=6)
    return end + 6, g


def p_mover(L, x, g, rng, diff, hard):
    """Moving-platform crossing: an 8-tile rift, too wide to jump, so you have to wait for the platform.

    The platform is 4 tiles wide and swings 20px each way, so its left end meets the ledge exactly — which is what makes waiting worthwhile.
    """
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    span = 8
    end = x + lead + span
    L.ground(end, end + 8, top=g, depth=6)
    L.mover_spawns.append(dict(
        x=(x + lead) * TILE + 10, y=float(g * TILE),
        w=4 * TILE, axis="x", span=2.5 * TILE,
        speed=rng.uniform(0.016, 0.024) * L.mode["mover"],
        phase=rng.uniform(0.0, 6.28)))
    L.coin_row(x + lead + 2, g - 5, 3)
    L.enemy(end + 3, g - 1)
    if rng.random() < 0.40:
        L.berry(x + lead + span // 2, g - 6)
    return end + 8, g


def p_spring(L, x, g, rng, diff, hard):
    """Spring crossing: a spring launches you onto a one-way platform 5 tiles up, and you run across above the spike strip.

    The strip is only 6 tiles wide — a full jump (7 tiles) clears it anyway, so there's a fallback for anyone who doesn't trust the spring.
    """
    lead = 5
    L.ground(x, x + lead, top=g, depth=6)
    L.spring(x + lead - 2, g)
    sw = 6
    spike_x = x + lead + 1
    L.spikes(spike_x, sw, g - 1)
    L.platform(spike_x - 1, g - 5, sw + 3)     # one-way platform up high: you can pass up through it from below
    L.coin_row(spike_x + 1, g - 7, 3)
    L.berry(spike_x + 3, g - 8)
    L.ground(spike_x + sw, spike_x + sw + 4, top=g, depth=6)
    return spike_x + sw + 4, g


def p_dash(L, x, g, rng, diff, hard):
    """Dash gap: 8-10 tiles wide, with a dash crystal in the middle.

    Jump range is 7 tiles, so this gap **has** to be covered with a dash; the crystal lets you dash again afterwards,
    and dashing twice is the "high line" through this section.
    """
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    gap = 8 + L.mode["rank"]
    _dash_hop(gap)
    cx = x + lead + gap // 2
    # The crystal sits at **body height** at the jump apex (not at foot level):
    # at the apex of a full jump the feet are around 4.9 tiles and the head around 6.5, so g-5 passes right through the body.
    # At g-4 the player drifts 7px above it — the bot hit it 0 times in 30 measured runs.
    L.crystal(cx, g - 5)
    L.coin(cx - 2, g - 4)
    L.coin(cx + 2, g - 4)
    if rng.random() < 0.50:
        L.berry(cx, g - 7)          # going for the strawberry: dash up or double jump after the crystal
    L.ground(x + lead + gap, x + lead + gap + 5, top=g, depth=6)
    return x + lead + gap + 5, g


def p_steep(L, x, g, rng, diff, hard):
    """Steep tower: 3 rows up and 3 tiles across per step, twice as steep as c_stair, for a net climb of 12-18 rows.

    A single jump gains 4.9 rows, so a 3-row step leaves only 1.9 rows of margin — from here on you have to use full jumps,
    instead of jogging up the way you do on the staircase.
    """
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    cur = x + lead
    row = g
    for _ in range(rng.randint(4, 6)):
        nxt = max(G_MIN + 1, row - 3)
        if nxt >= row:
            break
        row = nxt
        L.rect(cur, row, 3, 2, SOLID)
        L.coin(cur + 1, row - 3)
        _hop(3, 3)
        cur += 3
    L.ground(cur, cur + 8, top=row, depth=6)
    if rng.random() < 0.45:
        L.berry(cur + 4, row - 4)
    return cur + 8, row


def p_well(L, x, g, rng, diff, hard):
    """Well: a 13-17 row shaft with tower walls on both sides and staggered ledges inside.

    It's 5-8 rows taller than c_shaft and the main vertical piece: a climb takes 1-2 s,
    and the camera pans up the whole shaft after the player.
    """
    lead = 3
    L.ground(x, x + lead, top=g, depth=6)
    h = min(rng.randint(13, 17), g - G_MIN - 2)
    if h < 10:
        return p_flat(L, x, g, rng, diff, hard)
    left = x + lead
    right = left + 4
    top = g - h
    L.rect(left, top, 1, h, SOLID)
    L.rect(right, top, 1, h, SOLID)
    for i in range(1, h // 5 + 1):
        row = g - i * 5
        if row <= top + 1:
            break
        tx = left + 1 if i % 2 else right - 2
        L.rect(tx, row, 2, 1, SOLID)
        L.coin(tx, row - 2)
    L.ground(right, right + 10, top=top, depth=6)
    if rng.random() < 0.40:
        L.berry(left + 1, top + 3)
    _hop(5, 0, double=True)
    return right + 10, top


def p_atrium(L, x, g, rng, diff, hard):
    """Atrium: two storeys. The spring takes you to the upper one, or you can walk the lower one to its end and wall-jump up.

    The upper storey is an independent high line while the lower one keeps going — two levels of space over the same span,
    so falling isn't death, just a switch of route (the 7-tile wall at the end can be wall-jumped).
    """
    w = rng.randint(14, 18)
    L.ground(x, x + w, top=g, depth=6)
    L.spring(x + 2, g)
    L.platform(x + 5, g - 7, w - 5)            # upper-storey one-way walkway
    L.coin_row(x + 7, g - 9, 3)
    L.berry(x + w - 4, g - 9)
    if rng.random() < 0.7:
        L.enemy(x + w - 6, g - 1)
    # Upper exit: solid ground; its left face is a 7-tile wall, so even from the lower storey you can wall-jump up
    L.ground(x + w, x + w + 6, top=g - 7, depth=8)
    return x + w + 6, g - 7


def p_chasm(L, x, g, rng, diff, hard):
    """Chasm: a 10-14 row cliff with narrow ledges scattered down its face; drop to the bottom and carry on.

    Unlike canyon it's one sheer face: no gentle slope, just a drop two or three screens tall where the player has to
    choose between braking on the ledges and jumping straight down.
    """
    lead = 4
    L.ground(x, x + lead, top=g, depth=6)
    drop = min(rng.randint(10, 14), G_MAX - g)
    if drop < 8:
        return p_flat(L, x, g, rng, diff, hard)
    ng = g + drop
    w = rng.randint(16, 20)
    L.ground(x + lead, x + lead + w, top=ng, depth=6)
    cur = x + lead + 1
    for i in range(3):
        row = g + 3 + i * 3
        if row >= ng - 2:
            break
        # Keep the last 6 columns flat, so dropping off the lowest ledge lands on solid ground
        # instead of just clearing the chasm floor and falling into the next piece's first gap (on hard seed 1043: 44 falls in a row).
        if cur + 3 > x + lead + w - 6:
            break
        L.platform(cur, row, 3)
        L.coin(cur + 1, row - 2)
        cur += 4
    L.enemy(x + lead + w - 4, ng - 1)
    if rng.random() < 0.55:
        L.berry(x + lead + w // 2, ng - 4)
    return x + lead + w, ng


PIECES = {
    "flat": p_flat, "pit": p_pit, "steps": p_steps, "spikes": p_spikes,
    "ledge": p_ledge, "islands": p_islands, "bridge": p_bridge,
    "tower": p_tower, "tunnel": p_tunnel, "gauntlet": p_gauntlet,
    "chimney": p_chimney, "canyon": p_canyon, "spire": p_spire,
    "plaza": c_plaza, "rampart": c_rampart, "shaft": c_shaft,
    "stair": c_stair, "drop": c_drop,
    "crumble": p_crumble,
    "mover": p_mover,
    "spring": p_spring,
    "dash": p_dash,
    "steep": p_steep,
    "well": p_well,
    "atrium": p_atrium,
    "chasm": p_chasm,
}

GAP_LIKE = ("pit", "islands", "bridge", "dash")

# hazard / trap pieces: all scaled by the difficulty factor
RISK = ("pit", "spikes", "ledge", "islands", "bridge", "tunnel",
        "gauntlet", "canyon", "crumble", "mover", "dash", "spring",
        "steep", "well", "atrium", "chasm")


def _weights(mode, diff):
    w = {
        "flat": max(0.5, 2.4 - 2.0 * diff),
        "pit": 1.1,
        "steps": 1.0,
        "spikes": 0.4 + 1.3 * diff,
        "ledge": 0.2 + 1.2 * diff,
        "islands": 0.15 + 1.0 * diff,
        "bridge": 0.1 + 0.9 * diff,
        "tower": 0.5 + 0.5 * diff,
        "tunnel": 0.1 + 0.9 * diff,
        "gauntlet": 0.1 + 0.9 * diff,
        "chimney": 0.5 + 0.6 * diff,
        "plaza": 1.0,
        "rampart": 0.6 + 0.6 * diff,
        "shaft": 0.7 + 0.6 * diff,
        "stair": 0.8,
        "drop": 0.5,
        "crumble": 0.5 + 0.5 * diff,
        "mover": 0.4 + 0.5 * diff,
        "canyon": 0.4 + 0.6 * diff,
        "spire": 0.4 + 0.6 * diff,
        "spring": 0.55,
        "dash": 0.65,
        # Vertical pieces: give them real weight, or a 34-segment run only hits a tower now and then
        "steep": 0.55 + 0.25 * diff,
        "well": 0.55 + 0.25 * diff,
        "atrium": 0.45,
        "chasm": 0.40 + 0.25 * diff,
    }
    haz = mode["hazard"]
    for k in RISK:
        w[k] *= haz
    return w


def _pick(mode, rng, diff, prev):
    w = _weights(mode, diff)
    # Two "big gap" pieces can't sit next to each other; there must be solid ground between them
    if prev in GAP_LIKE:
        for k in GAP_LIKE:
            w[k] = 0.0
    if prev == "flat":
        w["flat"] *= 0.3
    # Spring and dash gaps teach new moves; they stay in on easy too, just less often
    if prev in ("spring", "dash"):
        w[prev] *= 0.4
    kinds = list(w)
    return rng.choices(kinds, weights=[w[k] for k in kinds])[0]



CLIMB = ("stair", "shaft", "chimney", "steep", "well", "atrium")
DROP = ("drop", "canyon", "chasm")


def _pick_castle(mode, rng, diff, prev, delta):
    """Pick a piece based on how far the current height is from the target.

    delta = current ground row - target row. Positive = below target (need to climb),
    negative = above target (can head down). The target itself is a diagonal with two "waves",
    so a run has two deliberate climbs and two deliberate descents rather than one monotonous staircase.
    """
    w = _weights(mode, diff)
    if delta >= 4.0:                    # behind: only pick pieces that climb
        for k in list(w):
            if k not in CLIMB:
                w[k] = 0.0
    elif delta <= -4.0:                 # ahead: stop climbing, head down deliberately
        for k in CLIMB:
            w[k] = 0.0
        for k in DROP:
            w[k] *= 2.5
        w["flat"] = max(w["flat"], 1.0)
    if prev in GAP_LIKE:
        for k in GAP_LIKE:
            w[k] = 0.0
    if prev == "flat":
        w["flat"] *= 0.3
    if prev in ("spring", "dash"):
        w[prev] *= 0.4
    if sum(w.values()) <= 0:
        return "plaza"
    kinds = list(w)
    return rng.choices(kinds, weights=[w[k] for k in kinds])[0]


# These two pieces teach new moves: each is guaranteed to appear once per run (around segments 12 / 22)
FORCED = {11: "dash", 21: "spring"}


def generate_level(seed: int, difficulty: int = DEFAULT_DIFF,
                   hard: bool = False) -> Level:
    if hard:                            # legacy interface: --hard is the third difficulty
        difficulty = 2
    difficulty = max(0, min(len(DIFFS) - 1, int(difficulty)))
    mode = DIFFS[difficulty]
    rng = random.Random(seed)
    L = Level(2400, LEVEL_H)
    L.mode = mode
    L.seed = seed
    L.name = f"SEED {seed} {mode['label']}"

    g = START_ROW
    x = 0

    # Foundation: a wide enough starting platform
    L.ground(x, x + 18, top=g, depth=12)
    L.player_spawn = (3 * TILE, (g - 3) * TILE)
    L.coin_row(7, g - 2, 3)
    L.enemy(13, g - 1)
    x += 18

    prev = None
    seen = set()
    for seg in range(SEGMENTS):
        diff = min(1.0, seg / 26.0) * mode["ramp"]
        # Height target = a diagonal from START_ROW to TARGET_TOP plus two sine waves.
        # That gives each run a "climb -> traverse/dive -> climb again" rhythm and uses the vertical space more fully;
        # the sine returns to 0 at the end, so the finish still lands near the top of the tower.
        prog = seg / max(1, SEGMENTS - 1)
        base = START_ROW + (TARGET_TOP - START_ROW) * prog
        wave = -WAVE_ROWS * math.sin(prog * math.pi * 2 * WAVE_TURNS)
        target = max(G_MIN, min(G_MAX, base + wave))
        delta = g - target
        kind = _pick_castle(mode, rng, diff, prev, delta)
        forced = FORCED.get(seg)
        if forced and forced not in seen and forced != prev:
            kind = forced
        x0, g0 = x, g
        x, g = PIECES[kind](L, x, g, rng, diff, difficulty == 2)
        g = max(G_MIN, min(G_MAX, g))
        L.pieces.append((kind, x0, x, g0))
        seen.add(kind)
        prev = kind

    # Tower top: the flag
    L.ground(x, x + 16, top=g, depth=10)
    L.coin_row(x + 3, g - 2, 3)
    L.flag(x + 10, g)
    L.w = x + 16

    compute_top(L)
    return L


def compute_top(L):
    """Per column, the "surface" = the top of the solid ground reachable from the bottom without a break.

    Taking the topmost solid tile doesn't work: a tunnel ceiling floats above the ground,
    and it would be treated as the surface, get turf painted on it and look like a floating slab.
    """
    L.top = []
    for tx in range(L.w):
        ty = L.h - 1
        while ty >= 0 and L.tiles[ty][tx] != SOLID:
            ty -= 1
        if ty < 0:
            L.top.append(L.h)
            continue
        while ty - 1 >= 0 and L.tiles[ty - 1][tx] == SOLID:
            ty -= 1
        L.top.append(ty)


# ---------------------------------------------------------------- entities


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "col", "size")

    def __init__(self, x, y, vx, vy, life, col, size=1):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life, self.col, self.size = life, col, size

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.18
        self.vx *= 0.96
        self.life -= 1

    def draw(self):
        pyxel.rect(int(self.x), int(self.y), self.size, self.size, self.col)


class Bullet:
    """Player bullet. It only hits solid tiles and breakable bricks, and passes through one-way platforms."""

    W, H = 5, 3

    def __init__(self, x, y, vx):
        self.x, self.y, self.vx = float(x), float(y), vx
        self.travel = 0.0
        self.alive = True

    def hit(self, x, y, w, h):
        return (self.x < x + w and self.x + self.W > x and
                self.y < y + h and self.y + self.H > y)

    def update(self, lvl, game):
        self.x += self.vx
        self.travel += abs(self.vx)
        if self.travel > BULLET_RANGE:
            self.alive = False
            return
        tx = int((self.x + (self.W if self.vx > 0 else 0)) // TILE)
        ty = int(self.y // TILE)
        t = lvl.tile(tx, ty)
        if t == BREAK:
            lvl.tiles[ty][tx] = EMPTY
            game.coins += 1
            game.total_coins += 1
            game.sfx(7)
            game.burst(tx * TILE + 4, ty * TILE + 4, 8, LGRAY, 1.6)
            self.alive = False
        elif t == SOLID:
            game.burst(self.x, self.y, 3, WHITE, 0.8)
            self.alive = False

    def draw(self):
        pyxel.rect(self.x, self.y, self.W, self.H, WHITE)
        pyxel.rect(self.x, self.y + 1, self.W, 1, YELLOW)


class Mover:
    """A floating platform that shuttles back and forth. The player can stand on it and be carried along."""

    H = 6                      # platform thickness (collision)

    def __init__(self, x, y, w, axis="x", span=20.0, speed=0.018, phase=0.0):
        self.x0, self.y0 = float(x), float(y)
        self.w = w
        self.axis = axis
        self.span = span
        self.speed = speed
        self.t = phase
        self.x, self.y = self.x0, self.y0
        self.dx = self.dy = 0.0

    def update(self):
        self.t += self.speed
        off = math.sin(self.t) * self.span
        nx = self.x0 + (off if self.axis == "x" else 0.0)
        ny = self.y0 + (off if self.axis == "y" else 0.0)
        self.dx, self.dy = nx - self.x, ny - self.y
        self.x, self.y = nx, ny

    def draw(self):
        w, h = int(self.w), self.H
        x, y = int(self.x), int(self.y)
        pyxel.rect(x, y, w, h, DGRAY)
        pyxel.rect(x, y, w, 2, LGRAY)
        for k in range(0, w, 8):
            pyxel.line(x + k, y + 2, x + k, y + h - 1, NAVY)
        # the rings at both ends
        pyxel.rect(x + 1, y + h - 2, 2, 2, NAVY)
        pyxel.rect(x + w - 3, y + h - 2, 2, 2, NAVY)


class Player:
    W, H = 8, 13

    def __init__(self, x, y, max_dashes=1):
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.facing = 1
        self.coyote = 0
        self.jump_buf = 0
        self.jumping = False
        self.anim = 0.0
        self.invuln = 0
        self.grounded_frames = 0
        self.checkpoint = (float(x), float(y))
        self.just_landed = False
        # new state
        self.jumps_left = MAX_JUMPS
        self.wall_dir = 0        # -1 wall on the left, 1 wall on the right, 0 no wall
        self.wall_lock = 0
        self.sliding = False
        self.cool_shoot = 0
        self.rider = None
        self.double_used = False
        self.prev_jump = False
        # dash
        self.max_dashes = max_dashes
        self.dash_left = max_dashes
        self.dash_frames = 0
        self.dash_cool = 0
        self.dash_vx = self.dash_vy = 0.0
        self.dash_buf = 0
        self.boost = 0           # frames of momentum kept after a dash
        self.prev_dash = False
        self.trail = []          # dash afterimages [(x, y, life)]

    # ---------- geometry
    def overlap(self, x, y, w, h):
        return (self.x < x + w and self.x + self.W > x and
                self.y < y + h and self.y + self.H > y)

    def _tiles_in(self, x, y, w, h):
        return (int(x) // TILE, int(x + w - 0.01) // TILE,
                int(y) // TILE, int(y + h - 0.01) // TILE)

    def _safe_spot(self, lvl):
        """The checkpoint needs footing to its right, or you get "fall to death -> respawn at a ledge edge -> fall again"."""
        ty = int((self.y + self.H + 1) // TILE)
        for d in (1, 2):
            tx = int((self.x + self.W + d * TILE) // TILE)
            if not (lvl.is_solid(tx, ty) or lvl.is_platform(tx, ty)):
                return False
        return True

    def _detect_wall(self, lvl):
        """Return the wall direction when airborne with a solid wall against the side of the body."""
        if self.on_ground:
            return 0
        ty0 = int((self.y + 2) // TILE)
        ty1 = int((self.y + self.H - 2) // TILE)
        left = int((self.x - 1) // TILE)
        right = int((self.x + self.W) // TILE)
        for ty in range(ty0, ty1 + 1):
            if lvl.is_solid(left, ty):
                return -1
            if lvl.is_solid(right, ty):
                return 1
        return 0

    def _probe_ground(self, lvl):
        """Probe 1 pixel further below the feet.

        Without this, a grounded character's height oscillates between y and y+0.34,
        on_ground drops every other frame — walking stutters and the bot can't jump at ledges.
        """
        ty = int((self.y + self.H + 1) // TILE)
        x0 = int(self.x + 1) // TILE
        x1 = int(self.x + self.W - 1) // TILE
        for tx in range(x0, x1 + 1):
            if lvl.is_solid(tx, ty) or lvl.is_platform(tx, ty):
                if self.y + self.H <= ty * TILE + 1.5:
                    self.y = ty * TILE - self.H
                    self.vy = 0.0
                    self.on_ground = True
                return

    # ---------- collision
    def _move_x(self, lvl, dx):
        """Return whether a solid tile was hit — this is what ends a dash into a wall."""
        if dx == 0:
            return False
        self.x += dx
        x0, x1, y0, y1 = self._tiles_in(self.x, self.y, self.W, self.H)
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                if lvl.is_solid(tx, ty):
                    if dx > 0:
                        self.x = tx * TILE - self.W
                    else:
                        self.x = (tx + 1) * TILE
                    self.vx = 0.0
                    return True
        return False

    def _move_y(self, lvl, dy):
        if dy == 0:
            return False
        prev_bottom = self.y + self.H
        self.y += dy
        self.on_ground = False
        x0, x1, y0, y1 = self._tiles_in(self.x, self.y, self.W, self.H)
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                t = lvl.tile(tx, ty)
                if t == SOLID:
                    if dy > 0:
                        self.y = ty * TILE - self.H
                        self.on_ground = True
                    else:
                        self.y = (ty + 1) * TILE
                    self.vy = 0.0
                    return True
                if t == PLATFORM and dy > 0 and prev_bottom <= ty * TILE + 1:
                    self.y = ty * TILE - self.H
                    self.vy = 0.0
                    self.on_ground = True
                    return True
        return False

    # ---------- jump / dash
    def _jump(self, game, vy, cost, fx=0.0, fy=0.0, kind="jump"):
        self.vy = vy
        self.jump_buf = 0
        self.coyote = 0
        self.jumping = True
        self.jumps_left = max(0, self.jumps_left - cost)
        game.stats["double" if kind == "double" else "jump"] += 1
        game.sfx(0)
        game.burst(fx, fy, 6 if kind != "jump" else 4, LGRAY, 1.0)

    def _wall_jump(self, game):
        self.vy = WALL_JUMP_VY
        self.vx = -self.wall_dir * WALL_JUMP_VX
        self.facing = -self.wall_dir
        self.wall_lock = WALL_LOCK
        self.jumping = True
        self.jump_buf = 0
        # Leave at least one air jump after a wall jump, so a wall jump can't burn every jump and leave you stuck
        self.jumps_left = max(self.jumps_left, 1)
        self.double_used = False
        wx = self.x if self.wall_dir > 0 else self.x + self.W
        game.stats["wall"] += 1
        game.sfx(0)
        game.burst(wx, self.y + self.H / 2, 6, LGRAY, 1.2)

    def _start_dash(self, game, keys):
        """8-way dash. With no direction held, dash the way you're facing — just like Celeste."""
        dx = (1 if keys.get("right") else 0) - (1 if keys.get("left") else 0)
        dy = (1 if keys.get("down") else 0) - (1 if keys.get("up") else 0)
        if dx == 0 and dy == 0:
            dx = self.facing
        n = math.hypot(dx, dy) or 1.0
        self.dash_vx = dx / n * DASH_SPEED
        self.dash_vy = dy / n * DASH_SPEED * (DASH_UP_SCALE if dy < 0 else 1.0)
        if dx:
            self.facing = 1 if dx > 0 else -1
        self.dash_frames = DASH_FRAMES
        self.dash_left -= 1
        self.jumping = False
        self.vy = self.dash_vy
        self.vx = self.dash_vx
        self.trail = []
        game.stats["dash"] += 1
        game.sfx(8)
        game.burst(self.x + self.W / 2, self.y + self.H / 2, 8, LAVENDER, 1.6)

    def _end_dash(self, game):
        if self.dash_frames == 0:
            return
        self.dash_frames = 0
        self.dash_cool = DASH_COOLDOWN
        self.boost = DASH_BOOST_FRAMES
        self.vy *= 0.4                  # damp vertical speed at the end, so an up-dash doesn't turn into a rocket
        self.vx = self.dash_vx * DASH_BOOST_KEEP
        self.dash_buf = 0

    # ---------- per frame
    def update(self, lvl, keys, game):
        self.rider = None
        if self.invuln > 0:
            self.invuln -= 1
        if self.wall_lock > 0:
            self.wall_lock -= 1
        if self.dash_cool > 0:
            self.dash_cool -= 1

        was_on_ground = self.on_ground

        # --- Dash input. Buffered like the jump: press it just before landing and it fires as soon as the dash refills
        dash_held = keys.get("dash", False)
        pressed_dash = dash_held and not self.prev_dash
        self.prev_dash = dash_held
        if pressed_dash:
            self.dash_buf = DASH_BUFFER
        elif self.dash_buf > 0:
            self.dash_buf -= 1
        if (self.dash_buf > 0 and self.dash_frames == 0
                and self.dash_left > 0 and self.dash_cool == 0):
            self._start_dash(game, keys)
            self.dash_buf = 0

        dashing = self.dash_frames > 0

        # --- horizontal
        move = (1 if keys["right"] else 0) - (1 if keys["left"] else 0)
        if move:
            self.facing = move
        if dashing:
            self.vx = self.dash_vx
        else:
            # brief loss of control after a wall jump, or you're pushed straight back to the wall and the "jump" goes nowhere
            accel = RUN_ACC if self.wall_lock == 0 else RUN_ACC * 0.22
            if self.boost > 0:
                # post-dash momentum window: the cap eases from 2.6 back to 1.8 instead of being cut in one frame
                frac = self.boost / DASH_BOOST_FRAMES
                cap = RUN_MAX + max(0.0, abs(self.dash_vx) * DASH_BOOST_KEEP
                                    - RUN_MAX) * frac
                self.boost -= 1
            else:
                cap = RUN_MAX
            if move:
                self.vx = max(-cap, min(cap, self.vx + move * accel))
            elif abs(self.vx) <= FRICTION:
                self.vx = 0.0
            else:
                self.vx -= FRICTION if self.vx > 0 else -FRICTION

        # Move horizontally first, then look for a wall, so a wall jump can use a wall touched on this very frame
        hit_x = self._move_x(lvl, self.vx)
        self.wall_dir = self._detect_wall(lvl)
        if dashing and hit_x:               # hit a wall: end the dash and turn it into a wall-jump opportunity
            self._end_dash(game)

        # --- Jump input buffering.
        # Key: air jumps (double / wall) must be a fresh press (rising edge),
        # otherwise holding the button eats the double jump on the frame after the first one.
        pressed = keys["jump"] and not self.prev_jump
        self.prev_jump = keys["jump"]

        if keys["jump"]:
            self.jump_buf = JUMP_BUFFER
        elif self.jump_buf > 0:
            self.jump_buf -= 1

        can_ground_jump = self.coyote > 0
        can_wall_jump = pressed and self.wall_dir != 0
        can_air_jump = pressed and self.jumps_left > 0
        # During a dash, only a fresh jump press counts: hold ↑ (= jump) and then press dash,
        # and the jump buffer interrupts the up-dash on the next frame — the up-dash may as well not have happened.
        if (self.jump_buf > 0 and (can_ground_jump or can_wall_jump or can_air_jump)
                and not (self.dash_frames > 0 and not pressed)):
            if self.dash_frames > 0:        # jumping out of a dash = a "super jump" that keeps your horizontal speed
                self._end_dash(game)
                self.vx = self.dash_vx
            if can_ground_jump:
                self._jump(game, JUMP_VEL, 1,
                           self.x + self.W / 2, self.y + self.H)
            elif can_wall_jump:
                self._wall_jump(game)
            else:
                self._jump(game, DOUBLE_JUMP_VEL, 1,
                           self.x + self.W / 2, self.y + self.H,
                           kind="double")
                self.double_used = True
                game.burst(self.x + self.W / 2, self.y + self.H, 8, PINK, 1.6)

        # --- Shooting (infinite ammo, constrained by cooldown and a bullet cap)
        if self.cool_shoot > 0:
            self.cool_shoot -= 1
        if keys.get("shoot") and self.cool_shoot == 0 and \
                len(game.bullets) < MAX_BULLETS:
            self.cool_shoot = SHOOT_COOLDOWN
            mx = self.x + (self.W + 2 if self.facing > 0 else -2)
            my = self.y + 5
            game.bullets.append(Bullet(mx, my, self.facing * BULLET_SPEED))
            game.stats["shot"] += 1
            game.sfx(6)
            game.burst(mx, my + 1, 3, YELLOW, 0.7)

        # Variable jump height: release the button on the way up and the jump gets cut
        if self.jumping and not keys["jump"] and self.vy < 0:
            self.vy *= JUMP_CUT
            self.jumping = False
        if self.vy >= 0:
            self.jumping = False

        # --- vertical
        self.trail = [(gx, gy, life - 1) for gx, gy, life in self.trail if life > 1]
        if self.dash_frames > 0:
            # no gravity during a dash, and the direction is locked
            self.sliding = False
            self.dash_frames -= 1
            self.vy = self.dash_vy
            hit_y = self._move_y(lvl, self.vy)
            self.trail.append((self.x, self.y, 7))
            if hit_y or self.dash_frames == 0:
                self._end_dash(game)
        else:
            # gravity is discounted while sliding down a wall
            self.sliding = bool(self.wall_dir and not self.on_ground
                                and self.vy > 0 and not self.jumping)
            self.vy = min(MAX_FALL, self.vy + GRAVITY)
            if self.sliding and self.vy > WALL_SLIDE:
                self.vy = WALL_SLIDE
            self._move_y(lvl, self.vy)

        self.just_landed = self.on_ground and not was_on_ground

        if not self.on_ground and self.vy >= 0:
            self._probe_ground(lvl)

        # Land on a moving platform (checked after tiles, so it can override a tile result)
        if self.vy >= 0:
            for m in game.movers:
                if (self.x + self.W > m.x + 1 and self.x < m.x + m.w - 1
                        and self.y + self.H >= m.y
                        and self.y + self.H - self.vy <= m.y + 3):
                    self.y = m.y - self.H
                    self.vy = 0.0
                    self.on_ground = True
                    self.rider = m
                    break

        if self.on_ground:
            self.coyote = COYOTE
            self.jumps_left = MAX_JUMPS
            self.double_used = False
            self.grounded_frames += 1
            # dashes only refill on landing — touching ground mid-dash doesn't count, or ground dashes could chain forever
            if self.dash_frames == 0:
                self.dash_left = self.max_dashes
            if self.grounded_frames > 8 and self._safe_spot(lvl):
                self.checkpoint = (self.x, self.y)
        else:
            self.coyote = max(0, self.coyote - 1)
            self.grounded_frames = 0

        if self.on_ground:
            # crumbling slab underfoot -> start the timer
            fy = int((self.y + self.H + 1) // TILE)
            for tx in (int(self.x + 1) // TILE, int(self.x + self.W - 1) // TILE):
                if lvl.tile(tx, fy) == CRUMBLE:
                    game.trigger_crumble(lvl, tx, fy)

        if self.on_ground and abs(self.vx) > 0.3:
            self.anim += abs(self.vx) * 0.16
        else:
            self.anim = 0.0

        # --- spikes
        x0, x1, y0, y1 = self._tiles_in(self.x, self.y, self.W, self.H)
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                if lvl.tile(tx, ty) == SPIKE:
                    game.hurt(self)
                    return

        if self.y > lvl.h * TILE + 16:
            game.hurt(self, fatal=True)

    def draw(self):
        # dash afterimages: the older they are, the darker they get
        for gx, gy, life in self.trail:
            col = LAVENDER if life > 3 else PURPLE
            pyxel.rect(int(gx) + 1, int(gy) + 2, self.W - 2, self.H - 4, col)
        if self.invuln > 0 and (self.invuln // 4) % 2 == 0:
            return
        if self.sliding:
            name = "player_jump"
        elif not self.on_ground:
            name = "player_jump"
        elif abs(self.vx) > 0.3:
            name = "player_run1" if int(self.anim) % 2 == 0 else "player_run2"
        else:
            name = "player_idle"
        # The sprite is bigger than the hitbox: feet on the hitbox's bottom edge, horizontally centred
        blit(name, self.x - 4, self.y + self.H - SPR[name][3],
             flip=self.facing < 0)


class WalkSlime:
    W, H = 9, 7

    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.vy = 0.0
        self.dir = 1
        self.alive = True
        self.timer = 0

    @property
    def rect(self):
        return (self.x, self.y, self.W, self.H)

    def update(self, lvl):
        if not self.alive:
            return
        self.timer += 1

        # gravity: let the enemy settle onto the tiles, so placement doesn't need pixel-perfect alignment
        self.vy = min(MAX_FALL, self.vy + GRAVITY)
        self.y += self.vy
        x0 = int(self.x) // TILE
        x1 = int(self.x + self.W - 1) // TILE
        y1 = int(self.y + self.H - 1) // TILE
        for tx in range(x0, x1 + 1):
            if lvl.is_solid(tx, y1) or lvl.is_platform(tx, y1):
                self.y = y1 * TILE - self.H
                self.vy = 0.0
                break

        dx = 0.32 * self.dir
        ahead = self.x + (self.W if dx > 0 else -1)
        if lvl.is_solid(int(ahead) // TILE, int(self.y + self.H / 2) // TILE):
            self.dir *= -1
        else:
            self.x += dx

        # turn around at the edge
        foot_tx = int(self.x + (self.W if self.dir > 0 else 0)) // TILE
        foot_ty = int(self.y + self.H + 1) // TILE
        if not (lvl.is_solid(foot_tx, foot_ty) or lvl.is_platform(foot_tx, foot_ty)):
            self.dir *= -1

    def draw(self):
        if not self.alive:
            return
        name = "slime_a" if (self.timer // 14) % 2 == 0 else "slime_b"
        u, v, w, h = SPR[name]
        blit(name, self.x - 1, self.y + (self.H - h) - 1, flip=self.dir < 0)


class Coin:
    W, H = 6, 8

    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.taken = False
        self.phase = int((x * 7 + y * 3) % 40)

    def update(self):
        self.phase = (self.phase + 1) % 40

    @property
    def bob(self):
        return int(1.5 * pyxel.sin(self.phase * 9))

    @property
    def rect(self):
        return (self.x, self.y + self.bob, self.W, self.H)

    def draw(self):
        name = "coin_a" if (self.phase // 10) % 2 == 0 else "coin_b"
        blit(name, self.x - 1, self.y + self.bob)


class Crystal:
    """Dash crystal: grab it for an instant dash refill; it grows back after a while."""

    W, H = 10, 10

    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.active = True
        self.timer = 0
        self.phase = int((x * 5 + y * 3) % 24)

    @property
    def rect(self):
        return (self.x, self.y, self.W, self.H)

    def update(self):
        self.phase = (self.phase + 1) % 24
        if not self.active:
            self.timer -= 1
            if self.timer <= 0:
                self.active = True

    def draw(self):
        if self.active:
            # bob up and down 1px, plus a glint in the corner
            oy = 1 if self.phase < 12 else 0
            blit("crystal", self.x, self.y + oy)
            if self.phase % 6 == 0:
                pyxel.pset(int(self.x) + 1, int(self.y) + oy + 1, WHITE)
        else:
            pyxel.rectb(int(self.x) + 1, int(self.y) + 1, 8, 8, NAVY)
            if self.timer % 12 < 6:
                pyxel.pset(int(self.x) + 5, int(self.y) + 5, BLUE)


class Berry:
    """Strawberry: a Celeste-style optional collectible. You can finish without it, but it's counted at the end of a run."""

    W, H = 8, 8

    def __init__(self, x, y):
        self.x, self.y = float(x), float(y)
        self.taken = False
        self.phase = int((x * 3 + y * 7) % 50)

    def update(self):
        self.phase = (self.phase + 1) % 50

    @property
    def bob(self):
        return int(1.5 * pyxel.sin(self.phase * 7.2))

    @property
    def rect(self):
        return (self.x, self.y + self.bob, self.W, self.H)

    def draw(self):
        if self.taken:
            return
        blit("berry", self.x, self.y + self.bob)


class Spring:
    """Spring: step on it and it launches you 8 tiles up, refilling your dash at the same time."""

    W, H = 8, 6

    def __init__(self, x, y):
        # y is the ground's top edge; the spring sits flush on it
        self.x, self.y = float(x), float(y - self.H)
        self.anim = 0

    @property
    def rect(self):
        return (self.x, self.y, self.W, self.H)

    def update(self):
        if self.anim > 0:
            self.anim -= 1

    def draw(self):
        if self.anim > 0:
            # the instant it's compressed: squashed flat
            pyxel.rect(int(self.x), int(self.y) + 4, 8, 2, YELLOW)
            pyxel.rect(int(self.x), int(self.y) + 3, 8, 1, WHITE)
        else:
            blit("spring", self.x, self.y)


class Wisp:
    """Floating wisp: hovers around its anchor and only drifts after the player once they're close.

    Its speed is capped at 0.3 px/frame, far slower than the player — it's pressure, not a death sentence.
    """

    W, H = 10, 8

    def __init__(self, x, y):
        self.ax, self.ay = float(x), float(y)
        self.x, self.y = float(x), float(y)
        self.t = 0
        self.alive = True

    @property
    def rect(self):
        return (self.x, self.y, self.W, self.H)

    def update(self, player):
        if not self.alive:
            return
        self.t += 1
        if abs(player.x - self.x) < 96 and abs(player.y - self.y) < 64:
            tx = player.x + player.W / 2 - self.W / 2
            ty = player.y + player.H / 2 - self.H / 2
            step = 0.28
        else:
            tx = self.ax
            ty = self.ay + math.sin(self.t * 0.045) * 6
            step = 0.20
        self.x += max(-step, min(step, tx - self.x))
        self.y += max(-step, min(step, ty - self.y))

    def draw(self):
        if not self.alive:
            return
        oy = 1 if (self.t // 8) % 2 == 0 else 0
        blit("wisp", self.x, self.y + oy)
        if self.t % 20 == 0:
            pyxel.pset(int(self.x) + 2, int(self.y) + oy + 3, PINK)


# ---------------------------------------------------------------- game


class Game:
    TITLE, PLAY, WIN, OVER = 0, 1, 2, 3
    headless = False
    godmode = False
    difficulty = DEFAULT_DIFF      # the difficulty picked on the title screen lives here; R restarts with it

    def __init__(self, seed=None, hard=False, difficulty=None):
        if difficulty is None:
            difficulty = Game.difficulty if not hard else 2
        self.difficulty = max(0, min(len(DIFFS) - 1, int(difficulty)))
        Game.difficulty = self.difficulty
        self.mode = DIFFS[self.difficulty]
        self.hard = self.difficulty == 2
        self.seed = seed if seed is not None else random.randint(1, 99999)
        self.level = generate_level(self.seed, self.difficulty)
        self.state = self.TITLE
        self.particles = []
        self.t = 0
        self.hearts = self.mode["hearts"]
        self.coins = 0
        self.berries = 0
        self.total_coins = len(self.level.coin_spawns)
        self.total_berries = len(self.level.berry_spawns)
        self.crumble_delay = self.mode["crumble"]
        self.crumble_respawn = self.mode["crumble_respawn"]
        self.stats = {"jump": 0, "double": 0, "wall": 0, "shot": 0, "dash": 0}
        self.bullets = []
        self.movers = []
        self.reset_level()

    def reset_level(self):
        lvl = self.level
        self.player = Player(*lvl.player_spawn, max_dashes=self.mode["dashes"])
        self.enemies = [WalkSlime(x, y) for x, y in lvl.enemy_spawns]
        self.wisps = [Wisp(x, y) for x, y in lvl.wisp_spawns]
        self.coin_objs = [Coin(x, y) for x, y in lvl.coin_spawns]
        self.berry_objs = [Berry(x, y) for x, y in lvl.berry_spawns]
        self.crystal_objs = [Crystal(x, y) for x, y in lvl.crystal_spawns]
        self.springs = [Spring(x, y) for x, y in lvl.spring_spawns]
        self.movers = [Mover(**kw) for kw in lvl.mover_spawns]
        self.bullets = []
        # Key: put the camera in place at the start. Otherwise it "flies" from the top of the world to the bottom,
        # sweeping through all three sky layers and the stars — which just looks like the background flashing.
        self.cam_y_smooth = float(self._cam_y_target())

    def set_difficulty(self, d):
        d = max(0, min(len(DIFFS) - 1, int(d)))
        if d == self.difficulty:
            return
        self.difficulty = d
        Game.difficulty = d
        self.mode = DIFFS[d]
        self.hard = d == 2
        self.level = generate_level(self.seed, d)
        self.hearts = self.mode["hearts"]
        self.coins = self.berries = 0
        self.total_coins = len(self.level.coin_spawns)
        self.total_berries = len(self.level.berry_spawns)
        self.crumble_delay = self.mode["crumble"]
        self.crumble_respawn = self.mode["crumble_respawn"]
        self.reset_level()

    # --- feedback
    def sfx(self, i):
        pyxel.play(3, i)

    def burst(self, x, y, n, col, spread=1.2):
        if self.headless:
            return
        for _ in range(n):
            self.particles.append(Particle(
                x, y, pyxel.rndf(-spread, spread), pyxel.rndf(-1.6, -0.3),
                pyxel.rndi(10, 20), col, 1))

    def hurt(self, player, fatal=False):
        """Take a hit.

        A normal hit only costs a heart, knocks you back and grants i-frames, with **no teleport**: warping back is too harsh,
        one enemy would set you back half a screen. Only falling out of the map (fatal) returns you to the checkpoint.
        """
        if self.godmode:
            if fatal:
                cx, cy = player.checkpoint
                player.x, player.y = float(cx), float(cy)
                player.vx = player.vy = 0.0
            return
        if player.invuln > 0 and not fatal:
            return

        self.hearts -= 1
        self.sfx(2)
        self.burst(player.x + 4, player.y + 6, 10, RED, 1.8)
        if self.hearts <= 0:
            self.state = self.OVER
            self.sfx(5)
            return

        if fatal:
            cx, cy = player.checkpoint
            player.x, player.y = float(cx), float(cy)
            player.vx = player.vy = 0.0
        else:
            player.vx = -2.6 * (player.facing or 1)
            player.vy = -2.8
            player.jumping = False
        player.dash_left = player.max_dashes    # dash refills after a hit, so you don't sit in a corner waiting to die
        player.dash_cool = 0
        player.dash_frames = 0
        player.trail = []
        player.invuln = 90

    # --- update
    def update(self):
        self.t += 1
        if self.state == self.TITLE:
            if pyxel.btnp(pyxel.KEY_LEFT) or pyxel.btnp(pyxel.KEY_A):
                self.set_difficulty(self.difficulty - 1)
            elif pyxel.btnp(pyxel.KEY_RIGHT) or pyxel.btnp(pyxel.KEY_D):
                self.set_difficulty(self.difficulty + 1)
            for i, key in enumerate((pyxel.KEY_1, pyxel.KEY_2, pyxel.KEY_3)):
                if pyxel.btnp(key):
                    self.set_difficulty(i)
            if pyxel.btnp(pyxel.KEY_SPACE) or pyxel.btnp(pyxel.KEY_RETURN):
                self.restart()
            return
        if self.state in (self.WIN, self.OVER):
            if pyxel.btnp(pyxel.KEY_R):
                self.restart()
            elif pyxel.btnp(pyxel.KEY_SPACE) or pyxel.btnp(pyxel.KEY_RETURN):
                self.state = self.TITLE
            return

        p = self.player
        for m in self.movers:
            m.update()
        if p.rider is not None and p.rider in self.movers:
            p.x += p.rider.dx
            p.y += p.rider.dy
        p.update(self.level, self.read_keys(), self)

        if p.just_landed:
            self.burst(p.x + p.W / 2, p.y + p.H, 4, LGRAY, 0.8)

        # --- Spring: bounces you when touched from above and refills the dash (Celeste's springs do too)
        for s in self.springs:
            s.update()
            if (p.vy >= 0 and p.overlap(*s.rect)
                    and p.y + p.H <= s.y + s.H + 5):
                p.y = s.y - p.H
                p.vy = SPRING_VEL
                p.jumping = False
                p.on_ground = False
                p.dash_left = p.max_dashes
                p.dash_cool = 0
                s.anim = SPRING_ANIM
                self.sfx(9)
                self.burst(s.x + s.W / 2, s.y, 8, YELLOW, 1.2)

        # --- dash crystals
        for c in self.crystal_objs:
            c.update()
            if c.active and p.overlap(*c.rect):
                c.active = False
                c.timer = CRYSTAL_RESPAWN
                p.dash_left = p.max_dashes
                p.dash_cool = 0
                self.sfx(11)
                self.burst(c.x + c.W / 2, c.y + c.H / 2, 10, BLUE, 1.6)

        # --- strawberries
        for b in self.berry_objs:
            b.update()
            if not b.taken and p.overlap(*b.rect):
                b.taken = True
                self.berries += 1
                self.sfx(10)
                self.burst(b.x + 4, b.y + 4, 8, RED, 1.2)

        for e in self.enemies:
            e.update(self.level)
            if not e.alive:
                continue
            if p.overlap(*e.rect):
                if p.vy > 0 and p.y + p.H - p.vy <= e.y + 3:
                    e.alive = False
                    p.vy = STOMP_BOUNCE
                    p.jumping = False
                    self.sfx(3)
                    self.burst(e.x + e.W / 2, e.y + 2, 8, PINK)
                elif p.invuln == 0:
                    self.hurt(p)

        # --- wisps: can be shot down or stomped
        for w in self.wisps:
            w.update(p)
            if not w.alive or not p.overlap(*w.rect):
                continue
            if p.vy > 0 and p.y + p.H - p.vy <= w.y + 4:
                w.alive = False
                p.vy = STOMP_BOUNCE
                p.jumping = False
                self.sfx(3)
                self.burst(w.x + w.W / 2, w.y + 3, 8, PURPLE)
            elif p.invuln == 0:
                self.hurt(p)

        for c in self.coin_objs:
            c.update()
            if not c.taken and p.overlap(*c.rect):
                c.taken = True
                self.coins += 1
                self.sfx(1)
                self.burst(c.x + 3, c.y + 4, 5, YELLOW, 1.0)

        self._update_crumble()

        for b in self.bullets:
            b.update(self.level, self)
            if not b.alive:
                continue
            hit = False
            for e in self.enemies:
                if e.alive and b.hit(*e.rect):
                    e.alive = False
                    self.sfx(7)
                    self.burst(e.x + e.W / 2, e.y + 2, 8, PINK)
                    hit = True
                    break
            if not hit:
                for w in self.wisps:
                    if w.alive and b.hit(*w.rect):
                        w.alive = False
                        self.sfx(7)
                        self.burst(w.x + w.W / 2, w.y + 3, 8, PURPLE)
                        hit = True
                        break
            if hit:
                b.alive = False
        self.bullets = [b for b in self.bullets if b.alive]

        for pt in self.particles:
            pt.update()
        self.particles = [pt for pt in self.particles if pt.life > 0]

        fx, fy = self.level.flag_pos
        # Test the whole flagpole (top to ground), not just the 18-pixel flag —
        # otherwise a high jump over the flag sails past the goal and off the map.
        if p.overlap(fx, 0, TILE, fy):
            self.state = self.WIN
            self.sfx(4)

    def trigger_crumble(self, lvl, tx, ty):
        if (tx, ty) not in lvl.crumble_state:
            lvl.crumble_state[(tx, ty)] = [0, self.crumble_delay]

    def _update_crumble(self):
        lvl = self.level
        for key, st in list(lvl.crumble_state.items()):
            st[1] -= 1
            if st[1] > 0:
                continue
            tx, ty = key
            if st[0] == 0:                     # break away
                lvl.tiles[ty][tx] = EMPTY
                st[0], st[1] = 1, self.crumble_respawn
            else:                              # grow back
                lvl.tiles[ty][tx] = CRUMBLE
                del lvl.crumble_state[key]

    def read_keys(self):
        return {
            "left": pyxel.btn(pyxel.KEY_LEFT) or pyxel.btn(pyxel.KEY_A),
            "right": pyxel.btn(pyxel.KEY_RIGHT) or pyxel.btn(pyxel.KEY_D),
            "up": pyxel.btn(pyxel.KEY_UP) or pyxel.btn(pyxel.KEY_W),
            "down": pyxel.btn(pyxel.KEY_DOWN) or pyxel.btn(pyxel.KEY_S),
            "jump": (pyxel.btn(pyxel.KEY_SPACE) or pyxel.btn(pyxel.KEY_Z)
                     or pyxel.btn(pyxel.KEY_UP) or pyxel.btn(pyxel.KEY_W)),
            "dash": (pyxel.btn(pyxel.KEY_C) or pyxel.btn(pyxel.KEY_J)
                     or pyxel.btn(pyxel.KEY_SHIFT)),
            "shoot": pyxel.btn(pyxel.KEY_X) or pyxel.btn(pyxel.KEY_K),
        }

    def restart(self, seed=None):
        self.seed = seed if seed is not None else random.randint(1, 99999)
        self.level = generate_level(self.seed, self.difficulty)
        self.total_coins = len(self.level.coin_spawns)
        self.total_berries = len(self.level.berry_spawns)
        self.hearts = self.mode["hearts"]
        self.coins = 0
        self.berries = 0
        self.crumble_delay = self.mode["crumble"]
        self.crumble_respawn = self.mode["crumble_respawn"]
        self.particles = []
        self.bullets = []
        self.reset_level()
        self.state = self.PLAY

    cam_override = None
    cam_y_smooth = 0.0

    def _cam_y_target(self):
        t = self.player.y + self.player.H / 2 - VIEW_H / 2
        return max(0.0, min(t, self.level.h * TILE - VIEW_H))

    @property
    def cam_y(self):
        # smooth follow, so the per-frame integer jump doesn't cause jitter
        self.cam_y_smooth += (self._cam_y_target() - self.cam_y_smooth) * 0.18
        return int(self.cam_y_smooth)

    @property
    def cam_x(self):
        if self.cam_override is not None:
            return int(max(0, min(self.cam_override,
                                  self.level.w * TILE - VIEW_W)))
        cx = self.player.x + self.player.W / 2 - VIEW_W / 2
        return int(max(0, min(cx, self.level.w * TILE - VIEW_W)))

    @property
    def meters(self):
        return int(self.player.x) // TILE

    # --- drawing
    def draw(self):
        if self.state == self.TITLE:
            self.draw_sky(0, 0, 0.0)
            pyxel.camera()
            self.draw_tiles(0, 0)
            self.draw_title()
            return
        self.draw_world()
        if self.state == self.OVER:
            self.draw_overlay("GAME OVER", "SPACE TITLE  R RETRY", RED)
        elif self.state == self.WIN:
            self.draw_overlay("CLEAR!", "SPACE TITLE  R AGAIN", YELLOW)

    def draw_world(self):
        cam = self.cam_x
        camy = self.cam_y
        self.draw_sky(cam, camy)
        pyxel.camera(cam, camy)
        self.draw_tiles(cam, camy)
        fx, fy = self.level.flag_pos
        blit("flag", fx, fy - 18)
        for s in self.springs:
            s.draw()
        for c in self.crystal_objs:
            c.draw()
        for b in self.berry_objs:
            b.draw()
        for c in self.coin_objs:
            if not c.taken:
                c.draw()
        for m in self.movers:
            m.draw()
        for e in self.enemies:
            e.draw()
        for w in self.wisps:
            w.draw()
        for b in self.bullets:
            b.draw()
        for pt in self.particles:
            pt.draw()
        if self.state in (self.PLAY, self.WIN, self.OVER):
            self.player.draw()
        pyxel.camera()
        self.draw_hud()

    def draw_sky(self, cam, camy=0, alt=None):
        """Sunset at the bottom, with night settling in as you go up and stars fading in with altitude.

        alt = 0 is the valley floor (three-band sunset plus the setting sun), 1 is the tower top (a black sky full of stars).
        The night doesn't switch on all at once, it **presses down from the top of the screen**: the height of the dark band follows
        alt continuously, and the boundary uses 4px dithering, so climbing looks like the sky slowly going dark.
        """
        # Purple -> peach: the 16-color palette has nothing in between, and rough dithering just turns into a band of grit.
        # So use three flat bands (purple / magenta / peach) with only 4px of dithering at the seams —
        # the classic three-band sunset of retro hardware, which stays clean at 2x scale.
        seam1, seam2 = 92, 124
        pyxel.cls(PURPLE)
        pyxel.rect(0, seam1, VIEW_W, seam2 - seam1, RED)
        pyxel.rect(0, seam2, VIEW_W, VIEW_H - seam2, PINK)
        for y in (seam1 - 2, seam2 - 2):
            for k in range(4):
                for x in range((y + k) % 2, VIEW_W, 2):
                    pyxel.pset(x, y + k, RED if y == seam1 - 2 else PINK)

        # setting sun
        pyxel.circ(252, seam2 - 6, 17, PEACH)
        pyxel.circ(252, seam2 - 6, 12, YELLOW)

        # White clouds (parallax 0.25 / 0.12).
        # Draw a whole column of copies vertically (k = -1..2), not just two:
        # with only two, on the frame where the modulo wraps, the number of clouds that fit on screen drops from 2 to 1
        # and the clouds look like they jump. Fill the screen plus one extra copy and the wrap becomes a one-step shift of the whole field.
        P = 130
        # Key: the vertical phase must hang off the "world column index", not the screen loop variable i.
        # Otherwise every time ox wraps, a screen column inherits its neighbour's phase — and since phases differ,
        # the cloud gets swapped for another one and its vertical position jumps.
        scroll = int(cam * 0.25)
        base = scroll // 160
        ox = scroll % 160
        oy = int(camy * 0.12)
        for i in range(9):
            col = i + base
            cx = i * 160 - ox + 10
            phase = (col * 137 - oy) % P
            for k in (-1, 0, 1, 2):
                cy = phase + k * P - 30
                if cy < -24 or cy > VIEW_H + 12:
                    continue
                pyxel.elli(cx, cy, 20, 8, WHITE)
                pyxel.elli(cx + 15, cy - 5, 14, 7, WHITE)
                pyxel.elli(cx - 12, cy + 2, 12, 5, WHITE)

        # ---- altitude: night presses down from the top
        if alt is None:
            span = max(1, self.level.h * TILE - VIEW_H)
            alt = 1.0 - max(0.0, min(1.0, camy / span))
        night = max(0.0, min(1.0, (alt - 0.28) / 0.58))
        if night > 0:
            dark_h = int(night * (VIEW_H - 50))
            pyxel.rect(0, 0, VIEW_W, dark_h, NAVY)
            # higher up, the very top is pressed to pure black
            deep = max(0.0, min(1.0, (alt - 0.70) / 0.30))
            if deep > 0:
                dh = int(deep * dark_h * 0.78)
                pyxel.rect(0, 0, VIEW_W, dh, BLACK)
                for k in range(4):
                    for x in range((dh + k) % 2, VIEW_W, 2):
                        pyxel.pset(x, dh + k, BLACK)
            # transition band at the bottom edge of the night sky (still 4px dithering, don't spread it wide)
            for k in range(4):
                for x in range((dark_h + k) % 2, VIEW_W, 2):
                    pyxel.pset(x, dark_h + k, NAVY)
            # stars: parallax 0.06, only lit inside the night band; the higher you go, the more there are
            star_n = int(8 + night * 40)
            sx0 = int(cam * 0.06)
            sy0 = int(camy * 0.06)
            for i in range(star_n):
                sx = (i * 71 + 13 - sx0) % VIEW_W
                sy = (i * 43 + 7 - sy0) % VIEW_H
                if sy >= dark_h - 5:
                    continue
                tw = (i + self.t // 12) % 31
                if tw == 0:
                    continue
                pyxel.pset(sx, sy, WHITE if tw % 3 else LAVENDER)


    def draw_tiles(self, cam, camy=0):
        lvl = self.level
        tx0 = cam // TILE
        tx1 = min(lvl.w - 1, tx0 + VIEW_W // TILE + 1)
        ty0 = max(0, camy // TILE)
        ty1 = min(lvl.h - 1, ty0 + VIEW_H // TILE + 2)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                t = lvl.tile(tx, ty)
                if t == EMPTY:
                    continue
                px, py = tx * TILE, ty * TILE
                if t == SOLID:
                    top = lvl.top[tx] if tx < len(lvl.top) else ty
                    depth = ty - top
                    if depth == 0:
                        # turf: green top + bright edge + accents
                        pyxel.rect(px, py, TILE, TILE, BROWN)
                        pyxel.rect(px, py, TILE, 3, GREEN)
                        pyxel.line(px, py, px + 7, py, LIME)
                        pyxel.line(px, py + 3, px + 7, py + 3, DGRAY)
                        if tx % 11 == 3:
                            pyxel.circ(px + 4, py - 2, 3, LIME)
                            pyxel.circ(px + 1, py - 1, 2, LIME)
                            pyxel.circ(px + 7, py - 1, 2, LIME)
                        elif tx % 13 == 6:
                            pyxel.pset(px + 3, py - 2, YELLOW)
                            pyxel.pset(px + 4, py - 3, WHITE)
                    elif depth == 1:
                        pyxel.rect(px, py, TILE, TILE, BROWN)
                        pyxel.rect(px, py, TILE, 2, DGRAY)
                        pyxel.line(px, py + 5, px + 3, py + 5, DGRAY)
                        pyxel.line(px + 4, py + 7, px + 7, py + 7, DGRAY)
                    else:
                        # deep soil: horizontal brick joints give it depth, plus a little grain, so it doesn't blur into one mass
                        pyxel.rect(px, py, TILE, TILE, BROWN)
                        if ty % 4 == 0:
                            pyxel.line(px, py, px + 7, py, DGRAY)
                        if (tx + ty) % 5 == 0:
                            pyxel.pset(px + 2, py + 3, DGRAY)
                            pyxel.pset(px + 5, py + 6, DGRAY)
                        if (tx * 3 + ty) % 9 == 0:
                            pyxel.line(px + 4, py + 4, px + 4, py + 7, DGRAY)
                    # pit walls: a dark edge on columns that face open air, for a cut-away look
                    if not lvl.is_solid(tx - 1, ty):
                        pyxel.rect(px, py, 1, TILE, DGRAY)
                        # every few rows of vertical wall gets a "wall-jump notch" to hint that it's climbable
                        if lvl.is_solid(tx, ty - 1) and ty % 3 == 0:
                            pyxel.rect(px + 2, py + 2, 5, 2, LGRAY)
                    if not lvl.is_solid(tx + 1, ty):
                        pyxel.rect(px + 7, py, 1, TILE, DGRAY)
                elif t == PLATFORM:
                    pyxel.rect(px, py, TILE, 2, ORANGE)
                    pyxel.rect(px, py + 2, TILE, 1, BROWN)
                    if not lvl.is_platform(tx - 1, ty):
                        pyxel.rect(px, py, 1, 2, BROWN)
                        pyxel.pset(px, py, WHITE)
                    if not lvl.is_platform(tx + 1, ty):
                        pyxel.rect(px + 7, py, 1, 2, BROWN)
                elif t == CRUMBLE:
                    st = lvl.crumble_state.get((tx, ty))
                    off = (st[1] // 3) % 2 if st and st[0] == 0 else 0
                    ox = px + off
                    pyxel.rect(ox, py, TILE, TILE, BROWN)
                    pyxel.line(ox, py, ox + 7, py, ORANGE)
                    pyxel.line(ox + 2, py + 1, ox + 5, py + 5, DGRAY)
                    pyxel.line(ox + 5, py + 5, ox + 3, py + 7, DGRAY)
                    if st and st[0] == 0:
                        pyxel.pset(ox + 1, py + 2, RED)
                elif t == BREAK:
                    pyxel.rect(px, py, TILE, TILE, LGRAY)
                    pyxel.rectb(px, py, TILE, TILE, DGRAY)
                    pyxel.line(px + 2, py + 1, px + 5, py + 4, DGRAY)
                    pyxel.line(px + 5, py + 4, px + 3, py + 6, DGRAY)
                elif t == DECOR:
                    # low battlements: just a 5px stone lip that the player walks straight past
                    pyxel.rect(px, py + 3, TILE, 5, LGRAY)
                    pyxel.rect(px, py + 3, TILE, 1, WHITE)
                    pyxel.rect(px, py + 7, TILE, 1, DGRAY)
                    pyxel.line(px + 2, py + 5, px + 2, py + 6, DGRAY)
                    pyxel.line(px + 5, py + 5, px + 5, py + 6, DGRAY)
                elif t == SPIKE:
                    blit("spike", px, py - 4)   # 12px tall, bottom-aligned with the tile

    def draw_hud(self):
        pyxel.rect(0, 0, VIEW_W, 18, BLACK)
        pyxel.rect(0, 18, VIEW_W, 1, NAVY)
        # hearts: 5 at most (only EASY has that many)
        for i in range(self.mode["hearts"]):
            blit("heart" if i < self.hearts else "heart_empty", 4 + i * 8, 5)
        # dash pips
        xo = 46
        for i in range(self.player.max_dashes):
            blit("orb" if i < self.player.dash_left else "orb_empty",
                 xo + i * 9, 5)
        blit("coin_a", 66, 4)
        pyxel.text(74, 5, f"{self.coins:02d}", WHITE)
        blit("berry", 94, 5)
        pyxel.text(104, 5, f"{self.berries:02d}", WHITE)
        pyxel.text(124, 5, f"{self.meters:4d}m", WHITE)
        col = (LIME, WHITE, RED)[self.difficulty]
        pyxel.text(168, 5, self.mode["label"], col)
        pyxel.text(252, 5, f"S{self.seed}", LAVENDER)

        # progress bar: how much further the player has to go
        total = max(1, self.level.w * TILE)
        prog = min(1.0, self.player.x / total)
        pyxel.rect(0, 19, VIEW_W, 1, DGRAY)
        pyxel.rect(0, 19, int(VIEW_W * prog), 1, LIME)

    def draw_title(self):
        pyxel.rect(36, 38, 248, 168, BLACK)
        pyxel.rectb(36, 38, 248, 168, NAVY)
        pyxel.rectb(37, 39, 246, 166, DGRAY)
        self._center("TINY RUNNER", 48, YELLOW)
        self._center("PROCEDURAL PLATFORMER", 62, WHITE)

        # difficulty picker: left/right or 1 2 3
        mode = self.mode
        col = (LIME, WHITE, RED)[self.difficulty]
        if self.difficulty > 0:
            pyxel.text(48, 78, "<", LAVENDER)
        if self.difficulty < len(DIFFS) - 1:
            pyxel.text(266, 78, ">", LAVENDER)
        self._center(mode["label"], 78, col)
        self._center(mode["blurb"], 90, LAVENDER)
        self._center("1 2 3", 100, DGRAY)

        blit("player_idle", 112, 106)
        blit("berry", 132, 112)
        blit("crystal", 144, 110)
        blit("slime_a", 158, 112)
        blit("wisp", 172, 110)

        self._center("ARROWS MOVE  SPACE JUMP  C DASH", 134, WHITE)
        self._center("X SHOOT   R NEW SEED   Q QUIT", 146, WHITE)
        if (self.t // 30) % 2 == 0:
            self._center("PRESS SPACE", 166, LIME)

    def _center(self, s, y, col):
        pyxel.text(160 - len(s) * 2, y, s, col)

    def draw_overlay(self, title, sub, col):
        pyxel.rect(88, 80, 144, 66, BLACK)
        pyxel.rectb(88, 80, 144, 66, col)
        self._center(title, 90, col)
        self._center(sub, 104, WHITE)
        self._center(f"COINS {self.coins}/{self.total_coins}", 118, YELLOW)
        self._center(f"BERRIES {self.berries}/{self.total_berries}", 130, RED)
        if self.berries >= self.total_berries and self.total_berries:
            self._center("ALL BERRIES!", 140, LIME)


# ---------------------------------------------------------------- self-test


# Measured jump tables (generated by calib.py): hold frames -> horizontal distance / apex height.
# The bot uses these to choose how many frames to hold, instead of blindly mashing full jumps.
JUMP_TABLE = [
    (1, 25.2), (2, 28.8), (3, 32.4), (4, 36.0), (5, 37.8), (6, 41.4),
    (7, 43.2), (8, 45.0), (9, 46.8), (10, 48.6), (11, 50.4), (12, 52.2),
    (14, 54.0), (15, 55.8),
]
APEX_TABLE = [
    (1, 7.3), (2, 11.4), (3, 15.1), (4, 18.7), (5, 21.9), (6, 24.8),
    (7, 27.5), (8, 29.9), (9, 32.1), (10, 33.9), (11, 35.5), (12, 36.8),
    (14, 38.7), (15, 39.2),
]


def hold_for_distance(px):
    for hold, dist in JUMP_TABLE:
        if dist >= px:
            return hold
    return 15


def hold_for_height(px):
    for hold, apex in APEX_TABLE:
        if apex >= px:
            return hold
    return 15


class Bot:
    """Auto-player: always heads right and jumps at walls / pits / spikes / enemies.

    Its reason to exist is as a live playability test — anywhere it can't get through, a human would be swearing.
    It does four things: pick a jump length by distance (JUMP_TABLE), add a double jump when it can't reach,
    wall-jump up whatever wall it touches (the only way past a tall face), and add an air dash over big gaps.
    """

    def __init__(self):
        self.hold = 0
        self.cool = 0
        self.release = 0        # the button must be released for at least 2 frames before the next press counts as "re-pressed"
        self.want_double = False
        self.want_dash = False
        self.dash_wait = 0      # cooldown after a dash, so it doesn't spam and waste them
        self.wait = 0           # frames spent waiting for a moving platform

    # ---------- airborne
    def _air(self, game, p, keys):
        # walking off a 1-tile battlement leaves you airborne, so the ground branch never runs;
        # if the next thing is a bottomless pit, use coyote time to get a jump in
        if (p.coyote > 0 and p.vy >= 0
                and self._mover_ahead(game, p) is None):
            lvl = game.level
            feet_ty = int((p.y + p.H + 1) // TILE)
            front = int((p.x + p.W + 1) // TILE)
            if self._pit(lvl, front, feet_ty):
                d, up = self._scan(lvl, front, feet_ty)
                need = d * TILE + 8
                self.hold = 15 if p.vx < 1.1 else hold_for_distance(need)
                if up > 0:
                    self.hold = max(self.hold, hold_for_height(up * TILE + 8))
                self.want_double = up >= 4
                self.want_dash = d >= 8
                self.cool = 3
                keys["jump"] = True
                return keys
        # gap too wide: add an air dash (horizontal, to the right)
        if (self.want_dash and self.dash_wait == 0 and p.dash_left > 0
                and p.dash_cool == 0 and p.vy > -0.8):
            self.want_dash = False
            self.dash_wait = 8
            self.hold = 0
            self.release = 2
            keys["jump"] = False
            keys["dash"] = True
            return keys
        if self.hold > 0 or self.release > 0:
            return keys
        # against a wall and already falling -> wall jump (the core of the climbing loop)
        if p.wall_dir != 0 and p.vy > -2.0:
            self.hold = 10
            keys["jump"] = True
            return keys
        # need height and already past the apex -> double jump
        if (self.want_double and not p.double_used
                and p.jumps_left > 0 and p.vy > -0.6):
            self.hold = 8
            keys["jump"] = True
            return keys
        return keys

    # ---------- grounded
    def think(self, game):
        p = game.player
        lvl = game.level
        keys = {"left": False, "right": True, "jump": self.hold > 0,
                "shoot": False, "dash": False, "up": False, "down": False}
        if self.hold > 0:
            self.hold -= 1
            if self.hold == 0:
                self.release = 2
        elif self.release > 0:
            self.release -= 1
        if self.cool > 0:
            self.cool -= 1
        if self.dash_wait > 0:
            self.dash_wait -= 1
        if not p.on_ground:
            return self._air(game, p, keys)
        if self.cool > 0:          # don't re-decide on the first few frames after a jump
            return keys

        self.want_dash = False
        feet_ty = int((p.y + p.H + 1) // TILE)

        # standing on a moving platform: just walk right, and hop over if there's a ledge within 2 tiles
        if p.rider is not None:
            front = int((p.x + p.W + 1) // TILE)
            for d in (1, 2):
                if lvl.is_solid(front + d, feet_ty - 1):
                    self.hold = 6
                    keys["jump"] = True
                    break
            return keys
        head_ty = int(p.y // TILE)
        front = int((p.x + p.W + 1) // TILE)

        blocked = (lvl.is_solid(front, head_ty) or
                   lvl.is_solid(front, feet_ty - 1))

        floor = None
        for ty in range(feet_ty, lvl.h):
            if lvl.is_solid(front, ty) or lvl.is_platform(front, ty):
                floor = ty
                break
        gap = floor is None

        spike_ahead = any(
            lvl.tile(int((p.x + p.W + d * TILE) // TILE), feet_ty - 1) == SPIKE
            for d in range(1, 3))

        enemy_ahead = any(
            e.alive and -6 < (e.x - p.x) < 46 and abs(e.y - p.y) < 22
            for e in game.enemies)

        # shoot whatever is in range first, then worry about stomping / dodging the rest
        if any(e.alive and 0 < (e.x - p.x) < BULLET_RANGE - 16
               and abs(e.y - p.y) < 18 for e in game.enemies):
            keys["shoot"] = True

        if blocked:
            # wall ahead: measure it first. A 1-tile battlement only deserves a short hop —
            # a full jump sails over the whole rampart and into the next gap (guaranteed wipeout on hard with gap=5).
            top = feet_ty
            while top > 0 and lvl.is_solid(front, top - 1):
                top -= 1
            h = feet_ty - top
            self.want_double = h >= 4
            self.hold = 12 if h >= 3 else (6 if h == 2 else 4)
            self.cool = 4
            keys["jump"] = True
            return keys

        self.want_double = False

        if enemy_ahead and not spike_ahead:
            self.hold = 5          # a short hop lands on the enemy's head = stomp
            self.cool = 3
            keys["jump"] = True
            return keys

        if spike_ahead:
            self.hold = 12
            self.cool = 4
            keys["jump"] = True
            return keys

        if gap:
            mv = self._mover_ahead(game, p)
            if mv is not None:
                if mv.x - (p.x + p.W) > 6 and self.wait < 300:
                    self.wait += 1
                    keys["right"] = False    # the platform hasn't arrived yet; stand and wait
                    return keys
                # the platform is right at your feet: walk on, don't jump (a jump overshoots it)
                self.wait = 0
                return keys
            self.wait = 0
            d, up = self._scan(lvl, front, feet_ty)
            need = d * TILE + 8
            if p.vx < 1.1:
                hold = 15              # just landed with no speed; don't trust the table here
            else:
                hold = hold_for_distance(need)
            if up > 0:
                hold = max(hold, hold_for_height(up * TILE + 8))
            elif up < 0:
                hold = max(1, hold - 1)
            if up >= 4:
                self.want_double = True    # out of single-jump range; top it up in the air
            if d >= 8:
                self.want_dash = True      # this gap needs the dash to cover the distance
            self.hold = hold
            self.cool = 3
            keys["jump"] = True

        return keys

    def _mover_ahead(self, game, p):
        best = None
        for m in game.movers:
            dx = m.x - (p.x + p.W)
            if -24 < dx < 96 and abs(m.y - (p.y + p.H)) <= 10:
                if best is None or dx < best[0]:
                    best = (dx, m)
        return best[1] if best else None

    def _pit(self, lvl, tx, feet_ty):
        """Is this column completely empty from the feet down (a bottomless pit)?"""
        for ty in range(feet_ty, lvl.h):
            if lvl.is_solid(tx, ty) or lvl.is_platform(tx, ty):
                return False
        return True

    def _scan(self, lvl, front, feet_ty):
        """Find the first landing surface ahead; return (distance in tiles, tiles above the feet)."""
        for d in range(1, 13):
            tx = front + d
            for ty in range(max(0, feet_ty - 6), min(lvl.h, feet_ty + 4)):
                if lvl.is_solid(tx, ty) or lvl.is_platform(tx, ty):
                    return d, feet_ty - ty
        return 12, 0


MILESTONES = [(0.12, "01_start"), (0.32, "02_run"), (0.52, "03_mid"),
              (0.72, "04_late"), (0.90, "05_finish")]


def run_demo(out_dir, seed=None, frames_limit=8000, godmode=False, fast=False,
             hard=False, difficulty=None):
    pyxel.rseed(7)
    game = Game(seed, hard, difficulty)
    game.headless = True
    game.godmode = godmode
    bot = Bot()

    if out_dir:
        game.state = Game.TITLE
        game.draw()
        if not fast:
            pyxel.flip()
        pyxel.screenshot(os.path.join(out_dir, "00_title.png"), 2)

    game.state = Game.PLAY
    frames = 0
    shot_idx = 0
    total_x = max(1, game.level.w * TILE)
    holder = {}
    if fast:
        # No window, no drawing: feed the bot's intent straight into the input, which makes it hundreds of times faster than real time.
        # Note: update must not call bot.think() again — the bot advances its internal state per frame,
        # and thinking twice in one frame throws off every jump / dash timing (measured: it gets stuck on the same gap).
        game.read_keys = lambda: holder["keys"]

    while frames < frames_limit:
        frames += 1
        keys = bot.think(game)
        if fast:
            holder["keys"] = keys
        else:
            pyxel.set_btn(pyxel.KEY_RIGHT, keys["right"])
            pyxel.set_btn(pyxel.KEY_LEFT, keys["left"])
            pyxel.set_btn(pyxel.KEY_SPACE, keys["jump"])
            pyxel.set_btn(pyxel.KEY_X, keys["shoot"])
            pyxel.set_btn(pyxel.KEY_C, keys["dash"])
        game.update()
        if not fast:
            game.draw()
            pyxel.flip()
            while shot_idx < len(MILESTONES) and \
                    game.player.x / total_x >= MILESTONES[shot_idx][0]:
                if out_dir:
                    pyxel.screenshot(
                        os.path.join(out_dir, f"{MILESTONES[shot_idx][1]}.png"), 2)
                shot_idx += 1
        if game.state == Game.WIN:
            if out_dir and not fast:
                pyxel.screenshot(os.path.join(out_dir, "06_win.png"), 2)
            print(f"[demo] WIN  seed={game.seed} mode={game.mode['label']} "
                  f"frame={frames} x={game.player.x:.0f} "
                  f"coins={game.coins}/{game.total_coins} "
                  f"berries={game.berries}/{game.total_berries} "
                  f"hearts={game.hearts} stats={game.stats}")
            return True
        if game.state == Game.OVER:
            print(f"[demo] DIED seed={game.seed} frame={frames} "
                  f"x={game.player.x:.0f} coins={game.coins}")
            return False
    print(f"[demo] TIMEOUT seed={game.seed} x={game.player.x:.0f} "
          f"state={game.state} hearts={game.hearts}")
    return False


def run_sweep(n, limit=9000, godmode=True, hard=False, difficulty=None):
    """Run n seeds in bulk and count how many generated levels can actually be finished.

    This is the generator's only objective check: the bot performs no fancy movement,
    so if it can finish, the main route has no breaks.
    """
    wins = 0
    results = []
    for i in range(n):
        seed = 1000 + i
        game = Game(seed, hard, difficulty)
        game.headless = True
        game.godmode = godmode
        game.state = Game.PLAY
        bot = Bot()
        game.read_keys = lambda g=game, b=bot: b.think(g)
        frame = 0
        for frame in range(1, limit + 1):
            game.update()
            if game.state == Game.WIN:
                wins += 1
                results.append((seed, True, frame, game.player.x))
                break
            if game.state == Game.OVER:
                results.append((seed, False, frame, game.player.x))
                break
        else:
            results.append((seed, False, frame, game.player.x))

    label = DIFFS[game.difficulty]["label"]
    print(f"\n=== sweep: {n} seeds, {label}, godmode={godmode} ===")
    for seed, ok, frame, x in results:
        print(f"  seed={seed:5d}  {'WIN ' if ok else 'FAIL'}  frames={frame:4d}  x={x:7.0f}")
    print(f"\nwin rate: {wins}/{n} = {100 * wins / n:.0f}%")
    if wins < n:
        bad = [s for s, ok, _, _ in results if not ok]
        print(f"failed seeds: {bad}")
    return wins, n


def run_tour(out_dir, seed=4242, hard=False, difficulty=None):
    """Terrain catalogue: **one screenshot per piece**.

    It doesn't depend on which pieces a particular run rolled — it walks PIECES in order, generating
    and framing them one at a time, so all 26 pieces get a shot and no stale images are left behind.
    """
    game = Game(seed, hard, difficulty)
    game.headless = True
    game.state = Game.PLAY
    game.t = 30
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    kinds = list(PIECES)
    for kind in kinds:
        L = Level(80, LEVEL_H)
        L.mode = game.mode
        L.seed = seed
        g0 = 50
        L.ground(0, 12, top=g0, depth=6)          # starting / landing foundation
        x1, g1 = PIECES[kind](L, 8, g0, rng, 0.6, game.difficulty == 2)
        L.w = max(40, x1 + 8)
        compute_top(L)
        game.level = L
        game.reset_level()
        game.player.x = float(8 * TILE + 4)
        game.player.y = float(g0 * TILE - Player.H)
        game.player.vx = game.player.vy = 0.0
        game.player.on_ground = True
        game.cam_override = 0
        # frame on the piece's midpoint: shafts / steep towers grow 13-18 rows upward, and framing on g0 alone would cut off the top
        mid = (g0 + g1) / 2
        game.cam_y_smooth = max(0.0, min(mid * TILE - VIEW_H / 2,
                                         L.h * TILE - VIEW_H))
        game.draw()
        pyxel.flip()
        pyxel.screenshot(os.path.join(out_dir, f"tile_{kind}.png"), 2)
        print(f"  {kind:9s} x=   8..{x1:3d}  g={g0}->{g1}")
    print(f"shot {len(kinds)} pieces -> {out_dir}")


def main():
    demo = "--demo" in sys.argv
    fast = "--fast" in sys.argv
    god = "--god" in sys.argv
    harsh = "--harsh" in sys.argv
    hard = "--hard" in sys.argv
    difficulty = None
    if "--easy" in sys.argv:
        difficulty = 0
    elif "--normal" in sys.argv:
        difficulty = 1
    elif hard:
        difficulty = 2
    seed = None
    out_dir = None
    sweep = 0
    tour = "--tour" in sys.argv

    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            out_dir = sys.argv[i + 1]
        elif a == "--seed" and i + 1 < len(sys.argv):
            seed = int(sys.argv[i + 1])
        elif a == "--sweep" and i + 1 < len(sys.argv):
            sweep = int(sys.argv[i + 1])

    headless = demo or sweep > 0
    if demo and out_dir:
        os.makedirs(out_dir, exist_ok=True)

    pyxel.init(VIEW_W, VIEW_H, title="TINY RUNNER", fps=60,
               quit_key=pyxel.KEY_Q, display_scale=2, headless=headless)
    bake_art()
    make_sounds()

    if sweep:
        wins, n = run_sweep(sweep, godmode=not harsh, hard=hard,
                            difficulty=difficulty)
        sys.exit(0 if wins == n else 1)

    if tour:
        run_tour(out_dir or "tour", seed or 4242, hard, difficulty)
        return

    if not demo:
        pyxel.mouse(False)
        game = Game(seed, hard, difficulty)
        pyxel.run(game.update, game.draw)
        return

    ok = run_demo(out_dir, seed=seed, godmode=god, fast=fast, hard=hard,
                  difficulty=difficulty)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
