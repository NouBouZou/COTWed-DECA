#!/usr/bin/env python3
"""
Projet de demonstration : un faux theHunter COTW, scanne par DECA, pour travailler sur l'interface sans le jeu.

Le faux jeu reprend la structure reelle relevee dans le journal de recherche :
  settings/hp_settings/equipment_data.bin    RTPC v2 : categories ammo, weapons, sights ; objets avec
                                             cash_price, locked, secondary_ids et compatibilities/compatibility_N
  settings/spawn_ammunition_defs.bin         RTPC v2 : spawn_system/resource_definitions (desc, tags, entity)
  settings/hp_settings/equipment_stats_ui.bin ADF XLSBook (feuilles ammo et weapons)
  editor/entities/.../<munition>.ee          SARC v2 : .epe (RTPC CAmmunitionItem) et .fmod_guids
  textures/ui/items/ammo/<munition>.dds      image DDS non compressee (onglet Image)
  settings/demo_notes.txt                    texte (onglet Text)
  + 3 fichiers dont le nom est efface        hashes orphelins (onglet Non-Mapped List)

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\demo_projet.py              cree le projet s'il n'existe pas, affiche son chemin
    python cotw\\demo_projet.py --recreer    le supprime et le recree
    python cotw\\demo_projet.py --dossier D  autre emplacement (par defaut <depot>\\..\\work_demo)

Le projet est cree a cote du vrai (work_demo, pas work) : il ne touche jamais work\\hp.
"""
import argparse
import contextlib
import shutil
import sqlite3
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Les fabriques de fichiers sont partagees avec les tests : le projet demo est une fixture, pour un humain.
sys.path.insert(0, str(HERE / 'tests'))

import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows)
import faux_projet  # noqa: E402
import adf_fabrique  # noqa: E402
import sarc as S  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_str, k_type_u32, k_type_f32  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402
from deca.util import deca_root  # noqa: E402

MARKER = 'cotwed_demo.txt'

# Munitions : (nom, prix, locked, secondary_ids, armes compatibles)
AMMO = [
    ('equipment_ammo_243_sp_01', 0, 1, '-1', ['equipment_weapon_ba_rifle_243_01', 'equipment_weapon_pistol_ss_243_01']),
    ('equipment_ammo_243_pt_01', 45, 0, '-1', ['equipment_weapon_ba_rifle_243_01']),
    ('equipment_ammo_270_sp_01', 52, 1, '-1', ['equipment_weapon_ba_rifle_270_01']),
    ('equipment_ammo_762x54r_sp_01', 77, 3, '1095272277', ['equipment_weapon_ba_rifle_762x54r_01']),
    ('equipment_ammo_762x54r_fmj_01', 80, 5, '1095272277', ['equipment_weapon_ba_rifle_762x54r_01']),
]
WEAPONS = [
    ('equipment_weapon_ba_rifle_243_01', 0, 1, '-1'),
    ('equipment_weapon_pistol_ss_243_01', 620, 0, '-1'),
    ('equipment_weapon_ba_rifle_270_01', 1150, 0, '-1'),
    ('equipment_weapon_ba_rifle_762x54r_01', 1680, 3, '1095272277'),
]
SIGHTS = [('equipment_sight_scope_4x32_01', 300, 0, '-1')]
# Noms effaces apres le scan : ils deviennent des hashes orphelins
ORPHANS = ['hidden/demo_orphan_01.bin', 'hidden/demo_orphan_02.rtpc', 'hidden/demo_orphan_03.bin']


def entity_path(name: str, sep: str = '/') -> str:
    return sep.join(['editor', 'entities', 'hp_weapons', 'ammunition', 'rifles', name + '.ee'])


def rtpc_bytes(root, version: int = 2) -> bytes:
    r = Rtpc()
    r.version = version
    r.root_node = root
    return W.rtpc_to_binary(r)


def item(name: str, price: int, locked: int, dlc: str, children=()):
    return W.make_node(name, [
        W.make_prop('name', k_type_str, name.encode()),
        W.make_prop('note', k_type_str, name.replace('equipment_', '').replace('_', ' ').encode()),
        W.make_prop('display_name', k_type_str, (name[:-3] + '_name').encode()),
        W.make_prop('cash_price', k_type_u32, price),
        W.make_prop('locked', k_type_u32, locked),
        W.make_prop('capacity_cost', k_type_f32, 0.02),
        W.make_prop('units', k_type_u32, 10),
    ], [W.make_node('secondary_ids', [W.make_prop(0xa4f0837b, k_type_str, dlc.encode())])] + list(children))


def equipment_data() -> bytes:
    ammo = []
    for name, price, locked, dlc, weapons in AMMO:
        compat = W.make_node('compatibilities', [], [
            W.make_node('compatibility_{}'.format(i), [
                W.make_prop('equipment_name', k_type_str, w.encode()),
                W.make_prop('ammunition_slot_index', k_type_u32, 0)])
            for i, w in enumerate(weapons)])
        icon = 'textures\\ui\\items\\ammo\\{}_alpha.ddsc'.format(name)
        node = item(name, price, locked, dlc, [compat])
        W.node_add_prop(node, W.make_prop('display_icon', k_type_str, icon.encode()), position=len(node.prop_table))
        ammo.append(node)
    weapons = [item(*w) for w in WEAPONS]
    sights = [item(*s) for s in SIGHTS]
    root = W.make_node(0, [], [W.make_node('ammo', [], ammo), W.make_node('weapons', [], weapons),
                               W.make_node('sights', [], sights)])
    return rtpc_bytes(root)


