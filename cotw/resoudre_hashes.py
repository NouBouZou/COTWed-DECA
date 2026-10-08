#!/usr/bin/env python3
"""
resoudre_hashes.py -- retrouve le nom en clair des hashes inconnus de COTW.

Cibles : les noms des proprietes et des enfants RTPC (hash32), et les valeurs StringHash des ADF
(32, 48 ou 64 bits), que ni DECA ni le dictionnaire du fork ne savent nommer.

Principe : un nom de champ du jeu vient presque toujours d'un identifiant du code (m_IsPartOfDLC ->
is_part_of_dlc), ou combine des mots de ce vocabulaire (cash + price). On fabrique des candidats a partir
des chaines de l'exe, on calcule leur hash, et on garde ceux qui tombent sur un hash cherche. Passes :
  directs  : chaque identifiant et ses variantes (minuscules, snake_case, sans prefixe m_, C, Set, Get...) ;
  affixes  : ces formes avec un prefixe (is_, has_...) ou un suffixe (_id, _list...) courant ;
  positionnels : un mot suivi d'un index, mot_0 a mot_127 (aussi mot_00 et mot0) : c'est ainsi que le jeu
             nomme les entrees de ses listes (compatibility_0, compatibility_1...) ;
  paires   : deux mots parmi les plus frequents, joints par "_" ;
  triplets : trois mots pris dans un vocabulaire plus court.

Faux positifs : un candidat quelconque tombe par hasard sur l'un des T hashes 32 bits cherches avec une
probabilite de T / 2^32. Pour N candidats, on attend donc N x T / 2^32 coincidences. Chaque passe affiche ce
nombre : si elle trouve beaucoup plus de noms, ses trouvailles sont presque toutes vraies. Par defaut,
seules les passes ou ce nombre est sous --seuil-fp alimentent le dictionnaire (cotw/dictionnaires/hp_noms.tsv).
Le mode --aveugle mesure la precision reelle : il cherche aussi les hashes deja nommes par DECA et compare.

Mode --cibles (2026-10-08) : les cibles sont les hashes inconnus releves dans TOUS les RTPC et ADF par
audit_noms_internes.py (inconnus.csv), sans relire les fichiers. Les categories sont decoupees en classes par
nombre d'occurrences, une passe supplementaire essaie verbe_objet (show_clues...), et un nom est retenu par
passe et par classe : en bloc si les faux positifs attendus restent faibles, sinon seulement s'il est
corrobore par un autre nom de meme racine (frame_0 ... frame_98). --temoin mesure les faux positifs reels.
Puis, si inconnus.csv a la colonne familles (audit du 2026-10-08 soir), des passes par famille de fichiers
(familles.py) : le vocabulaire d'une famille (noms_par_famille.tsv, vocabulaire_familles.txt) contre ses seules
cibles, ce qui rend les paires et les triplets surs sur les petites familles (trophy_feature_data.bin).

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --out D:\\dev\\audits\\noms.md
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --aveugle --out D:\\dev\\audits\\noms_aveugle.md
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --motif "settings/hp_settings/%.bin" --out ...
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --cibles D:\\dev\\audits\\02-noms-internes\\inconnus.csv ^
        --out D:\\dev\\audits\\03-noms-internes\\resoudre.md
        (cibles : les hashes inconnus releves par audit_noms_internes.py dans tous les RTPC, par categorie)
    python cotw\\resoudre_hashes.py injecter D:\\dev\\work\\hp   (noms du dictionnaire -> interface DECA)
    python cotw\\resoudre_hashes.py annuler D:\\dev\\work\\hp    (retire exactement ce qui a ete injecte)
"""
import argparse
import collections
import datetime
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import familles as FA  # noqa: E402
import rtpc_dump as D  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_none  # noqa: E402
from deca.hashes import hash32_func_bytes  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GHIDRA_HP = REPO / 'resources' / 'deca' / 'ghidra_strings' / 'hp.txt'
VOCABULARY = HERE / 'dictionnaires' / 'vocabulaire_cotw.txt'
INJECTION_LOG = 'cotw_injections.tsv'

DEFAULT_FILES = [
    'settings/hp_settings/equipment_data.bin',
    'settings/hp_settings/equipment_tuning.bin',
    'settings/hp_settings/equipment_stats_ui.bin',
    'settings/spawn_ammunition_defs.bin',
    'settings/spawn_weapon_defs.bin',
]
MAX_CONTEXTS = 3
WIDTH_BY_SIZE = {4: 32, 6: 48, 8: 64}
MASK = {32: 0xFFFFFFFF, 48: 0xFFFFFFFFFFFF, 64: 0xFFFFFFFFFFFFFFFF}

VERB_PREFIXES = {'get', 'set', 'is', 'has', 'can', 'on', 'update', 'show', 'hide', 'request', 'add', 'remove',
                 'enable', 'disable', 'try', 'do', 'should'}
AFFIX_PREFIXES = ['is_', 'has_', 'can_', 'num_', 'max_', 'min_', 'default_', 'base_', 'show_', 'hide_', 'use_']
AFFIX_SUFFIXES = ['_id', '_ids', '_list', '_name', '_names', '_hash', '_hashes', '_count', '_type', '_index',
                  '_data', '_value', '_key', '_flag', '_enabled', 's']


# ---------------------------------------------------------------------------------------------------------
# Cibles : hashes rencontres dans les fichiers, avec leur contexte
# ---------------------------------------------------------------------------------------------------------

@dataclass
class Target:
    width: int
    value: int
    kinds: Set[str] = field(default_factory=set)
    count: int = 0
    contexts: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[int, int]:
        return self.width, self.value


class Targets:
    def __init__(self):
        self.items: Dict[Tuple[int, int], Target] = {}

    def add(self, width: int, value: int, kind: str, context: str):
        value &= MASK[width]
        if value == 0:
            return
        t = self.items.get((width, value))
        if t is None:
            t = self.items[(width, value)] = Target(width, value)
        t.kinds.add(kind)
        t.count += 1
        if len(t.contexts) < MAX_CONTEXTS:
            t.contexts.append(context)

    def by_width(self, width: int) -> Set[int]:
        return {v for (w, v) in self.items if w == width}

    def __len__(self):
        return len(self.items)


def short(text: str, limit: int = 60) -> str:
    text = text.replace('\n', ' ').replace('\t', ' ')
    return text if len(text) <= limit else text[:limit - 3] + '...'


def collect_rtpc(tree, label: str, targets: Targets, with_children: bool = True):
    """Noms des proprietes (hors 'none') et des enfants de tous les noeuds."""
    def walk(node, path):
        for p in node.prop_table:
            if p.type == k_type_none or p.name_hash == 0:
                continue
            targets.add(32, p.name_hash, 'propriete', '{} {} : propriete {} = {}'.format(
                label, path, D.type_name(p.type), short(D.format_value(p, {}))))
        for i, c in enumerate(node.child_table):
            child_path = '{}.{}'.format(path, i)
            if with_children:
                targets.add(32, c.name_hash, 'enfant', '{} {} : enfant ({} prop., {} enfants)'.format(
                    label, child_path, len(c.prop_table), len(c.child_table)))
            walk(c, child_path)

    if with_children:
        targets.add(32, tree.root_node.name_hash, 'enfant', '{} 0 : racine'.format(label))
    walk(tree.root_node, '0')


def collect_adf(adf, label: str, targets: Targets):
    """Valeurs StringHash que la table de chaines du fichier ne nomme pas."""
    from deca.ff_adf import AdfValue, MetaType
    type_map = adf.extended_map_typedef

    def walk(v, path):
        if isinstance(v, AdfValue):
            td = type_map.get(v.type_id)
            if v.type_id == 0xdefe88ed:
                walk(v.value, path)
            elif td is not None and td.metatype == MetaType.StringHash:
                width = WIDTH_BY_SIZE.get(td.size)
                if width and v.hash_string is None and isinstance(v.value, int):
                    targets.add(width, v.value, 'stringhash', '{} {}'.format(label, path))
            else:
                walk(v.value, path)
        elif isinstance(v, dict):
            for k, iv in v.items():
                walk(iv, '{}.{}'.format(path, _projet.to_text(k)))
        elif isinstance(v, list):
            for i, iv in enumerate(v):
                walk(iv, '{}[{}]'.format(path, i))

    for ins, fv in zip(adf.table_instance, adf.table_instance_full_values):
        walk(fv, _projet.to_text(ins.name) or 'instance')


# ---------------------------------------------------------------------------------------------------------
# Lecture des fichiers
# ---------------------------------------------------------------------------------------------------------

