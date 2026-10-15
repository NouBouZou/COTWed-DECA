"""
hash_lot.py -- hash32 de COTW (lookup3 hashlittle2 de Bob Jenkins, deca.hashes) en lot, compile par numba.

Pourquoi : les passes guidees par l'ordre des archives (generer_noms.py, passes greffe et grammaire) essaient des
centaines de millions de noms de la forme prefixe + suffixe. En Python, chaque essai construit une chaine, l'encode
et appelle le hash : environ 1,2 million d'essais par seconde sur le PC de jeu. Ici, un prefixe et une liste de suffixes (tableau
d'octets concatenes + positions) sont hashes dans une seule boucle compilee ; seuls les indices des suffixes dont le
hash est une cible reviennent en Python.

Le calcul est celui de deca.hashes.hashlittle2, reecrit ici en entiers 64 bits masques et sans appels (les
fonctions de deca.hashes ne sont pas inlinees par numba). Mesure sur la machine de developpement : 1,8 million
d'essais par seconde en Python, 4,5 millions ici (recherche des cibles comprise).
Les tests comparent les deux sur des noms reels.
"""
from typing import Iterable, List, Sequence, Tuple

import numpy as np
from numba import njit



class Suffixes:
    """Liste triee de suffixes ASCII, concatenes dans un tableau d'octets (pour la boucle compilee)."""

    def __init__(self, suffixes: Iterable[str]):
        self.textes: List[str] = sorted(set(suffixes))
        data = [s.encode('ascii') for s in self.textes]
        self.offs = np.zeros(len(data) + 1, dtype=np.int64)
        if data:
            self.offs[1:] = np.cumsum([len(d) for d in data])
        self.buf = np.frombuffer(b''.join(data), dtype=np.uint8) if data else np.zeros(0, dtype=np.uint8)
        self.long_max = max((len(d) for d in data), default=0)

    def __len__(self):
        return len(self.textes)


M32 = 0xFFFFFFFF


@njit(cache=True, inline='always')
def _rot(x, k):
    return ((x << k) | (x >> (32 - k))) & M32


@njit(cache=True, inline='always')
def _mot(d, p, n):
    """Mot de 32 bits petit-boutiste des n (1 a 4) octets de d a partir de p."""
    v = np.int64(d[p])
    if n > 1:
        v |= np.int64(d[p + 1]) << 8
    if n > 2:
        v |= np.int64(d[p + 2]) << 16
    if n > 3:
        v |= np.int64(d[p + 3]) << 24
    return v


@njit(cache=True, inline='always')
def lookup3(d, length):
    """hashlittle2(d[:length], 0, 0)[0] de Bob Jenkins (lookup3.c), comme deca.hashes.hash32_func_bytes."""
    a = b = c = (0xdeadbeef + length) & M32
    p = 0
    rest = length
    while rest > 12:
        a = (a + _mot(d, p, 4)) & M32
        b = (b + _mot(d, p + 4, 4)) & M32
        c = (c + _mot(d, p + 8, 4)) & M32
        a = (a - c) & M32; a ^= _rot(c, 4); c = (c + b) & M32
        b = (b - a) & M32; b ^= _rot(a, 6); a = (a + c) & M32
        c = (c - b) & M32; c ^= _rot(b, 8); b = (b + a) & M32
        a = (a - c) & M32; a ^= _rot(c, 16); c = (c + b) & M32
        b = (b - a) & M32; b ^= _rot(a, 19); a = (a + c) & M32
        c = (c - b) & M32; c ^= _rot(b, 4); b = (b + a) & M32
        p += 12
        rest -= 12
    if rest == 0:
        return c
    if rest > 8:
        c = (c + _mot(d, p + 8, rest - 8)) & M32
        b = (b + _mot(d, p + 4, 4)) & M32
        a = (a + _mot(d, p, 4)) & M32
    elif rest > 4:
        b = (b + _mot(d, p + 4, rest - 4)) & M32
        a = (a + _mot(d, p, 4)) & M32
    else:
        a = (a + _mot(d, p, rest)) & M32
    c ^= b; c = (c - _rot(b, 14)) & M32
    a ^= c; a = (a - _rot(c, 11)) & M32
    b ^= a; b = (b - _rot(a, 25)) & M32
    c ^= b; c = (c - _rot(b, 16)) & M32
    a ^= c; a = (a - _rot(c, 4)) & M32
    b ^= a; b = (b - _rot(a, 14)) & M32
    c ^= b; c = (c - _rot(b, 24)) & M32
    return c


