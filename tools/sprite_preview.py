"""Render the ASCII art in tiny_runner at a large scale into a single image, so pixel tweaking can be done by eye."""
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
import sys, pyxel
import tiny_runner as T

SCALE = 5
pyxel.init(320, 240, fps=60, quit_key=pyxel.KEY_Q, headless=True)
T.bake_art()

names = [n for n in T.SPR if not n.endswith("_l")]
pyxel.cls(1)                      # dark background, so the white edges and lighter colors actually show
x, y, rowh = 6, 6, 0
for n in names:
    u, v, w, h = T.SPR[n]
    if x + w * SCALE > 314:
        x, y = 6, y + rowh + 8
        rowh = 0
    pyxel.blt(x, y, 0, u, v, w, h, 0, 0, SCALE)     # palette index 0 is transparent
    x += w * SCALE + 10
    rowh = max(rowh, h * SCALE)
pyxel.screenshot(f"{sys.argv[1] if len(sys.argv) > 1 else 'sprite_sheet.png'}", 2)
print("rendered", len(names), "sprites")
