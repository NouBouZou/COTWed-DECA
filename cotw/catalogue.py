#!/usr/bin/env python3
"""
catalogue.py -- les munitions et les armes du jeu, lues dans les fichiers d'origine, pour l'atelier de l'interface.

Pour chaque munition d'equipment_data.bin (categorie ammo) :
  - nom interne, libelle (note), prix (cash_price), locked, DLC (secondary_ids) ;
  - nom et description affiches dans une langue, lus dans string_ids.stringlookup par le hash32 des cles
    display_name et display_description ;
  - armes compatibles (compatibilities/compatibility_N/equipment_name) ;
  - entite (.ee) d'apres spawn_ammunition_defs.bin ;
  - ligne de la feuille ammo d'equipment_stats_ui.bin, et si le jeu la lit (avant la premiere ligne vide, test12 H).

Disponibilite au magasin, d'apres le releve du 2026-10-01 (256 objets, jeu sans DLC) :
  - jeu de base (secondary_ids = -1) : visible si et seulement si locked != 5 ;
  - DLC : visible seulement si l'un des DLC est possede, ou offert a tous (3 identifiants releves).

Usage (controle), depuis la racine du depot :
    python cotw\\catalogue.py D:\\dev\\work\\hp [--langue fr]
"""
import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)
import cloner_munition as CM  # noqa: E402

import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_str, k_type_u32  # noqa: E402

H_LOCKED = W.name_hash('locked')
H_SECONDARY_IDS = W.name_hash('secondary_ids')
CATEGORY_AMMO = 'ammo'
CATEGORY_WEAPONS = 'weapons'
STATS_SHEET = 'ammo'
BASE_GAME = '-1'
LOCKED_HIDDEN = 5
# DLC offerts a tous : leurs objets sont visibles sans achat (releve du magasin, 2026-10-01)
FREE_DLC = {'3988076135', '2667029300', '1246326955'}

VISIBLE = 'base game'
HIDDEN = 'hidden (locked = 5)'
DLC = 'DLC'
FREE = 'free DLC'


class ECatalogue(Exception):
    pass


@dataclass
class Item:
    category: str
    name: str
    label: str = ''
    display_name: Optional[str] = None       # dans la langue du catalogue ; None : cle introuvable
    description: Optional[str] = None
    display_key: Optional[str] = None
    description_key: Optional[str] = None
    price: Optional[int] = None
    locked: Optional[int] = None
    dlc: List[str] = field(default_factory=list)
    weapons: List[str] = field(default_factory=list)       # munitions : armes compatibles
    entity: Optional[str] = None                            # munitions : chemin de l'entite (.ee), '/' comme separateur
    stats: Optional[List[object]] = None                    # munitions : ligne de la feuille ammo
    stats_row: Optional[int] = None
    stats_read_by_game: bool = False                         # ligne avant la premiere ligne vide de la feuille

    @property
    def availability(self) -> str:
        if not self.dlc or self.dlc == [BASE_GAME]:
            return HIDDEN if self.locked == LOCKED_HIDDEN else VISIBLE
        return FREE if any(d in FREE_DLC for d in self.dlc) else DLC

    def shown(self) -> str:
        """Nom affiche, ou nom interne s'il manque."""
        return self.display_name or self.name


@dataclass
class Catalogue:
    language: str
    ammo: List[Item]
    weapons: List[Item]
    stats_header: List[object] = field(default_factory=list)
    stats_first_empty_row: Optional[int] = None
    warnings: List[str] = field(default_factory=list)

    def find(self, name: str) -> Optional[Item]:
        for it in self.ammo + self.weapons:
            if it.name == name:
                return it
        return None

    def names(self) -> set:
        return {it.name for it in self.ammo + self.weapons}


def _u32(node, h) -> Optional[int]:
    p = W.node_get_prop(node, h)
    return p.data if p is not None and p.type == k_type_u32 else None


def _str(node, h) -> Optional[str]:
    p = W.node_get_prop(node, h)
    return bytes(p.data).decode('utf-8', 'replace') if p is not None and p.type == k_type_str else None


def _dlc(node) -> List[str]:
    child = W.node_get_child(node, H_SECONDARY_IDS)
    if child is None:
        return []
    return [bytes(p.data).decode('utf-8', 'replace') for p in child.prop_table if p.type == k_type_str]


def _weapons(node) -> List[str]:
    compat = W.node_get_child(node, CM.H_COMPAT)
    if compat is None:
        return []
    out = []
    for e in compat.child_table:   # deja tries par cle, donc par position (compatibility_N)
        name = _str(e, CM.H_EQUIPMENT_NAME)
        if name:
            out.append(name)
    return out


def _items(root, category: str) -> List[Item]:
    cat = W.node_get_child(root, W.name_hash(category))
    if cat is None:
        return []
    out = []
    for node in cat.child_table:
        name = _str(node, CM.H_NAME)
        if not name:
            continue
        out.append(Item(category=category, name=name, label=_str(node, CM.H_NOTE) or '',
                        display_key=_str(node, CM.H_DISPLAY_NAME), description_key=_str(node, CM.H_DISPLAY_DESC),
                        price=_u32(node, CM.H_PRICE), locked=_u32(node, H_LOCKED), dlc=_dlc(node),
                        weapons=_weapons(node) if category == CATEGORY_AMMO else []))
    return out


