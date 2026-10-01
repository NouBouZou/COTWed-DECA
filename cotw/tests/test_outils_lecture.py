#!/usr/bin/env python3
"""
Tests des fonctions de formatage de cotw/rtpc_dump.py et de cotw/deca_refs.py (sans projet DECA).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_outils_lecture.py
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows)
import rtpc_dump as D  # noqa: E402
import deca_refs as R  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_u32, k_type_str, k_type_array_u32, k_type_f32  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402

EE = 'editor/entities/hp_weapons/ammunition/rifles/equipment_ammo_243_sp_01.ee'


def sample_tree():
    """Structure d'equipment_data.bin telle que decrite par le Mod Builder : categorie -> objets -> compatibilite."""
    ammo = W.make_node('equipment_ammo_243_sp_01', [
        W.make_prop('name', k_type_str, b'equipment_ammo_243_sp_01'),
        W.make_prop(0x33df3b2f, k_type_u32, 450),
        W.make_prop(0x5555AAAA, k_type_u32, int(hash32_func(EE.encode()))),
    ], [W.make_node(0x77C2B3BA, [W.make_prop(0x1000, k_type_str, b'equipment_weapon_ba_rifle_243_01')])])
    other = W.make_node('other', [W.make_prop('name', k_type_str, b'equipment_ammo_308_pt_01'),
                                  W.make_prop('ratio', k_type_f32, 0.25)])
    r = Rtpc()
    r.version = 1
    r.root_node = W.make_node('root', [], [W.make_node('ammo', [], [ammo, other])])
    return r


class TestFormat(unittest.TestCase):
    def setUp(self):
        self.names = {int(hash32_func(b'name')): ['name'], int(hash32_func(EE.encode())): [EE]}

    def test_u32_recognised_as_hash(self):
        p = W.make_prop('x', k_type_u32, int(hash32_func(EE.encode())))
        self.assertIn('= hash de "{}"'.format(EE), D.format_value(p, self.names))

    def test_decimal_string_annotated(self):
        p = W.make_prop('id', k_type_str, str(int(hash32_func(EE.encode()))).encode())
        self.assertIn('= hash de "{}"'.format(EE), D.format_value(p, self.names))
        self.assertIsNone(D.decimal_hash(b'-1'))
        self.assertIsNone(D.decimal_hash(b'99999999999'))

    def test_array_and_float(self):
        self.assertIn('2 elements', D.format_value(W.make_prop('a', k_type_array_u32, [1, 2]), {}))
        self.assertEqual(D.format_value(W.make_prop('f', k_type_f32, 0.25), {}), '0.25')

    def test_filter_keeps_only_matching_item_with_parents(self):
        lines = []
        n = D.format_filtered(sample_tree(), self.names, [b'equipment_ammo_243_sp_01'], None, lines)
        text = '\n'.join(lines)
        self.assertEqual(n, 1)
        self.assertIn('equipment_weapon_ba_rifle_243_01', text)   # enfant (compatibilite) affiche
        self.assertNotIn('equipment_ammo_308_pt_01', text)        # autre objet exclu
        self.assertIn('parent', text)

    def test_filter_by_hash(self):
        # un noeud qui ne cite le fichier que par son hash32 est trouve
        lines = []
        n = D.format_filtered(sample_tree(), self.names, [EE.encode()], None, lines)
        self.assertEqual(n, 1)

    def test_json_is_serialisable(self):
        import json
        json.dumps(D.to_json(sample_tree().root_node, self.names))


class TestReadByPath(unittest.TestCase):
    class Node:
        def __init__(self, uid, size, h):
            self.uid, self.offset, self.size_u, self.content_hash = uid, 0, size, h

    class Vfs:
        def __init__(self, nodes):
            self.nodes = nodes

        def nodes_where_match(self, v_path=None):
            return self.nodes

        def file_obj_from(self, node):
            import io
            return io.BytesIO(b'x' * node.size_u)

    def read(self, nodes):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            data = _projet.read_by_path(self.Vfs(nodes), 'settings/a.bin')
        return data, err.getvalue()

    def test_identical_copies_are_silent(self):
        data, err = self.read([self.Node(1, 4, 'h'), self.Node(2, 4, 'h')])
        self.assertEqual(data, b'xxxx')
        self.assertEqual(err, '')

    def test_different_copies_are_reported(self):
        _data, err = self.read([self.Node(1, 4, 'h1'), self.Node(2, 6, 'h2')])
        self.assertIn('2 versions differentes', err)


class TestRefs(unittest.TestCase):
    def test_patterns_include_text_and_path_hashes(self):
        paths = [(EE, 'sarc', 100, 1)]
        patterns = R.build_patterns('equipment_ammo_243_sp_01', paths, {})
        self.assertIn(b'equipment_ammo_243_sp_01', patterns)
        self.assertIn(struct.pack('<I', int(hash32_func(EE.encode()))), patterns)
        self.assertIn(struct.pack('<I', int(hash32_func(b'equipment_ammo_243_sp_01'))), patterns)


if __name__ == '__main__':
    unittest.main(verbosity=2)
