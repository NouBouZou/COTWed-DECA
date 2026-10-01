#!/usr/bin/env python3
"""
liste_objets.py -- liste a cocher des objets d'equipment_data.bin, pour dire lesquels on voit au magasin.

Produit un fichier CSV (separateur ';', UTF-8 avec BOM : Excel l'ouvre directement) avec une ligne par objet :
categorie, nom interne, libelle anglais (propriete note), prix, locked, secondary_ids, et une colonne
"visible" vide. On coche (x) les objets vus au magasin, on enregistre, puis on donne le fichier a
differentiel.py et separateurs.py avec --liste : les objets coches y sont les presents, les autres les absents.

Le prix et le libelle aident a retrouver chaque objet du magasin ; le jeu affiche une traduction, pas le
champ note, donc le libelle peut differer un peu de celui du jeu.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\liste_objets.py D:\\dev\\work\\hp --out D:\\dev\\audits\\objets.csv
    python cotw\\liste_objets.py D:\\dev\\work\\hp --categorie ammo --categorie weapons --categorie sights --out ...
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import cloner_munition as C  # noqa: E402
import rtpc_dump as D  # noqa: E402
import separateurs as SE  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_str, k_type_u32  # noqa: E402

COLUMNS = ['categorie', 'nom', 'libelle', 'prix', 'locked', 'secondary_ids', 'visible']
DEFAULT_CATEGORIES = ['ammo', 'weapons']
H_SECONDARY_IDS = W.name_hash('secondary_ids')
H_LOCKED = W.name_hash('locked')


def u32_of(node, h) -> str:
    p = W.node_get_prop(node, h)
    return str(p.data) if p is not None and p.type == k_type_u32 else ''


def str_of(node, h) -> str:
    p = W.node_get_prop(node, h)
    return bytes(p.data).decode('utf-8', 'replace') if p is not None and p.type == k_type_str else ''


def secondary_ids(node) -> str:
    child = W.node_get_child(node, H_SECONDARY_IDS)
    if child is None:
        return ''
    return '|'.join(bytes(p.data).decode('utf-8', 'replace') for p in child.prop_table if p.type == k_type_str)


def rows_for(items, categories):
    # une categorie se designe par son nom ; si DECA ne connait pas ce nom, elle apparait sous son hash
    wanted = None if 'tout' in categories else (
        set(categories) | {'0x{:08x}'.format(W.name_hash(c)) for c in categories})
    rows = []
    for name, (category, node) in items.items():
        if wanted is not None and category not in wanted:
            continue
        rows.append({'categorie': category, 'nom': name, 'libelle': str_of(node, C.H_NOTE),
                     'prix': u32_of(node, C.H_PRICE), 'locked': u32_of(node, H_LOCKED),
                     'secondary_ids': secondary_ids(node), 'visible': ''})
    rows.sort(key=lambda r: (r['categorie'], r['libelle'].lower(), r['nom']))
    return rows


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=';')
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Liste a cocher des objets d\'equipment_data.bin.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--categorie', action='append', default=[],
                    help='categorie a lister (repetable ; defaut : ammo et weapons ; "tout" pour toutes)')
    ap.add_argument('--base', help='dossier au format dropzone dont lire equipment_data.bin s\'il y est')
    ap.add_argument('--out', required=True, help='fichier CSV a produire')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    base = Path(args.base).expanduser().resolve() if args.base else None
    data, _src = C.load(vfs, base, C.EQUIPMENT_DATA)
    tree = W.rtpc_from_bytes(data)
    names = _projet.resolve_hash32(vfs, D.collect_hashes(tree))
    items = SE.load_items(tree, names)
    categories = args.categorie or DEFAULT_CATEGORIES
    rows = rows_for(items, categories)
    if not rows:
        known = sorted({c for c, _n in items.values()})
        raise SystemExit('ERREUR : aucun objet dans {} (categories connues : {})'.format(categories, ', '.join(known)))
    write_csv(Path(args.out), rows)
    print('{} objets ecrits dans {}. Mettre un x dans la colonne "visible" pour chaque objet vu au magasin.'.format(
        len(rows), args.out))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
