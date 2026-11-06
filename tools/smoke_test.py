"""Smoke test: state machine + three difficulties + dash/crystal/spring/strawberry + vertical space.

Run with: ../.venv/bin/python tools/smoke_test.py
"""
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
import random

import pyxel
import tiny_runner as T

pyxel.init(T.VIEW_W, T.VIEW_H, fps=60, headless=True, quit_key=pyxel.KEY_Q)
T.bake_art()
T.make_sounds()

NO_KEYS = {"left": False, "right": False, "jump": False, "dash": False,
           "shoot": False, "up": False, "down": False}


def make_game(difficulty=1, seed=7):
    g = T.Game(seed, difficulty=difficulty)
    g.headless = True
    g.state = T.Game.PLAY
    return g


def on_start_ground(g):
    """Move the player onto the starting foundation and let them stand there (no falling animation needed)."""
    p = g.player
    ty = T.START_ROW
    p.x, p.y = 3.0 * T.TILE, float(ty * T.TILE - p.H)
    p.vx = p.vy = 0.0
    p.on_ground = True
    return p


def run(g, keys, n):
    g.read_keys = lambda: keys
    for _ in range(n):
        g.update()


# ---------------------------------------------------------------- state machine
def frame(space=False, r=False):
    pyxel.set_btn(pyxel.KEY_SPACE, space)
    pyxel.set_btn(pyxel.KEY_R, r)
    g.update()
    g.draw()
    pyxel.flip()


g = T.Game()
g.headless = True
assert g.state == T.Game.TITLE

frame(space=True)          # press space once
frame()
assert g.state == T.Game.PLAY, g.state
print("title -> play ok")

g.hearts = 1
g.state = T.Game.WIN
frame(r=True)
assert g.state == T.Game.PLAY and g.hearts == 3, (g.state, g.hearts)
print("win -> restart ok, hearts =", g.hearts)

g.state = T.Game.OVER
frame(space=True)
assert g.state == T.Game.TITLE, g.state
print("over -> title ok")

# fall into a pit: entering one should cost a heart and return you to the checkpoint
frame(space=True)
frame()
p = g.player
p.checkpoint = (100.0, 160.0)
p.x, p.y = 10.0, g.level.h * 8 + 40.0
frame()
assert g.hearts == 2, g.hearts
assert abs(p.x - 100.0) < 40 and p.y < 300, (p.x, p.y)
print("pit death -> respawn ok, hearts =", g.hearts, "pos =", (round(p.x), round(p.y)))

# ---------------------------------------------------------------- difficulty
expected = {0: (5, 2), 1: (3, 1), 2: (3, 1)}
for d, (hearts, dashes) in expected.items():
    gd = T.Game(7, difficulty=d)
    assert gd.hearts == hearts, (d, gd.hearts)
    assert gd.player.max_dashes == dashes, (d, gd.player.max_dashes)
    assert gd.player.dash_left == dashes
    assert gd.mode["label"] == T.DIFFS[d]["label"]
    gd.state = T.Game.TITLE
    gd.draw()                    # the title screen's difficulty picker has to be drawable
assert "dash" in {k for k, *_ in T.generate_level(7, 2).pieces}
print("difficulty presets ok:", {d: T.DIFFS[d]["label"] for d in expected})

# Switch difficulty left/right on the title screen: NORMAL -> EASY -> NORMAL -> HARD
gd = T.Game(7, difficulty=1)
gd.state = T.Game.TITLE


def press(key):
    # btnp works on frame boundaries: set_btn must be followed by a flip, or presses within the same frame don't register
    pyxel.set_btn(key, True)
    gd.update()
    pyxel.flip()
    pyxel.set_btn(key, False)
    gd.update()
    pyxel.flip()


press(pyxel.KEY_LEFT)
assert gd.difficulty == 0, gd.difficulty
press(pyxel.KEY_RIGHT)
assert gd.difficulty == 1, gd.difficulty
press(pyxel.KEY_2)
assert gd.difficulty == 1, gd.difficulty
print("title difficulty switch ok")

# ---------------------------------------------------------------- dash
def dist_after(dash):
    g = make_game()
    p = on_start_ground(g)
    start = p.x
    keys = dict(NO_KEYS, right=True, dash=dash)
    run(g, keys, 1)
    keys["dash"] = False
    run(g, keys, 19)
    return p.x - start, p


d_plain, p1 = dist_after(False)
d_dash, p2 = dist_after(True)
assert d_dash > d_plain + 20, (d_plain, d_dash)
assert p2.dash_left == p2.max_dashes          # refilled after getting back on the ground
print(f"dash distance ok: 20 frames cover {d_plain:.0f}px -> {d_dash:.0f}px, refilled on landing")

# Dash charges: you have to land between dashes (only one per airtime)
g = make_game()
p = on_start_ground(g)
g.player.y -= 40
run(g, dict(NO_KEYS, right=True), 2)
p.on_ground = False
p.dash_left = 1
keys = dict(NO_KEYS, right=True, dash=True)
run(g, keys, 1)
keys["dash"] = False
run(g, keys, 10)                             # press again in mid-air; it shouldn't dash a second time
assert p.dash_left == 0, p.dash_left
print("air dash limited to one charge ok")

