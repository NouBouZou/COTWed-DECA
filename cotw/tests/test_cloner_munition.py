#!/usr/bin/env python3
"""
Tests de cotw/cloner_munition.py sur des arbres qui reproduisent la structure reelle observee dans COTW
(RTPC version 2, proprietes 'none' de hash 0, liste de compatibilite 0x77c2b3ba, spawn defs a 4 champs).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_cloner_munition.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _projet  # noqa: E402,F401
import cloner_munition as C  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_none, k_type_str, k_type_u32, k_type_f32  # noqa: E402

SRC = 'equipment_ammo_243_sp_01'
NEW = 'equipment_ammo_243_test_01'
EE = 'editor\\entities\\hp_weapons\\ammunition\\rifles\\equipment_ammo_243_sp_01.ee'


def none_prop():
    return W.make_prop(0, k_type_none, 0)


def raw_node(name_hash, props, children=()):
    """Noeud construit sans les controles de make_node : autorise plusieurs 'none' de hash 0, comme le jeu."""
    n = W.make_node(name_hash)
    n.prop_table = list(props)
    n.prop_map = {p.name_hash: p for p in props}
    n.child_table = list(children)
    n.child_map = {c.name_hash: c for c in children}
    n.prop_count, n.child_count = len(n.prop_table), len(n.child_table)
    return n


def compat_entry(key, weapon):
    return raw_node(key, [W.make_prop(C.H_EQUIPMENT_NAME, k_type_str, weapon.encode()),
                          W.make_prop(0xaa8d9f49, k_type_u32, 0), none_prop(), none_prop()])


def ammo(name, label, keys_weapons, price=0):
    props = [W.make_prop(C.H_PRICE, k_type_u32, price), W.make_prop(C.H_NOTE, k_type_str, label.encode()),
             W.make_prop(C.H_NAME, k_type_str, name.encode()), W.make_prop(0x765fbed2, k_type_u32, 4),
             none_prop(), none_prop(), none_prop(),
             W.make_prop('display_name', k_type_str, b'equipment_ammo_243_sp_name'),
             W.make_prop('locked', k_type_u32, 1), W.make_prop(0x3d214106, k_type_f32, 0.02),
             W.make_prop('units', k_type_u32, 10),
             W.make_prop('display_icon', k_type_str, b'textures\\ui\\items\\ammo\\' + name.encode() + b'_alpha.ddsc'),
             W.make_prop(0x12345678, k_type_u32, W.name_hash(name))]
    dlc = raw_node(0x5e330413, [W.make_prop(0xa4f0837b, k_type_str, b'-1'), none_prop(), none_prop(), none_prop()])
    compat = raw_node(C.H_COMPAT, [], sorted([compat_entry(k, w) for k, w in keys_weapons], key=lambda c: c.name_hash))
    return raw_node(W.name_hash(name), props, sorted([dlc, compat], key=lambda c: c.name_hash))


def weapon(name):
    return raw_node(W.name_hash(name), [W.make_prop(C.H_NAME, k_type_str, name.encode()), none_prop()])


def equipment_tree():
    items = [
        ammo(SRC, '.243 Soft-Point Bullet', [(0x29c1d9ed, 'equipment_weapon_ba_rifle_243_01'),
                                             (0x52b65512, 'equipment_weapon_pistol_ss_243_02'),
                                             (0xd4835e23, 'equipment_weapon_pistol_ss_243_01')]),
        ammo('equipment_ammo_308_pt_01', '.308 Polymer Tip', [(0x0dc7fe39, 'equipment_weapon_ba_rifle_308_01')], 900),
    ]
    weapons = [weapon(w) for w in ('equipment_weapon_ba_rifle_243_01', 'equipment_weapon_pistol_ss_243_01',
                                   'equipment_weapon_pistol_ss_243_02', 'equipment_weapon_ba_rifle_308_01')]
    cat_ammo = raw_node(W.name_hash('ammo'), [], sorted(items, key=lambda c: c.name_hash))
    cat_weapons = raw_node(C.H_WEAPONS, [], sorted(weapons, key=lambda c: c.name_hash))
    r = Rtpc()
    r.version = 2
    r.root_node = raw_node(0, [], sorted([cat_ammo, cat_weapons], key=lambda c: c.name_hash))
    return W.rtpc_to_binary(r)


def spawn_tree():
    def entry(name, path):
        return raw_node(W.name_hash(name), [
            W.make_prop(C.H_SPAWN_NAME, k_type_str, name.encode()),
            W.make_prop(C.H_TAGS, k_type_str, (name + ',ammo_cal_243,ammo_type_sp,ammunition').encode()),
            W.make_prop(C.H_ENTITY, k_type_str, path.encode()),
            W.make_prop('debug', k_type_str, b'Ammunition')])
    entries = [entry(SRC, EE), entry('equipment_ammo_308_pt_01', EE.replace('243_sp', '308_pt'))]
    r = Rtpc()
    r.version = 2
    r.root_node = raw_node(0, [], [raw_node(0x83647b76, [], [raw_node(0x0b621fb6, [], sorted(entries, key=lambda c: c.name_hash))])])
    return W.rtpc_to_binary(r)


class TestEquipment(unittest.TestCase):
    def setUp(self):
        self.orig = equipment_tree()
        self.tree = W.rtpc_from_bytes(self.orig)

    def test_fixture_matches_game_layout_rules(self):
        self.assertEqual(W.rtpc_to_binary(W.rtpc_from_bytes(self.orig)), self.orig)

    def test_clone_adds_only_the_new_item(self):
        parent, item, _ = C.clone_equipment(self.tree, SRC, NEW, label='.243 Test', price=10)
        out = W.rtpc_to_binary(self.tree)
        C.verify_addition(self.orig, out, W.name_hash(NEW))
        reread = W.rtpc_from_bytes(out)
        _, new = C.find_item(reread.root_node, NEW, C.H_NAME)
        self.assertEqual(W.node_get_prop(new, C.H_NOTE).data, b'.243 Test')
        self.assertEqual(W.node_get_prop(new, C.H_PRICE).data, 10)
        self.assertEqual(W.node_get_prop(new, 0x12345678).data, W.name_hash(NEW))   # u32 = hash du nom
        self.assertEqual(W.node_get_prop(new, 'display_name').data, b'equipment_ammo_243_sp_name')  # cle gardee
        self.assertIn(b'243_sp_01_alpha', W.node_get_prop(new, 'display_icon').data)  # icone gardee
        self.assertEqual(sum(1 for p in new.prop_table if p.type == k_type_none), 3)  # disposition gardee
        hashes = [c.name_hash for c in parent.child_table]
        self.assertEqual(hashes, sorted(hashes))

    def test_source_untouched(self):
        C.clone_equipment(self.tree, SRC, NEW)
        _, src = C.find_item(self.tree.root_node, SRC, C.H_NAME)
        self.assertEqual(W.node_get_prop(src, C.H_NAME).data, SRC.encode())

    def test_compat_keys_are_positional(self):
        # cles relevees dans le jeu : compatibility_0, _1, _2, _4 et "compatibility_01"
        self.assertEqual(C.compat_key(0), 0x29c1d9ed)
        self.assertEqual(C.compat_key(1), 0xd4835e23)
        self.assertEqual(C.compat_key(2), 0x52b65512)
        self.assertEqual(C.compat_key(4), 0x0dc7fe39)
        self.assertEqual(W.name_hash('compatibility_01'), 0xa10e30c1)
        self.assertEqual(C.H_COMPAT, 0x77c2b3ba)

    def test_compatibility_with_more_weapons_uses_positional_keys(self):
        weapons = ['equipment_weapon_ba_rifle_308_01', 'equipment_weapon_ba_rifle_243_01',
                   'equipment_weapon_pistol_ss_243_01', 'equipment_weapon_pistol_ss_243_02']
        _, item, _ = C.clone_equipment(self.tree, SRC, NEW, weapons=weapons)
        _, entries = C.compat_entries(item)
        by_key = {e.name_hash: bytes(W.node_get_prop(e, C.H_EQUIPMENT_NAME).data).decode() for e in entries}
        self.assertEqual(by_key, {C.compat_key(i): w for i, w in enumerate(weapons)})
        C.verify_addition(self.orig, W.rtpc_to_binary(self.tree), W.name_hash(NEW))

    def test_existing_weapon_keeps_its_entry_fields(self):
        _, src = C.find_item(self.tree.root_node, SRC, C.H_NAME)
        _, src_entries = C.compat_entries(src)
        pistol = [e for e in src_entries if W.node_get_prop(e, C.H_EQUIPMENT_NAME).data == b'equipment_weapon_pistol_ss_243_02'][0]
        W.node_get_prop(pistol, 0xaa8d9f49).data = 1     # ammunition_slot_index propre a cette arme
        _, item, _ = C.clone_equipment(self.tree, SRC, NEW, weapons=['equipment_weapon_pistol_ss_243_02'])
        _, entries = C.compat_entries(item)
        self.assertEqual(W.node_get_prop(entries[0], 0xaa8d9f49).data, 1)
        self.assertEqual(entries[0].name_hash, C.compat_key(0))

    def test_compatibility_with_one_weapon(self):
        _, item, _ = C.clone_equipment(self.tree, SRC, NEW, weapons=['equipment_weapon_ba_rifle_243_01'])
        _, entries = C.compat_entries(item)
        self.assertEqual(len(entries), 1)

    def test_unknown_weapon_rejected(self):
        with self.assertRaises(C.EClone):
            C.clone_equipment(self.tree, SRC, NEW, weapons=['equipment_weapon_inexistante_01'])

    def test_existing_name_rejected(self):
        with self.assertRaises(C.EClone):
            C.clone_equipment(self.tree, SRC, 'equipment_ammo_308_pt_01')

    def test_unknown_source_rejected(self):
        with self.assertRaises(C.EClone):
            C.clone_equipment(self.tree, 'equipment_ammo_inexistante_01', NEW)


class TestSpawn(unittest.TestCase):
    def test_clone_keeps_entity_by_default(self):
        orig = spawn_tree()
        tree = W.rtpc_from_bytes(orig)
        _, item, _ = C.clone_spawn(tree, SRC, NEW)
        out = W.rtpc_to_binary(tree)
        C.verify_addition(orig, out, W.name_hash(NEW))
        self.assertEqual(W.node_get_prop(item, C.H_TAGS).data, (NEW + ',ammo_cal_243,ammo_type_sp,ammunition').encode())
        self.assertEqual(W.node_get_prop(item, C.H_ENTITY).data, EE.encode())

    def test_clone_with_new_entity(self):
        tree = W.rtpc_from_bytes(spawn_tree())
        _, item, _ = C.clone_spawn(tree, SRC, NEW, entity='editor/entities/test/' + NEW + '.ee')
        self.assertEqual(W.node_get_prop(item, C.H_ENTITY).data,
                         ('editor\\entities\\test\\' + NEW + '.ee').encode())


class TestVerification(unittest.TestCase):
    def test_detects_unwanted_change(self):
        orig = equipment_tree()
        tree = W.rtpc_from_bytes(orig)
        C.clone_equipment(tree, SRC, NEW)
        _, other = C.find_item(tree.root_node, 'equipment_ammo_308_pt_01', C.H_NAME)
        W.node_get_prop(other, C.H_PRICE).data = 1  # modification parasite
        with self.assertRaises(C.EClone):
            C.verify_addition(orig, W.rtpc_to_binary(tree), W.name_hash(NEW))


if __name__ == '__main__':
    unittest.main(verbosity=2)
