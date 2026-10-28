#!/usr/bin/env python3
"""
adf_dump.py -- affiche un fichier ADF du jeu sous forme d'arbre lisible, ou seulement ce qui cite un objet.

Complement de rtpc_dump.py pour l'autre grand format de donnees de COTW (equipment_stats_ui.bin,
player_initial_loadout.bin, player_rewards.bin, .ammotunec...). Les types viennent de la base DECA (types
trouves dans l'exe et dans les fichiers). Les StringHash et les entiers egaux au hash32 d'une chaine connue
sont annotes avec leur nom (base DECA + dictionnaire du fork).

Modes :
  (defaut)        arbre complet (--profondeur et --max-elements bornent la sortie) ;
  --contient T    chaque valeur qui cite T (texte, hash32, hash48, hash64), avec son chemin, son offset et
                  la "ligne" qui la contient (element de tableau englobant) ; option repetable ;
  --offset X      le champ qui contient l'offset X du fichier (ex : un offset donne par indexer_refs ou
                  differentiel) ; option repetable.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\adf_dump.py D:\\dev\\work\\hp settings/hp_settings/equipment_stats_ui.bin --out D:\\dev\\audits\\stats_ui.txt
    python cotw\\adf_dump.py D:\\dev\\work\\hp settings/hp_settings/equipment_stats_ui.bin ^
        --contient equipment_ammo_243_sp_01 --contient equipment_ammo_762x54r_fmj_01
    python cotw\\adf_dump.py D:\\dev\\work\\hp settings/hp_settings/player_rewards.bin --offset 0x1a2c
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import citations as CI  # noqa: E402
import resoudre_hashes as RH  # noqa: E402

MAX_COLLECT = 300000


def type_name(adf, type_id) -> str:
    from deca.ff_adf import adf_type_id_to_str
    try:
        return adf_type_id_to_str(type_id, adf.extended_map_typedef)
    except Exception:
        return '0x{:08x}'.format(type_id)


def collect_ints(adf) -> set:
    out = set()
    for leaf in CI.adf_leaves(adf):
        if isinstance(leaf.value, int) and not isinstance(leaf.value, bool) and 0 < leaf.value <= 0xFFFFFFFF:
            out.add(leaf.value)
            if len(out) >= MAX_COLLECT:
                break
    return out


def show(value, kind: str, note: Optional[str], names: Dict[int, List[str]]) -> str:
    if kind in ('chaine', 'octets'):
        if isinstance(value, (bytes, bytearray)):
            text = bytes(value[:200]).decode('utf-8', 'replace')
            return '"{}"{}'.format(text, '...' if len(value) > 200 else '')
        return repr(value)
    if kind.startswith('hash'):
        name = note or (names.get(value)[0] if isinstance(value, int) and names.get(value) else None)
        return '0x{:x}{}'.format(value, '  # "{}"'.format(name) if name else '  # ?')
    if kind == 'enum':
        return '{} ({})'.format(note, value)
    if isinstance(value, float):
        return '{:.7g}'.format(value)
    if isinstance(value, int) and not isinstance(value, bool) and names.get(value):
        return '{} (0x{:08x})  # = hash de "{}"'.format(value, value, names[value][0])
    return repr(value)


def format_tree(adf, names, max_depth: Optional[int], max_elems: int) -> List[str]:
    """Arbre indente : structures et tableaux de structures, puis valeurs."""
    from deca.ff_adf import AdfValue, AdfPointer, MetaType
    type_map = adf.extended_map_typedef
    lines: List[str] = []

    def rec(v, label, depth):
        pad = '  ' * depth
        if max_depth is not None and depth > max_depth:
            return
        if isinstance(v, AdfValue):
            td = type_map.get(v.type_id)
            mt = td.metatype if td is not None else None
            if v.type_id == CI.DEFERRED_TYPE:
                rec(v.value, label + ' (differe)', depth)
            elif mt == MetaType.Pointer and isinstance(v.value, AdfPointer):
                lines.append('{}{} : {} {}  @0x{:x}'.format(pad, label, type_name(adf, v.type_id),
                                                           v.value.describe(), v.info_offset))
                if v.value.value is not None:
                    rec(v.value.value, '*' + label, depth + 1)
            elif isinstance(v.value, dict):
                lines.append('{}{} : {}  @0x{:x}'.format(pad, label, type_name(adf, v.type_id), v.data_offset))
                for k, iv in v.value.items():
                    rec(iv, k, depth + 1)
            elif mt in (MetaType.Array, MetaType.InlineArray):
                elems = v.value if v.value is not None else []
                lines.append('{}{} : {} [{} elements]  @0x{:x}'.format(pad, label, type_name(adf, v.type_id), len(elems),
                                                                     v.data_offset))
                if isinstance(elems, (bytes, bytearray)):
                    lines.append('{}  {}'.format(pad, bytes(elems[:64]).hex(' ') + (' ...' if len(elems) > 64 else '')))
                elif len(elems) and not isinstance(elems[0], AdfValue):
                    shown = [show(x, 'nombre', None, names) for x in elems[:max_elems]]
                    lines.append('{}  [{}{}]'.format(pad, ', '.join(shown), ', ...' if len(elems) > max_elems else ''))
                else:
                    for i, iv in enumerate(elems[:max_elems]):
                        rec(iv, '[{}]'.format(i), depth + 1)
                    if len(elems) > max_elems:
                        lines.append('{}  ... {} autres elements (option --max-elements)'.format(pad, len(elems) - max_elems))
            else:
                kind = ('hash' if mt == MetaType.StringHash else 'chaine' if v.type_id == CI.STRING_TYPE
                        else 'enum' if mt == MetaType.Enumeration else 'nombre')
                note = v.hash_string if kind == 'hash' else v.enum_string if kind == 'enum' else None
                if isinstance(note, bytes):
                    note = note.decode('utf-8', 'replace')
                lines.append('{}{} = {}  [{}] @0x{:x}'.format(pad, label, show(v.value, kind, note, names),
                                                             type_name(adf, v.type_id), v.data_offset))
        elif isinstance(v, tuple):
            lines.append('{}{} = pointeur {}'.format(pad, label, v[1] if len(v) > 1 else v))
        elif v is None:
            lines.append('{}{} = (absent)'.format(pad, label))
        else:
            lines.append('{}{} = {!r}'.format(pad, label, v))

    for ins, fv in zip(adf.table_instance, adf.table_instance_full_values):
        name = ins.name.decode('utf-8', 'replace') if isinstance(ins.name, bytes) else str(ins.name)
        lines.append('# instance {} : type 0x{:08x} {}'.format(name, ins.type_hash, type_name(adf, ins.type_hash)))
        rec(fv, name, 0)
        lines.append('')
    return lines


def format_matches(adf, names, terms: List[str]) -> List[str]:
    lines = []
    for term in terms:
        found = CI.find_in_adf(adf, CI.pattern(term))
        lines.append('## "{}" : {} citation(s)'.format(term, len(found)))
        rows_shown = set()
        for leaf, how in found:
            lines.append('- {} = {}  ({}, @0x{:x})'.format(leaf.path, show(leaf.value, leaf.kind, leaf.note, names),
                                                          how, leaf.offset if leaf.offset is not None else -1))
            row = CI.row_of(leaf)
            if row is not None and row[0] not in rows_shown:
                rows_shown.add(row[0])
                lines.append('  ligne {} :'.format(row[0]))
                sub = _SubAdf(adf, row[1])
                lines += ['    ' + l for l in format_tree(sub, names, 3, 40)[1:] if l]
        lines.append('')
    return lines


class _SubAdf:
    """Vue d'une seule valeur d'un ADF, pour reutiliser format_tree sur une ligne."""

    def __init__(self, adf, value):
        self.extended_map_typedef = adf.extended_map_typedef

        class _Ins:
            name = b'ligne'
            type_hash = value.type_id
        self.table_instance = [_Ins()]
        self.table_instance_full_values = [value]


def format_offsets(adf, names, offsets: List[int]) -> List[str]:
    leaves = [l for l in CI.adf_leaves(adf) if l.offset is not None]
    lines = []
    for off in offsets:
        best = [l for l in leaves if l.offset <= off < l.offset + max(_size(l), 1)]
        if not best:
            near = min(leaves, key=lambda l: abs(l.offset - off)) if leaves else None
            lines.append('- 0x{:x} : aucun champ ne commence ici{}'.format(
                off, ' ; le plus proche : {} @0x{:x}'.format(near.path, near.offset) if near else ''))
        for l in best:
            lines.append('- 0x{:x} : {} = {}  ({})'.format(off, l.path, show(l.value, l.kind, l.note, names), l.kind))
    return lines


def _size(leaf) -> int:
    if leaf.kind in ('chaine', 'octets') and isinstance(leaf.value, (bytes, bytearray)):
        return len(leaf.value) + 1
    if leaf.kind == 'hash48':
        return 6
    if leaf.kind == 'hash64':
        return 8
    if isinstance(leaf.value, int) and not isinstance(leaf.value, bool) and leaf.value > 0xFFFFFFFF:
        return 8
    return 4


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Affiche un fichier ADF du jeu.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('fichier', help='chemin virtuel (ex : settings/hp_settings/equipment_stats_ui.bin) ou fichier local')
    ap.add_argument('--contient', action='append', default=[], help='terme a chercher (option repetable)')
    ap.add_argument('--offset', action='append', default=[], help='offset du fichier, ex : 0x1a2c (option repetable)')
    ap.add_argument('--profondeur', type=int, help='profondeur maximale de l\'arbre')
    ap.add_argument('--max-elements', type=int, default=200, help='elements affiches par tableau (defaut 200)')
    ap.add_argument('--out', help='fichier texte de sortie (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    kind, adf = RH.FileReader(vfs).decode(args.fichier)
    if kind != 'adf':
        raise SystemExit('ERREUR : {} : {}'.format(args.fichier, 'fichier RTPC : utiliser rtpc_dump.py' if kind == 'rtpc' else adf))
    names = _projet.resolve_hash32(vfs, collect_ints(adf))
    L = ['# {} : ADF, {} instance(s), {} type(s) definis dans le fichier'.format(
        args.fichier, len(adf.table_instance), len(adf.map_typedef)), '']
    if args.contient:
        L += format_matches(adf, names, args.contient)
    if args.offset:
        L += ['## Offsets', ''] + format_offsets(adf, names, [int(o, 0) for o in args.offset]) + ['']
    if not args.contient and not args.offset:
        L += format_tree(adf, names, args.profondeur, args.max_elements)
    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
