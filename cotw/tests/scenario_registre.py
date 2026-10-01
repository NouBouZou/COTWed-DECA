"""
Scenario commun aux tests de indexer_refs.py et differentiel.py : un faux jeu ou un registre cache decide
quels objets sont disponibles.

  equipment_data.bin      : 3 munitions disponibles (A1, A2, A3), 2 absentes (U1, U2), 1 arme ; toutes y sont
  spawn_ammunition_defs   : les 5 munitions, avec leur entite (comme dans le vrai jeu : non discriminant)
  store_registry.bin      : RTPC qui cite A1, A2, A3 par hash32 dans des valeurs u32 inline, dont certaines
                            a des positions non alignees sur 4 octets (propriete d'indice 1, 2, 3...)
  unknown.bin             : fichier sans type, table brute de hash32 alignes : A1, A2, A3
  equipment_stats_ui.bin  : faux fichier "texte" qui cite A1, A2, A3 et U1 en clair (fuite : pas un registre)
  exe                     : table alignee des hash64 de A1, A2 (registre compile dans l'exe)
"""
import struct
from typing import Dict, List, Tuple

import deca.ff_rtpc_write as W
from deca.ff_rtpc import Rtpc, k_type_none, k_type_str, k_type_u32, k_type_f32
from deca.hashes import hash32_func, hash64_func

import faux_projet

AVAILABLE = ['equipment_ammo_243_sp_01', 'equipment_ammo_308_pt_01', 'equipment_ammo_762x54r_sp_01']
ABSENT = ['equipment_ammo_762x54r_fmj_01', 'equipment_ammo_410_slug_01']
WEAPON = 'equipment_weapon_ba_rifle_243_01'
REGISTRY = 'settings/hp_settings/store_registry.bin'
UNKNOWN = 'settings/hp_settings/unknown.bin'
STATS = 'settings/hp_settings/equipment_stats_ui.bin'


def h32(s: str) -> int:
    return int(hash32_func(s.encode()))


def h64(s: str) -> int:
    return int(hash64_func(s.encode())) & 0xFFFFFFFFFFFFFFFF


def entity(name: str) -> str:
    return 'editor\\entities\\hp_weapons\\ammunition\\rifles\\' + name + '.ee'


def rtpc_bytes(root) -> bytes:
    r = Rtpc()
    r.version = 2
    r.root_node = root
    return W.rtpc_to_binary(r)


def ammo(name: str, price: int):
    return W.make_node(name, [
        W.make_prop('name', k_type_str, name.encode()),
        W.make_prop('cash_price', k_type_u32, price),
        W.make_prop('display_name', k_type_str, (name[:-3] + '_name').encode()),
        W.make_prop('display_icon', k_type_str, ('textures\\ui\\items\\ammo\\' + name + '_alpha.ddsc').encode()),
        W.make_prop('note', k_type_str, b'Libelle anglais'),
        W.make_prop('capacity_cost', k_type_f32, 0.02)])


def equipment_data() -> bytes:
    items = [ammo(n, 100 + i) for i, n in enumerate(AVAILABLE + ABSENT)]
    weapon = W.make_node(WEAPON, [W.make_prop('name', k_type_str, WEAPON.encode())])
    root = W.make_node(0, [], [W.make_node('ammo', [], items), W.make_node('weapons', [], [weapon])])
    return rtpc_bytes(root)


def spawn_defs() -> bytes:
    entries = [W.make_node(n, [W.make_prop(0xf71c2a21, k_type_str, n.encode()),
                               W.make_prop('entity', k_type_str, entity(n).encode())])
               for n in AVAILABLE + ABSENT]
    return rtpc_bytes(W.make_node(0, [], [W.make_node('spawn_system', [], [
        W.make_node('resource_definitions', [], entries)])]))


def registry() -> bytes:
    # une seule entree avec plusieurs proprietes u32 : les valeurs des proprietes 1, 2, 3 sont non alignees
    props = [W.make_prop('version', k_type_u32, 3)]
    props += [W.make_prop('slot_{}'.format(i), k_type_u32, h32(n)) for i, n in enumerate(AVAILABLE)]
    return rtpc_bytes(W.make_node(0, [], [W.make_node('store', props)]))


def unknown_table() -> bytes:
    return b'\x13\x37\x00\x00' + b''.join(struct.pack('<I', h32(n)) for n in AVAILABLE) + b'\0' * 64


def stats_text() -> bytes:
    return b'\n'.join(n.encode() for n in AVAILABLE + ABSENT[:1]) + b'\n'


def exe_bytes() -> bytes:
    table = b''.join(struct.pack('<Q', h64(n)) for n in AVAILABLE[:2])
    return b'MZ' + b'\0' * 126 + table + b'\0' * 256


def build(root: str) -> str:
    files = [
        ('settings/hp_settings/equipment_data.bin', equipment_data()),
        ('settings/spawn_ammunition_defs.bin', spawn_defs()),
        (REGISTRY, registry()),
        (UNKNOWN, unknown_table()),
        (STATS, stats_text()),
    ]
    return faux_projet.build_fake_project(root, files, exe_bytes())