def spawn_defs() -> bytes:
    entries = []
    for name, *_ in AMMO:
        cal, kind = name.split('_')[2], name.split('_')[3]
        entries.append(W.make_node(name, [
            W.make_prop('desc', k_type_str, name.encode()),
            W.make_prop('tags', k_type_str, '{},ammo_cal_{},ammo_type_{},ammunition'.format(name, cal, kind).encode()),
            W.make_prop('entity', k_type_str, entity_path(name, '\\').encode()),
            W.make_prop('debug', k_type_str, b'Ammunition')]))
    return rtpc_bytes(W.make_node(0, [], [W.make_node('spawn_system', [], [
        W.make_node('resource_definitions', [], entries)])]))


def stats_ui() -> bytes:
    ammo_rows = [['name', 'class', 'penetration', 'expansion', 'note']]
    for name, *_ in AMMO:
        ammo_rows.append([name, '2-6', 0.25, 0.75, name.split('_')[3]])
    weapon_rows = [['name', 'zoom', 'enabled']] + [[w[0], 1.5, True] for w in WEAPONS]
    # disposition et chaine de relocation du vrai fichier (donnees avant les tables, voir adf_ecriture.py)
    return adf_fabrique.xlsbook([('ammo', ammo_rows), ('weapons', weapon_rows)], order='donnees_avant')


def entity(name: str) -> bytes:
    """Entite .ee d'une munition, comme celle de la .243 SP : un SARC v2 avec .epe (RTPC) et .fmod_guids."""
    base = 'editor/entities/hp_weapons/ammunition/rifles/' + name
    epe = rtpc_bytes(W.make_node(0, [], [W.make_node('CAmmunitionItem', [
        W.make_prop('_class', k_type_str, b'CAmmunitionItem'),
        W.make_prop('item_id', k_type_str, name.encode()),
        W.make_prop('item_category', k_type_u32, 0),
        W.make_prop('tuning_file', k_type_str, (base + '.ammotune').encode())])]), version=1)
    data, _ = S.build(S.Sarc(2, [S.Entry(base + '.epe', epe, len(epe)),
                                  S.Entry(base + '.fmod_guids', b'\x01\x02\x03\x04\x05\x06\x07\x08', 8)]))
    return data


def dds_rgba(width: int = 64, height: int = 64) -> bytes:
    """DDS non compresse 32 bits (A8R8G8B8) : degrade, pour tester l'onglet Image sans bibliotheque C++."""
    header = struct.pack('<4sIIIIIII', b'DDS ', 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x8, height, width, width * 4, 0, 1)
    header += b'\0' * 44
    header += struct.pack('<IIIIIIII', 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    header += struct.pack('<IIIII', 0x1000, 0, 0, 0, 0)
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes((x * 4 & 0xFF, y * 4 & 0xFF, 0x80, 0xFF))  # B, G, R, A
    return header + bytes(pixels)


def orphan_rtpc() -> bytes:
    return rtpc_bytes(W.make_node(0, [W.make_prop(0x4923c2e2, k_type_u32, 7)]), version=1)


def files():
    out = [
        ('settings/hp_settings/equipment_data.bin', equipment_data()),
        ('settings/spawn_ammunition_defs.bin', spawn_defs()),
        ('settings/hp_settings/equipment_stats_ui.bin', stats_ui()),
        ('settings/demo_notes.txt', 'COTWed DECA demo project.\nNo game data here: every file is synthetic.\n'.encode()),
    ]
    for name, *_ in AMMO[:3]:
        out.append((entity_path(name), entity(name)))
    out.append(('textures/ui/items/ammo/equipment_ammo_243_sp_01_alpha.dds', dds_rgba()))
    out += [(ORPHANS[0], b'\x13\x37\x00\x00' + b'\0' * 60),
            (ORPHANS[1], orphan_rtpc()),
            (ORPHANS[2], bytes(range(256)))]
    return out


def default_root() -> Path:
    return Path(deca_root()).resolve().parent / 'work_demo'


def project_file(root: Path) -> Path:
    return root / 'work' / 'hp' / 'project.json'


def build(root: Path) -> Path:
    """Cree le faux jeu et son projet dans root (qui ne doit pas exister) ; renvoie le chemin de project.json."""
    working_dir = Path(faux_projet.build_fake_project(str(root), files()))
    db = sqlite3.connect(str(working_dir / 'db' / 'core.db'))
    for p in ORPHANS:
        db.execute('UPDATE core_nodes SET v_path = NULL WHERE v_hash = ?', (int(hash32_func(p.encode())),))
    db.commit()
    db.close()
    (root / MARKER).write_text('Projet de demonstration COTWed DECA (cotw/demo_projet.py). Supprimable.\n', encoding='utf-8')
    return working_dir / 'project.json'


def ensure(root: Path, recreate: bool = False) -> Path:
    if recreate and root.exists():
        if not (root / MARKER).is_file():
            raise SystemExit('{} existe mais n\'est pas un projet demo ({} absent) : rien n\'est supprime.'.format(root, MARKER))
        shutil.rmtree(root)
    if project_file(root).is_file():
        return project_file(root)
    if root.exists() and any(root.iterdir()):
        raise SystemExit('{} existe deja et n\'est pas un projet demo complet : choisis un autre --dossier.'.format(root))
    if root.exists():
        root.rmdir()
    return build(root)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dossier', type=Path, default=None, help='emplacement (defaut : <depot>\\..\\work_demo)')
    ap.add_argument('--recreer', action='store_true', help='supprimer le projet demo et le recreer')
    args = ap.parse_args(argv)
    root = (args.dossier or default_root()).resolve()
    # Le journal du scan DECA part sur stderr : stdout ne contient que le chemin du projet, que
    # lancer_demo.bat recupere.
    with contextlib.redirect_stdout(sys.stderr):
        pf = ensure(root, args.recreer)
    print(pf)
    return pf


if __name__ == '__main__':
    main()
