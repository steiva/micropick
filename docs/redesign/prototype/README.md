# Prototype screens (reference only)

Exported from the design canvas. These files are not runnable here: they
need the canvas runtime (`support.js`). Read them as source:

- the markup and inline styles give exact layout, sizes, colors and copy;
- the `renderVals()` script at the bottom of each file shows which states
  a screen has and how they change (tweak props such as `situation` list
  the states the designers reviewed).

| File | Screen | Spec section |
|---|---|---|
| Main.dc.html | Home | 4 |
| Profile.dc.html | Robot setup + all sheets (camera, calibration, platforms, positions), Reset | 5 |
| AutoDeck.dc.html | Deck (shared step) | 6.1 |
| TipScreen.dc.html | Tip + calibration (shared step) | 6.2 |
| AutoPlan.dc.html | Auto plan | 6.3 |
| AutoBottom.dc.html | Dish bottom (shared step) | 6.4 |
| AutoPicking.dc.html | Picking (tweak `situation`: ready, running, paused, waiting, done, no tip) | 6.5 |
| LiquidCenter.dc.html | Well center | 6.7 |
| LiquidPlan.dc.html | Liquid plan | `LIQUID.md` §3, §5 |
| LiquidRun.dc.html | Liquid run | `LIQUID.md` §6 |

Where a file and REDESIGN.md disagree, REDESIGN.md wins.
