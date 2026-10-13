"""
noms_tri.py -- passes de generer_noms.py guidees par l'ordre des archives.

Pourquoi (2026-10-13, mesure 10-voisins) : les .arc de COTW sont tries par chemin virtuel (ordre des octets). Un
fichier sans nom a donc un nom strictement compris entre ceux de ses voisins nommes dans l'archive
(voisins_orphelins.bornes). Deux consequences :
  - controle : un candidat dont le hash est celui d'un fichier sans nom mais qui tombe hors de son intervalle est un
    faux positif (generer_noms.Chercheur.essayer le refuse) ;
  - guidage : on n'essaie que des noms qui tombent dans un intervalle. Un candidat au hasard ne peut alors toucher
    que les quelques fichiers de cet intervalle, et les faux positifs attendus deviennent
    somme(candidats de l'intervalle x fichiers de l'intervalle) / 2^32 au lieu de candidats x 5 051 / 2^32.

Passes :
  - greffe : un sous-chemin connu (tout ce qui suit un '/' d'un nom connu, et ses extensions associees) place sous
    un dossier de l'intervalle (dossier du voisin d'avant, du voisin d'apres, et leurs parents jusqu'au dossier commun).
    Prototype du 13/10 : 467 noms, dont 402 textures de camouflage (textures/ui/camo_thumbnails/X -> textures/camo/X) ;
  - grammaire : les noms d'un dossier sont coupes en mots ('_') ; pour chaque nombre de mots, chaque position prend
    les mots vus a cette position ; un mot fait de lettres puis de chiffres prend toutes les valeurs de sa largeur
    (au plus GRAMMAIRE_LARGEUR chiffres) si la position en a une serie (deux valeurs vues au moins). Le produit n'est parcouru que dans l'intervalle (elagage par prefixe) et
    dans un budget par intervalle et par forme. Prototype : 139 noms, dont 137 que la greffe ne trouve pas.
Les deux passes hashent en lot (hash_lot.py).
"""
import bisect
import collections
import posixpath
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import hash_lot as HL
import voisins_orphelins as VO

HAUT_MAX = '\U0010ffff'
PLAGE_MAX = 3_000_000          # suffixes d'une plage au-dela desquels la greffe saute la plage (intervalle trop large)
GREFFE_PROFONDEUR = 2          # composants au plus d'un sous-chemin greffe (un dossier + le nom)
GRAMMAIRE_LARGEUR = 3          # chiffres au plus d'un groupe de chiffres varie par la grammaire
GRAMMAIRE_BUDGET = 2_000_000   # candidats par intervalle, dossier et forme
RX_MOT_CHIFFRES = re.compile(r'^([a-z]*)(\d+)$')


def _dossier(path: str) -> str:
    """'a/b/c.x' -> 'a/b/' ; 'c.x' -> ''."""
    return path[:path.rfind('/') + 1]


def prefixes(bas: str, haut: str) -> Set[str]:
    """Dossiers ou un nom de l'intervalle (bas, haut) peut etre : dossiers de bas et de haut, et leurs parents
    jusqu'au dossier commun inclus ('' si bas et haut n'ont pas de dossier commun)."""
    n = 0
    while n < min(len(bas), len(haut)) and bas[n] == haut[n]:
        n += 1
    commun = _dossier(bas[:n])
    out = {commun}
    for p in (bas, haut):
        d = _dossier(p)
        while len(d) > len(commun):
            out.add(d)
            d = _dossier(d[:-1])
    return out


def plage_sous(prefixe: str, bas: str, haut: Optional[str], textes: List[str]) -> Optional[Tuple[int, int]]:
    """[k0, k1) des textes t (tries) tels que bas < prefixe + t < haut, ou None si aucun ne peut l'etre."""
    if bas.startswith(prefixe):
        b = bas[len(prefixe):]
    elif bas < prefixe:
        b = ''
    else:
        return None
    if haut is None:
        h = None
    elif haut.startswith(prefixe):
        h = haut[len(prefixe):]
    elif haut > prefixe:
        h = None
    else:
        return None
    k0 = bisect.bisect_right(textes, b)
    k1 = bisect.bisect_left(textes, h) if h is not None else len(textes)
    return (k0, k1) if k1 > k0 else None


