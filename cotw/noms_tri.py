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
    dans un budget par intervalle et par forme. Un nom touche n'est garde que s'il differe d'un nom connu du dossier en
    deux mots au plus, de meme nature (proche()). Prototype : 139 noms, dont 137 que la greffe ne trouve pas.
  - chaines : les chaines connues du projet en forme de nom de base ([a-z0-9_]) + les extensions du dossier.
Les passes hashent en lot (hash_lot.py).
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


def _essayer_lot(ch, stat, prefixe: str, suffixes: 'HL.Suffixes', k0: int, k1: int, fichiers: int, filtre=None):
    """Essaie prefixe + suffixes[k0:k1] ; fichiers : fichiers sans nom de l'intervalle explore (FP attendus) ;
    filtre : condition supplementaire sur un nom dont le hash touche une cible."""
    touches = ch.hacheur.touches(prefixe, suffixes, k0, k1)
    stat.candidats += (k1 - k0) - len(touches)
    stat.fp_guides = (stat.fp_guides or 0.0) + (k1 - k0) * fichiers / float(1 << 32)
    for k in touches:
        nom = prefixe + suffixes.textes[k]
        if filtre is not None and not filtre(nom):
            stat.candidats += 1
            stat.notes['loin des noms connus'] += 1
            continue
        ch.essayer(nom, stat, guide=True)


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
# Chaines
# ---------------------------------------------------------------------------------------------------------------

RX_IDENTIFIANT = re.compile(r'^[a-z0-9_]{3,120}$')


def plage_cle(prefixe: str, bas: str, haut: Optional[str], textes: List[str], ext: str) -> Optional[Tuple[int, int]]:
    """Comme plage_sous, pour les noms prefixe + t + ext. Les textes sont des identifiants [a-z0-9_], dont tous les
    caracteres viennent apres '.' : t -> t + ext garde l'ordre (si t1 est le debut de t2, t1 + ext < t2 + ext car
    '.' < t2[len(t1)]). La recherche dichotomique sur les textes tries reste donc valable."""
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
    cle = (lambda t: t + ext)
    k0 = bisect.bisect_right(textes, b, key=cle)
    k1 = bisect.bisect_left(textes, h, key=cle) if h is not None else len(textes)
    return (k0, k1) if k1 > k0 else None


class Chaines:
    """Passe chaines : les chaines connues du projet (core_strings : chaines des ADF, des RTPC, de l'executable...)
    qui ont la forme d'un nom de fichier sans extension ([a-z0-9_], 3 a 120 caracteres) sont essayees comme nom, avec
    les extensions du dossier, dans chaque intervalle.

    Pourquoi : un fichier sans nom est souvent cite par son seul nom de base (une ligne de dialogue, une ressource
    d'un ADF) ; DECA ne l'essaie que sous la forme d'un chemin complet."""

    def __init__(self, ch, chaines: Iterable[str]):
        self.ch = ch
        ids = set()
        for c in chaines:
            if not c:
                continue
            c = c.strip().lower().replace('\\', '/')
            base = c[c.rfind('/') + 1:]
            dot = base.find('.')
            if dot > 0:
                base = base[:dot]
            if RX_IDENTIFIANT.match(base):
                ids.add(base)
        self.identifiants = HL.Suffixes(ids)
        self.exts_dossier: Dict[str, Set[str]] = collections.defaultdict(set)

    def ajouter(self, noms: Iterable[str]):
        for p in noms:
            d = _dossier(p)
            b = p[len(d):]
            dot = b.find('.')
            if dot > 0 and _ascii(b):
                self._ajouter_ext(d, b[dot:])

    def _ajouter_ext(self, d: str, ext: str):
        self.exts_dossier[d].add(ext)
        i = ext.rfind('.')
        for autre in self.ch.assoc.get(ext[i:], ()):
            self.exts_dossier[d].add(ext[:i] + autre)

    def passe(self):
        def run(stat):
            textes = self.identifiants.textes
            for (bas, haut), n in sorted(intervalles_actifs(self.ch).items()):
                for p in sorted(prefixes(bas, haut)):
                    exts = set(self.exts_dossier.get(p, ()))
                    for voisin in (bas, haut):
                        if _dossier(voisin) == p:
                            b = voisin[len(p):]
                            if b.find('.') > 0:
                                exts.add(b[b.find('.'):])
                    for ext in sorted(exts):
                        if not _ascii(ext):
                            continue
                        r = plage_cle(p, bas, haut, textes, ext)
                        if r is None:
                            continue
                        k0, k1 = r
                        stat.notes['plages'] += 1
                        touches = self.ch.hacheur.touches(p, self.identifiants, k0, k1, ext)
                        stat.candidats += (k1 - k0) - len(touches)
                        stat.fp_guides = (stat.fp_guides or 0.0) + (k1 - k0) * n / float(1 << 32)
                        for k in touches:
                            self.ch.essayer(p + textes[k] + ext, stat, guide=True)
        return run


# ---------------------------------------------------------------------------------------------------------------
# Grammaire
# ---------------------------------------------------------------------------------------------------------------