def _texts(vfs, items: List[Item], language: str, warnings: List[str]):
    import stringlookup as SL
    try:
        vue = SL.Vue(_projet.read_by_path(vfs, SL.STRING_IDS))
    except (SystemExit, SL.EStringLookup) as e:
        warnings.append('noms affiches indisponibles : {}'.format(e))
        return
    lang = SL.language_index(language)
    if lang >= vue.n_lang:
        warnings.append('string_ids n\'a que {} langues'.format(vue.n_lang))
        return
    offsets = dict(vue.pairs(lang))
    for it in items:
        for key_attr, text_attr in (('display_key', 'display_name'), ('description_key', 'description')):
            key = getattr(it, key_attr)
            if key:
                off = offsets.get(SL.key_hash(key))
                setattr(it, text_attr, vue.text(off) if off is not None else None)


def _spawn(vfs, ammo: List[Item], warnings: List[str]):
    try:
        tree = W.rtpc_from_bytes(_projet.read_by_path(vfs, CM.SPAWN_AMMO))
    except SystemExit as e:
        warnings.append('entites indisponibles : {}'.format(e))
        return
    by_name = {}

    def walk(node):
        for c in node.child_table:
            name = _str(c, CM.H_SPAWN_NAME)
            if name:
                by_name[name] = c
            walk(c)

    walk(tree.root_node)
    for it in ammo:
        node = by_name.get(it.name)
        if node is not None:
            path = _str(node, CM.H_ENTITY)
            it.entity = path.replace('\\', '/') if path else None


def _stats(vfs, cat: Catalogue):
    import adf_ajouter_ligne as AL
    import resoudre_hashes as RH
    import xlsbook as X
    try:
        data = _projet.read_by_path(vfs, AL.STATS_UI)
        values = AL.decoder(RH.FileReader(vfs))(data)
    except SystemExit as e:
        cat.warnings.append('equipment_stats_ui indisponible : {}'.format(e))
        return
    if values is None:
        cat.warnings.append('equipment_stats_ui illisible par DECA')
        return
    values = values.table_instance_values[0] if hasattr(values, 'table_instance_values') else values
    try:
        X.require_book(values)
    except X.EXls as e:
        cat.warnings.append('equipment_stats_ui : {}'.format(e))
        return
    sheet = next((i for i, s in enumerate(values['Sheet'])
                  if bytes(s['Name']).decode('utf-8', 'replace') == STATS_SHEET), None)
    if sheet is None:
        cat.warnings.append('equipment_stats_ui : pas de feuille "{}"'.format(STATS_SHEET))
        return
    rows = X.sheet_rows(values, sheet)
    if not rows:
        return
    cat.stats_header = rows[0]
    cat.stats_first_empty_row = X.first_empty_row(values, sheet)
    by_name = {r[0]: (i, r) for i, r in enumerate(rows) if i > 0 and isinstance(r[0], str)}
    for it in cat.ammo:
        hit = by_name.get(it.name)
        if hit is not None:
            it.stats_row, it.stats = hit
            it.stats_read_by_game = hit[0] < cat.stats_first_empty_row


def charger(vfs, language: str = 'en', etape: Optional[Callable[[str], None]] = None) -> Catalogue:
    """Catalogue des munitions et des armes, lu dans les fichiers d'origine du jeu."""
    def step(t):
        if etape is not None:
            etape(t)
    step('equipment_data')
    tree = W.rtpc_from_bytes(_projet.read_by_path(vfs, CM.EQUIPMENT_DATA))
    cat = Catalogue(language, _items(tree.root_node, CATEGORY_AMMO), _items(tree.root_node, CATEGORY_WEAPONS))
    if not cat.ammo:
        raise ECatalogue('aucune munition dans {} (categorie "{}" absente ?)'.format(CM.EQUIPMENT_DATA, CATEGORY_AMMO))
    step('string_ids ({})'.format(language))
    _texts(vfs, cat.ammo + cat.weapons, language, cat.warnings)
    step('spawn_ammunition_defs')
    _spawn(vfs, cat.ammo, cat.warnings)
    step('equipment_stats_ui')
    _stats(vfs, cat)
    return cat


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Liste les munitions du jeu, comme l\'atelier de l\'interface.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--langue', default='en', help='langue des noms affiches (en, fr, de, es, ru, pl, ja, pt, cs, zh)')
    args = ap.parse_args(argv)
    cat = charger(_projet.open_project(args.projet), args.langue)
    for it in cat.ammo:
        print('{:<40} {:<40} {:>6} {:<20} {}'.format(it.name, it.shown()[:40], '' if it.price is None else it.price,
                                                     it.availability, ' '.join(it.weapons)))
    for w in cat.warnings:
        print('ATTENTION : ' + w)
    return 0


if __name__ == '__main__':
    sys.exit(main())