def intervalles_actifs(ch) -> Dict[Tuple[str, str], int]:
    """Intervalles bornes des deux cotes ou reste un fichier sans nom -> nombre de ces fichiers."""
    out = collections.Counter()
    for h, liste in ch.bornes.items():
        if h in ch.cibles and not ch.trouves.get(h):
            for bas, haut in liste:
                if bas and haut is not None:
                    out[(bas, haut)] += 1
    return out


def _ascii(s: str) -> bool:
    return s.isascii() and '\n' not in s


def _essayer_lot(ch, stat, prefixe: str, suffixes: 'HL.Suffixes', k0: int, k1: int, fichiers: int):
    """Essaie prefixe + suffixes[k0:k1] ; fichiers : fichiers sans nom de l'intervalle explore (FP attendus)."""
    touches = ch.hacheur.touches(prefixe, suffixes, k0, k1)
    stat.candidats += (k1 - k0) - len(touches)
    stat.fp_guides = (stat.fp_guides or 0.0) + (k1 - k0) * fichiers / float(1 << 32)
    for k in touches:
        ch.essayer(prefixe + suffixes.textes[k], stat, guide=True)


# ---------------------------------------------------------------------------------------------------------------
# Greffe
# ---------------------------------------------------------------------------------------------------------------

class Greffe:
    """Passe greffe, incrementale : chaque appel n'essaie que les sous-chemins pas encore essayes."""

    def __init__(self, ch, plage_max: int = PLAGE_MAX):
        self.ch = ch
        self.vus: Set[str] = set()
        self.plage_max = plage_max

    def sous_chemins(self, noms: Iterable[str]) -> Set[str]:
        """Sous-chemins de GREFFE_PROFONDEUR composants au plus (prototype : tous les noms trouves en ont 1 ou 2 ; les
        plus longs ne donnaient que des faux positifs), avec leurs extensions associees."""
        out = set()
        for p in noms:
            if not _ascii(p):
                continue
            parts = p.split('/')
            for k in range(1, min(GREFFE_PROFONDEUR, len(parts) - 1) + 1):
                s = '/'.join(parts[-k:])
                out.add(s)
                dot = s.rfind('.')
                if dot > s.rfind('/') + 1:
                    for autre in self.ch.assoc.get(s[dot:], ()):
                        out.add(s[:dot] + autre)
        return out - self.vus

    def passe(self, noms: Iterable[str]):
        def run(stat):
            nouveaux = self.sous_chemins(noms)
            self.vus |= nouveaux
            suffixes = HL.Suffixes(nouveaux)
            if not len(suffixes):
                return
            for (bas, haut), n in sorted(intervalles_actifs(self.ch).items()):
                for p in sorted(prefixes(bas, haut)):
                    r = plage_sous(p, bas, haut, suffixes.textes)
                    if r is None:
                        continue
                    if r[1] - r[0] > self.plage_max:
                        stat.notes['plages trop larges'] += 1
                        continue
                    stat.notes['plages'] += 1
                    _essayer_lot(self.ch, stat, p, suffixes, r[0], r[1], n)
        return run


# ---------------------------------------------------------------------------------------------------------------
# Grammaire
# ---------------------------------------------------------------------------------------------------------------

class Forme:
    """Noms d'un dossier qui ont n mots : vocabulaire de chaque position, et derniers mots + extensions en lot."""

    def __init__(self, n: int, vocab: List[List[str]], derniers: 'HL.Suffixes'):
        self.n = n
        self.vocab = vocab                      # positions 0 .. n-2, triees
        self.ensembles = [set(v) for v in vocab]
        self.derniers = derniers                # dernier mot + extension, tries


def _mots(stem: str) -> List[str]:
    return stem.split('_')


