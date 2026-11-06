"""Measure the new moves: double-jump height, wall-jump climb, and chained wall jumps in a narrow well."""
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
import pyxel, tiny_runner as T

pyxel.init(320, 240, fps=60, headless=True, quit_key=pyxel.KEY_Q)
T.bake_art()

class Stub:
    headless = True
    def sfx(self, i): pass
    def burst(self, *a, **k): pass
    def hurt(self, p, fatal=False): pass

stub = Stub()

def flat():
    L = T.Level(60, T.LEVEL_H); L.ground(0, 60, top=22); L.top = [22]*60
    return L

# --- A. single jump / double jump height
print("== jump height ==")
for label, second_at in [("single", None), ("double", 15), ("double (later)", 20)]:
    L = flat()
    p = T.Player(40, 22*8 - T.Player.H)
    p.vx = T.RUN_MAX
    top = p.y
    for f in range(1, 200):
        jump = (f <= 15) or (second_at is not None and second_at + 1 <= f <= second_at + 8)
        p.update(L, {"left": False, "right": True, "jump": jump}, stub)
        top = min(top, p.y)
        if p.on_ground and f > 3:
            break
    print(f"  {label:10s} apex {22*8-T.Player.H-top:5.1f} px  ({(22*8-T.Player.H-top)/8:4.1f} tiles)")

# --- B. wall jump: kick up against a pillar
print("== wall-jump climb ==")
L = T.Level(60, T.LEVEL_H)
L.ground(0, 60, top=22)
L.rect(20, 4, 3, 18, T.SOLID)      # a wall running from the ground all the way to the sky
L.top = [22]*60
p = T.Player(17*8, 22*8 - T.Player.H)
start_y = p.y
press = 0
climb = 0
for f in range(1, 400):
    keys = {"left": False, "right": True, "jump": press > 0}
    if press > 0: press -= 1
    if p.on_ground:
        press = 12
    elif p.wall_dir != 0 and p.vy > -2:
        press = 10
    p.update(L, keys, stub)
    climb = min(climb, p.y)
print(f"  start y={start_y:.0f}  apex y={climb:.0f}  climbed {(start_y-climb)/8:.1f} tiles")