class FileReader:
    """Lit un fichier du jeu (chemin virtuel) ou un fichier local, et le decode en RTPC ou ADF."""

    def __init__(self, vfs):
        self.vfs = vfs
        self._adf_db = None

    @property
    def adf_db(self):
        if self._adf_db is None:
            from deca.ff_adf import AdfDatabase
            self._adf_db = AdfDatabase(self.vfs)
        return self._adf_db

    def decode(self, name: str) -> Tuple[str, object]:
        """('rtpc', arbre) | ('adf', adf) | ('autre', raison)."""
        local = Path(name)
        if local.is_file():
            data = local.read_bytes()
            if data[:4] == W.RTPC_MAGIC:
                return 'rtpc', W.rtpc_from_bytes(data)
            if data[:4] == b' FDA':
                adf = self.adf_db._load_adf(data)
                return ('adf', adf) if adf is not None else ('autre', 'ADF illisible')
            return 'autre', 'format non pris en charge (magic {})'.format(data[:4].hex(' '))
        nodes = [n for n in _projet.nodes_by_path(self.vfs, name) if n.offset is not None]
        if not nodes:
            return 'autre', 'absent du projet'
        node = nodes[0]
        data = _projet.read_node(self.vfs, node)
        if data[:4] == W.RTPC_MAGIC:
            return 'rtpc', W.rtpc_from_bytes(data)
        from deca.ff_types import ftype_adf_family
        if node.file_type in ftype_adf_family or data[:4] == b' FDA':
            try:
                adf = self.adf_db.read_node(self.vfs, node)
            except Exception as e:  # type ADF absent de la base, fichier tronque...
                return 'autre', 'ADF illisible ({})'.format(type(e).__name__)
            return ('adf', adf) if adf is not None else ('autre', 'ADF illisible')
        return 'autre', 'format non pris en charge ({})'.format(node.file_type or 'sans type')


def select_files(vfs, files: List[str], patterns: List[str]) -> List[str]:
    chosen = list(files)
    for pattern in patterns:
        rows = vfs.db_query_all('SELECT DISTINCT v_path FROM core_nodes WHERE v_path LIKE ? ORDER BY v_path',
                                [pattern.replace('\\', '/')], dbg='cotw_resolve_select')
        chosen += [_projet.to_text(r[0]) for r in rows]
    if not chosen:
        chosen = list(DEFAULT_FILES)
    seen, result = set(), []
    for f in chosen:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


# ---------------------------------------------------------------------------------------------------------
# Sources de candidats
# ---------------------------------------------------------------------------------------------------------

IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{1,79}')
BINARY_IDENT_RE = re.compile(rb'(?<=\x00)[A-Za-z_][A-Za-z0-9_]{2,79}(?=\x00)')


def identifiers_from_lines(lines: Iterable[str]) -> Set[str]:
    out = set()
    for line in lines:
        s = line.strip()
        if IDENT_RE.fullmatch(s):
            out.add(s)
        else:  # chaine composee (chemin, phrase) : on garde ses identifiants
            out.update(m.group(0) for m in IDENT_RE.finditer(s) if len(m.group(0)) >= 3)
    return out


def identifiers_from_binary(data: bytes) -> Set[str]:
    """Identifiants ASCII termines par un octet nul, comme les chaines des sections de donnees d'un exe."""
    return {m.group(0).decode('ascii') for m in BINARY_IDENT_RE.finditer(data)}


def source_ghidra(path: Path = GHIDRA_HP) -> Set[str]:
    if not path.is_file():
        return set()
    return identifiers_from_lines(path.read_text(encoding='utf-8', errors='replace').splitlines())


def exe_paths(vfs) -> List[str]:
    rows = vfs.db_query_all("SELECT DISTINCT p_path FROM core_nodes WHERE file_type = 'exe'", [],
                            dbg='cotw_resolve_exe')
    return [_projet.to_text(r[0]) for r in rows if r[0]]


def source_exe(vfs) -> Set[str]:
    out = set()
    for p in exe_paths(vfs):
        if Path(p).is_file():
            out |= identifiers_from_binary(Path(p).read_bytes())
    return out


def source_adf_types(vfs) -> Set[str]:
    """Noms des types ADF connus de DECA (exe et fichiers) et de leurs membres."""
    from deca.ff_adf import AdfDatabase
    out = set()
    for td in AdfDatabase(vfs).type_map_def.values():
        for name in [td.name] + [m.name for m in (td.members or []) if getattr(m, 'name', None) is not None]:
            text = _projet.to_text(name)
            if text:
                out |= identifiers_from_lines([text])
    return out


def load_word_list(path: Path) -> List[str]:
    if not path.is_file():
        return []
    words = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            words.append(line)
    return words


# ---------------------------------------------------------------------------------------------------------
# Variantes et vocabulaire
# ---------------------------------------------------------------------------------------------------------

def snake(s: str) -> str:
    """IsPartOfDLC -> is_part_of_dlc, m_AmmoType -> ammo_type, DLCOwnership -> dlc_ownership."""
    s = re.sub(r'^(m_|s_|k_|g_|p_)', '', s)
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    s = re.sub(r'_+', '_', s).strip('_')
    return s.lower()


def variants(ident: str) -> Set[str]:
    out = {ident, ident.lower()}
    core = re.sub(r'^(m_|s_|k_|g_|p_)', '', ident)
    core = re.sub(r'^[CSEI](?=[A-Z][a-z])', '', core)  # CAmmunitionItem, SInventory, EState, IThing
    sn = snake(core)
    out |= {core, core.lower(), sn, sn.replace('_', '')}
    # notation hongroise du C++ : m_bIsLocked, bReceived, m_fWeight, m_nCount, m_szName
    hungarian = re.sub(r'^(b|f|n|i|u|sz|p|e)(?=[A-Z])', '', core)
    if hungarian != core:
        out.add(snake(hungarian))
    parts = sn.split('_')
    if len(parts) > 1 and parts[0] in VERB_PREFIXES:
        out.add('_'.join(parts[1:]))
    return {v for v in out if v}


def tokens_of(identifiers: Iterable[str]) -> collections.Counter:
    counter = collections.Counter()
    for ident in identifiers:
        for t in snake(ident).split('_'):
            if 2 <= len(t) <= 20 and t.isalpha():
                counter[t] += 1
    return counter


def build_vocab(counter: collections.Counter, size: int, extra: List[str]) -> List[str]:
    vocab = [t for t, _ in counter.most_common(size)]
    seen = set(vocab)
    for w in extra:
        if w not in seen:
            seen.add(w)
            vocab.append(w)
    return vocab


# ---------------------------------------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------------------------------------

@dataclass
class PassStats:
    name: str
    candidates: int
    expected_false: float
    seconds: float
    found: int = 0
    written: int = 0


class Searcher:
    """Calcule le hash de chaque candidat et note ceux qui tombent sur une cible."""

    def __init__(self, targets: Targets):
        self.t32 = targets.by_width(32)
        self.t48 = targets.by_width(48)
        self.t64 = targets.by_width(64)
        self.hits: Dict[Tuple[int, int], Dict[str, str]] = {}  # cle -> {nom: passe}
        self.stats: List[PassStats] = []

    def rate(self) -> float:
        return len(self.t32) / 2 ** 32 + len(self.t48) / 2 ** 48 + len(self.t64) / 2 ** 64

    def _record(self, key, name, pass_name, new_keys):
        names = self.hits.setdefault(key, {})
        if name not in names:
            names[name] = pass_name
            new_keys.add((key, name))

    def run(self, pass_name: str, candidates: Iterable[str]) -> PassStats:
        import mmh3
        t32, t48, t64 = self.t32, self.t48, self.t64
        wide = bool(t48 or t64)
        n = 0
        new = set()
        t0 = time.time()
        for s in candidates:
            b = s.encode('utf-8')
            n += 1
            h = int(hash32_func_bytes(b))
            if h in t32:
                self._record((32, h), s, pass_name, new)
            if wide:
                v = mmh3.hash128(b, x64arch=True)
                h48 = (v >> 16) & MASK[48]
                h64 = v & MASK[64]
                if h48 in t48:
                    self._record((48, h48), s, pass_name, new)
                if h64 in t64:
                    self._record((64, h64), s, pass_name, new)
        stats = PassStats(pass_name, n, n * self.rate(), time.time() - t0, found=len(new))
        self.stats.append(stats)
        return stats


def gen_direct(identifiers: Iterable[str], words: Iterable[str]) -> Iterator[str]:
    seen = set()
    for ident in identifiers:
        for v in variants(ident):
            if v not in seen:
                seen.add(v)
                yield v
    for w in words:
        for v in (w, w + 's'):
            if v not in seen:
                seen.add(v)
                yield v


def snake_forms(identifiers: Iterable[str]) -> Set[str]:
    out = set()
    for ident in identifiers:
        for v in variants(ident):
            if re.fullmatch(r'[a-z][a-z0-9_]*', v):
                out.add(v)
    return out


def gen_affixes(forms: Iterable[str]) -> Iterator[str]:
    for f in forms:
        for p in AFFIX_PREFIXES:
            yield p + f
        for s in AFFIX_SUFFIXES:
            yield f + s


def gen_positional(vocab: List[str], count: int) -> Iterator[str]:
    for w in vocab:
        for i in range(count):
            yield '{}_{}'.format(w, i)
            if i < 10:
                yield '{}_0{}'.format(w, i)
            yield '{}{}'.format(w, i)


def gen_pairs(vocab: List[str]) -> Iterator[str]:
    for a in vocab:
        prefix = a + '_'
        for b in vocab:
            yield prefix + b


def gen_triples(vocab: List[str]) -> Iterator[str]:
    for a in vocab:
        for b in vocab:
            prefix = a + '_' + b + '_'
            for c in vocab:
                yield prefix + c


# ---------------------------------------------------------------------------------------------------------
# Noms deja connus
# ---------------------------------------------------------------------------------------------------------

