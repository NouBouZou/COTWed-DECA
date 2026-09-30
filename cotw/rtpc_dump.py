#!/usr/bin/env python3
"""
rtpc_dump.py -- affiche un fichier RTPC du jeu sous forme d'arbre lisible.

Pour chaque noeud et chaque propriete : hash, nom (quand DECA le connait), type et valeur. Un u32 qui
correspond au hash32 d'une chaine connue est annote ("= hash de ..."), ce qui revele les references par
hash (identifiants d'equipement, chemins de fichiers...).

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\rtpc_dump.py D:\\dev\\work\\hp settings/hp_settings/equipment_data.bin --out D:\\dev\\audits\\equipment_data.txt
    python cotw\\rtpc_dump.py D:\\dev\\work\\hp settings/hp_settings/equipment_data.bin --contient equipment_ammo_243_sp_01 --out D:\\dev\\audits\\ammo_243.txt
    python cotw\\rtpc_dump.py D:\\dev\\work\\hp C:\\chemin\\vers\\un_fichier_extrait.epe --json D:\\dev\\audits\\fichier.json

Le 2e argument est un chemin virtuel du jeu, ou un fichier present sur le disque. Le projet sert a lire
le fichier et a retrouver les noms des hashes.

--contient : n'affiche que les noeuds dont une propriete contient ce texte (ou vaut son hash32), avec la
liste de leurs parents et tout leur contenu. Option repetable.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import (  # noqa: E402
    PropType_names, k_type_none, k_type_u32, k_type_f32, k_type_str, k_type_array_u32,
    k_type_array_f32, k_type_array_u8, k_type_objid, k_type_event, k_type_unk_15, k_type_unk_16,
)

MAX_STR = 300
MAX_ARRAY = 16


# ---------------------------------------------------------------------------
# Formatage (fonctions pures, testees dans cotw/tests/test_outils_lecture.py)
# ---------------------------------------------------------------------------

def name_of(h: int, names: Dict[int, List[str]]) -> str:
    candidates = names.get(h & 0xFFFFFFFF)
    if not candidates:
        return '?'
    return candidates[0] if len(candidates) == 1 else '|'.join(candidates[:3])


def _hash_note(v: int, names: Dict[int, List[str]]) -> str:
    candidates = names.get(v & 0xFFFFFFFF)
    return '  = hash de "{}"'.format(candidates[0]) if candidates else ''


def _quote(data) -> str:
    text = data.decode('utf-8', 'replace') if isinstance(data, (bytes, bytearray)) else str(data)
    if len(text) > MAX_STR:
        text = text[:MAX_STR] + '...({} car.)'.format(len(text))
    return json.dumps(text, ensure_ascii=False)


def _floats(values) -> str:
    return '[' + ', '.join('{:.7g}'.format(v) for v in values) + ']'


def format_value(prop, names: Dict[int, List[str]]) -> str:
    t, d = prop.type, prop.data
    if t == k_type_u32:
        return '{} (0x{:08x}){}'.format(d, d, _hash_note(d, names))
    if t == k_type_f32:
        return '{:.7g}'.format(d)
    if t == k_type_str:
        return _quote(d)
    if t == k_type_array_u32:
        shown = ['0x{:08x}{}'.format(v, _hash_note(v, names).replace('  = hash de ', '=')) for v in d[:MAX_ARRAY]]
        more = ' ...' if len(d) > MAX_ARRAY else ''
        return '{} elements [{}{}]'.format(len(d), ', '.join(shown), more)
    if t == k_type_array_f32:
        return '{} elements {}{}'.format(len(d), _floats(d[:MAX_ARRAY]), ' ...' if len(d) > MAX_ARRAY else '')
    if t == k_type_array_u8:
        return '{} octets {}{}'.format(len(d), bytes(d[:64]).hex(' '), ' ...' if len(d) > 64 else '')
    if t == k_type_objid:
        return '0x{:016x}'.format(d)
    if t == k_type_event:
        return '{} elements [{}]'.format(len(d), ', '.join('0x{:016x}'.format(v) for v in d[:MAX_ARRAY]))
    if t in (k_type_none, k_type_unk_15, k_type_unk_16):
        raw = d if d is not None else prop.data_raw
        return '0x{:08x}'.format(raw & 0xFFFFFFFF)
    if isinstance(d, (list, tuple)):
        return _floats(d)
    return repr(d)


def type_name(t: int) -> str:
    return PropType_names[t] if 0 <= t < len(PropType_names) else 'type{}'.format(t)


def format_node(node, names, path: str, indent: int, max_depth: Optional[int], lines: List[str]):
    pad = '  ' * indent
    lines.append('{}[{}] noeud 0x{:08x} {}  ({} proprietes, {} enfants)'.format(
        pad, path, node.name_hash, name_of(node.name_hash, names), len(node.prop_table), len(node.child_table)))
    for p in node.prop_table:
        lines.append('{}    0x{:08x} {:<30} {:<7} {}'.format(
            pad, p.name_hash, name_of(p.name_hash, names), type_name(p.type), format_value(p, names)))
    if max_depth is not None and indent >= max_depth:
        if node.child_table:
            lines.append('{}    ... {} enfants non affiches (--profondeur)'.format(pad, len(node.child_table)))
        return
    for i, c in enumerate(node.child_table):
        format_node(c, names, '{}.{}'.format(path, i), indent + 1, max_depth, lines)


def node_matches(node, terms: Sequence[bytes], term_hashes: Sequence[int]) -> bool:
    for p in node.prop_table:
        if p.type == k_type_str and any(t in bytes(p.data) for t in terms):
            return True
        if p.type == k_type_u32 and p.data in term_hashes:
            return True
        if p.type == k_type_array_u32 and any(v in term_hashes for v in p.data):
            return True
    return False


def format_filtered(rtpc, names, terms: Sequence[bytes], max_depth, lines: List[str]) -> int:
    """Noeuds correspondant a un des termes, precedes de leurs parents (en-tetes seulement)."""
    term_hashes = [W.name_hash(t) for t in terms]
    count = 0

    def walk(node, path, ancestors):
        nonlocal count
        if node_matches(node, terms, term_hashes):
            count += 1
            lines.append('')
            lines.append('=== correspondance {} : noeud [{}]'.format(count, path))
            for depth, (apath, anode) in enumerate(ancestors):
                lines.append('{}[{}] parent 0x{:08x} {}  ({} proprietes, {} enfants)'.format(
                    '  ' * depth, apath, anode.name_hash, name_of(anode.name_hash, names),
                    len(anode.prop_table), len(anode.child_table)))
            format_node(node, names, path, len(ancestors),
                        None if max_depth is None else len(ancestors) + max_depth, lines)
            return  # le contenu est deja affiche en entier
        for i, c in enumerate(node.child_table):
            walk(c, '{}.{}'.format(path, i), ancestors + [(path, node)])

    walk(rtpc.root_node, '0', [])
    return count


def collect_hashes(rtpc, max_values: int = 200000) -> set:
    found = set()

    def walk(node):
        found.add(node.name_hash)
        for p in node.prop_table:
            found.add(p.name_hash)
            if p.type == k_type_u32:
                found.add(p.data)
            elif p.type == k_type_array_u32 and len(found) < max_values:
                found.update(p.data[:MAX_ARRAY])
        for c in node.child_table:
            walk(c)

    walk(rtpc.root_node)
    return found


def summary(rtpc, names, label: str, size: int) -> List[str]:
    stats = {'noeuds': 0, 'proprietes': 0}

    def walk(node):
        stats['noeuds'] += 1
        stats['proprietes'] += len(node.prop_table)
        for c in node.child_table:
            walk(c)

    walk(rtpc.root_node)
    root = rtpc.root_node
    lines = ['# {}'.format(label),
             '# {} octets, RTPC version {}, {} noeuds, {} proprietes'.format(
                 size, rtpc.version, stats['noeuds'], stats['proprietes']),
             '# Enfants de la racine :']
    for i, c in enumerate(root.child_table):
        lines.append('#   [0.{}] 0x{:08x} {:<30} {} proprietes, {} enfants'.format(
            i, c.name_hash, name_of(c.name_hash, names), len(c.prop_table), len(c.child_table)))
    return lines


def to_json(node, names) -> dict:
    def value(p):
        d = p.data
        if isinstance(d, (bytes, bytearray)):
            return d.decode('utf-8', 'replace')
        if p.type in (k_type_none, k_type_unk_15, k_type_unk_16) and d is None:
            return p.data_raw
        return d
    return {
        'hash': '0x{:08x}'.format(node.name_hash),
        'nom': name_of(node.name_hash, names),
        'proprietes': [{'hash': '0x{:08x}'.format(p.name_hash), 'nom': name_of(p.name_hash, names),
                        'type': type_name(p.type), 'valeur': value(p)} for p in node.prop_table],
        'enfants': [to_json(c, names) for c in node.child_table],
    }


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Affiche un RTPC du jeu sous forme d\'arbre lisible.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('fichier', help='chemin virtuel du jeu (ex: settings/hp_settings/equipment_data.bin) ou fichier local')
    ap.add_argument('--out', help='fichier texte de sortie (sinon : affichage a l\'ecran)')
    ap.add_argument('--json', help='fichier JSON de sortie (arbre complet)')
    ap.add_argument('--contient', action='append', default=[],
                    help='n\'afficher que les noeuds contenant ce texte (option repetable)')
    ap.add_argument('--profondeur', type=int, help='profondeur maximale affichee')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    local = Path(args.fichier)
    if local.is_file():
        data, label = local.read_bytes(), str(local)
    else:
        data, label = _projet.read_by_path(vfs, args.fichier), args.fichier
    if data[:4] != W.RTPC_MAGIC:
        raise SystemExit('ERREUR : {} n\'est pas un RTPC (4 premiers octets : {})'.format(label, data[:4]))

    rtpc = W.rtpc_from_bytes(data)
    names = _projet.resolve_hash32(vfs, collect_hashes(rtpc))
    lines = summary(rtpc, names, label, len(data))
    if args.contient:
        terms = [t.encode('utf-8') for t in args.contient]
        n = format_filtered(rtpc, names, terms, args.profondeur, lines)
        lines.insert(2, '# Filtre --contient {} : {} noeud(s)'.format(args.contient, n))
    else:
        lines.append('')
        format_node(rtpc.root_node, names, '0', 0, args.profondeur, lines)

    text = '\n'.join(lines) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {} ({} lignes)'.format(args.out, len(lines)))
    else:
        sys.stdout.write(text)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(to_json(rtpc.root_node, names), f, ensure_ascii=False, indent=1)
        print('Ecrit : {}'.format(args.json))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
