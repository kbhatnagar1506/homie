"""Find where the mouth and eyes are in a figurine set by diffing its frames, so only those regions animate.

    python -m scripts.figurine_regions fix
"""

import json
import sys

import numpy as np
from PIL import Image, ImageFilter

from homie.config import ROOT


def box(diff: np.ndarray, thresh: float) -> tuple[float, float, float, float]:
    ys, xs = np.where(diff > thresh)
    h, w = diff.shape
    # keep the dense core: ignore stray far-away pixels
    x0, x1 = np.percentile(xs, [3, 97]); y0, y1 = np.percentile(ys, [3, 97])
    return x0 / w, y0 / h, x1 / w, y1 / h


def main(role: str):
    folder = ROOT / "hub" / "static" / "figurines" / role
    load = lambda n: np.asarray(Image.open(folder / f"{n}.jpg").convert("L").filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)
    rest = load("rest")
    mouth = np.max([np.abs(load(n) - rest) for n in ("aa", "E", "O", "U")], axis=0)
    eyes = np.abs(load("blink") - rest)
    e = box(eyes, 28)
    mouth[: int((e[3] + 0.02) * mouth.shape[0])] = 0  # the mouth lives below the eyes (ignore eyebrow lifts)
    m = box(mouth, 28)
    pad = lambda b, px, py: (max(0, b[0] - px), max(0, b[1] - py), min(1, b[2] + px), min(1, b[3] + py))
    regions = {"mouth": pad(m, 0.06, 0.04), "eyes": pad(e, 0.05, 0.05)}
    (folder / "regions.json").write_text(json.dumps({k: [round(v, 4) for v in b] for k, b in regions.items()}))
    print({k: [round(float(v), 3) for v in b] for k, b in regions.items()})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "fix")