def signed64(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def known_names(vfs, targets: Targets, use_dictionary: bool) -> Dict[Tuple[int, int], List[str]]:
    """Noms deja connus de DECA (et du dictionnaire du fork si use_dictionary) pour chaque cible."""
    known: Dict[Tuple[int, int], List[str]] = {}
    for h, names in _projet.resolve_hash32(vfs, targets.by_width(32), use_dictionary=use_dictionary).items():
        known[(32, h)] = names
    dictionary = _projet.load_dictionary() if use_dictionary else {}
    for width in (48, 64):
        for v in targets.by_width(width):
            if width == 48:
                rows = vfs.hash_string_match(hash48=v)
            else:
                rows = vfs.hash_string_match(hash64=signed64(v))
            names = sorted({_projet.to_text(r[1]) for r in rows} | set(dictionary.get((width, v), [])))
            if names:
                known[(width, v)] = names
    return known


# ---------------------------------------------------------------------------------------------------------
# Dictionnaire
# ---------------------------------------------------------------------------------------------------------

def dictionary_lines(entries) -> List[str]:
    lines = []
    for (width, value), name, pass_name, expected, context in entries:
        digits = {32: 8, 48: 12, 64: 16}[width]
        lines.append('0x{:0{}x}\t{}\t{}\t{}\t{:.2g}\t{}'.format(value, digits, width, name, pass_name, expected,
                                                                short(context, 120)))
    return lines


def append_to_dictionary(path: Path, entries) -> list:
    """Ajoute les entrees absentes du dictionnaire ; renvoie celles qui ont ete ecrites."""
    existing = _projet.load_dictionary(path)
    fresh = [e for e in entries if e[1] not in existing.get(e[0], [])]
    if not fresh:
        return []
    text = path.read_text(encoding='utf-8') if path.is_file() else (
        '# Noms retrouves pour des hashes de theHunter: COTW (voir resoudre_hashes.py).\n')
    if not text.endswith('\n'):
        text += '\n'
    text += '\n'.join(dictionary_lines(fresh)) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    _projet.forget_dictionary_cache()
    return fresh


# ---------------------------------------------------------------------------------------------------------
# Cibles tirees d'un audit (audit_noms_internes.py) : recherche par classe, corroboration, temoin
# ---------------------------------------------------------------------------------------------------------
#
# Un nom trouve n'est ecrit que si le nombre de faux positifs attendus parmi les noms ecrits reste faible.
# Trois leviers rendent ce calcul moins pessimiste que "candidats x toutes les cibles / 2^32" :
#   - les classes : chaque categorie est decoupee par nombre d'occurrences. Un candidat quelconque tombe sur
#     l'une des 150 cibles de noeuds vues 1000 fois ou plus avec une probabilite 150 / 2^32, pas 78 909 / 2^32 ;
#     or ces 150 hashes portent 76 % des occurrences de noeuds inconnus (96 % pour les proprietes, 303 cibles) ;
#   - le taux : une passe qui touche 178 cibles quand 0,15 coincidence est attendue est fiable a 99,9 % ;
#     elle est retenue si ses faux positifs attendus restent sous --seuil-fp OU sous --seuil-taux x touchees ;
#   - la corroboration : les noms d'une meme racine se confirment entre eux (frame_0 ... frame_98, show_clues
#     et hide_clues). Deux coincidences sur une meme racine sont tres improbables ; quand une passe est trop
#     bruitee pour etre retenue en bloc, ses noms corrobores peuvent l'etre, avec leur propre estimation.
# Le temoin (--temoin) cherche en meme temps autant de cibles fictives, de memes largeurs, par classe, et leur
# applique les memes decisions : ce qu'il retient est une mesure directe des faux positifs.

STRATES = ((1000, '>=1000'), (100, '100-999'), (10, '10-99'), (2, '2-9'), (1, '1'))
SEP = '|'
TEMOIN = 'temoin:'
VERBES = ['show', 'hide', 'enable', 'disable', 'start', 'stop', 'spawn', 'despawn', 'activate', 'deactivate',
          'open', 'close', 'play', 'pause', 'resume', 'reset', 'set', 'unset', 'begin', 'end', 'on', 'trigger',
          'toggle', 'add', 'remove', 'lock', 'unlock', 'load', 'unload', 'init', 'update', 'fade_in', 'fade_out',
          'turn_on', 'turn_off', 'complete', 'fail', 'enter', 'exit', 'use', 'kill', 'clear']


def strate(occurrences: int) -> str:
    for low, label in STRATES:
        if occurrences >= low:
            return label
    return STRATES[-1][1]


def classe(category: str, occurrences: int) -> str:
    return category + SEP + strate(occurrences)


def categorie(cls: str) -> str:
    return cls[len(TEMOIN):].split(SEP)[0] if cls.startswith(TEMOIN) else cls.split(SEP)[0]


def width_of(category: str, value: int, largeur: int) -> int:
    """Largeur sous laquelle chercher une valeur. Les evenements RTPC sont des u64 dont DECA essaie le hash48
    puis le hash32 des 32 bits bas ; sur COTW, 70 450 des 73 918 evenements inconnus tiennent sur 32 bits :
    ce sont des hash32."""
    if category in ('rtpc_event', 'rtpc_objid') and value < (1 << 32):
        return 32
    return largeur


def targets_from_csv(path: Path, categories: Iterable[str], min_occurrences: int = 1) -> Dict[str, Targets]:
    """Cibles par classe (categorie|strate), lues dans inconnus.csv (categorie;largeur;hash;occurrences;...)."""
    import csv
    wanted = set(categories)
    out: Dict[str, Targets] = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f, delimiter=';'):
            cat = r['categorie']
            occurrences = int(r['occurrences'])
            if cat not in wanted or occurrences < min_occurrences:
                continue
            value = int(r['hash'], 16)
            width = width_of(cat, value, int(r['largeur']))
            t = out.setdefault(classe(cat, occurrences), Targets())
            t.add(width, value, cat, r.get('exemple', ''))
            key = (width, value & MASK[width])
            if key in t.items:
                t.items[key].count = occurrences
    return out


def fictive_targets(real: Dict[str, Targets], seed: int = 20261008) -> Dict[str, Targets]:
    """Pour chaque classe, autant de valeurs tirees au hasard, de memes largeurs, qu'aucune cible reelle."""
    import random
    rng = random.Random(seed)
    taken = {k for t in real.values() for k in t.items}
    out = {}
    for cls, t in real.items():
        f = Targets()
        for (width, _v) in t.items:
            while True:
                v = rng.getrandbits(width)
                if v and (width, v) not in taken:
                    taken.add((width, v))
                    break
            f.add(width, v, TEMOIN + cls, '')
        out[TEMOIN + cls] = f
    return out


def source_apex() -> Set[str]:
    """Chaines des autres jeux APEX (Ghidra) et noms de champs communs : les memes noms de proprietes RTPC
    reviennent d'un jeu a l'autre (from_anim_id, segment_id...)."""
    base = REPO / 'resources' / 'deca'
    files = [f for f in sorted((base / 'ghidra_strings').glob('*.txt')) if f.name != GHIDRA_HP.name]
    files += sorted((base / 'field_strings').glob('*.txt')) + [base / 'strings.txt']
    out = set()
    for f in files:
        out |= source_ghidra(f)
    return out


def source_chemins(vfs) -> List[str]:
    """Mots des chemins de fichiers connus, du plus frequent au moins frequent. Les noms de champs reprennent
    souvent ces mots (reserve, spawn, trophy, vegetation...)."""
    counter = collections.Counter()
    for (v,) in vfs.db_query_all('SELECT DISTINCT v_path FROM core_nodes WHERE v_path IS NOT NULL', [],
                                 dbg='cotw_resolve_paths'):
        for t in re.split(r'[/._\\-]+', (_projet.to_text(v) or '').lower()):
            if 2 <= len(t) <= 30 and not t.isdigit():
                counter[t] += 1
    return [t for t, _n in counter.most_common()]


def gen_verbs(objects: Iterable[str]) -> Iterator[str]:
    """verbe_objet : la forme des evenements RTPC (show_clues, hide_clues, start_muted...)."""
    for o in objects:
        for v in VERBES:
            yield v + '_' + o


def racines(pass_name: str, name: str) -> Tuple[str, ...]:
    """Racines d'un nom pour la corroboration : deux noms d'une meme passe qui partagent une racine se
    confirment. Vide pour les passes sans structure (directs)."""
    if pass_name == 'positionnels':
        m = re.fullmatch(r'(.*\D)\d+', name)
        return (m.group(1),) if m else ()
    if pass_name == 'affixes':
        return tuple(sorted({name[len(p):] for p in AFFIX_PREFIXES if name.startswith(p)} |
                            {name[:-len(s)] for s in AFFIX_SUFFIXES if name.endswith(s)}))
    if pass_name == 'verbes':
        return tuple(sorted({name[len(v) + 1:] for v in VERBES if name.startswith(v + '_')}))
    if pass_name in ('paires', 'triplets'):
        parts = name.split('_')
        return ('1:' + parts[0], '9:' + parts[-1]) if len(parts) > 1 else ()
    return ()


