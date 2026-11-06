"""Measure the new moves: dash (horizontal / up), dash-jump, spring.

Run with: ../.venv/bin/python tools/calib3.py
"""
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
import pyxel
import tiny_runner as T

pyxel.init(320, 240, fps=60, headless=True, quit_key=pyxel.KEY_Q)
T.bake_art()


class Stub:
    headless = True
    stats = {"jump": 0, "double": 0, "wall": 0, "shot": 0, "dash": 0}
    bullets = []
    movers = []

    def sfx(self, i):
        pass

    def burst(self, *a, **k):
        pass

    def hurt(self, p, fatal=False):
        pass


stub = Stub()
GROUND = 66


def flat(cols=200):
    L = T.Level(cols, T.LEVEL_H)
    L.ground(0, cols, top=GROUND)
    L.top = [GROUND] * cols
    return L


def keys(right=True, jump=False, dash=False, up=False, down=False):
    return {"left": False, "right": right, "jump": jump, "dash": dash,
            "shoot": False, "up": up, "down": down}


def spawn():
    return T.Player(20.0, GROUND * T.TILE - T.Player.H)


# ---------------------------------------------------------------- A. ground dash
L = flat()
p = spawn()
start = p.x
for f in range(1, 9):
    p.update(L, keys(dash=(f == 1)), stub)
print(f"ground dash, 8 frames: {p.x - start:5.1f} px ({(p.x - start) / T.TILE:4.1f} tiles)"
      f"  dash_left={p.dash_left}")

# ---------------------------------------------------------------- B. dash jump
def jump_run(dash_at_apex):
    L = flat()
    p = spawn()
    start = p.x
    jump_hold = 0
    dashed = False
    apex = p.y
    for i in range(1, 240):
        if i == 8:
            jump_hold = 15
        k = keys(jump=jump_hold > 0)
        if not dash_at_apex and i == 8:
            pass
        if (dash_at_apex and not dashed and p.coyote == 0
                and p.vy > -0.4 and jump_hold == 0 and not p.on_ground):
            k["dash"] = True
            dashed = True
        if jump_hold > 0:
            jump_hold -= 1
        p.update(L, k, stub)
        apex = min(apex, p.y)
        if i > 12 and p.on_ground and p.vy == 0:
            break
    return p.x - start, GROUND * T.TILE - T.Player.H - apex


d1, h1 = jump_run(False)
d2, h2 = jump_run(True)
print(f"full-speed jump: {d1:5.1f} px horizontally ({d1 / T.TILE:4.1f} tiles)  apex {h1:4.1f} px")
print(f"dash at the apex: {d2:5.1f} px horizontally ({d2 / T.TILE:4.1f} tiles)  apex {h2:4.1f} px")

# ---------------------------------------------------------------- C. up dash
L = flat()
p = spawn()
top = p.y
for i in range(1, 90):
    p.update(L, keys(jump=False, dash=(i == 1), up=True), stub)
    top = min(top, p.y)
print(f"up dash: {GROUND * T.TILE - T.Player.H - top:5.1f} px"
      f" ({(GROUND * T.TILE - T.Player.H - top) / T.TILE:4.1f} tiles)")

# ---------------------------------------------------------------- D. spring
L = flat()
p = spawn()
p.vy = T.SPRING_VEL
p.on_ground = False
top = p.y
for i in range(1, 200):
    p.update(L, keys(), stub)
    top = min(top, p.y)
    if p.on_ground and i > 3:
        break
print(f"spring: {GROUND * T.TILE - T.Player.H - top:5.1f} px"
      f" ({(GROUND * T.TILE - T.Player.H - top) / T.TILE:4.1f} tiles)")

# ---------------------------------------------------------------- E. dash refill on landing
L = flat()
p = spawn()
p.dash_left = 0
p.update(L, keys(), stub)
print(f"after one grounded frame dash_left={p.dash_left} (expected {p.max_dashes})")
