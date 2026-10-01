#!/usr/bin/env python3
"""
Tests de deca.ff_rtpc_write (ecriture RTPC).

Le test principal ne compare pas l'ecrivain a lui-meme : il construit a la main, offset par offset,
un fichier RTPC suivant les regles de disposition documentees (en-tetes, bourrage 0x50, alignements,
chaines dedupliquees, parcours en profondeur), le relit avec le lecteur de DECA, le reecrit, et exige
les memes octets.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_rtpc_write.py
"""
import struct
import sys
import unittest

if not sys.platform.startswith('win'):
    # Sous Linux, deca importe zugbruecke (pont vers les DLL Windows, qui exige Wine) : inutile pour COTW.
    import ctypes
    import types
    sys.modules.setdefault('zugbruecke', types.ModuleType('zugbruecke')).ctypes = ctypes  # paquet parent
    sys.modules['zugbruecke.ctypes'] = ctypes

from deca.hashes import hash32_func
from deca.ff_rtpc import (
    Rtpc, RtpcVisitorGatherStrings,
    k_type_none, k_type_u32, k_type_f32, k_type_str, k_type_vec2, k_type_vec3, k_type_vec4,
    k_type_mat3x3, k_type_mat4x4, k_type_array_u32, k_type_array_f32, k_type_array_u8,
    k_type_depreciated_12, k_type_objid, k_type_event, k_type_unk_15, k_type_unk_16,
)
import deca.ff_rtpc_write as W

P = 0x50  # octet de bourrage


def h(name):
    return int(hash32_func(name))


def build_reference_file():
    """
    Fichier de reference construit a la main. Disposition attendue (offsets decimaux) :

      0   'RTPC', version 1
      8   en-tete racine : root, offset 20, 5 proprietes, 1 enfant
      20  5 en-tetes de proprietes (45 octets) -> 65, bourrage -> 68
      68  en-tete de l'enfant (12) -> 80
      80  "abc\\0"                    (chaine, sans alignement)
      84  vec3 (12)                  -> 96   ; la 2e chaine "abc" reutilise l'offset 80
      96  bloc enfant : 3 en-tetes (27) -> 123, bourrage -> 124
      124 en-tete du petit-enfant (12) -> 136, bourrage -> 144 (vec4 aligne sur 16)
      144 vec4 (16)                  -> 160
      160 A[u32] : n=3 + 12 octets   -> 176
      176 event : n=2 + 16 octets    -> 196
      196 bloc petit-enfant : 3 en-tetes (27) -> 223, bourrage -> 224 ; chaine "abc" -> 80
      224 objid (8)                  -> 232
      232 A[u8] : n=5 + 5 octets     -> 241   (fin du fichier, sans bourrage final)
    """
    b = bytearray(241)
    b[:] = bytes([P]) * 241

    def put(off, fmt, *vals):
        struct.pack_into(fmt, b, off, *vals)

    put(0, '<4sI', b'RTPC', 1)
    put(8, '<IIHH', h('root'), 20, 5, 1)
    root_props = [
        (h('count'), 42, k_type_u32),
        (h('ratio'), struct.unpack('<I', struct.pack('<f', 0.5))[0], k_type_f32),
        (h('label'), 80, k_type_str),
        (h('pos'), 84, k_type_vec3),
        (h('label2'), 80, k_type_str),
    ]
    for i, (nh, raw, t) in enumerate(root_props):
        put(20 + 9 * i, '<IIB', nh, raw, t)
    put(68, '<IIHH', h('child'), 96, 3, 1)
    b[80:84] = b'abc\0'
    put(84, '<3f', 1.0, 2.0, 3.0)

    child_props = [(h('rot'), 144, k_type_vec4), (h('ids'), 160, k_type_array_u32), (h('ev'), 176, k_type_event)]
    for i, (nh, raw, t) in enumerate(child_props):
        put(96 + 9 * i, '<IIB', nh, raw, t)
    put(124, '<IIHH', h('grandchild'), 196, 3, 0)
    put(144, '<4f', 0.0, 0.0, 0.0, 1.0)
    put(160, '<I3I', 3, 10, 20, 30)
    put(176, '<I2Q', 2, 0x1122334455667788, 0x99AABBCCDDEEFF00)

    gc_props = [(h('tag'), 80, k_type_str), (h('_object_id'), 224, k_type_objid), (h('blob'), 232, k_type_array_u8)]
    for i, (nh, raw, t) in enumerate(gc_props):
        put(196 + 9 * i, '<IIB', nh, raw, t)
    put(224, '<Q', 0x0123456789ABCDEF)
    put(232, '<I5B', 5, 1, 2, 3, 4, 5)
    return bytes(b)