@dataclass
class PassInfo:
    name: str
    candidates: int
    seconds: float
    group_size: int = 0        # candidats par racine (0 : pas de corroboration)
    keys_per_candidate: int = 1

    @property
    def groups(self) -> int:
        if not self.group_size:
            return 0
        return -(-self.candidates * self.keys_per_candidate // self.group_size)


@dataclass
class Decision:
    pass_name: str
    cls: str
    touched: int               # cibles touchees par un nom de la passe
    expected_all: float        # faux positifs attendus parmi elles
    corroborated: int          # cibles dont un nom partage une racine avec une autre cible touchee
    expected_corr: float       # faux positifs attendus parmi les corroborees
    mode: str                  # tous, corrobores, aucun
    retained: Set[Tuple[int, int]]
    temoin_touched: int = 0
    temoin_retained: int = 0

    @property
    def expected(self) -> float:
        return {'tous': self.expected_all, 'corrobores': self.expected_corr}.get(self.mode, 0.0)


class CategorySearcher:
    """Une seule enumeration des candidats pour toutes les classes ; statistiques par (passe, classe)."""

    def __init__(self, targets: Dict[str, Targets]):
        self.targets = targets
        self.index: Dict[int, Dict[int, List[str]]] = {32: {}, 48: {}, 64: {}}
        for cls, t in targets.items():
            for (width, value) in t.items:
                self.index[width].setdefault(value, []).append(cls)
        self.hits: Dict[str, Dict[Tuple[int, int], Dict[str, str]]] = {c: {} for c in targets}
        self.passes: List[PassInfo] = []

    def rate(self, cls: str) -> float:
        return sum(1.0 / (1 << w) for (w, _v) in self.targets[cls].items)

    def run(self, pass_name: str, candidates: Iterable[str], group_size: int = 0, keys_per_candidate: int = 1):
        import mmh3
        i32, i48, i64 = self.index[32], self.index[48], self.index[64]
        wide = bool(i48 or i64)
        n = 0
        t0 = time.time()
        for s in candidates:
            b = s.encode('utf-8')
            n += 1
            h = int(hash32_func_bytes(b))
            if h in i32:
                self._record(32, h, s, pass_name)
            if wide:
                v = mmh3.hash128(b, x64arch=True)
                h48 = (v >> 16) & MASK[48]
                if h48 in i48:
                    self._record(48, h48, s, pass_name)
                if (v & MASK[64]) in i64:
                    self._record(64, v & MASK[64], s, pass_name)
        info = PassInfo(pass_name, n, time.time() - t0, group_size, keys_per_candidate)
        self.passes.append(info)
        return info

    def _record(self, width, value, name, pass_name):
        for cls in self.index[width][value]:
            names = self.hits[cls].setdefault((width, value), {})
            if name not in names:
                names[name] = pass_name

    def touched(self, pass_name: str, cls: str) -> Dict[Tuple[int, int], List[str]]:
        out = {}
        for key, names in self.hits.get(cls, {}).items():
            mine = [n for n, p in names.items() if p == pass_name]
            if mine:
                out[key] = mine
        return out


def decide(searcher: CategorySearcher, classes: Iterable[str], threshold: float, rate_threshold: float,
           write_all: bool = False) -> List[Decision]:
    """Decision par (passe, classe), passes dans l'ordre ou elles ont tourne.

    Corroboration : un nom touche est corrobore si l'une de ses racines (racines()) est partagee avec une autre
    cible de la meme categorie, touchee par la meme passe (toutes classes confondues) ou deja retenue par une
    passe precedente (segment_type est corrobore par segment_id, retenu en passe directe).

    Faux positifs attendus parmi les corrobores d'une classe C, pour des racines de g candidats, lambda_C = g x r_C
    (r = somme des 1/2^largeur des cibles) et lambda_cat = g x r de toute la categorie :
      - un faux nom tombe dans une racine deja peuplee : (racines peuplees) x lambda_C ;
      - deux faux noms dans une meme racine vide : (nombre de racines) x lambda_C x lambda_cat.
    Le temoin passe par les memes regles : ses cibles fictives sont corroborees par les racines peuplees (vraies
    ou fictives), ce qui mesure les deux termes."""
    by_cat: Dict[str, List[str]] = collections.defaultdict(list)
    for cls in classes:
        by_cat[categorie(cls)].append(cls)
    retained_by_cat: Dict[str, Dict[Tuple[str, Tuple[int, int]], Set[str]]] = collections.defaultdict(dict)
    out = []
    for info in searcher.passes:
        for cat, cls_list in by_cat.items():
            touched = {c: searcher.touched(info.name, c) for c in cls_list}
            fake = {c: searcher.touched(info.name, TEMOIN + c) if (TEMOIN + c) in searcher.targets else {}
                    for c in cls_list}
            roots: Dict[str, Set] = collections.defaultdict(set)
            fake_roots: Dict[str, Set] = collections.defaultdict(set)
            if info.group_size:
                for c, t in touched.items():
                    for key, names in t.items():
                        for n in names:
                            for r in racines(info.name, n):
                                roots[r].add((c, key))
                for member, names in retained_by_cat[cat].items():
                    for n in names:
                        for r in racines(info.name, n):
                            roots[r].add(member)
                for c, t in fake.items():
                    for key, names in t.items():
                        for n in names:
                            for r in racines(info.name, n):
                                fake_roots[r].add((TEMOIN + c, key))
            lam_cat = info.group_size * sum(searcher.rate(c) for c in cls_list)
            for cls in cls_list:
                t = touched[cls]
                r = searcher.rate(cls)
                e_all = info.candidates * r
                corr = {key for key, names in t.items()
                        if any(len(roots[x]) >= 2 for n in names for x in racines(info.name, n))}
                lam = info.group_size * r
                e_corr = len(roots) * lam + info.groups * lam * lam_cat
                if t and (write_all or e_all <= max(threshold, rate_threshold * len(t))):
                    mode, retained = 'tous', set(t)
                elif corr and e_corr <= max(threshold, rate_threshold * len(corr)):
                    mode, retained = 'corrobores', corr
                else:
                    mode, retained = 'aucun', set()
                d = Decision(info.name, cls, len(t), e_all, len(corr), e_corr, mode, retained)
                d.temoin_touched = len(fake[cls])
                if mode == 'tous':
                    d.temoin_retained = len(fake[cls])
                elif mode == 'corrobores':
                    d.temoin_retained = sum(
                        1 for key, names in fake[cls].items()
                        if any(len(roots[x] | fake_roots[x]) >= 2 for n in names for x in racines(info.name, n)))
                out.append(d)
            for d in out[-len(cls_list):]:
                for key in d.retained:
                    retained_by_cat[cat].setdefault((d.cls, key), set()).update(touched[d.cls][key])
    return out


@dataclass
class Selection:
    """Noms retenus par classe : {cle: (nom, passe, faux positifs attendus de la decision)} ; hashes ecartes
    parce que plusieurs noms retenus y tombent (a trancher a la main)."""
    retained: Dict[str, Dict[Tuple[int, int], Tuple[str, str, float]]]
    ambiguous: List[Tuple[str, Tuple[int, int], List[str]]]


def select_names(searcher: CategorySearcher, decisions: List[Decision]) -> Selection:
    by_class: Dict[str, Dict[Tuple[int, int], Dict[str, Tuple[str, float]]]] = collections.defaultdict(dict)
    for d in decisions:
        for key in d.retained:
            for n, p in searcher.hits[d.cls][key].items():
                if p == d.pass_name:
                    by_class[d.cls].setdefault(key, {})[n] = (p, d.expected)
    retained, ambiguous = {}, []
    for cls, keys in by_class.items():
        picks = retained[cls] = {}
        for key, names in sorted(keys.items()):
            if len(names) > 1:
                ambiguous.append((cls, key, sorted(names)))
            else:
                (n, (p, e)), = names.items()
                picks[key] = (n, p, e)
    return Selection(retained, ambiguous)


def load_sources(spec: str, vfs) -> Dict[str, Set[str]]:
    sources: Dict[str, Set[str]] = {}
    for src in [x.strip() for x in spec.split(',') if x.strip()]:
        if src == 'ghidra':
            sources[src] = source_ghidra()
        elif src == 'apex':
            sources[src] = source_apex()
        elif src == 'exe':
            sources[src] = source_exe(vfs)
        elif src == 'adf-types':
            sources[src] = source_adf_types(vfs)
        elif src.startswith('fichier:'):
            sources[src] = identifiers_from_lines(load_word_list(Path(src[len('fichier:'):])))
        else:
            raise SystemExit('ERREUR : source inconnue "{}" (ghidra, apex, exe, adf-types, fichier:CHEMIN)'.format(src))
    return sources


def run_target_passes(searcher: CategorySearcher, identifiers: Set[str], words: List[str], path_words: List[str],
                      args, log=print):
    """Les passes du mode --cibles, dans l'ordre : un nom garde la premiere passe qui l'a produit."""
    counter = tokens_of(identifiers)
    vocab = build_vocab(counter, args.paires or 3000, words + path_words)
    passes = [('directs', lambda: gen_direct(identifiers, words + path_words), 0, 1)]
    if args.affixes:
        forms = snake_forms(identifiers) | set(words) | set(path_words)
        passes.append(('affixes', lambda: gen_affixes(forms), len(AFFIX_PREFIXES) + len(AFFIX_SUFFIXES), 1))
    if args.verbes:
        passes.append(('verbes', lambda: gen_verbs(vocab), len(VERBES), 1))
    if args.positionnels > 0:
        passes.append(('positionnels', lambda: gen_positional(vocab, args.positionnels), args.positionnels + 10, 1))
    if args.paires > 0:
        vocab2 = build_vocab(counter, args.paires, words)
        passes.append(('paires', lambda: gen_pairs(vocab2), len(vocab2), 2))
    if args.triplets > 0:
        vocab3 = build_vocab(counter, args.triplets, words)
        passes.append(('triplets', lambda: gen_triples(vocab3), len(vocab3) ** 2, 2))
    for pass_name, make, group_size, keys in passes:
        info = searcher.run(pass_name, make(), group_size, keys)
        log('  passe {:<12} : {:>11,} candidats ({:.0f} s)'.format(pass_name, info.candidates, info.seconds)
            .replace(',', ' '))


# ---------------------------------------------------------------------------------------------------------
# Passes par famille de fichiers (familles.py) : candidats d'une famille contre les seules cibles de la famille
# ---------------------------------------------------------------------------------------------------------
#
# Les faux positifs attendus d'une passe sont candidats x cibles / 2^32. Les passes globales confrontent tous les
# candidats a toutes les cibles ; une passe par famille ne confronte que les mots d'une famille (noms deja connus
# dans ses fichiers, vocabulaire du domaine) aux hashes inconnus de cette famille. Sur les 195 cibles des
# trophy_feature_data, 2,8 millions de triplets n'attendent que 0,13 coincidence.

FAMILLE = 'f:'
VOCABULARY_FAMILIES = HERE / 'dictionnaires' / 'vocabulaire_familles.txt'
CATEGORIES_FAMILLES = ('rtpc_noeud', 'rtpc_propriete', 'rtpc_event')


def family_targets_from_csv(path: Path, categories: Iterable[str], min_occurrences: int = 1
                            ) -> Dict[str, Dict[str, Targets]]:
    """famille -> {classe 'categorie|f:famille': cibles}, d'apres la colonne familles d'inconnus.csv
    ('famille:occurrences|...'). Le compte d'une cible est son nombre d'occurrences dans la famille.
    Vide si le fichier n'a pas cette colonne (audit anterieur au 2026-10-08 soir)."""
    import csv
    wanted = set(categories) & set(CATEGORIES_FAMILLES)
    out: Dict[str, Dict[str, Targets]] = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter=';')
        if 'familles' not in (reader.fieldnames or []):
            return {}
        for r in reader:
            cat = r['categorie']
            if cat not in wanted or int(r['occurrences']) < min_occurrences or not r.get('familles'):
                continue
            value = int(r['hash'], 16)
            width = width_of(cat, value, int(r['largeur']))
            for part in r['familles'].split('|'):
                fam, _sep, n = part.rpartition(':')
                if not fam:
                    continue
                t = out.setdefault(fam, {}).setdefault(cat + SEP + FAMILLE + fam, Targets())
                t.add(width, value, cat, r.get('exemple', ''))
                key = (width, value & MASK[width])
                if key in t.items:
                    t.items[key].count = int(n)
    return out


