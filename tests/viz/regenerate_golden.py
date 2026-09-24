"""Rewrite tests/fixtures/overlays_golden.png from the current code.

Deliberate, and never a response to a failing test: the golden records what an
operator sees on a recorded clip, so a change to it is a change worth reading
in the diff. Run as `python -m tests.viz.regenerate_golden` from the repository
root, and look at the image before committing it.
"""

import cv2

from micropick.viz import overlays

from .scene import golden_path, load_frame, scene

if __name__ == "__main__":
    out = overlays.annotate(load_frame(), **scene())
    cv2.imwrite(str(golden_path()), out)
    print(f"wrote {golden_path()}  {out.shape[1]}x{out.shape[0]}")
