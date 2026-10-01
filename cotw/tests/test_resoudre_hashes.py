#!/usr/bin/env python3
"""
Tests de cotw/resoudre_hashes.py : variantes de noms, recherche par hash, collecte des cibles RTPC et ADF,
dictionnaire, puis essai de bout en bout sur un faux projet DECA (vrai scan) avec injection et annulation.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_resoudre_hashes.py
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import resoudre_hashes as RH  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_none, k_type_str, k_type_u32  # noqa: E402
from deca.hashes import hash32_func, hash48_func, hash64_func  # noqa: E402


def h32(s):
    return int(hash32_func(s.encode()))


class TestVariants(unittest.TestCase):
    def test_snake_case(self):
        self.assertEqual(RH.snake('m_IsPartOfDLC'), 'is_part_of_dlc')
        self.assertEqual(RH.snake('DLCOwnershipUpdate'), 'dlc_ownership_update')
        self.assertEqual(RH.snake('m_AmmoType'), 'ammo_type')

    def test_variants(self):
        self.assertIn('is_part_of_dlc', RH.variants('m_IsPartOfDLC'))
        self.assertIn('part_of_dlc', RH.variants('m_IsPartOfDLC'))        # verbe retire
        self.assertIn('ammunition_item', RH.variants('CAmmunitionItem'))   # prefixe de classe retire
        self.assertIn('compatible_weapon_items', RH.variants('SetCompatibleWeaponItems'))
        self.assertIn('is_locked', RH.variants('m_bIsLocked'))             # notation hongroise
        self.assertIn('received', RH.variants('bReceived'))

    def test_identifiers_from_binary(self):
        data = b'MZ\x90\x00m_CashPrice\x00\x01\x02ab\x00IsPartOfDLC\x00\xff'
        self.assertEqual(RH.identifiers_from_binary(data), {'m_CashPrice', 'IsPartOfDLC'})

    def test_tokens_and_vocab(self):
        counter = RH.tokens_of(['m_CashPrice', 'CashValue', 'm_Price'])
        self.assertEqual(counter['cash'], 2)
        self.assertEqual(counter['price'], 2)
        self.assertEqual(RH.build_vocab(counter, 1, ['dlc'])[-1], 'dlc')


class TestSearcher(unittest.TestCase):
    def targets(self):
        t = RH.Targets()
        t.add(32, h32('cash_price'), 'propriete', 'test')
        t.add(32, h32('is_part_of_dlc'), 'propriete', 'test')
        t.add(48, int(hash48_func(b'equipment_name')), 'stringhash', 'test')
        t.add(64, int(hash64_func(b'equipment_hash')) & RH.MASK[64], 'stringhash', 'test')
        return t

    def test_passes_find_names_in_all_widths(self):
        s = RH.Searcher(self.targets())
        s.run('directs', RH.gen_direct(['m_IsPartOfDLC', 'EquipmentName', 'm_EquipmentHash'], []))
        s.run('paires', RH.gen_pairs(['cash', 'price', 'value']))
        found = {name: key for key, names in s.hits.items() for name in names}
        self.assertEqual(found['cash_price'], (32, h32('cash_price')))
        self.assertEqual(found['is_part_of_dlc'][0], 32)
        self.assertEqual(found['equipment_name'][0], 48)
        self.assertEqual(found['equipment_hash'][0], 64)
        self.assertEqual(s.stats[1].candidates, 9)

    def test_expected_false_positives(self):
        t = RH.Targets()
        for i in range(1, 11):
            t.add(32, i, 'propriete', 'test')
        s = RH.Searcher(t)
        st = s.run('x', ('c{}'.format(i) for i in range(1000)))
        self.assertAlmostEqual(st.expected_false, 1000 * 10 / 2 ** 32)


class TestCollect(unittest.TestCase):
    def test_rtpc(self):
        item = W.make_node('item', [W.make_prop('cash_price', k_type_u32, 10), W.make_prop(0, k_type_none, 0)],
                           [W.make_node('secondary_ids', [W.make_prop(0xa4f0837b, k_type_str, b'-1')])])
        r = Rtpc()
        r.version = 2
        r.root_node = W.make_node(0, [], [item])
        t = RH.Targets()
        RH.collect_rtpc(W.rtpc_from_bytes(W.rtpc_to_binary(r)), 'f.bin', t)
        self.assertEqual(t.items[(32, h32('cash_price'))].kinds, {'propriete'})
        self.assertEqual(t.items[(32, h32('secondary_ids'))].kinds, {'enfant'})
        self.assertIn((32, 0xa4f0837b), t.items)
        self.assertNotIn((32, 0), t.items)       # racine et propriete none ignorees
        self.assertIn('u32 = 10', t.items[(32, h32('cash_price'))].contexts[0])
        t2 = RH.Targets()
        RH.collect_rtpc(W.rtpc_from_bytes(W.rtpc_to_binary(r)), 'f.bin', t2, with_children=False)
        self.assertNotIn((32, h32('secondary_ids')), t2.items)

    def test_adf_stringhash(self):
        from deca.ff_adf import Adf, AdfValue, InstanceEntry, MetaType, TypeDef

        def typedef(metatype, size):
            td = TypeDef()
            td.metatype, td.size = metatype, size
            return td
        adf = Adf()
        adf.extended_map_typedef = {1: typedef(MetaType.Structure, 16), 2: typedef(MetaType.StringHash, 4),
                                    3: typedef(MetaType.StringHash, 8), 4: typedef(MetaType.Array, 8)}
        ins = InstanceEntry()
        ins.name = b'sheet'
        adf.table_instance = [ins]
        adf.table_instance_full_values = [AdfValue({
            'Known': AdfValue(111, 2, 0, hash_string=b'already_named'),
            'Unknown32': AdfValue(222, 2, 4),
            'Rows': AdfValue([AdfValue(333, 3, 8)], 4, 8),
        }, 1, 0)]
        t = RH.Targets()
        RH.collect_adf(adf, 'ui.bin', t)
        self.assertEqual(set(t.items), {(32, 222), (64, 333)})
        self.assertEqual(t.items[(64, 333)].contexts[0], 'ui.bin sheet.Rows[0]')


class TestDictionary(unittest.TestCase):
    def test_append_is_idempotent_and_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'noms.tsv'
            e = ((32, h32('cash_price')), 'cash_price', 'paires', 0.03, 'equipment_data.bin')
            self.assertEqual(len(RH.append_to_dictionary(path, [e])), 1)
            self.assertEqual(RH.append_to_dictionary(path, [e]), [])
            self.assertEqual(_projet.load_dictionary(path)[(32, h32('cash_price'))], ['cash_price'])

    def test_repo_dictionary_hashes_are_correct(self):
        """Chaque ligne du dictionnaire versionne doit redonner son hash."""
        entries = _projet.load_dictionary(_projet.DICTIONARY_PATH)
        self.assertGreater(len(entries), 0)
        for (width, value), names in entries.items():
            for n in names:
                self.assertEqual(width, 32)
                self.assertEqual(h32(n), value, n)


def equipment_data():
    item = W.make_node('equipment_ammo_243_sp_01', [
        W.make_prop('name', k_type_str, b'equipment_ammo_243_sp_01'),
        W.make_prop('cash_price', k_type_u32, 0),
        W.make_prop('is_part_of_dlc', k_type_u32, 0)],
        [W.make_node('secondary_ids', [W.make_prop('received', k_type_str, b'-1')])])
    r = Rtpc()
    r.version = 2
    r.root_node = W.make_node(0, [], [W.make_node('ammo', [], [item])])
    return W.rtpc_to_binary(r)


class TestEndToEnd(unittest.TestCase):
    """Vrai scan DECA d'un faux jeu, puis recherche, injection et annulation."""

    @classmethod
    def setUpClass(cls):
        import faux_projet
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_resolve_')
        root = cls.tmp.name
        exe = (b'MZ' + b'\0' * 64 + b'\0m_Name\0m_IsPartOfDLC\0m_SecondaryIds\0bReceived\0CEquipmentScreenStore\0'
               + b'\0' * 64)
        cls.project = faux_projet.build_fake_project(root, [('settings/hp_settings/equipment_data.bin', equipment_data())], exe)
        cls.vocab = Path(root) / 'vocab.txt'
        cls.vocab.write_text('cash\nprice\nreceived\n', encoding='utf-8')
        cls.dictionary = Path(root) / 'noms.tsv'

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_search(self, *extra):
        out = Path(self.tmp.name) / 'rapport.md'
        RH.main(['chercher', self.project, 'settings/hp_settings/equipment_data.bin', '--sources', 'exe',
                 '--vocabulaire', str(self.vocab), '--paires', '40', '--triplets', '0',
                 '--dictionnaire', str(self.dictionary), '--out', str(out)] + list(extra))
        return out.read_text(encoding='utf-8')

    def test_1_search_writes_new_names(self):
        text = self.run_search()
        names = {n for ns in _projet.load_dictionary(self.dictionary).values() for n in ns}
        self.assertTrue({'is_part_of_dlc', 'secondary_ids', 'cash_price', 'received'} <= names, names)
        self.assertIn('## Passes', text)
        self.assertIn('RTPC', text)

    def test_2_blind_mode_measures_precision(self):
        text = self.run_search('--aveugle', '--sans-ecrire')
        self.assertIn('Mode aveugle', text)
        # 'name' est connu de DECA (dictionnaires fournis) : la passe directe le retrouve depuis m_Name de l'exe
        self.assertRegex(text, r'Retrouvés avec le même nom : [1-9]')
        self.assertIn('Noms différents de ceux de DECA (faux positif ou vraie collision) : 0', text)

    def test_3_inject_then_undo(self):
        db_path = Path(self.project) / 'db' / 'core.db'

        def count(s):
            db = sqlite3.connect(str(db_path))
            n = db.execute('SELECT COUNT(*) FROM core_strings WHERE string = ?', (s,)).fetchone()[0]
            db.close()
            return n
        name_before = count('name')
        RH.main(['injecter', self.project, '--dictionnaire', str(self.dictionary)])
        self.assertEqual(count('cash_price'), 1)
        self.assertTrue((Path(self.project) / RH.INJECTION_LOG).is_file())
        RH.main(['annuler', self.project])
        self.assertEqual(count('cash_price'), 0)
        self.assertEqual(count('name'), name_before)   # une chaine preexistante n'est jamais retiree


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
