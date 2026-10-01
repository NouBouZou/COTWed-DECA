#!/usr/bin/env python3
"""
comparer_objets.py -- compare cote a cote plusieurs objets d'equipement du jeu.

But : trouver ce qui distingue un objet disponible en jeu d'un objet present dans les fichiers mais
absent du jeu (munitions "placeholder" connues de la communaute : equipment_ammo_762x54r_fmj_01,
equipment_ammo_410_slug_01, equipment_ammo_28ga_*), et donc ce qui manque a un objet ajoute.

Pour chaque objet :
  1. ses proprietes et ses enfants dans settings/hp_settings/equipment_data.bin ;
  2. son entree dans chaque settings/spawn_*_defs.bin (entite, etiquettes) ;
  3. l'existence de son entite (.ee) dans les archives du jeu ;
  4. ses citations dans des fichiers de reference RTPC ou ADF (par defaut equipment_stats_ui,
     player_initial_loadout, player_rewards, equipment_tuning ; --fichier pour d'autres), en texte ou par
     hash32/48/64, avec le chemin du champ ;
  5. la liste des fichiers ou le scan DECA a trouve son nom en texte.
Les lignes dont les valeurs different entre objets sont marquees d'un "≠".

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\comparer_objets.py D:\\dev\\work\\hp equipment_ammo_762x54r_sp_01 equipment_ammo_762x54r_fmj_01 ^
        equipment_ammo_410ga_slug_01 equipment_ammo_410_slug_01 --out D:\\dev\\audits\\comparaison.md
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import rtpc_dump as D  # noqa: E402
import cloner_munition as C  # noqa: E402
import citations as CI  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_none, k_type_str  # noqa: E402

EQUIPMENT_DATA = C.EQUIPMENT_DATA
MISSING = '—'
REFERENCE_FILES = [
    'settings/hp_settings/equipment_stats_ui.bin',
    'settings/hp_settings/player_initial_loadout.bin',
    'settings/hp_settings/player_rewards.bin',
    'settings/hp_settings/equipment_tuning.bin',
]


def reference_rows(vfs, base, files, items) -> List[List[str]]:
    """Une ligne par fichier de reference : nombre de citations de chaque objet et premier champ concerne."""
    import resoudre_hashes as RH
    reader = RH.FileReader(vfs)
    rows = []
    for v_path in files:
        local = base / Path(v_path) if base is not None else None
        kind, obj = reader.decode(str(local) if local is not None and local.is_file() else v_path)
        row = ['{} ({})'.format(Path(v_path).name, kind.upper() if kind in ('rtpc', 'adf') else obj)]
        for it in items:
            found = CI.find(kind, obj, it)
            row.append('{} : {}'.format(len(found), found[0]) if found else MISSING)
        rows.append(row)
    return rows


def cell(text: str, limit: int = 60) -> str:
    text = text.replace('|', '/').replace('\n', ' ')
    if len(text) <= limit:
        return text
    if '\\' in text or '/' in text:
        return '...' + text[-(limit - 3):]  # pour un chemin, la fin est la partie qui distingue
    return text[:limit - 3] + '...'


def find_node_by_name(root, name: str):
    """(chemin, noeud) du noeud dont le hash est hash32(name) et qui porte ce nom en texte ; sinon le 1er de ce hash."""
    h = W.name_hash(name)
    target = name.encode('utf-8')
    candidates = []

    def walk(node, path):
        for i, c in enumerate(node.child_table):
            p = '{}.{}'.format(path, i)
            if c.name_hash == h:
                named = any(q.type == k_type_str and bytes(q.data) == target for q in c.prop_table)
                candidates.append((not named, p, c))
            walk(c, p)

    walk(root, '0')
    if not candidates:
        return None, None
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1], candidates[0][2]


def child_summary(child, names) -> str:
    """Resume d'un enfant : liste d'armes pour une liste de compatibilite, sinon ses valeurs."""
    entries = [c for c in child.child_table if W.node_get_prop(c, C.H_EQUIPMENT_NAME) is not None]
    if entries:
        return '{} entree(s) : {}'.format(len(entries), ', '.join(
            bytes(W.node_get_prop(e, C.H_EQUIPMENT_NAME).data).decode('utf-8', 'replace') for e in entries))
    strs = [bytes(p.data).decode('utf-8', 'replace') for p in child.prop_table if p.type == k_type_str]
    if strs and not child.child_table:
        return '{} chaine(s) : {}'.format(len(strs), ', '.join(strs))
    vals = ['{}={}'.format(D.name_of(p.name_hash, names), D.format_value(p, {}))
            for p in child.prop_table if p.type != k_type_none]
    extra = ' +{} enfants'.format(len(child.child_table)) if child.child_table else ''
    return (', '.join(vals) or '(vide)') + extra


def equipment_rows(tree, items: Sequence[str], names) -> List[List[str]]:
    nodes = {}
    rows = [['chemin dans le fichier']]
    for it in items:
        path, node = find_node_by_name(tree.root_node, it)
        nodes[it] = node
        rows[0].append(path or 'ABSENT')
    order: List[int] = []
    for it in items:
        n = nodes[it]
        if n is None:
            continue
        for p in n.prop_table:
            if p.type != k_type_none and p.name_hash not in order:
                order.append(p.name_hash)
    for h in order:
        row = ['0x{:08x} {}'.format(h, D.name_of(h, names))]
        for it in items:
            n = nodes[it]
            p = W.node_get_prop(n, h) if n is not None else None
            row.append(D.format_value(p, {}) if p is not None and p.type != k_type_none else MISSING)
        rows.append(row)
    row = ['emplacements (dont none)']
    for it in items:
        n = nodes[it]
        row.append(MISSING if n is None else '{} ({})'.format(
            len(n.prop_table), sum(1 for p in n.prop_table if p.type == k_type_none)))
    rows.append(row)
    child_hashes: List[int] = []
    for it in items:
        n = nodes[it]
        for c in (n.child_table if n is not None else []):
            if c.name_hash not in child_hashes:
                child_hashes.append(c.name_hash)
    for h in child_hashes:
        row = ['enfant 0x{:08x} {}'.format(h, D.name_of(h, names))]
        for it in items:
            n = nodes[it]
            c = W.node_get_child(n, h) if n is not None else None
            row.append(child_summary(c, names) if c is not None else MISSING)
        rows.append(row)
    return rows


def spawn_rows(spawn_trees: Dict[str, object], items: Sequence[str]) -> List[List[str]]:
    rows = []
    for v_path, tree in spawn_trees.items():
        found = {it: find_node_by_name(tree.root_node, it)[1] for it in items}
        if not any(found.values()):
            continue
        for label, prop in (('entite', C.H_ENTITY), ('tags', C.H_TAGS)):
            row = ['{} : {}'.format(Path(v_path).name, label)]
            for it in items:
                n = found[it]
                p = W.node_get_prop(n, prop) if n is not None else None
                row.append(bytes(p.data).decode('utf-8', 'replace') if p is not None else MISSING)
            rows.append(row)
    return rows


def markdown_table(items: Sequence[str], rows: List[List[str]]) -> List[str]:
    lines = ['| | Champ | ' + ' | '.join('`{}`'.format(it) for it in items) + ' |',
             '|---|---|' + '---|' * len(items)]
    for r in rows:
        values = r[1:]
        differs = '≠' if len(set(values)) > 1 else ''
        lines.append('| {} | {} | {} |'.format(differs, cell(r[0], 50), ' | '.join(cell(v) for v in values)))
    return lines


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Compare cote a cote plusieurs objets d\'equipement.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('objets', nargs='+', help='noms des objets, ex : equipment_ammo_762x54r_sp_01')
    ap.add_argument('--base', help='dossier au format dropzone dont lire les fichiers s\'ils y sont (ex : sortie de cloner_munition)')
    ap.add_argument('--fichier', action='append', default=[],
                    help='fichier de reference RTPC ou ADF ou chercher les objets (option repetable ; '
                         'defaut : equipment_stats_ui, player_initial_loadout, player_rewards, equipment_tuning)')
    ap.add_argument('--out', help='fichier markdown de sortie (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    base = Path(args.base).expanduser().resolve() if args.base else None
    items = args.objets

    eq_bytes, eq_src = C.load(vfs, base, EQUIPMENT_DATA)
    eq_tree = W.rtpc_from_bytes(eq_bytes)
    names = _projet.resolve_hash32(vfs, D.collect_hashes(eq_tree))

    spawn_paths = sorted({_projet.to_text(r[0]) for r in vfs.db_query_all(
        "SELECT DISTINCT v_path FROM core_nodes WHERE v_path LIKE 'settings/spawn_%_defs.bin'", [])})
    spawn_trees = {}
    for v in spawn_paths:
        data, _ = C.load(vfs, base, v)
        if data[:4] == W.RTPC_MAGIC:
            spawn_trees[v] = W.rtpc_from_bytes(data)

    L = ['# Comparaison d\'objets', '', '- equipment_data : {}'.format(eq_src),
         '- fichiers spawn lus : {}'.format(', '.join(spawn_trees) or 'aucun'), '',
         '## 1. equipment_data.bin et fichiers spawn', '']
    rows = equipment_rows(eq_tree, items, names) + spawn_rows(spawn_trees, items)

    entity_row = ['entite presente dans le jeu']
    for it in items:
        paths = [r for r in rows if r[0].endswith(': entite')]
        ent = next((r[1 + items.index(it)] for r in paths if r[1 + items.index(it)] != MISSING), None)
        if ent is None:
            entity_row.append(MISSING)
        else:
            nodes = [n for n in _projet.nodes_by_path(vfs, ent.replace('\\', '/')) if n.offset is not None]
            entity_row.append('oui' if nodes else 'NON')
    rows.append(entity_row)
    L += markdown_table(items, rows)

    L += ['', '## 2. Citations dans les fichiers de reference (texte, hash32, hash48, hash64)', '']
    L += markdown_table(items, reference_rows(vfs, base, args.fichier or REFERENCE_FILES, items))

    L += ['', '## 3. Fichiers ou le scan a trouve le nom en texte', '']
    import deca_refs as R
    per_item = {}
    for it in items:
        files = set()
        for _s, refs in R.find_strings(vfs, it).items():
            files.update(f for f, _t in refs)
        per_item[it] = files
    all_files = sorted(set().union(*per_item.values()))
    file_rows = [[f] + [('oui' if f in per_item[it] else MISSING) for it in items] for f in all_files]
    L += markdown_table(items, file_rows)
    L += ['', 'Les lignes marquees ≠ sont celles ou les objets different.']

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