class Forme:
    """Noms d'un dossier qui ont n mots : vocabulaire de chaque position, et derniers mots + extensions en lot."""

    def __init__(self, n: int, vocab: List[List[str]], derniers: 'HL.Suffixes',
                 connus: Optional[List[Tuple[List[str], str]]] = None):
        self.n = n
        self.vocab = vocab                      # positions 0 .. n-2, triees
        self.ensembles = [set(v) for v in vocab]
        self.derniers = derniers                # dernier mot + extension, tries
        self.connus = connus or []              # (mots, extension) des noms connus de cette forme


def _mots(stem: str) -> List[str]:
    return stem.split('_')


def apprendre(basenames: Iterable[str], largeur: int = GRAMMAIRE_LARGEUR) -> List[Forme]:
    """Formes des noms d'un dossier. Un mot lettres + chiffres ("t1", "05", "mm1150") ne prend toutes les valeurs de sa
    largeur que si sa position est une serie : au moins deux valeurs vues pour ces lettres et cette largeur. Sinon il
    reste tel quel (prototype : developper chaque nombre vu une seule fois multipliait les candidats et ne donnait
    que des faux positifs du genre "trophy_lodge_stand_00_04_43_38")."""
    par_n = collections.defaultdict(lambda: (collections.defaultdict(set), set()))
    connus = collections.defaultdict(list)
    for b in basenames:
        dot = b.find('.')
        if dot <= 0 or not _ascii(b):
            continue
        mots = _mots(b[:dot])
        voc, exts = par_n[len(mots)]
        exts.add(b[dot:])
        connus[len(mots)].append((mots, b[dot:]))
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
        formes.append(Forme(n, listes[:-1], derniers, connus[n]))
    return formes


DISTANCE_MAX = 2


def _compatibles(a: str, b: str) -> bool:
    """Deux mots interchangeables : deux nombres apres les memes lettres, de meme largeur ou sans zero en tete
    ("t1"/"t4", "02"/"17", "72"/"221"), ou deux mots sans chiffres ("dif"/"mpm")."""
    ga, gb = RX_MOT_CHIFFRES.match(a), RX_MOT_CHIFFRES.match(b)
    if ga and gb:
        na, nb = ga.group(2), gb.group(2)
        return ga.group(1) == gb.group(1) and (len(na) == len(nb) or (na[0] != '0' and nb[0] != '0'))
    return ga is None and gb is None and not any(c.isdigit() for c in a + b)


def _meme_extension(a: str, b: str, assoc: Dict[str, List[str]]) -> bool:
    """Memes extensions, ou extensions associees par hp.json sur la derniere ('.ddsc' / '.hmddsc')."""
    if a == b:
        return True
    ia, ib = a.rfind('.'), b.rfind('.')
    return a[:ia] == b[:ib] and b[ib:] in assoc.get(a[ia:], ())


def distance_mots(a: List[str], b: List[str], limite: int) -> int:
    """Distance d'edition entre deux suites de mots : remplacer un mot par un mot compatible, inserer ou retirer un
    mot coutent 1 ; un remplacement incompatible coute 2 (retirer puis inserer). Au-dela de limite : limite + 1."""
    inf = limite + 1
    if abs(len(a) - len(b)) > limite:
        return inf
    prec = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cour = [i] + [inf] * len(b)
        for j in range(1, len(b) + 1):
            x, y = a[i - 1], b[j - 1]
            sub = prec[j - 1] if x == y else (prec[j - 1] + 1 if _compatibles(x, y) else inf)
            cour[j] = min(inf, sub, prec[j] + 1, cour[j - 1] + 1)
        prec = cour
    return min(prec[len(b)], inf)


def proche(nom: str, connus: List[Tuple[List[str], str]], assoc: Dict[str, List[str]],
           distance_max: int = DISTANCE_MAX) -> bool:
    """Le nom est a distance_max au plus d'un nom connu du dossier : distance_mots() sur les mots, plus 1 si
    l'extension differe (sauf extension associee : .ddsc / .hmddsc).

    Pourquoi : les essais de la grammaire sur les dialogues (dossier dense, intervalles de 250 fichiers) ont accepte
    des noms comme mm4100_ale_m_87_832.wavc, recombines de plusieurs noms. Les vrais noms trouves sont a 1 ou 2 d'un
    nom connu : un numero ou un mot change (dia_peru_mission07_221 / _72), un numero insere
    (spruce_foliage_tree_02_mpm / spruce_foliage_tree_mpm). Prototype : 132 sur 139 ; les 7 autres etaient absurdes."""
    b = nom[nom.rfind('/') + 1:]
    dot = b.find('.')
    mots, ext = _mots(b[:dot]), b[dot:]
    for km, ke in connus:
        d = 0 if _meme_extension(ke, ext, assoc) else 1
        if d + distance_mots(km, mots, distance_max - d) <= distance_max:
            return True
    return False


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

    def connus(self, d: str) -> List[Tuple[List[str], str]]:
        """(mots, extension) de tous les noms connus du dossier, toutes formes confondues."""
        return [x for f in self.formes(d) for x in f.connus]

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
                    _essayer_lot(self.ch, stat, p, forme.derniers, k0, k1, fichiers,
                                 lambda nom: proche(nom, self.connus(d), self.ch.assoc))
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
