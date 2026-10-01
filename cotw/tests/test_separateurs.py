#!/usr/bin/env python3
"""
Tests de cotw/separateurs.py et des options --champ / --modifier de cotw/cloner_munition.py, sur un faux
projet DECA (vrai scan) dont equipment_data.bin reproduit la situation observee dans le jeu le 2026-10-01 :
  munitions vendues : locked = 3, 0x4923c2e2 = 1, prix > 0 ;
  munition de depart (.243 SP) : locked = 1, 0x4923c2e2 = 0, prix 0 ;
  munition absente du jeu (7.62x54R FMJ) : locked = 5, le reste comme une munition vendue.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_separateurs.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import cloner_munition as C  # noqa: E402
import faux_projet  # noqa: E402
import scenario_registre as S  # noqa: E402
import separateurs as SE  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_f32, k_type_str, k_type_u32  # noqa: E402

FMJ = 'equipment_ammo_762x54r_fmj_01'
SP243 = 'equipment_ammo_243_sp_01'
SOLD = ['equipment_ammo_308_pt_01', 'equipment_ammo_762x54r_sp_01', 'equipment_ammo_410ga_slug_01',
        'equipment_ammo_270_sp_01', 'equipment_ammo_30_06_sp_01']


def ammo(name, price, locked, flag):
    entry = W.make_node('compatibility_0', [W.make_prop('equipment_name', k_type_str, S.WEAPON.encode()),
                                            W.make_prop('ammunition_slot_index', k_type_u32, 0)])
    return W.make_node(name, [
        W.make_prop('name', k_type_str, name.encode()),
        W.make_prop('cash_price', k_type_u32, price),
        W.make_prop('locked', k_type_u32, locked),
        W.make_prop(0x4923c2e2, k_type_u32, flag),
        W.make_prop('capacity_cost', k_type_f32, 0.02),
        W.make_prop('note', k_type_str, b'Libelle')], [W.make_node('compatibilities', [], [entry])])


def equipment_data() -> bytes:
    items = [ammo(n, 300 + i, 3, 1) for i, n in enumerate(SOLD)] + [ammo(SP243, 0, 1, 0), ammo(FMJ, 600, 5, 1)]
    weapon = W.make_node(S.WEAPON, [W.make_prop('name', k_type_str, S.WEAPON.encode())])
    return S.rtpc_bytes(W.make_node(0, [], [W.make_node('ammo', [], items), W.make_node('weapons', [], [weapon])]))


def spawn_defs() -> bytes:
    entries = [W.make_node(n, [W.make_prop(0xf71c2a21, k_type_str, n.encode()),
                               W.make_prop('entity', k_type_str, S.entity(n).encode())]) for n in SOLD + [SP243, FMJ]]
    return S.rtpc_bytes(W.make_node(0, [], [W.make_node('spawn_system', [], [W.make_node('resource_definitions', [], entries)])]))


class TestSeparators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_sep_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, [
            ('settings/hp_settings/equipment_data.bin', equipment_data()),
            ('settings/spawn_ammunition_defs.bin', spawn_defs())])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def report(self, *extra):
        out = Path(self.tmp.name) / 'sep.md'
        SE.main([self.project, '--out', str(out)] + list(extra))
        return out.read_text(encoding='utf-8')

    def test_locked_is_perfect_separator(self):
        text = self.report()
        part1 = text.split('## 1.')[1].split('## 2.')[0]
        self.assertIn('locked', part1)
        self.assertIn('{} = 5'.format(FMJ), part1)
        self.assertNotIn('0x4923c2e2', part1)   # FMJ a la meme valeur que les munitions vendues
        self.assertIn('Ignorés (pas dans equipment_data.bin) : `equipment_ammo_410_slug_01`', text)

    def test_distribution_lists_rare_values(self):
        text = self.report('--champ', 'locked')
        part3 = text.split('## 3.')[1]
        self.assertIn('| 3 | 5 |', part3)
        self.assertRegex(part3, r'\| 1 \| 1 \| \S+ 1 \| `{}` \|'.format(SP243))   # categorie : nom ou hash

    def test_presents_list_marks_partial(self):
        # si l'on declare la .243 SP absente aussi, 0x4923c2e2 = 0 n'appartient qu'a elle : separateur partiel
        text = self.report('--absents', '{},{}'.format(FMJ, SP243))
        self.assertIn('0x4923c2e2', text.split('## 2.')[1].split('## 3.')[0])

    def test_cloner_champ_and_modifier(self):
        out = Path(self.tmp.name) / 'mod'
        C.main([self.project, '--source', SOLD[1], '--nom', 'equipment_ammo_762x54r_test_01', '--prix', '77',
                '--champ', 'locked=3', '--champ', '0x4923c2e2=1', '--modifier', FMJ + ':locked=3', '--sortie', str(out)])
        produced = (out / 'dropzone' / 'settings' / 'hp_settings' / 'equipment_data.bin').read_bytes()
        items = SE.load_items(W.rtpc_from_bytes(produced), {})
        fmj = items[FMJ][1]
        self.assertEqual(W.node_get_prop(fmj, 'locked').data, 3)
        new = items['equipment_ammo_762x54r_test_01'][1]
        self.assertEqual(W.node_get_prop(new, 'cash_price').data, 77)
        report = (out / 'rapport_clonage.txt').read_text(encoding='utf-8')
        self.assertIn('Modification : {} : locked : 5 (0x00000005) -> 3 (0x00000003)'.format(FMJ), report)


class TestSetField(unittest.TestCase):
    def node(self):
        return ammo('equipment_ammo_x_01', 1, 3, 1)

    def test_types_are_kept(self):
        n = self.node()
        C.set_field(n, 'locked', '0x5', 't')
        C.set_field(n, 'capacity_cost', '0.5', 't')
        C.set_field(n, 'note', 'Autre', 't')
        self.assertEqual(W.node_get_prop(n, 'locked').data, 5)
        self.assertAlmostEqual(W.node_get_prop(n, 'capacity_cost').data, 0.5)
        self.assertEqual(W.node_get_prop(n, 'note').data, b'Autre')

    def test_errors(self):
        n = self.node()
        with self.assertRaises(C.EClone):
            C.set_field(n, 'inexistant', '1', 't')
        with self.assertRaises(C.EClone):
            C.set_field(n, 'locked', 'abc', 't')
        with self.assertRaises(C.EClone):
            C.set_field(n, 'locked', str(2 ** 32), 't')
        with self.assertRaises(C.EClone):
            C.parse_field_spec('locked3', with_item=False)
        self.assertEqual(C.parse_field_spec('a_01:locked=3', with_item=True), ('a_01', 'locked', '3'))


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
