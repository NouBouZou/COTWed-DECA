#!/usr/bin/env python3
"""
Tests de cotw/resoudre_hashes.py : variantes de noms, recherche par hash, collecte des cibles RTPC et ADF,
dictionnaire, mode --cibles (classes, corroboration, temoin), puis essai de bout en bout sur un faux projet DECA
(vrai scan) avec recherche sur fichiers, recherche sur les cibles d'un audit, injection et annulation.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_resoudre_hashes.py
"""
import random
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

    def test_positional_pass_finds_list_keys(self):
        t = RH.Targets()
        t.add(32, h32('compatibility_3'), 'enfant', 'test')
        t.add(32, h32('compatibility_01'), 'enfant', 'test')
        s = RH.Searcher(t)
        s.run('positionnels', RH.gen_positional(['compatibility'], 16))
        found = {n for names in s.hits.values() for n in names}
        self.assertEqual(found, {'compatibility_3', 'compatibility_01'})

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


def write_audit_csv(path, rows):
    """inconnus.csv au format d'audit_noms_internes.py : (categorie, largeur, hash, occurrences)."""
    lines = ['categorie;largeur;hash;occurrences;fichiers;exemple']
    lines += ['{};{};{:x};{};1;fichier_{}'.format(c, w, h, n, i) for i, (c, w, h, n) in enumerate(rows)]
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')