def all_types_tree():
    props = [
        W.make_prop('none', k_type_none, 0),
        W.make_prop('u32', k_type_u32, 0xFFFFFFFF),
        W.make_prop('f32', k_type_f32, -0.0),
        W.make_prop('str', k_type_str, 'équipement'),
        W.make_prop('vec2', k_type_vec2, [1.5, -2.5]),
        W.make_prop('vec3', k_type_vec3, [1, 2, 3]),
        W.make_prop('vec4', k_type_vec4, [1, 2, 3, 4]),
        W.make_prop('mat3', k_type_mat3x3, list(range(9))),
        W.make_prop('mat4', k_type_mat4x4, list(range(16))),
        W.make_prop('au32', k_type_array_u32, [1, 2, 0xFFFFFFFF]),
        W.make_prop('af32', k_type_array_f32, [0.25, 0.5]),
        W.make_prop('au8', k_type_array_u8, [0, 255, 7]),
        W.make_prop('empty_au32', k_type_array_u32, []),
        W.make_prop('objid', k_type_objid, 0xFFFFFFFFFFFFFFFF),
        W.make_prop('event', k_type_event, [1, 2, 3]),
        W.make_prop('unk15', k_type_unk_15, 15),
        W.make_prop('unk16', k_type_unk_16, 16),
    ]
    empty = W.make_node('empty')
    leaf = W.make_node('leaf', [W.make_prop('s', k_type_str, b'x')])
    mid = W.make_node('mid', [W.make_prop('s', k_type_str, b'x')], [empty, leaf])
    r = Rtpc()
    r.version = 1
    r.root_node = W.make_node('root', props, [mid, W.make_node('empty2')])
    return r


class TestReferenceLayout(unittest.TestCase):
    def test_reference_file_roundtrip_is_byte_identical(self):
        ref = build_reference_file()
        tree = W.rtpc_from_bytes(ref)
        self.assertEqual(W.rtpc_to_binary(tree), ref)

    def test_reference_file_is_read_as_expected(self):
        tree = W.rtpc_from_bytes(build_reference_file())
        root = tree.root_node
        self.assertEqual(W.node_get_prop(root, 'count').data, 42)
        self.assertEqual(W.node_get_prop(root, 'label2').data, b'abc')
        gc = root.child_table[0].child_table[0]
        self.assertEqual(W.node_get_prop(gc, 'blob').data, [1, 2, 3, 4, 5])


def build_reference_file_v2():
    """
    Fichier de reference version 2, construit a la main. Seule difference avec la version 1 : un u32
    apres les en-tetes des enfants de chaque noeud, egal au nombre de proprietes de type != 'none'.

      0   'RTPC', version 2
      8   en-tete racine : root, offset 20, 2 proprietes, 1 enfant
      20  2 en-tetes de proprietes (18) -> 38, bourrage -> 40
      40  en-tete de l'enfant (12) -> 52
      52  u32 = 1   (proprietes 'none' et 'label' : seule 'label' compte)
      56  "hi\\0" -> 59, bourrage -> 60
      60  bloc enfant : 1 en-tete (9) -> 69, bourrage -> 72 ; 0 enfant
      72  u32 = 1   (propriete u32 inline) -> 76 = fin du fichier
    """
    b = bytearray(bytes([P]) * 76)
    struct.pack_into('<4sI', b, 0, b'RTPC', 2)
    struct.pack_into('<IIHH', b, 8, h('root'), 20, 2, 1)
    struct.pack_into('<IIB', b, 20, h('unused'), 0, k_type_none)
    struct.pack_into('<IIB', b, 29, h('label'), 56, k_type_str)
    struct.pack_into('<IIHH', b, 40, h('child'), 60, 1, 0)
    struct.pack_into('<I', b, 52, 1)
    b[56:59] = b'hi\0'
    struct.pack_into('<IIB', b, 60, h('count'), 7, k_type_u32)
    struct.pack_into('<I', b, 72, 1)
    return bytes(b)