# Up dash: hold ↑ (= the jump key) then press dash; the jump must not interrupt it
g = make_game()
p = on_start_ground(g)
run(g, dict(NO_KEYS), 1)                     # stand for a frame first to get coyote time
keys = dict(NO_KEYS, jump=True, up=True)
run(g, keys, 1)                              # jump
keys["dash"] = True
run(g, keys, 1)                              # hold up + press dash
keys["dash"] = False
run(g, keys, 1)
assert p.dash_frames > 0 and p.vy < -3.0, (p.dash_frames, p.vy)
print(f"up-dash ok: frames={p.dash_frames}, vy={p.vy:.2f}")

# ---------------------------------------------------------------- crystal
g = make_game()
p = on_start_ground(g)
p.dash_left = 0
c = T.Crystal(p.x + 2, p.y + 2)
g.crystal_objs = [c]
run(g, dict(NO_KEYS), 1)
assert p.dash_left == p.max_dashes, p.dash_left
assert not c.active
p.x += 32                    # move off it, or the crystal is eaten again the moment it grows back
run(g, dict(NO_KEYS), T.CRYSTAL_RESPAWN + 2)
assert c.active, "the crystal should have grown back"
print("dash crystal refill + respawn ok")

# ---------------------------------------------------------------- spring
g = make_game()
p = on_start_ground(g)
p.dash_left = 0
s = T.Spring(p.x, p.y + p.H)
g.springs = [s]
run(g, dict(NO_KEYS), 1)
assert p.vy < -5.0, p.vy
assert p.dash_left == p.max_dashes
assert s.anim > 0
print(f"spring bounce ok: vy={p.vy:.1f}, dash refilled")

# ---------------------------------------------------------------- strawberry
g = make_game()
p = on_start_ground(g)
b = T.Berry(p.x + 1, p.y + 2)
g.berry_objs = [b]
total = g.total_berries
run(g, dict(NO_KEYS), 1)
assert g.berries == 1 and b.taken and g.total_berries == total
print("berry pickup ok, total =", total)

# ---------------------------------------------------------------- level geometry
for d in (0, 1, 2):
    for seed in (1000, 1007, 1013):
        lvl = T.generate_level(seed, d)
        kinds = [k for k, *_ in lvl.pieces]
        assert "dash" in kinds and "spring" in kinds, (seed, d)
        for k, x0, x1, g0 in lvl.pieces:
            if k != "dash":
                continue
            gap = 0
            tx = x0
            while tx < x1:
                ty = g0
                while ty < lvl.h and not lvl.is_solid(tx, ty):
                    ty += 1
                if ty >= lvl.h:
                    gap += 1
                tx += 1
            assert gap == 8 + T.DIFFS[d]["rank"], (seed, d, gap)
print("level geometry ok: every run has dash / spring, dash gap width scales with difficulty")

# ---------------------------------------------------------------- vertical space
for seed in (1000, 1001, 1013):
    lvl = T.generate_level(seed, 1)
    gs = [g for *_, g in lvl.pieces]
    climbs = sum(1 for a, b in zip(gs, gs[1:]) if a - b >= 6)
    descents = sum(1 for a, b in zip(gs, gs[1:]) if b - a >= 6)
    kinds = {k for k, *_ in lvl.pieces}
    assert max(gs) - min(gs) >= 40, (seed, max(gs) - min(gs))
    assert climbs >= 4 and descents >= 2, (seed, climbs, descents)
    assert len(kinds & {"steep", "well", "atrium", "chasm"}) >= 2, (seed, kinds)
print("vertical profile ok: climbs then descends within a run, height span > 40 rows")

# net height delta of the four vertical pieces
def piece_delta(fn):
    L = T.Level(80, T.LEVEL_H)
    L.mode = T.DIFFS[1]
    L.ground(0, 12, top=50, depth=6)
    return fn(L, 8, 50, random.Random(3), 0.6, False)[1] - 50

assert piece_delta(T.p_steep) <= -12
assert piece_delta(T.p_well) <= -12
assert piece_delta(T.p_atrium) == -7
assert piece_delta(T.p_chasm) >= 10
print("vertical pieces ok: steep/well/atrium/chasm net height deltas match the design")

# Altitude sky: sunset at the valley floor, night sky at the tower top (and it must not blow up)
gsky = make_game()
gsky.state = T.Game.PLAY
gsky.draw_sky(0, 0, 0.0)
assert pyxel.pget(8, 8) == T.PURPLE, pyxel.pget(8, 8)
gsky.draw_sky(0, 0, 1.0)
assert pyxel.pget(8, 8) in (T.BLACK, T.NAVY, T.WHITE, T.LAVENDER)
gsky.draw_sky(0, 0, 0.55)
print("altitude sky ok: sunset at the valley floor -> night sky at the tower top")

# ---------------------------------------------------------------- a surviving bot plays a full run
game = T.Game(1000, difficulty=1)
game.headless = True
game.godmode = True
game.state = T.Game.PLAY
bot = T.Bot()
holder = {}
game.read_keys = lambda: holder["keys"]
for f in range(1, 9001):
    holder["keys"] = bot.think(game)
    game.update()
    if game.state == T.Game.WIN:
        break
assert game.state == T.Game.WIN, (game.state, game.player.x)
assert game.stats["dash"] > 0, game.stats
print(f"bot run ok: seed=1000 frame={f} berries={game.berries}/{game.total_berries} "
      f"stats={game.stats}")

print("ALL SMOKE TESTS PASSED")
