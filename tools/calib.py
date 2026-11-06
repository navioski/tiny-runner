"""Measure how hold frames map to horizontal jump distance / apex height, so the bot can look up "which tile to jump to"."""
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
import pyxel, tiny_runner as T

pyxel.init(320, 240, fps=60, headless=True, quit_key=pyxel.KEY_Q)
T.bake_art(); T.make_sounds()

class Stub:
    headless = True
    def sfx(self, i): pass
    def burst(self, *a, **k): pass
    def hurt(self, p, fatal=False): pass

lvl = T.Level(80, T.LEVEL_H)
lvl.ground(0, 80, top=22)
stub = Stub()

print(" hold  dist(px)  dist(tiles)  apex(px)")
for hold in range(1, 17):
    p = T.Player(40, 22 * 8 - T.Player.H)
    p.vx = T.RUN_MAX                    # already at full speed when jumping
    best = p.y
    start_x = p.x
    for f in range(1, 200):
        keys = {"left": False, "right": True, "jump": f <= hold}
        p.update(lvl, keys, stub)
        best = min(best, p.y)
        if p.on_ground and f > 3:
            break
    print(f"  {hold:2d}   {p.x - start_x:6.1f}   {(p.x-start_x)/8:6.2f}   {22*8-T.Player.H-best:6.1f}")