@njit(cache=True)
def _touches(prefixe, buf, offs, k0, k1, cibles, filtre, tampon, postfixe):
    """Indices k de [k0, k1) tels que hash32(prefixe + suffixe[k] + postfixe) est dans cibles (uint32 trie). filtre :
    un octet par valeur des 24 bits hauts du hash (1 si une cible les a), pour eviter la recherche dans presque tous
    les cas."""
    n = len(prefixe)
    q = len(postfixe)
    for i in range(n):
        tampon[i] = prefixe[i]
    out = []
    nc = len(cibles)
    for k in range(k0, k1):
        a = offs[k]
        b = offs[k + 1]
        m = b - a
        for j in range(m):
            tampon[n + j] = buf[a + j]
        for j in range(q):
            tampon[n + m + j] = postfixe[j]
        h = lookup3(tampon, n + m + q)
        if filtre[h >> 8] == 0:
            continue
        h = np.uint32(h)
        pos = np.searchsorted(cibles, h)
        if pos < nc and cibles[pos] == h:
            out.append(k)
    return out


class Hacheur:
    """Les hashes cibles, et l'essai en lot d'un prefixe sur une plage de suffixes."""

    def __init__(self, cibles: Iterable[int]):
        self.cibles = np.array(sorted({int(h) & 0xFFFFFFFF for h in cibles}), dtype=np.uint32)
        self.filtre = np.zeros(1 << 24, dtype=np.uint8)
        self.filtre[self.cibles >> 8] = 1
        self._tampon = np.zeros(4096, dtype=np.uint8)

    def touches(self, prefixe: str, suffixes: Suffixes, k0: int = 0, k1: int = -1, postfixe: str = '') -> List[int]:
        """Indices des suffixes (dans [k0, k1)) dont prefixe + suffixe + postfixe a pour hash l'une des cibles."""
        if k1 < 0:
            k1 = len(suffixes)
        if k1 <= k0 or not len(self.cibles):
            return []
        p = np.frombuffer(prefixe.encode('ascii'), dtype=np.uint8)
        q = np.frombuffer(postfixe.encode('ascii'), dtype=np.uint8) if postfixe else np.zeros(0, dtype=np.uint8)
        besoin = len(p) + suffixes.long_max + len(q)
        if besoin > len(self._tampon):
            self._tampon = np.zeros(max(besoin, 2 * len(self._tampon)), dtype=np.uint8)
        return list(_touches(p, suffixes.buf, suffixes.offs, k0, k1, self.cibles, self.filtre,
                             self._tampon, q))


def hash32_lot(noms: Sequence[str]) -> List[int]:
    """hash32 de chaque nom par la boucle compilee (pour les tests)."""
    out = []
    t = np.zeros(4096, dtype=np.uint8)
    for nom in noms:
        d = nom.encode('ascii')
        t[:len(d)] = np.frombuffer(d, dtype=np.uint8)
        out.append(int(lookup3(t, len(d))))
    return out


def plage(suffixes: Suffixes, bas: str, haut: str) -> Tuple[int, int]:
    """[k0, k1) des suffixes s tels que bas < s < haut (ordre des octets ; haut '' = pas de borne haute)."""
    import bisect
    k0 = bisect.bisect_right(suffixes.textes, bas)
    k1 = bisect.bisect_left(suffixes.textes, haut) if haut else len(suffixes.textes)
    return k0, max(k0, k1)