def load_family_names(path: Optional[Path]) -> Dict[str, collections.Counter]:
    """noms_par_famille.tsv (audit_noms_internes.py) : famille -> {nom connu: occurrences}."""
    out: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    if path is None or not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        cols = line.split('\t')
        if len(cols) >= 4 and cols[2]:
            out[cols[0]][cols[2]] += int(cols[3]) if cols[3].isdigit() else 1
    return out


def family_vocabulary(names: Iterable[str], fam: str, domain: List[str]) -> List[str]:
    """Mots des noms connus de la famille, du plus repandu (nombre de noms qui le contiennent) au moins repandu,
    puis les mots du nom de la famille, puis le vocabulaire du domaine."""
    counter = collections.Counter()
    for n in names:
        for m in set(FA.mots(n)):
            counter[m] += 1
    ranked = [m for m, _c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
    stem = '' if fam == FA.SANS_NOM or fam.startswith('.') else fam.rsplit('.', 1)[0]    # trophy_feature_data
    return list(dict.fromkeys(ranked + FA.mots(stem) + [w for w in domain if w]))


@dataclass
class FamilyRun:
    famille: str
    real: Dict[str, Targets]
    searcher: CategorySearcher
    decisions: List[Decision]
    selection: Selection
    known: int
    vocab2: int
    vocab3: int
    triplets: bool

    @property
    def occurrences(self) -> int:
        return sum(x.count for t in self.real.values() for x in t.items.values())

    @property
    def candidates(self) -> int:
        return sum(p.candidates for p in self.searcher.passes)


def run_family(fam: str, real_f: Dict[str, Targets], names: List[str], domain: List[str], args) -> FamilyRun:
    """Passes d'une famille : directs (variantes des noms connus), affixes, positionnels, paires, et triplets si
    au moins une classe de la famille les supporte (faux positifs attendus <= --famille-triplets-fp)."""
    import zlib
    vocab = family_vocabulary(names, fam, domain)
    vocab2 = vocab[:args.famille_paires]
    vocab3 = vocab[:args.famille_triplets]
    targets = dict(real_f)
    if args.temoin:
        targets.update(fictive_targets(real_f, seed=zlib.crc32(fam.encode('utf-8'))))
    s = CategorySearcher(targets)
    s.run('directs', gen_direct(names, vocab2))
    if args.affixes:
        s.run('affixes', gen_affixes(snake_forms(names) | set(vocab2)), len(AFFIX_PREFIXES) + len(AFFIX_SUFFIXES), 1)
    if args.positionnels > 0:
        s.run('positionnels', gen_positional(vocab2, args.positionnels), args.positionnels + 10, 1)
    if len(vocab2) > 1:
        s.run('paires', gen_pairs(vocab2), len(vocab2), 2)
    n3 = len(vocab3) ** 3
    triplets = len(vocab3) > 2 and min(n3 * s.rate(c) for c in real_f) <= args.famille_triplets_fp
    if triplets:
        s.run('triplets', gen_triples(vocab3), len(vocab3) ** 2, 2)
    decisions = decide(s, list(real_f), args.seuil_fp_famille, args.seuil_taux, args.tout_ecrire)
    return FamilyRun(fam, real_f, s, decisions, select_names(s, decisions), len(names), len(vocab2), len(vocab3),
                     triplets)


def run_families(args, real: Dict[str, Targets], found: Selection, log=print) -> List[FamilyRun]:
    """Toutes les passes par famille : familles triees par occurrences inconnues, au plus --familles-max, au moins
    --familles-min-occurrences. Une cible deja nommee (audit, dictionnaire) est absente de `real` et ignoree ici."""
    categories = [c.strip() for c in args.categories.split(',') if c.strip()]
    by_family = family_targets_from_csv(Path(args.cibles), categories, args.min_occurrences)
    if not by_family:
        log('  passes par famille : pas de colonne familles dans {} (audit a relancer)'.format(args.cibles))
        return []
    alive = {(categorie(c), k) for c, t in real.items() for k in t.items}
    for fam, classes in by_family.items():
        for cls, t in classes.items():
            for k in [k for k in t.items if (categorie(cls), k) not in alive]:
                del t.items[k]
        by_family[fam] = {c: t for c, t in classes.items() if len(t)}
    weight = {fam: sum(x.count for t in cl.values() for x in t.items.values()) for fam, cl in by_family.items()}
    chosen = [f for f in sorted(weight, key=lambda f: (-weight[f], f))
              if weight[f] >= args.familles_min_occurrences][:args.familles_max]
    names_path = Path(args.familles_noms) if args.familles_noms else Path(args.cibles).parent / 'noms_par_famille.tsv'
    names_by_family = load_family_names(names_path)
    domain = load_word_list(Path(args.vocabulaire_familles)) + (
        load_word_list(Path(args.vocabulaire)) if args.vocabulaire else [])
    # noms retenus par les passes globales de cette session, rattaches aux familles de leurs cibles
    session = collections.defaultdict(set)
    for cls, picks in found.retained.items():
        for key, (n, _p, _e) in picks.items():
            for fam, classes in by_family.items():
                if any(key in t.items for t in classes.values()):
                    session[fam].add(n)
    log('  passes par famille : {} familles retenues sur {} ; vocabulaire connu : {}'.format(
        len(chosen), len(by_family), names_path if names_path.is_file() else 'absent'))
    runs = []
    for fam in chosen:
        names = sorted(set(names_by_family.get(fam, {})) | session[fam])
        t0 = time.time()
        run = run_family(fam, by_family[fam], names, domain, args)
        runs.append(run)
        log('    {:<40} : {:>6} cibles, {:>4} noms connus, {:>10,} candidats, {:>4} retenus ({:.0f} s)'.format(
            fam[:40], sum(len(t) for t in run.real.values()), run.known, run.candidates,
            sum(len(p) for p in run.selection.retained.values()), time.time() - t0).replace(',', ' '))
    return runs


@dataclass
class Merged:
    """Noms retenus toutes passes confondues : cle -> (nom, categorie, passe, faux positifs attendus, classe) ;
    une cle qui recoit deux noms differents (dans une classe, ou entre passes globales et par famille) est
    ecartee et listee dans ambiguous."""
    names: Dict[Tuple[int, int], Tuple[str, str, str, float, str]]
    ambiguous: List[Tuple[str, Tuple[int, int], List[str]]]


def merge_selections(selections: List[Tuple[str, Selection]]) -> Merged:
    """selections : (prefixe de passe, selection). Le prefixe distingue au dictionnaire les passes par famille."""
    by_key: Dict[Tuple[int, int], Dict[str, Tuple[str, str, float, str]]] = collections.defaultdict(dict)
    ambiguous: List[Tuple[str, Tuple[int, int], List[str]]] = []
    for prefix, sel in selections:
        ambiguous += sel.ambiguous
        for cls, picks in sel.retained.items():
            for key, (n, p, e) in picks.items():
                by_key[key].setdefault(n, (categorie(cls), prefix + p, e, cls))
    names = {}
    for key, ns in by_key.items():
        if len(ns) > 1:
            ambiguous.append(('/'.join(sorted({v[0] for v in ns.values()})), key, sorted(ns)))
        else:
            (n, (cat, p, e, cls)), = ns.items()
            names[key] = (n, cat, p, e, cls)
    for _c, key, _n in ambiguous:
        names.pop(key, None)
    seen, unique = set(), []
    for a in ambiguous:
        if a[1] not in seen:
            seen.add(a[1])
            unique.append(a)
    return Merged(names, unique)


def cmd_search_targets(args, vfs) -> int:
    """chercher --cibles : cibles lues dans inconnus.csv, sans relire les fichiers du jeu."""
    categories = [c.strip() for c in args.categories.split(',') if c.strip()]
    real = targets_from_csv(Path(args.cibles), categories, args.min_occurrences)
    dict_path = Path(args.dictionnaire) if args.dictionnaire else _projet.DICTIONARY_PATH
    dictionary = _projet.load_dictionary(dict_path)
    # un hash nomme depuis l'audit (dictionnaire, chaine ajoutee a DECA) n'est plus une cible
    already = collections.Counter()
    for cls, t in real.items():
        known = set(known_names(vfs, t, use_dictionary=False)) | {k for k in t.items if k in dictionary}
        for k in known:
            del t.items[k]
        already[categorie(cls)] += len(known)
    real = {c: t for c, t in sorted(real.items()) if len(t)}
    for cat in categories:
        n = sum(len(t) for c, t in real.items() if categorie(c) == cat)
        print('  {:<18} : {} cibles ({} nommees depuis l\'audit)'.format(cat, n, already[cat]))
    if not real:
        text = '# Résolution de hashes sur les cibles d\'un audit\n\nAucune cible : tous les hashes de `{}` ' \
               '(catégories {}) sont déjà nommés.\n'.format(args.cibles, args.categories)
        print('Aucune cible.')
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding='utf-8')
        return 0
    targets = dict(real)
    if args.temoin:
        targets.update(fictive_targets(real))

    sources = load_sources(args.sources or 'ghidra,apex,exe,adf-types', vfs)
    identifiers = set().union(*sources.values()) if sources else set()
    words = load_word_list(Path(args.vocabulaire)) if args.vocabulaire else []
    path_words = source_chemins(vfs) if args.mots_chemins else []
    for src, ids in sources.items():
        print('  source {} : {} identifiants'.format(src, len(ids)))
    print('  mots des chemins : {}'.format(len(path_words)))

    searcher = CategorySearcher(targets)
    run_target_passes(searcher, identifiers, words, path_words, args)
    decisions = decide(searcher, list(real), args.seuil_fp, args.seuil_taux, args.tout_ecrire)
    found = select_names(searcher, decisions)
    family_runs = run_families(args, real, found) if args.familles else []
    merged = merge_selections([('', found)] + [('famille-', r.selection) for r in family_runs])

    # une seule ligne au dictionnaire par (hash, nom), meme si le hash est cible dans deux categories
    contexts = {}
    for cls, t in real.items():
        for key, x in t.items.items():
            contexts.setdefault(key, x.contexts[0] if x.contexts else '')
    entries = [(key, n, p, e, '{} ; {}'.format(cls, contexts.get(key, '')))
               for key, (n, _cat, p, e, cls) in sorted(merged.names.items())]
    written = [] if args.sans_ecrire else append_to_dictionary(dict_path, entries)
    print('{} nom(s) ajoute(s) a {}'.format(len(written), dict_path))

    L = report_targets(args, real, searcher, decisions, merged, family_runs, sources, path_words, written, dict_path)
    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0


