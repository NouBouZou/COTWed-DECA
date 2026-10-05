"""
noms.py -- noms en clair des hashes, pour l'affichage (visionneuses de l'interface, sauvegarde).

Sources, dans l'ordre :
  1. les noms enregistres par l'appelant (register : noms d'objets d'equipment_data, d'un mod...) ;
  2. cotw/dictionnaires/hp_noms.tsv (noms retrouves par resoudre_hashes.py, dont compatibility_0..67) ;
  3. la table core_strings du projet DECA (chaines relevees au scan, dictionnaires de DECA).

Les hash32 (Jenkins hashlittle2) sont ceux des noms de proprietes et de noeuds RTPC, des cles de traduction et
des noms d'objets. Les StringHash de 48 et 64 bits des ADF ne sont cherches que dans le dictionnaire.

Pourquoi un cache avec prefetch : une visionneuse affiche des centaines de valeurs ; une requete SQL par valeur
serait lente. prefetch() resout un lot d'un coup (core_strings par paquets de 500), get() lit ensuite le cache.
"""
from typing import Dict, Iterable, List, Optional

import _projet
from deca.hashes import hash32_func

MASK32 = 0xFFFFFFFF


def hash32(text) -> int:
    return int(hash32_func(text.encode('utf-8') if isinstance(text, str) else bytes(text)))


class Noms:
    def __init__(self, vfs=None, use_dictionary: bool = True):
        self.vfs = vfs
        self.use_dictionary = use_dictionary
        self._cache: Dict[int, List[str]] = {}
        self._registered: Dict[int, List[str]] = {}

    def register(self, names: Iterable[str]):
        """Noms connus de l'appelant (prioritaires) : leur hash32 est calcule ici."""
        for name in names:
            if not name:
                continue
            h = hash32(name)
            lst = self._registered.setdefault(h, [])
            if name not in lst:
                lst.append(name)
            self._cache.pop(h, None)   # recalcule avec ce nom en tete

    def prefetch(self, hashes: Iterable[int]):
        unknown = sorted({h & MASK32 for h in hashes
                          if isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= MASK32} - set(self._cache))
        if not unknown:
            return
        if self.vfs is not None:
            found = _projet.resolve_hash32(self.vfs, unknown, use_dictionary=self.use_dictionary)
        elif self.use_dictionary:
            dictionary = _projet.load_dictionary()
            found = {h: list(dictionary.get((32, h), [])) for h in unknown}
        else:
            found = {}
        for h in unknown:
            names = list(self._registered.get(h, []))
            names += [n for n in found.get(h, []) if n not in names]
            self._cache[h] = names

    def all(self, h: int) -> List[str]:
        if not isinstance(h, int) or isinstance(h, bool) or not 0 <= h <= MASK32:
            return []
        self.prefetch([h])
        return self._cache.get(h, [])

    def get(self, h: int) -> Optional[str]:
        names = self.all(h)
        return names[0] if names else None

    def label(self, h: int) -> str:
        """Nom, ou 0x%08x s'il est inconnu."""
        name = self.get(h)
        return name if name is not None else '0x{:08x}'.format(h & MASK32)

    def table(self) -> Dict[int, List[str]]:
        """Le cache (hash -> noms), au format attendu par rtpc_dump.format_value : faire prefetch avant."""
        return self._cache

    def wide(self, value: int, width: int) -> Optional[str]:
        """StringHash de 48 ou 64 bits : nom du dictionnaire du fork, s'il y en a un."""
        if width == 32:
            return self.get(value)
        names = _projet.load_dictionary().get((width, value), []) if self.use_dictionary else []
        return names[0] if names else None