class TestVersion2(unittest.TestCase):
    def test_v2_reference_roundtrip_is_byte_identical(self):
        ref = build_reference_file_v2()
        tree = W.rtpc_from_bytes(ref)
        self.assertEqual(tree.version, 2)
        self.assertEqual(W.rtpc_to_binary(tree), ref)

    def test_v2_trailer_values_and_offsets(self):
        ref = build_reference_file_v2()
        tree = W.rtpc_from_bytes(ref)
        for node in (tree.root_node, tree.root_node.child_table[0]):
            off = W.node_trailer_offset(node)
            self.assertEqual(struct.unpack_from('<I', ref, off)[0], W.node_trailer_value(node, 'valid_props'))
        self.assertEqual(W.node_trailer_value(tree.root_node, 'all_props'), 2)

    def test_v1_layout_on_v2_tree_drops_trailers(self):
        tree = W.rtpc_from_bytes(build_reference_file_v2())
        out = W.rtpc_to_binary(tree, W.DEFAULT_LAYOUT)
        self.assertEqual(len(out), 76 - 8)
        self.assertIsNone(W.rtpc_diff(tree, W.rtpc_from_bytes(out)))

    def test_v2_edit_updates_trailer(self):
        tree = W.rtpc_from_bytes(build_reference_file_v2())
        child = tree.root_node.child_table[0]
        W.node_add_prop(child, W.make_prop('extra', k_type_str, b'x'))
        out = W.rtpc_to_binary(tree)
        reread = W.rtpc_from_bytes(out)
        c2 = reread.root_node.child_table[0]
        self.assertEqual(struct.unpack_from('<I', out, W.node_trailer_offset(c2))[0], 2)

    def test_unknown_trailer_mode_rejected(self):
        with self.assertRaises(ValueError):
            W.RtpcLayout(node_trailer='bogus')


class TestAllTypes(unittest.TestCase):
    def test_every_type_survives_write_and_read(self):
        tree = all_types_tree()
        data = W.rtpc_to_binary(tree)
        self.assertIsNone(W.rtpc_diff(tree, W.rtpc_from_bytes(data)))

    def test_rewrite_is_stable(self):
        data = W.rtpc_to_binary(all_types_tree())
        self.assertEqual(W.rtpc_to_binary(W.rtpc_from_bytes(data)), data)

    def test_alignment_of_values(self):
        data = W.rtpc_to_binary(all_types_tree())
        tree = W.rtpc_from_bytes(data)
        for p in tree.root_node.prop_table:
            if p.type in W.INLINE_TYPES:
                continue
            align = W.DEFAULT_ALIGNMENTS[p.type]
            self.assertEqual(p.data_raw % align, 0, 'type {} a l\'offset {}'.format(p.type, p.data_raw))

    def test_scan_visitor_reads_output(self):
        # Le scan DECA utilise un second lecteur (RtpcVisitor) : il doit aussi accepter le fichier.
        visitor = RtpcVisitorGatherStrings()
        visitor.visit(W.rtpc_to_binary(all_types_tree()))
        self.assertIn('équipement'.encode('utf-8'), visitor.strings)
        self.assertIn(b'x', visitor.strings)


class TestLayoutOptions(unittest.TestCase):
    def test_string_dedup(self):
        tree = all_types_tree()
        on = W.rtpc_to_binary(tree)
        off = W.rtpc_to_binary(tree, W.RtpcLayout(dedup_strings=False))
        # la chaine b'x' est portee par deux noeuds : un seul offset avec deduplication, deux sans
        def offsets_of_x(data):
            found = set()

            def walk(node):
                found.update(p.data_raw for p in node.prop_table if p.type == k_type_str and p.data == b'x')
                for c in node.child_table:
                    walk(c)
            walk(W.rtpc_from_bytes(data).root_node)
            return found
        self.assertEqual(len(offsets_of_x(on)), 1)
        self.assertEqual(len(offsets_of_x(off)), 2)
        self.assertIsNone(W.rtpc_diff(tree, W.rtpc_from_bytes(off)))

    def test_pad_byte_and_trailing_pad(self):
        ref = build_reference_file()
        tree = W.rtpc_from_bytes(ref)
        zero = W.rtpc_to_binary(tree, W.RtpcLayout(pad_byte=0))
        self.assertEqual(zero[65:68], b'\0\0\0')
        padded = W.rtpc_to_binary(tree, W.RtpcLayout(trailing_pad=True))
        self.assertEqual(len(padded), 244)
        self.assertEqual(padded[241:], bytes([P]) * 3)