def report_targets(args, real, searcher: CategorySearcher, decisions: List[Decision], merged: Merged,
                   family_runs: List[FamilyRun], sources, path_words, written, dict_path) -> List[str]:
    written_keys = {(e[0], e[1]) for e in written}
    categories = list(dict.fromkeys(categorie(c) for c in real))
    occ = {}                                   # (categorie, cle) -> occurrences totales (classes globales)
    for cls, t in real.items():
        for key, x in t.items.items():
            occ[(categorie(cls), key)] = x.count
    all_decisions = decisions + [d for r in family_runs for d in r.decisions]
    L = ['# Résolution de hashes sur les cibles d\'un audit', '',
         '- Cibles : `{}` (occurrences ≥ {}) ; classes : catégorie × nombre d\'occurrences.'.format(
             args.cibles, args.min_occurrences),
         '- Sources : {} ; mots des chemins : {} ; vocabulaire : `{}`'.format(
             ', '.join('{} ({} identifiants)'.format(s, len(i)) for s, i in sources.items()) or 'aucune',
             len(path_words), args.vocabulaire),
         '- Passes globales : ' + ', '.join('{} ({:,} candidats)'.format(p.name, p.candidates).replace(',', ' ')
                                            for p in searcher.passes),
         '- Passes par famille : {}'.format(
             '{} familles, {:,} candidats ; vocabulaire du domaine `{}`'.format(
                 len(family_runs), sum(r.candidates for r in family_runs), args.vocabulaire_familles).replace(',', ' ')
             if family_runs else 'aucune'),
         '- Décision par passe et classe : tous les noms si les faux positifs attendus restent ≤ {} ({} pour une '
         'famille) ou ≤ {} × cibles touchées ; sinon les seuls noms corroborés (racine partagée) si leur estimation '
         'passe le même test ; sinon aucun. Un hash qui reçoit deux noms retenus n\'est pas écrit.'.format(
             args.seuil_fp, args.seuil_fp_famille, args.seuil_taux),
         '- Témoin : {}'.format('autant de cibles fictives que de vraies par classe ; « témoin retenu » compte '
                                'celles que les mêmes décisions auraient nommées (faux positifs certains).'
                                if args.temoin else 'non (option --temoin).'),
         '']
    total_exp = sum(d.expected for d in all_decisions if d.retained)
    L += ['## Bilan', '',
          '| Catégorie | Cibles | Occurrences | Noms retenus | dont passes par famille | Occurrences nommées | Part |',
          '|---|---:|---:|---:|---:|---:|---:|']
    for cat in categories:
        keys = [k for (c, k) in occ if c == cat]
        total = sum(occ[(cat, k)] for k in keys)
        named = [k for k in keys if k in merged.names]
        fam = sum(1 for k in named if merged.names[k][2].startswith('famille-'))
        occ_ret = sum(occ[(cat, k)] for k in named)
        L.append('| {} | {} | {} | {} | {} | {} | {:.1%} |'.format(
            cat, len(keys), total, len(named), fam, occ_ret, occ_ret / total if total else 0))
    L += ['', 'Noms retenus : {}, nouveaux au dictionnaire : {} ; faux positifs attendus parmi eux : {:.2g}{}.'.format(
        len(merged.names), len(written), total_exp,
        ' ; témoin retenu : {}'.format(sum(d.temoin_retained for d in all_decisions)) if args.temoin else ''), '']
    for cat in categories:
        L += ['## {}'.format(cat), '',
              '| Passe | Classe | Cibles | Touchées | FP attendus | Corroborées | FP attendus (corr.) | Décision | '
              'Témoin touché / retenu |',
              '|---|---|---:|---:|---:|---:|---:|---|---:|']
        for d in decisions:
            if categorie(d.cls) != cat or not (d.touched or d.temoin_touched):
                continue
            L.append('| {} | {} | {} | {} | {:.3g} | {} | {:.3g} | {} | {} |'.format(
                d.pass_name, d.cls.split(SEP)[1], len(real[d.cls]), d.touched, d.expected_all, d.corroborated,
                d.expected_corr, d.mode,
                '{} / {}'.format(d.temoin_touched, d.temoin_retained) if args.temoin else '-'))
        rows = sorted(((occ[(cat, k)], k, n, p) for k, (n, c, p, _e, _cls) in merged.names.items()
                       if (cat, k) in occ), reverse=True)
        if rows:
            L += ['', '{} noms retenus ; les plus fréquents :'.format(len(rows)), '',
                  '| Hash | Nom | Passe | Occurrences | Nouveau |', '|---|---|---|---:|---|']
            L += ['| `{:x}` | `{}` | {} | {} | {} |'.format(k[1], n, p, c, 'oui' if (k, n) in written_keys else 'non')
                  for c, k, n, p in rows[:40]]
        L.append('')
    if family_runs:
        L += ['## Passes par famille', '',
              'Chaque famille confronte son vocabulaire (mots de ses noms connus, de son nom, du domaine) à ses '
              'seules cibles. Triplets : seulement si une classe de la famille les supporte.', '',
              '| Famille | Cibles | Occurrences | Noms connus | Vocabulaire (paires / triplets) | Candidats | '
              'Noms retenus | FP attendus | Témoin touché / retenu |',
              '|---|---:|---:|---:|---|---:|---:|---:|---:|']
        for r in family_runs:
            ds = r.decisions
            L.append('| `{}` | {} | {} | {} | {} / {} | {:,} | {} | {:.2g} | {} |'.format(
                r.famille, sum(len(t) for t in r.real.values()), r.occurrences, r.known, r.vocab2,
                r.vocab3 if r.triplets else '-', r.candidates, sum(len(p) for p in r.selection.retained.values()),
                sum(d.expected for d in ds if d.retained),
                '{} / {}'.format(sum(d.temoin_touched for d in ds), sum(d.temoin_retained for d in ds))
                if args.temoin else '-').replace(',', ' '))
        rows = sorted(((occ.get((c, k), 0), k, n, p, cls) for k, (n, c, p, _e, cls) in merged.names.items()
                       if p.startswith('famille-')), reverse=True)
        if rows:
            L += ['', 'Noms trouvés par les passes par famille, les plus fréquents :', '',
                  '| Hash | Nom | Famille | Passe | Occurrences |', '|---|---|---|---|---:|']
            L += ['| `{:x}` | `{}` | `{}` | {} | {} |'.format(k[1], n, cls.split(SEP + FAMILLE, 1)[-1],
                                                           p[len('famille-'):], c)
                  for c, k, n, p, cls in rows[:60]]
        L.append('')
    if merged.ambiguous:
        L += ['## Hashes à plusieurs noms retenus (non écrits)', '']
        L += ['- {} `{:x}` : {}'.format(cls, k[1], ', '.join('`{}`'.format(n) for n in names))
              for cls, k, names in merged.ambiguous[:50]]
        L.append('')
    L += ['Dictionnaire : `{}`'.format(dict_path)]
    return L