class TestTargetsMode(unittest.TestCase):
    def test_classes_and_widths(self):
        self.assertEqual(RH.classe('rtpc_noeud', 79074), 'rtpc_noeud|>=1000')
        self.assertEqual(RH.classe('rtpc_noeud', 1), 'rtpc_noeud|1')
        self.assertEqual(RH.classe('rtpc_noeud', 9), 'rtpc_noeud|2-9')
        self.assertEqual(RH.categorie(RH.TEMOIN + 'rtpc_noeud|1'), 'rtpc_noeud')
        self.assertEqual(RH.width_of('rtpc_event', 0x1234, 48), 32)            # evenement sur 32 bits : hash32
        self.assertEqual(RH.width_of('rtpc_event', 0xFFFF00001234, 48), 48)

    def test_targets_from_csv(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'inconnus.csv'
            write_audit_csv(path, [('rtpc_propriete', 32, h32('a_b'), 5000), ('rtpc_noeud', 32, 0x42, 3),
                                   ('rtpc_objid', 64, 0xFFFF00001234ABCD, 1), ('rtpc_event', 48, 0x77, 2),
                                   ('rtpc_noeud', 32, 0x43, 1)])
            real = RH.targets_from_csv(path, ['rtpc_propriete', 'rtpc_noeud', 'rtpc_event'])
            self.assertEqual(sorted(real), ['rtpc_event|2-9', 'rtpc_noeud|1', 'rtpc_noeud|2-9',
                                            'rtpc_propriete|>=1000'])
            self.assertEqual(real['rtpc_propriete|>=1000'].items[(32, h32('a_b'))].count, 5000)
            self.assertIn((32, 0x77), real['rtpc_event|2-9'].items)
            self.assertEqual(sorted(RH.targets_from_csv(path, ['rtpc_noeud'], min_occurrences=2)), ['rtpc_noeud|2-9'])

    def test_fictive_targets_mirror_widths_and_never_overlap(self):
        real = {'c|1': RH.Targets()}
        for v in range(1, 200):
            real['c|1'].add(32, v, 'c', '')
        real['c|1'].add(48, 0xABCDEF012345, 'c', '')
        fake = RH.fictive_targets(real)
        self.assertEqual(list(fake), [RH.TEMOIN + 'c|1'])
        widths = sorted(w for (w, _v) in fake[RH.TEMOIN + 'c|1'].items)
        self.assertEqual(widths, sorted(w for (w, _v) in real['c|1'].items))
        self.assertFalse(set(fake[RH.TEMOIN + 'c|1'].items) & set(real['c|1'].items))

    def test_roots(self):
        self.assertEqual(RH.racines('positionnels', 'frame_12'), ('frame_',))
        self.assertEqual(RH.racines('positionnels', 'bone5'), ('bone',))
        self.assertIn('clues', RH.racines('affixes', 'show_clues'))
        self.assertIn('animal_type', RH.racines('affixes', 'animal_type_id'))
        self.assertEqual(RH.racines('verbes', 'hide_haggis'), ('haggis',))
        self.assertEqual(RH.racines('paires', 'cluster_radius'), ('1:cluster', '9:radius'))
        self.assertEqual(RH.racines('directs', 'track_events'), ())

    def searcher_with_noise(self, real_names, noise):
        """Classe 'rtpc_noeud|1' : les hashes de real_names, plus `noise` valeurs aleatoires (qui elevent le taux
        de faux positifs de la classe comme le feraient des dizaines de milliers de noeuds inconnus)."""
        t = RH.Targets()
        for n in real_names:
            t.add(32, h32(n), 'rtpc_noeud', '')
        rng = random.Random(1)
        while len(t) < len(real_names) + noise:
            t.add(32, rng.getrandbits(32), 'rtpc_noeud', '')
        real = {'rtpc_noeud|1': t}
        targets = dict(real)
        targets.update(RH.fictive_targets(real))
        return real, RH.CategorySearcher(targets)

    def test_decide_all_when_expected_false_positives_are_low(self):
        real, s = self.searcher_with_noise(['track_events', 'sub_files'], 0)
        s.run('directs', RH.gen_direct(['TrackEvents', 'SubFiles', 'Other'], []))
        (d,) = RH.decide(s, list(real), 0.1, 0.01)
        self.assertEqual((d.mode, d.touched), ('tous', 2))
        sel = RH.select_names(s, [d])
        self.assertEqual(sorted(n for n, _p, _e in sel.retained['rtpc_noeud|1'].values()), ['sub_files', 'track_events'])

    def test_decide_corroborated_only_in_a_noisy_pass(self):
        names = ['frame_{}'.format(i) for i in range(5)] + ['lonely_7']
        real, s = self.searcher_with_noise(names, 20000)
        info = s.run('positionnels', RH.gen_positional(['frame', 'lonely'], 16), 26, 1)
        info.candidates = 10 ** 7     # comme une vraie passe : ~47 coincidences attendues sur la classe
        (d,) = RH.decide(s, list(real), 0.1, 0.01)
        self.assertEqual(d.mode, 'corrobores')
        self.assertGreater(d.expected_all, 10)
        self.assertLess(d.expected_corr, 0.1)
        sel = RH.select_names(s, [d])
        kept = sorted(n for n, _p, _e in sel.retained['rtpc_noeud|1'].values())
        self.assertEqual(kept, ['frame_{}'.format(i) for i in range(5)])   # lonely_7 : seul de sa racine
        self.assertEqual(d.temoin_retained, 0)

    def test_decide_corroboration_by_a_name_retained_earlier(self):
        real, s = self.searcher_with_noise(['segment_id', 'segment_type'], 20000)
        s.run('directs', RH.gen_direct(['SegmentId'], []))
        info = s.run('affixes', RH.gen_affixes(['segment']), len(RH.AFFIX_PREFIXES) + len(RH.AFFIX_SUFFIXES), 1)
        info.candidates = 10 ** 7
        d_direct, d_affix = RH.decide(s, list(real), 0.1, 0.01)
        self.assertEqual((d_direct.mode, d_affix.mode), ('tous', 'corrobores'))   # segment_type <- segment_id
        names = sorted(n for n, _p, _e in RH.select_names(s, [d_direct, d_affix]).retained['rtpc_noeud|1'].values())
        self.assertEqual(names, ['segment_id', 'segment_type'])

    def test_decide_nothing_when_corroboration_is_not_enough(self):
        real, s = self.searcher_with_noise(['cluster_radius', 'cluster_density'], 20000)
        info = s.run('paires', RH.gen_pairs(['cluster', 'radius', 'density']), 3, 2)
        info.candidates, info.group_size = 10 ** 9, 10 ** 6     # racines immenses : la corroboration ne prouve rien
        (d,) = RH.decide(s, list(real), 0.1, 0.01)
        self.assertEqual(d.mode, 'aucun')
        self.assertFalse(RH.select_names(s, [d]).retained)

    def test_two_retained_names_on_one_hash_are_ambiguous(self):
        t = RH.Targets()
        t.add(32, h32('alpha'), 'rtpc_propriete', '')
        s = RH.CategorySearcher({'rtpc_propriete|1': t})
        s.run('directs', iter(['alpha']))
        s.hits['rtpc_propriete|1'][(32, h32('alpha'))]['collision_fabriquee'] = 'directs'
        sel = RH.select_names(s, RH.decide(s, ['rtpc_propriete|1'], 0.1, 0.01))
        self.assertEqual(len(sel.ambiguous), 1)
        self.assertFalse(sel.retained['rtpc_propriete|1'])


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


TO_FIND = ('is_part_of_dlc', 'secondary_ids', 'cash_price', 'received')


class TestEndToEnd(unittest.TestCase):
    """Vrai scan DECA d'un faux jeu, puis recherche, injection et annulation."""

    @classmethod
    def setUpClass(cls):
        import faux_projet
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_resolve_')
        root = cls.tmp.name
        exe = (b'MZ' + b'\0' * 64 + b'\0m_Name\0m_IsPartOfDLC\0m_SecondaryIds\0bReceived\0CEquipmentScreenStore\0'
               b'm_CotwCibleFabriquee\0'
               + b'\0' * 64)
        cls.project = faux_projet.build_fake_project(root, [('settings/hp_settings/equipment_data.bin', equipment_data())], exe)
        # Depuis 2026-10-05, le scan charge cotw/dictionnaires/hp_noms.tsv : cash_price, secondary_ids... y sont deja
        # connus. Le cas teste ici est celui d'un projet scanne AVANT que ces noms soient au dictionnaire (ou d'un
        # nom trouve apres le scan) : on les retire de core_strings.
        db = sqlite3.connect(str(Path(cls.project) / 'db' / 'core.db'))
        db.execute('DELETE FROM core_strings WHERE string IN (?, ?, ?, ?)', TO_FIND)
        db.commit()
        db.close()
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
        self.assertTrue(set(TO_FIND) <= names, names)
        self.assertIn('## Passes', text)
        self.assertIn('RTPC', text)

    def test_2_blind_mode_measures_precision(self):
        text = self.run_search('--aveugle', '--sans-ecrire')
        self.assertIn('Mode aveugle', text)
        # 'name' est connu de DECA (dictionnaires fournis) : la passe directe le retrouve depuis m_Name de l'exe
        self.assertRegex(text, r'\| propriete \| \d+ \| [1-9]\d* \| \d+% \|')
        self.assertIn('Noms différents de ceux de DECA (faux positifs) : 0,', text)

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


    def test_4_search_targets_from_audit(self):
        """--cibles : hashes d'un inconnus.csv ; un nom de l'exe (directs), une serie (positionnels), un hash deja
        nomme depuis l'audit (retire des cibles), un objid (categorie non cherchee par defaut)."""
        csv_path = Path(self.tmp.name) / 'inconnus.csv'
        rows = [('rtpc_propriete', 32, h32('cotw_cible_fabriquee'), 1200)]        # m_CotwCibleFabriquee de l'exe
        rows += [('rtpc_noeud', 32, h32('received_{}'.format(i)), 1) for i in range(4)]
        rows += [('rtpc_propriete', 32, h32('name'), 7),                            # connu de DECA
                 ('rtpc_objid', 64, 0xFFFF00001234ABCD, 1)]
        write_audit_csv(csv_path, rows)
        dictionary = Path(self.tmp.name) / 'noms_cibles.tsv'
        out = Path(self.tmp.name) / 'cibles.md'
        RH.main(['chercher', self.project, '--cibles', str(csv_path), '--sources', 'exe', '--vocabulaire',
                 str(self.vocab), '--paires', '0', '--triplets', '0', '--positionnels', '8', '--temoin',
                 '--dictionnaire', str(dictionary), '--out', str(out)])
        text = out.read_text(encoding='utf-8')
        names = {n for ns in _projet.load_dictionary(dictionary).values() for n in ns}
        self.assertEqual(names, {'cotw_cible_fabriquee', 'received_0', 'received_1', 'received_2', 'received_3'})
        self.assertIn('## Bilan', text)
        self.assertIn('| rtpc_propriete | 1 | 1200 | 1 | 1200 | 100.0% |', text)   # 'name' n'est plus une cible
        self.assertIn('| positionnels | 1 | 4 | 4 |', text)
        self.assertNotIn('rtpc_objid', text.split('## Bilan')[1])
        lines = [l for l in dictionary.read_text(encoding='utf-8').splitlines() if 'cotw_cible_fabriquee' in l]
        self.assertIn('rtpc_propriete|>=1000', lines[0])
        # relance : tout est deja au dictionnaire, plus rien a chercher
        RH.main(['chercher', self.project, '--cibles', str(csv_path), '--sources', 'exe', '--vocabulaire',
                 str(self.vocab), '--paires', '0', '--triplets', '0', '--dictionnaire', str(dictionary),
                 '--out', str(out)])
        self.assertIn('Aucune cible', out.read_text(encoding='utf-8'))


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
