#!/usr/bin/env python3
"""
Tests de cotw/sarc.py (lecture, ecriture, aller-retour), cotw/entite.py (copie d'une entite .ee) et de
l'option --entite-propre de cloner_munition.py sur un faux projet DECA (vrai scan).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_entite.py
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import adf_minimal as AM  # noqa: E402
import cloner_munition as C  # noqa: E402
import entite as E  # noqa: E402
import faux_projet  # noqa: E402
import sarc as S  # noqa: E402
import sarc_dump as SD  # noqa: E402
import sarc_roundtrip as SR  # noqa: E402
import test_separateurs as TS  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_str, k_type_u32  # noqa: E402

SRC = 'equipment_ammo_243_sp_01'
NEW = 'equipment_ammo_243_test_01'
DIR = 'editor/entities/hp_weapons/ammunition/rifles/'
LINK = 'models/hp_ammunition/rifles/bullet_243.modelc'


def epe(name: str) -> bytes:
    obj = W.make_node('item', [
        W.make_prop('item_id', k_type_str, name.encode()),
        W.make_prop('tuning_file', k_type_str, (DIR.replace('/', '\\') + name + '.ammotunec').encode()),
        W.make_prop('item_hash', k_type_u32, W.name_hash(name)),
        W.make_prop('model', k_type_str, LINK.encode())])
    return TS.S.rtpc_bytes(W.make_node(0, [], [obj]))


def entity(name: str, version: int = 2) -> bytes:
    data, _ = S.build(S.Sarc(version, [
        S.Entry(DIR + name + '.epe', epe(name), 0),
        S.Entry(DIR + name + '.ammotunec', AM.build([(name, 1, 2)], [3]), 0),   # ADF valide : le scan le lit
        S.Entry(LINK, None, 1234)]))
    return data


class TestSarc(unittest.TestCase):
    def test_parse_build_round_trip_v2_v3(self):
        for version in (2, 3):
            data = entity(SRC, version)
            s = S.parse(data)
            self.assertEqual(s.version, version)
            self.assertEqual([e.path for e in s.entries], [DIR + SRC + '.epe', DIR + SRC + '.ammotunec', LINK])
            self.assertTrue(s.entries[2].is_link)
            self.assertEqual(s.entries[2].length, 1234)
            r = S.roundtrip(data)
            self.assertTrue(r.identical, r.detail)

    def test_roundtrip_detects_other_alignment_and_padding(self):
        data = bytearray(entity(SRC))
        s = S.parse(bytes(data))
        self.assertEqual(S.roundtrip(bytes(data) + b'\0' * 12).category, 'fin')
        # deplacer la 2e entree de 16 octets plus loin (alignement different de celui de DECA)
        second = s.entries[1]
        moved = bytearray(data[:second.offset]) + b'\0' * 16 + data[second.offset:]
        # reecrire l'offset de la 2e entree dans le repertoire v2 : en-tete 16 octets, puis entrees
        pos = 16
        for i, e in enumerate(s.entries):
            n = struct.unpack_from('<I', moved, pos)[0]
            if i == 1:
                struct.pack_into('<I', moved, pos + 4 + n, second.offset + 16)
            pos += 4 + n + 8
        r = S.roundtrip(bytes(moved))
        self.assertEqual(r.category, 'decalage')
        self.assertIn('.ammotunec', r.detail)

    def test_not_a_sarc(self):
        with self.assertRaises(S.ESarc):
            S.parse(b'RTPC' + b'\0' * 20)


class TestEntity(unittest.TestCase):
    def test_clone_entity(self):
        out, renames, report = E.clone_entity(entity(SRC), SRC, NEW)
        s = S.parse(out)
        self.assertEqual([e.path for e in s.entries], [DIR + NEW + '.epe', DIR + NEW + '.ammotunec', LINK])
        self.assertTrue(s.entries[2].is_link)
        tree = W.rtpc_from_bytes(s.entries[0].data)
        obj = tree.root_node.child_table[0]
        self.assertEqual(W.node_get_prop(obj, 'item_id').data, NEW.encode())
        self.assertEqual(W.node_get_prop(obj, 'tuning_file').data, (DIR.replace('/', '\\') + NEW + '.ammotunec').encode())
        self.assertEqual(W.node_get_prop(obj, 'item_hash').data, W.name_hash(NEW))
        self.assertEqual(W.node_get_prop(obj, 'model').data, LINK.encode())   # ressource partagee : intacte
        self.assertEqual(s.entries[1].data, S.parse(entity(SRC)).entries[1].data)   # ADF recopie a l'identique
        self.assertEqual(len(renames), 2)
        self.assertTrue(any('item_id' not in line and '->' in line for line in report))

    def test_entity_without_named_entry_refused(self):
        data, _ = S.build(S.Sarc(2, [S.Entry('a/other.epe', epe('x_01'), 0)]))
        with self.assertRaises(E.EEntite):
            E.clone_entity(data, SRC, NEW)


class TestClonerOwnEntity(unittest.TestCase):
    def test_cloner_writes_own_entity_and_points_spawn_to_it(self):
        src = TS.SOLD[1]   # equipment_ammo_762x54r_sp_01
        new = 'equipment_ammo_762x54r_test_01'
        with tempfile.TemporaryDirectory(prefix='cotw_entite_') as root:
            project = faux_projet.build_fake_project(root, [
                ('settings/hp_settings/equipment_data.bin', TS.equipment_data()),
                ('settings/spawn_ammunition_defs.bin', TS.spawn_defs()),
                (DIR + src + '.ee', entity(src))])
            out = Path(root) / 'mod'
            C.main([project, '--source', src, '--nom', new, '--prix', '77', '--entite-propre', '--sortie', str(out)])
            ee = out / 'dropzone' / Path(DIR + new + '.ee')
            self.assertTrue(ee.is_file())
            s = S.parse(ee.read_bytes())
            self.assertEqual(W.node_get_prop(W.rtpc_from_bytes(s.entries[0].data).root_node.child_table[0], 'item_id').data,
                             new.encode())
            spawn = W.rtpc_from_bytes((out / 'dropzone' / 'settings' / 'spawn_ammunition_defs.bin').read_bytes())
            _, entry = C.find_item(spawn.root_node, new, C.H_SPAWN_NAME)
            self.assertEqual(W.node_get_prop(entry, C.H_ENTITY).data, (DIR + new + '.ee').replace('/', '\\').encode())
            report = (out / 'rapport_clonage.txt').read_text(encoding='utf-8')
            self.assertIn('entree renommee', report)
            # outils de lecture sur le meme projet
            rt = Path(root) / 'rt.md'
            SR.main([project, '--out', str(rt)])
            self.assertIn("Identiques à l'octet près : 1 / 1", rt.read_text(encoding='utf-8'))
            dump = Path(root) / 'dump.md'
            SD.main([project, DIR + src + '.ee', '--rtpc', '--out', str(dump)])
            text = dump.read_text(encoding='utf-8')
            self.assertIn('| 2 | `{}` | 1234 | lien |'.format(LINK), text)
            self.assertIn('"{}"'.format(src), text)            # item_id affiche dans le .epe


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