# ---------------------------------------------------------------------------------------------------------
# Commande "chercher"
# ---------------------------------------------------------------------------------------------------------

def cmd_search(args) -> int:
    vfs = _projet.open_project(args.projet)
    if args.cibles:
        return cmd_search_targets(args, vfs)
    reader = FileReader(vfs)
    targets = Targets()
    read_report = []
    for name in select_files(vfs, args.fichiers, args.motif):
        kind, obj = reader.decode(name)
        if kind == 'rtpc':
            collect_rtpc(obj, Path(name).name, targets, with_children=not args.sans_enfants)
            read_report.append((name, 'RTPC'))
        elif kind == 'adf':
            collect_adf(obj, Path(name).name, targets)
            read_report.append((name, 'ADF'))
        else:
            read_report.append((name, 'ignore : ' + obj))
    n_read = sum(1 for _n, k in read_report if not k.startswith('ignore'))
    print('{} hashes distincts releves dans {} fichier(s) lu(s) sur {}'.format(len(targets), n_read, len(read_report)))

    known_db = known_names(vfs, targets, use_dictionary=False)
    dictionary = _projet.load_dictionary(Path(args.dictionnaire)) if args.dictionnaire else _projet.load_dictionary()
    known_dict = {k: v for k, v in dictionary.items() if k in targets.items}

    search_targets = Targets()
    for key, t in targets.items.items():
        if args.aveugle or (key not in known_db and key not in known_dict):
            search_targets.items[key] = t
    print('{} cibles ({} deja nommees par DECA, {} par le dictionnaire du fork){}'.format(
        len(search_targets), len(known_db), len(known_dict), ' : mode aveugle' if args.aveugle else ''))

    sources = load_sources(args.sources or 'ghidra,exe,adf-types', vfs)
    identifiers = set().union(*sources.values()) if sources else set()
    words = load_word_list(Path(args.vocabulaire)) if args.vocabulaire else []
    counter = tokens_of(identifiers)
    for src, ids in sources.items():
        print('  source {} : {} identifiants'.format(src, len(ids)))

    searcher = Searcher(search_targets)
    passes = [('directs', lambda: gen_direct(identifiers, words))]
    if args.affixes:
        forms = snake_forms(identifiers) | set(words)
        passes.append(('affixes', lambda: gen_affixes(forms)))
    if args.positionnels > 0:
        vocab_pos = build_vocab(counter, args.paires or 3000, words)
        passes.append(('positionnels', lambda: gen_positional(vocab_pos, args.positionnels)))
    if args.paires > 0:
        vocab2 = build_vocab(counter, args.paires, words)
        passes.append(('paires', lambda: gen_pairs(vocab2)))
    if args.triplets > 0:
        vocab3 = build_vocab(counter, args.triplets, words)
        passes.append(('triplets', lambda: gen_triples(vocab3)))
    if search_targets:
        for pass_name, make in passes:
            st = searcher.run(pass_name, make())
            print('  passe {:<12} : {:>11,} candidats, {:>3} noms, faux positifs attendus {:.3g} ({:.0f} s)'.format(
                pass_name, st.candidates, st.found, st.expected_false, st.seconds).replace(',', ' '))

    expected_by_pass = {st.name: st.expected_false for st in searcher.stats}
    stats_by_pass = {st.name: st for st in searcher.stats}
    new_entries, blind_rows = [], []
    for key, names in sorted(searcher.hits.items()):
        t = search_targets.items[key]
        for name, pass_name in names.items():
            if key in known_db:
                blind_rows.append((key, name, known_db[key], name in known_db[key]))
                continue
            new_entries.append((key, name, pass_name, expected_by_pass[pass_name], t.contexts[0] if t.contexts else ''))

    dict_path = Path(args.dictionnaire) if args.dictionnaire else _projet.DICTIONARY_PATH
    eligible = [e for e in new_entries if args.tout_ecrire or e[3] <= args.seuil_fp]
    written = [] if args.sans_ecrire else append_to_dictionary(dict_path, eligible)
    for e in written:
        stats_by_pass[e[2]].written += 1
    print('{} nom(s) ajoute(s) a {}'.format(len(written), dict_path))

    L = report(args, read_report, targets, search_targets, known_db, known_dict, sources, words,
               searcher, new_entries, written, blind_rows, dict_path)
    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0


def report(args, read_report, targets, search_targets, known_db, known_dict, sources, words, searcher,
           new_entries, to_write, blind_rows, dict_path) -> List[str]:
    widths = collections.Counter(w for (w, _v) in search_targets.items)
    L = ['# Résolution de hashes', '',
         '- Fichiers :']
    L += ['  - `{}` : {}'.format(n, k) for n, k in read_report]
    L += ['- Hashes relevés : {} ; déjà nommés par DECA : {} ; par le dictionnaire du fork : {}'.format(
              len(targets), len(known_db), len(known_dict)),
          '- Cibles cherchées : {} ({}){}'.format(
              len(search_targets), ', '.join('{} bits : {}'.format(w, n) for w, n in sorted(widths.items())) or 'aucune',
              ' — **mode aveugle** : les hashes déjà nommés sont cherchés aussi, pour mesurer la précision'
              if args.aveugle else ''),
          '- Sources : {} ; vocabulaire du domaine : {} mots'.format(
              ', '.join('{} ({} identifiants)'.format(s, len(i)) for s, i in sources.items()) or 'aucune', len(words)),
          '', '## Passes', '',
          '| Passe | Candidats | Faux positifs attendus | Noms trouvés | Écrits au dictionnaire |',
          '|---|---:|---:|---:|---:|']
    for st in searcher.stats:
        L.append('| {} | {:,} | {:.3g} | {} | {} |'.format(st.name, st.candidates, st.expected_false, st.found,
                                                         st.written).replace(',', ' '))
    L += ['', 'Faux positifs attendus = candidats × cibles / 2^32 (2^48, 2^64 pour les hashes plus larges). '
          'Une passe qui trouve beaucoup plus de noms que ce nombre a surtout de vraies trouvailles. '
          'Seuil d\'écriture : {} (option --seuil-fp){}.'.format(
              args.seuil_fp, ' ; --tout-ecrire actif' if args.tout_ecrire else ''), '']

    written_keys = {(e[0], e[1]) for e in to_write}
    L += ['## Noms trouvés', '']
    if new_entries:
        collisions = collections.Counter(e[0] for e in new_entries)
        L += ['| Hash | Bits | Nom | Passe | FP attendus | Écrit | Contexte |', '|---|---|---|---|---:|---|---|']
        for key, name, pass_name, expected, context in new_entries:
            flag = ' (plusieurs noms : à trancher)' if collisions[key] > 1 else ''
            L.append('| `0x{:x}` | {} | `{}`{} | {} | {:.2g} | {} | {} |'.format(
                key[1], key[0], name, flag, pass_name, expected,
                'oui' if (key, name) in written_keys else ('déjà' if name in known_dict.get(key, []) else 'non'),
                short(context, 90).replace('|', '/')))
    else:
        L.append('Aucun nouveau nom.')
    L.append('')

    if args.aveugle:
        known_targets = [k for k in search_targets.items if k in known_db]
        confirmed = {k for k, _n, _d, ok in blind_rows if ok}
        wrong = [(k, n, d) for k, n, d, ok in blind_rows if not ok]
        expected = sum(st.expected_false for st in searcher.stats)
        share = len(known_targets) / len(search_targets) if len(search_targets) else 0

        def genre(k):
            kinds = search_targets.items[k].kinds
            return 'propriete' if 'propriete' in kinds else sorted(kinds)[0]
        L += ['## Mode aveugle : précision mesurée sur les hashes déjà nommés par DECA', '',
              'Le générateur vise des noms de champs. Les noms de nœuds enfants sont surtout des noms d\'objets '
              '(equipment_ammo_...), qu\'il ne sait pas fabriquer : leur rappel est faible par construction.', '',
              '| Genre | Déjà nommés | Retrouvés avec le même nom | Rappel |', '|---|---:|---:|---:|']
        for g in sorted({genre(k) for k in known_targets}):
            ks = [k for k in known_targets if genre(k) == g]
            ok = [k for k in ks if k in confirmed]
            L.append('| {} | {} | {} | {:.0%} |'.format(g, len(ks), len(ok), len(ok) / len(ks) if ks else 0))
        L += ['',
              '- Noms différents de ceux de DECA (faux positifs) : {}, pour environ {:.1f} attendus sur ces hashes '
              '({:.1f} faux positifs attendus en tout × {:.0%} de cibles déjà nommées)'.format(
                  len(wrong), expected * share, expected, share), '']
        for k, n, d in wrong[:50]:
            L.append('  - `0x{:x}` ({}) : trouvé `{}`, DECA connaît `{}`'.format(k[1], genre(k), n, ', '.join(d[:3])))
        L.append('')

    found_keys = {e[0] for e in new_entries}
    unknown = [t for k, t in search_targets.items.items()
               if k not in found_keys and k not in known_db and k not in known_dict]
    unknown.sort(key=lambda t: -t.count)
    L += ['## Toujours inconnus ({}), les plus fréquents d\'abord'.format(len(unknown)), '',
          '| Hash | Bits | Genre | Occurrences | Contexte |', '|---|---|---|---:|---|']
    for t in unknown[:args.max_inconnus]:
        L.append('| `0x{:x}` | {} | {} | {} | {} |'.format(t.value, t.width, ', '.join(sorted(t.kinds)), t.count,
                                                         short(t.contexts[0] if t.contexts else '', 90).replace('|', '/')))
    if len(unknown) > args.max_inconnus:
        L.append('| … | | | | {} autres (option --max-inconnus) |'.format(len(unknown) - args.max_inconnus))
    L += ['', 'Dictionnaire : `{}`'.format(dict_path)]
    return L