def apprendre(basenames: Iterable[str], largeur: int = GRAMMAIRE_LARGEUR) -> List[Forme]:
    """Formes des noms d'un dossier. Un mot lettres + chiffres ("t1", "05", "mm1150") ne prend toutes les valeurs de sa
    largeur que si sa position est une serie : au moins deux valeurs vues pour ces lettres et cette largeur. Sinon il
    reste tel quel (prototype : developper chaque nombre vu une seule fois multipliait les candidats et ne donnait
    que des faux positifs du genre "trophy_lodge_stand_00_04_43_38")."""
    par_n = collections.defaultdict(lambda: (collections.defaultdict(set), set()))
    for b in basenames:
        dot = b.find('.')
        if dot <= 0 or not _ascii(b):
            continue
        mots = _mots(b[:dot])
        voc, exts = par_n[len(mots)]
        exts.add(b[dot:])
        for i, m in enumerate(mots):
            voc[i].add(m)
    formes = []
    for n, (voc, exts) in sorted(par_n.items()):
        listes = []
        for i in range(n):
            series = collections.defaultdict(set)
            for m in voc[i]:
                g = RX_MOT_CHIFFRES.match(m)
                if g and len(g.group(2)) <= largeur:
                    series[(g.group(1), len(g.group(2)))].add(m)
            mots = set(voc[i])
            for (lettres, w), vus in series.items():
                if len(vus) >= 2:
                    mots.update(lettres + str(v).zfill(w) for v in range(10 ** w))
            listes.append(sorted(mots))
        derniers = HL.Suffixes(m + e for m in listes[-1] for e in exts)
        formes.append(Forme(n, listes[:-1], derniers))
    return formes


class Grammaire:
    """Passe grammaire : formes apprises par dossier sur les noms connus, explorees intervalle par intervalle."""

    def __init__(self, ch, largeur: int = GRAMMAIRE_LARGEUR, budget: int = GRAMMAIRE_BUDGET):
        self.ch = ch
        self.largeur = largeur
        self.budget = budget
        self.par_dossier: Dict[str, Set[str]] = collections.defaultdict(set)
        self._formes: Dict[str, List[Forme]] = {}

    def ajouter(self, noms: Iterable[str]) -> Set[str]:
        """Ajoute des noms connus ; renvoie les dossiers modifies (leurs formes seront reapprises)."""
        modifies = set()
        for p in noms:
            d = _dossier(p)
            b = p[len(d):]
            if b not in self.par_dossier[d]:
                self.par_dossier[d].add(b)
                modifies.add(d)
                self._formes.pop(d, None)
        return modifies

    def formes(self, d: str) -> List[Forme]:
        f = self._formes.get(d)
        if f is None:
            f = self._formes[d] = apprendre(self.par_dossier.get(d, ()), self.largeur)
        return f

    def passe(self, dossiers: Optional[Set[str]] = None):
        """dossiers : None = tous ; sinon seulement les intervalles dont un voisin est dans l'un d'eux."""
        def run(stat):
            for (bas, haut), n in sorted(intervalles_actifs(self.ch).items()):
                for d in sorted({_dossier(bas), _dossier(haut)}):
                    if not d or (dossiers is not None and d not in dossiers):
                        continue
                    for forme in self.formes(d):
                        reste = self._explorer(stat, forme, d, bas, haut, n)
                        if reste <= 0:
                            stat.notes['budget epuise'] += 1
        return run

    def _explorer(self, stat, forme: Forme, d: str, bas: str, haut: str, fichiers: int) -> int:
        budget = [self.budget]

        def rec(i: int, prefixe: str):
            if budget[0] <= 0:
                return
            if i == forme.n - 1:
                p = prefixe if i == 0 else prefixe + '_'
                r = plage_sous(p, bas, haut, forme.derniers.textes)
                if r is not None:
                    k0, k1 = r[0], min(r[1], r[0] + budget[0])
                    budget[0] -= k1 - k0
                    _essayer_lot(self.ch, stat, p, forme.derniers, k0, k1, fichiers)
                return
            p = prefixe if i == 0 else prefixe + '_'
            lst, ens = forme.vocab[i], forme.ensembles[i]
            k0, k1, avant = 0, len(lst), []
            if bas.startswith(p):
                reste = bas[len(p):]
                k0 = bisect.bisect_left(lst, reste)
                # mots plus petits que le reste de bas mais qui en sont le debut : le nom peut encore etre > bas
                avant = [reste[:k] for k in range(1, len(reste) + 1) if reste[:k] in ens and reste[:k] < reste]
            elif p < bas:
                return
            if haut.startswith(p):
                k1 = bisect.bisect_right(lst, haut[len(p):])
            elif p > haut:
                return
            for m in avant:
                rec(i + 1, p + m)
            for m in lst[k0:k1]:
                if budget[0] <= 0:
                    return
                rec(i + 1, p + m)

        rec(0, d)
        return budget[0]
