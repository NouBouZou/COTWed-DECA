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
  paires   : deux mots parmi les plus frequents, joints par "_" ;
  triplets : trois mots pris dans un vocabulaire plus court.

Faux positifs : un candidat quelconque tombe par hasard sur l'un des T hashes 32 bits cherches avec une
probabilite de T / 2^32. Pour N candidats, on attend donc N x T / 2^32 coincidences. Chaque passe affiche ce
nombre : si elle trouve beaucoup plus de noms, ses trouvailles sont presque toutes vraies. Par defaut,
seules les passes ou ce nombre est sous --seuil-fp alimentent le dictionnaire (cotw/dictionnaires/hp_noms.tsv).
Le mode --aveugle mesure la precision reelle : il cherche aussi les hashes deja nommes par DECA et compare.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --out D:\\dev\\audits\\noms.md
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --aveugle --out D:\\dev\\audits\\noms_aveugle.md
    python cotw\\resoudre_hashes.py chercher D:\\dev\\work\\hp --motif "settings/hp_settings/%.bin" --out ...
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
# Commande "chercher"
# ---------------------------------------------------------------------------------------------------------

def cmd_search(args) -> int:
    vfs = _projet.open_project(args.projet)
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

    sources: Dict[str, Set[str]] = {}
    for src in [s.strip() for s in args.sources.split(',') if s.strip()]:
        if src == 'ghidra':
            sources[src] = source_ghidra()
        elif src == 'exe':
            sources[src] = source_exe(vfs)
        elif src == 'adf-types':
            sources[src] = source_adf_types(vfs)
        elif src.startswith('fichier:'):
            sources[src] = identifiers_from_lines(load_word_list(Path(src[len('fichier:'):])))
        else:
            raise SystemExit('ERREUR : source inconnue "{}" (ghidra, exe, adf-types, fichier:CHEMIN)'.format(src))
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
    if args.paires > 0:
        vocab2 = build_vocab(counter, args.paires, words)
        passes.append(('paires', lambda: gen_pairs(vocab2)))
    if args.triplets > 0:
        vocab3 = build_vocab(counter, args.triplets, words)
        passes.append(('triplets', lambda: gen_triples(vocab3)))
    if search_targets:
        for pass_name, make in passes:
            st = searcher.run(pass_name, make())
            print('  passe {:<8} : {:>11,} candidats, {:>3} noms, faux positifs attendus {:.3g} ({:.0f} s)'.format(
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
        L += ['## Mode aveugle : précision mesurée sur les hashes déjà nommés par DECA', '',
              '- Hashes déjà nommés cherchés : {}'.format(len(known_targets)),
              '- Retrouvés avec le même nom : {} ({:.0%})'.format(
                  len(confirmed), len(confirmed) / len(known_targets) if known_targets else 0),
              '- Noms différents de ceux de DECA (faux positif ou vraie collision) : {}'.format(len(wrong)), '']
        for k, n, d in wrong[:50]:
            L.append('  - `0x{:x}` : trouvé `{}`, DECA connaît `{}`'.format(k[1], n, ', '.join(d[:3])))
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
    s.add_argument('--sources', default='ghidra,exe,adf-types',
                   help='sources d\'identifiants : ghidra, exe, adf-types, fichier:CHEMIN (defaut : ghidra,exe,adf-types)')
    s.add_argument('--vocabulaire', default=str(VOCABULARY), help='mots du domaine ajoutes aux paires et triplets')
    s.add_argument('--paires', type=int, default=3000, help='nombre de mots les plus frequents combines par deux (0 = passe sautee)')
    s.add_argument('--triplets', type=int, default=250, help='nombre de mots les plus frequents combines par trois (0 = passe sautee)')
    s.add_argument('--sans-affixes', dest='affixes', action='store_false', help='sauter la passe des prefixes et suffixes')
    s.add_argument('--sans-enfants', action='store_true', help='ne chercher que les noms de proprietes RTPC')
    s.add_argument('--aveugle', action='store_true', help='chercher aussi les hashes deja nommes, pour mesurer la precision')
    s.add_argument('--seuil-fp', type=float, default=0.1, help='faux positifs attendus maximum d\'une passe pour ecrire ses noms (defaut 0.1)')
    s.add_argument('--tout-ecrire', action='store_true', help='ecrire tous les noms trouves, quel que soit le seuil')
    s.add_argument('--sans-ecrire', action='store_true', help='ne pas modifier le dictionnaire')
    s.add_argument('--dictionnaire', help='dictionnaire a utiliser (defaut : cotw\\dictionnaires\\hp_noms.tsv)')
    s.add_argument('--max-inconnus', type=int, default=200, help='nombre de hashes inconnus listes dans le rapport')
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