# ---------------------------------------------------------------------------------------------------------
# Commandes "injecter" et "annuler" : noms du dictionnaire dans core.db, pour l'interface de DECA
# ---------------------------------------------------------------------------------------------------------

def strings_present(vfs, names: List[str]) -> Set[str]:
    present = set()
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        rows = vfs.db_query_all('SELECT string FROM core_strings WHERE string IN ({})'.format(','.join('?' * len(chunk))),
                                chunk, dbg='cotw_inject_present')
        present.update(_projet.to_text(r[0]) for r in rows)
    return present


def cmd_inject(args) -> int:
    from deca.db_types import make_hash_string_tuple
    vfs = _projet.open_project(args.projet)
    dictionary = _projet.load_dictionary(Path(args.dictionnaire)) if args.dictionnaire else _projet.load_dictionary()
    names = sorted({n for names in dictionary.values() for n in names})
    new = [n for n in names if n not in strings_present(vfs, names)]
    if not new:
        print('Rien a injecter : les {} noms du dictionnaire sont deja dans la base.'.format(len(names)))
        return 0
    vfs.hash_string_add_many_basic([make_hash_string_tuple(n.encode('utf-8')) for n in new])
    log = Path(args.projet) / INJECTION_LOG
    stamp = datetime.datetime.now().isoformat(timespec='seconds')
    with open(log, 'a', encoding='utf-8') as f:
        for n in new:
            f.write('{}\t{}\n'.format(stamp, n))
    print('{} nom(s) ajoute(s) a la base ({} deja presents). Journal : {}'.format(len(new), len(names) - len(new), log))
    print('Pour revenir en arriere : python cotw\\resoudre_hashes.py annuler {}'.format(args.projet))
    return 0


def cmd_undo(args) -> int:
    vfs = _projet.open_project(args.projet)
    log = Path(args.projet) / INJECTION_LOG
    if not log.is_file():
        print('Aucune injection a annuler ({} absent).'.format(log))
        return 0
    names = sorted({line.split('\t', 1)[1] for line in log.read_text(encoding='utf-8').splitlines() if '\t' in line})
    removed = 0
    for n in names:
        # seulement si aucun fichier du jeu ne reference la chaine : un scan posterieur a pu la trouver aussi
        cur = vfs.db_execute_one('DELETE FROM core_strings WHERE string = ? AND rowid NOT IN '
                                 '(SELECT string_rowid FROM core_string_references)', [n], dbg='cotw_undo_delete')
        removed += max(cur.rowcount, 0)
    vfs.db_conn.commit()
    log.rename(log.with_name(log.name + '.annule-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    print('{} chaine(s) retiree(s) de la base sur {} injectee(s).'.format(removed, len(names)))
    return 0


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Retrouve le nom en clair des hashes inconnus.')
    sub = ap.add_subparsers(dest='commande', required=True)

    s = sub.add_parser('chercher', help='chercher les noms des hashes inconnus de fichiers du jeu')
    s.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    s.add_argument('fichiers', nargs='*', help='chemins virtuels ou fichiers locaux (defaut : equipment_data, '
                                               'equipment_tuning, equipment_stats_ui, spawn defs munitions et armes)')
    s.add_argument('--motif', action='append', default=[], help='motif SQL LIKE de chemins virtuels, ex : "settings/%%.bin"')
    s.add_argument('--sources', default=None,
                   help='sources d\'identifiants : ghidra (chaines de HP), apex (autres jeux APEX, noms de champs '
                        'communs), exe, adf-types, fichier:CHEMIN (defaut : ghidra,exe,adf-types ; avec --cibles : '
                        'ghidra,apex,exe,adf-types)')
    s.add_argument('--vocabulaire', default=str(VOCABULARY), help='mots du domaine ajoutes aux paires et triplets')
    s.add_argument('--paires', type=int, default=3000, help='nombre de mots les plus frequents combines par deux (0 = passe sautee)')
    s.add_argument('--triplets', type=int, default=250, help='nombre de mots les plus frequents combines par trois (0 = passe sautee)')
    s.add_argument('--positionnels', type=int, default=128, help='index essayes apres chaque mot : mot_0 a mot_N-1 (0 = passe sautee)')
    s.add_argument('--sans-affixes', dest='affixes', action='store_false', help='sauter la passe des prefixes et suffixes')
    s.add_argument('--sans-enfants', action='store_true', help='ne chercher que les noms de proprietes RTPC')
    s.add_argument('--aveugle', action='store_true', help='chercher aussi les hashes deja nommes, pour mesurer la precision')
    s.add_argument('--seuil-fp', type=float, default=0.1, help='faux positifs attendus maximum d\'une passe pour ecrire ses noms (defaut 0.1)')
    s.add_argument('--seuil-taux', type=float, default=0.01,
                   help='avec --cibles : une passe est aussi retenue si ses faux positifs attendus restent sous '
                        'cette fraction des cibles touchees (defaut 0.01)')
    s.add_argument('--sans-verbes', dest='verbes', action='store_false',
                   help='avec --cibles : sauter la passe verbe_objet (show_x, hide_x...)')
    s.add_argument('--sans-familles', dest='familles', action='store_false',
                   help='avec --cibles : sauter les passes par famille de fichiers')
    s.add_argument('--familles-noms', help='avec --cibles : vocabulaire connu par famille (defaut : '
                                           'noms_par_famille.tsv a cote de inconnus.csv)')
    s.add_argument('--vocabulaire-familles', default=str(VOCABULARY_FAMILIES),
                   help='vocabulaire du domaine des passes par famille (trophees, animation, monde)')
    s.add_argument('--familles-max', type=int, default=40, help='nombre maximal de familles (les plus touchees)')
    s.add_argument('--familles-min-occurrences', type=int, default=100,
                   help='occurrences inconnues minimales d\'une famille pour ses passes')
    s.add_argument('--seuil-fp-famille', type=float, default=0.02,
                   help='faux positifs attendus maximum d\'une passe par famille pour retenir tous ses noms')
    s.add_argument('--famille-paires', type=int, default=400, help='mots combines par deux dans une famille')
    s.add_argument('--famille-triplets', type=int, default=120, help='mots combines par trois dans une famille')
    s.add_argument('--famille-triplets-fp', type=float, default=2.0,
                   help='triplets d\'une famille seulement si une de ses classes en attend au plus ce nombre de '
                        'faux positifs')
    s.add_argument('--tout-ecrire', action='store_true', help='ecrire tous les noms trouves, quel que soit le seuil')
    s.add_argument('--sans-ecrire', action='store_true', help='ne pas modifier le dictionnaire')
    s.add_argument('--dictionnaire', help='dictionnaire a utiliser (defaut : cotw\\dictionnaires\\hp_noms.tsv)')
    s.add_argument('--max-inconnus', type=int, default=200, help='nombre de hashes inconnus listes dans le rapport')
    s.add_argument('--cibles', help='inconnus.csv d\'audit_noms_internes.py : chercher ces hashes (par categorie) '
                                    'au lieu de relire des fichiers')
    s.add_argument('--categories', default='rtpc_propriete,rtpc_noeud,rtpc_event,adf_stringhash_32',
                   help='avec --cibles : categories cherchees (defaut : toutes sauf rtpc_objid, identifiants aleatoires)')
    s.add_argument('--min-occurrences', type=int, default=1, help='avec --cibles : occurrences minimales d\'une cible')
    s.add_argument('--temoin', action='store_true',
                   help='avec --cibles : chercher aussi autant de cibles fictives, pour mesurer les faux positifs')
    s.add_argument('--sans-mots-chemins', dest='mots_chemins', action='store_false',
                   help='avec --cibles : ne pas ajouter les mots des chemins de fichiers aux candidats')
    s.add_argument('--out', help='rapport markdown (sinon : affichage a l\'ecran)')
    s.set_defaults(func=cmd_search)

    i = sub.add_parser('injecter', help='ajouter les noms du dictionnaire a core.db (affichage dans DECA)')
    i.add_argument('projet')
    i.add_argument('--dictionnaire')
    i.set_defaults(func=cmd_inject)

    u = sub.add_parser('annuler', help='retirer de core.db les noms ajoutes par "injecter"')
    u.add_argument('projet')
    u.set_defaults(func=cmd_undo)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