class TestEditing(unittest.TestCase):
    def test_add_child_keeps_existing_content(self):
        original = W.rtpc_from_bytes(build_reference_file())
        edited = W.rtpc_from_bytes(build_reference_file())
        item = W.make_node('equipment_ammo_test_01', [
            W.make_prop('name', k_type_str, b'equipment_ammo_test_01'),
            W.make_prop('units', k_type_u32, 20),
        ])
        W.node_add_child(edited.root_node, item, position=len(edited.root_node.child_table))
        reread = W.rtpc_from_bytes(W.rtpc_to_binary(edited))
        self.assertEqual(len(reread.root_node.child_table), 2)
        self.assertIsNone(W.rtpc_diff(
            _tree(original.root_node.child_table[0]), _tree(reread.root_node.child_table[0])))
        added = reread.root_node.child_table[1]
        self.assertEqual(W.node_get_prop(added, 'name').data, b'equipment_ammo_test_01')
        self.assertEqual(W.node_get_prop(added, 'units').data, 20)

    def test_extend_string_list(self):
        # Cas d'une liste de compatibilite : un noeud dont les proprietes sont des chaines.
        compat = W.make_node(0x77C2B3BA, [W.make_prop(0x1000, k_type_str, b'equipment_weapon_a_01')])
        r = Rtpc()
        r.version = 1
        r.root_node = W.make_node('item', [], [compat])
        W.node_add_prop(compat, W.make_prop(0x1001, k_type_str, b'equipment_weapon_b_01'))
        reread = W.rtpc_from_bytes(W.rtpc_to_binary(r))
        values = [p.data for p in reread.root_node.child_table[0].prop_table]
        self.assertEqual(values, [b'equipment_weapon_a_01', b'equipment_weapon_b_01'])

    def test_sorted_insertion(self):
        n = W.make_node('n', [W.make_prop(10, k_type_u32, 1), W.make_prop(30, k_type_u32, 3)])
        self.assertEqual(W.node_add_prop(n, W.make_prop(20, k_type_u32, 2)), 1)
        u = W.make_node('u', [W.make_prop(30, k_type_u32, 3), W.make_prop(10, k_type_u32, 1)])
        self.assertEqual(W.node_add_prop(u, W.make_prop(20, k_type_u32, 2)), 2)

    def test_duplicate_property_rejected(self):
        n = W.make_node('n', [W.make_prop('a', k_type_u32, 1)])
        with self.assertRaises(ValueError):
            W.node_add_prop(n, W.make_prop('a', k_type_u32, 2))

    def test_clone_is_independent(self):
        tree = W.rtpc_from_bytes(build_reference_file())
        clone = W.clone_node(tree.root_node)
        W.node_get_prop(clone.child_table[0], 'ids').data.append(40)
        self.assertEqual(W.node_get_prop(tree.root_node.child_table[0], 'ids').data, [10, 20, 30])


class TestErrors(unittest.TestCase):
    def test_invalid_values(self):
        with self.assertRaises(W.ERtpcWrite):
            W.make_prop('s', k_type_str, b'a\0b')
        with self.assertRaises(W.ERtpcWrite):
            W.make_prop('v', k_type_vec3, [1, 2])
        with self.assertRaises(W.ERtpcWrite):
            W.make_prop('d', k_type_depreciated_12, 0)
        with self.assertRaises(W.ERtpcWrite):
            W.make_prop('u', k_type_u32, 1 << 40)


def _tree(node):
    r = Rtpc()
    r.version = 1
    r.root_node = node
    return r


if __name__ == '__main__':
    unittest.main(verbosity=2)
