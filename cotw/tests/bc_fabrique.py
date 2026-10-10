"""
Fabrique de niveaux de texture sans en-tete (comme les .hmddsc de COTW) pour les tests de deca.ff_bc_raw : aucune
texture du jeu n'est versionnee.

Images synthetiques lisses (degrades et taches), encodees par l'encodeur BC3 de cotw/icone.py : BC3 tel quel, BC1 =
moitie couleur des blocs BC3 (couleur0 > couleur1 : le mode 4 couleurs de BC1), BC5 = blocs alpha de BC3 calcules
sur les canaux rouge puis vert (un bloc BC4 a la meme disposition qu'un bloc alpha de BC3).
Comme dans un .hmddsc, le plus petit niveau vient en premier, le plus grand en dernier.
"""
import sys
from pathlib import Path
from typing import List

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import icone as I  # noqa: E402


def image(w: int, h: int, seed: int = 0) -> np.ndarray:
    """RGBA lisse : degrades et taches gaussiennes."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    out = np.zeros((h, w, 4), dtype=np.float32)
    for c in range(3):
        v = 128 + 60 * np.sin(x / w * (2 + c) * np.pi + seed) * np.cos(y / h * (3 - c) * np.pi)
        for _ in range(6):
            cx, cy, r, a = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(w / 16, w / 5), rng.uniform(-60, 60)
            v += a * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * r * r))
        out[:, :, c] = v
    out[:, :, 3] = 255
    return np.clip(out, 0, 255).astype(np.uint8)


def _levels(rgba: np.ndarray, n: int) -> List[np.ndarray]:
    h, w = rgba.shape[:2]
    return I.niveaux(rgba, [(max(1, w >> i), max(1, h >> i)) for i in range(n)])


def _bc3(level: np.ndarray) -> np.ndarray:
    return np.frombuffer(I.encoder_bc3(level), dtype=np.uint8).reshape(-1, 16)


def bc3_level(level: np.ndarray) -> bytes:
    return _bc3(level).tobytes()


def bc1_level(level: np.ndarray) -> bytes:
    return _bc3(level)[:, 8:].tobytes()


def bc5_level(level: np.ndarray) -> bytes:
    red, green = level.copy(), level.copy()
    red[:, :, 3] = level[:, :, 0]
    green[:, :, 3] = level[:, :, 1]
    return np.concatenate([_bc3(red)[:, :8], _bc3(green)[:, :8]], axis=1).tobytes()


ENCODERS = {'BC1': bc1_level, 'BC3': bc3_level, 'BC5': bc5_level}


def hmddsc(fmt: str, w: int, h: int, levels: int, seed: int = 0) -> bytes:
    """Niveaux 0..levels-1 d'une image w x h au format fmt, du plus petit au plus grand."""
    encode = ENCODERS[fmt]
    return b''.join(encode(lv) for lv in reversed(_levels(image(w, h, seed), levels)))
