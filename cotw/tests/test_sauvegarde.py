#!/usr/bin/env python3
"""
Tests de cotw/sauvegarde.py (lecture d'une sauvegarde) et de la fenetre Save viewer de l'interface.

Les sauvegardes sont fabriquees (adf_fabrique.profile / save_container) d'apres le format releve le 2026-10-03 ;
les membres non releves (contenu de PPWeaponData_4, LoadoutName) sont inventes pour le test.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_sauvegarde.py [--spawn]
"""
import contextlib
import io
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (niveau module : les processus spawn reimportent ce fichier)
import adf_fabrique as F  # noqa: E402
import demo_projet  # noqa: E402
import noms as N  # noqa: E402
import sauvegarde as SV  # noqa: E402

APP = None
h = N.hash32
AMMO = 'equipment_ammo_243_sp_01'
RIFLE = 'equipment_weapon_ba_rifle_243_01'
SCOPE = 'equipment_sight_scope_4x32_01'
CLONE_88 = 'equipment_ammo_243_test_03'           # munition du mod test14
GONE = 0x0BADF00D                                  # objet que personne ne nomme
MOD_TEST14 = HERE.parent / 'mods' / 'test14.cotwmod.json'


def setUpModule():
    global APP
    from PySide6.QtWidgets import QApplication
    APP = QApplication.instance() or QApplication([])


def payload(**kw):
    args = dict(owned=[(h(AMMO), 30), (h(RIFLE), 1), (h(CLONE_88), 8), (GONE, 3)],
                backpack=[h(AMMO), h(RIFLE), h(CLONE_88)], unlocked=[h(AMMO), h(RIFLE)],
                slots={9: [h(RIFLE), h(AMMO), h(SCOPE)]},
                loadouts=[('Deer', {0: [h(RIFLE), h(AMMO)]}), ('', {})],
                weapons=[(h(RIFLE), 3)])
    args.update(kw)
    return F.profile(**args)


def demo_index():
    idx = SV.Index()
    idx.ajouter_equipment_data(demo_projet.equipment_data(), N.Noms())
    return idx


class TestConteneur(unittest.TestCase):
    def test_decode(self):
        p = payload()
        data = F.save_container(p)
        c = SV.decoder(data)
        self.assertEqual(c.charge, p)
        self.assertEqual((c.save_version, c.comp_version, c.comp_taille, c.zlib_offset),
                         (SV.SAVE_VERSION, SV.COMP_VERSION, len(p), 32))
        self.assertEqual(c.taille_compressee, len(data) - 32)
        self.assertEqual(c.remarques, ['u64 de SAVE = {} : taille decompressee'.format(len(p))])
        self.assertIn('zlib a 0x20', c.resume())

    def test_zlib_level_1_round_trip(self):
        """Constat du 2026-10-03 : zlib niveau 1 redonne les octets d'origine (reecriture exacte possible)."""
        p = payload()
        data = F.save_container(p, level=1)
        self.assertEqual(data[32:], zlib.compress(SV.decoder(data).charge, 1))

    def test_save_u64_meaning(self):
        p = payload()
        for value, what in ((123, 'non reconnu'), (None, 'taille decompressee')):
            c = SV.decoder(F.save_container(p, save_size=value))
            self.assertTrue(c.remarques[-1].endswith(what), c.remarques)
        size = len(F.save_container(p))
        c = SV.decoder(F.save_container(p, save_size=size - 16))
        self.assertTrue(c.remarques[-1].endswith('taille du fichier - 16'))

    def test_anomalies_reported(self):
        p = payload()
        data = bytearray(F.save_container(p))
        struct.pack_into('<Q', data, 24, len(p) + 1)            # COMP annonce une autre taille
        struct.pack_into('<I', data, 4, 0x00330000)             # autre version de SAVE
        c = SV.decoder(bytes(data) + b'tail')
        text = '\n'.join(c.remarques)
        self.assertIn('COMP annonce {} octets, {} decompresses'.format(len(p) + 1, len(p)), text)
        self.assertIn('version SAVE inconnue : 0x00330000', text)
        self.assertIn('4 octets apres le flux zlib', text)
        self.assertEqual(c.charge, p)

    def test_truncated_stream(self):
        data = F.save_container(payload(), level=9)
        c = SV.decoder(data[:-40])
        self.assertIn('flux zlib tronque', '\n'.join(c.remarques))
        with self.assertRaises(SV.ESauvegarde):
            SV.decoder(data[:32] + b'\xff\xff\xff\xff')

    def test_comp_not_at_16(self):
        p = payload()
        data = F.save_container(p)
        moved = data[:16] + b'\0' * 8 + data[16:]
        c = SV.decoder(moved)
        self.assertEqual(c.zlib_offset, 40)
        self.assertEqual(c.charge, p)
        self.assertIn('en-tete COMP a 0x18', '\n'.join(c.remarques))

    def test_uncompressed_and_garbage(self):
        p = payload()
        self.assertEqual(SV.decoder(p).charge, p)                     # ADF5 deja decompresse
        self.assertEqual(SV.decoder(p[5:]).charge, p[5:])             # ADF nu
        self.assertEqual(SV.adf_base(p), 5)
        self.assertEqual(SV.adf_base(p[5:]), 0)
        for bad in (b'', b'PK\x03\x04' + b'\0' * 40, b'SAVE' + b'\0' * 8):
            with self.assertRaises(SV.ESauvegarde):
                SV.decoder(bad)
        with self.assertRaises(SV.ESauvegarde):
            SV.adf_base(b'\x01\x01\x00\x00\x00RTPC')


class TestProfil(unittest.TestCase):
    def read(self, data):
        adf = SV.lire_adf(data)
        return adf, SV.profil(adf)

    def test_summary(self):
        adf, p = self.read(payload())
        self.assertEqual((p.instance, p.type_nom), ('thp_player_profile_adf', 'PlayerProfileData_22'))
        self.assertEqual(p.absents, [])
        self.assertEqual([(e.index, e.hash, e.quantite) for e in p.possedes],
                         [(0, h(AMMO), 30), (1, h(RIFLE), 1), (2, h(CLONE_88), 8), (3, GONE, 3)])
        self.assertEqual((p.sac_mode, p.debloques_mode), (SV.MODE_HASHES, SV.MODE_HASHES))
        self.assertEqual([e.sac for e in p.possedes], [True, True, True, False])
        self.assertEqual([e.debloque for e in p.possedes], [True, True, False, False])
        self.assertEqual(len(p.emplacements), F.PROFILE_SLOTS)
        self.assertEqual(p.emplacements[9], [h(RIFLE), h(AMMO), h(SCOPE)])
        self.assertEqual(sum(1 for s in p.emplacements if s), 1)
        self.assertEqual([label for label, _s in p.loadouts], ['loadout 0 (Deer)', 'loadout 1'])
        self.assertEqual(p.loadouts[0][1][0], [h(RIFLE), h(AMMO)])
        self.assertEqual(p.armes, [(0, {'WeaponHash': h(RIFLE), 'KillCount': 3})])
        self.assertEqual(p.hashes(), [h(AMMO), h(RIFLE), h(CLONE_88), GONE, h(SCOPE)])
        self.assertEqual(p.trous, [])

    def test_gap_and_parallel_flags(self):
        """Une entree nulle au milieu d'EquipmentOwned est signalee ; un sac de petites valeurs est lu en
        parallele a EquipmentOwned [hypothese du lecteur]."""
        owned = [(h(AMMO), 30), (0, 0), (h(RIFLE), 1)]
        _adf, p = self.read(payload(owned=owned, backpack=[1, 0, 0], unlocked=[]))
        self.assertEqual([e.index for e in p.possedes], [0, 2])
        self.assertEqual(p.trous, [2])
        self.assertEqual(p.sac_mode, SV.MODE_PARALLEL)
        self.assertEqual([e.sac for e in p.possedes], [True, False])
        self.assertEqual(p.debloques_mode, SV.MODE_PARALLEL)       # que des zeros
        self.assertEqual([e.debloque for e in p.possedes], [False, False])

    def test_not_a_profile(self):
        adf = SV.lire_adf(demo_projet.stats_ui())
        self.assertIsNone(SV.profil(adf))
        self.assertEqual(SV.type_name(adf, adf.table_instance[0].type_hash), 'XLSBook')

    def test_member_search(self):
        values = {'a': 1, 'inner': {'deep': {'EquipmentOwned': [5]}}}
        self.assertEqual(SV._member(values, 'EquipmentOwned'), [5])
        self.assertIsNone(SV._member(values, 'EquipmentOwned', depth=2))
        self.assertEqual(SV._slot_hashes({'Slots': [1, 2]}), [1, 2])
        self.assertEqual(SV._slot_hashes([3]), [3])
        self.assertEqual(SV._slot_hashes(7), [7])
        self.assertEqual(SV._slot_hashes({'Other': 'x'}), [])


class TestIndex(unittest.TestCase):
    def test_equipment_data(self):
        noms = N.Noms()
        noms.register(['ammo', 'weapons', 'sights'])
        idx = SV.Index()
        n = idx.ajouter_equipment_data(demo_projet.equipment_data(), noms)
        expected = len(demo_projet.AMMO) + len(demo_projet.WEAPONS) + len(demo_projet.SIGHTS)
        self.assertEqual(n, expected)
        self.assertEqual(idx.get(h(SCOPE)).categorie, 'sights')
        self.assertEqual(idx.get(h(AMMO)).cle_nom, demo_projet.display_key(AMMO, '_name'))
        self.assertIsNone(idx.get(h('secondary_ids')), 'un noeud sans propriete name n\'est pas un objet')
        self.assertEqual(idx.sources, ['equipment_data.bin ({} objets)'.format(expected)])
        idx2 = SV.Index()
        idx2.ajouter_equipment_data(demo_projet.equipment_data())
        self.assertEqual(idx2.get(h(SCOPE)).categorie, '0x{:08x}'.format(h('sights')))   # categorie inconnue

    def test_in_game_names(self):
        idx = demo_index()
        self.assertEqual(idx.textes(demo_projet.string_ids(), 'fr'), len(idx))
        fr = idx.get(h(RIFLE)).nom_affiche
        self.assertTrue(fr and fr.isupper(), fr)
        self.assertIn('"{}"'.format(fr), idx.get(h(RIFLE)).libelle())
        self.assertEqual(SV.Index().textes(demo_projet.string_ids(), 'en'), 0)

    def test_mod_file(self):
        idx = demo_index()
        n = idx.ajouter_mod(MOD_TEST14)
        self.assertEqual(n, 2)
        o = idx.get(h(CLONE_88))
        self.assertEqual((o.nom, o.categorie, o.source), (CLONE_88, 'ammo (mod)', 'mod test14.cotwmod.json'))
        self.assertIn('88', o.nom_affiche or '')
        self.assertIn('mod test14.cotwmod.json (2 objets)', idx.sources)


class TestRapport(unittest.TestCase):
    def report(self, idx, arbre=False):
        data = F.save_container(payload())
        c = SV.decoder(data)
        adf = SV.lire_adf(c.charge)
        return '\n'.join(SV.rapport('profile.sav', c, adf, SV.profil(adf), SV.Nommeur(idx, N.Noms()), arbre))

    def test_report_names_and_unknown(self):
        text = self.report(demo_index())
        self.assertIn('thp_player_profile_adf : PlayerProfileData_22', text)
        self.assertIn('Objets possedes (4) ; sac : hashes ; debloques : hashes', text)
        self.assertRegex(text, r'0  {:08x} +30  oui +oui +{}'.format(h(AMMO), AMMO))
        self.assertIn('[9] {}, {}, {}'.format(RIFLE, AMMO, SCOPE), text)
        self.assertIn('loadout 0 (Deer)', text)
        self.assertIn('KillCount=3', text)
        self.assertIn('WeaponHash={} ({})'.format(h(RIFLE), RIFLE), text)
        self.assertIn('Hashes d\'objets inconnus : 2', text)
        self.assertIn('{:08x} : possede, sac'.format(h(CLONE_88)), text)
        self.assertIn('{:08x} : possede'.format(GONE), text)

    def test_mod_names_its_items(self):
        idx = demo_index()
        idx.ajouter_mod(MOD_TEST14)
        text = self.report(idx)
        self.assertIn('Hashes d\'objets inconnus : 1', text)
        self.assertIn(CLONE_88, text)

    def test_no_index_and_tree(self):
        text = self.report(SV.Index(), arbre=True)
        self.assertIn('aucun index', text)
        self.assertIn('Arbre de l\'ADF :', text)
        self.assertIn('instance thp_player_profile_adf', text)
        self.assertIn('EquipmentOwned', text)

    def test_command_line(self):
        tmp = Path(tempfile.mkdtemp(prefix='cotwed_save_'))
        try:
            save = tmp / 'profile.sav'
            save.write_bytes(F.save_container(payload()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = SV.main([str(save), '--mod', str(MOD_TEST14), '--charge', str(tmp / 'charge.bin')])
            self.assertEqual(code, 0)
            self.assertIn('Objets possedes (4)', out.getvalue())
            self.assertEqual((tmp / 'charge.bin').read_bytes(), payload())
            (tmp / 'bad.sav').write_bytes(b'not a save')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(SV.main([str(tmp / 'bad.sav')]), 1)
            self.assertIn('pas une sauvegarde', err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def wait_until(predicate, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return predicate()


class TestSaveViewer(unittest.TestCase):
    """Fenetre Save viewer : sans projet, puis depuis la fenetre principale avec le projet demo ouvert."""
    root = None

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='cotwed_save_'))
        cls.save = cls.root / 'profile.sav'
        cls.save.write_bytes(F.save_container(payload()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def column(self, table, c):
        return [table.item(r, c).text() for r in range(table.rowCount())]

    def test_without_project(self):
        from deca_gui.save_viewer import SaveViewer
        v = SaveViewer()
        self.assertTrue(v.open_file(str(self.save)))
        self.assertEqual(v.items.rowCount(), 4)
        self.assertEqual(self.column(v.items, 1), ['{:08x}'.format(x) for x in (h(AMMO), h(RIFLE), h(CLONE_88), GONE)])
        unknown = v.items.item(3, 2)
        self.assertTrue(unknown.text().startswith('unknown'))
        self.assertIn('Add mod file', unknown.toolTip())
        self.assertIn('no item names', v.status.text())
        self.assertEqual(v.tabs.count(), 5)
        self.assertEqual(v.adf.model.root.children()[0].cols[1], '1 instances')
        v.add_mod(str(MOD_TEST14))
        self.assertEqual(v.items.item(2, 2).text(), CLONE_88)
        self.assertIn('mod test14.cotwmod.json', v.status.text())
        # fichier illisible : rien ne plante, la raison est affichee
        bad = self.root / 'bad.sav'
        bad.write_bytes(b'nothing')
        self.assertFalse(v.open_file(str(bad)))
        self.assertEqual(v.items.rowCount(), 0)
        self.assertIn('pas une sauvegarde', v.status.text())
        v.close()

    def test_from_main_window_with_project(self):
        from deca_gui.main import MainWindow
        project = demo_projet.build(self.root / 'demo')
        errors = []
        w = MainWindow()
        w.error_dialog = errors.append
        try:
            self.assertEqual(w.ui.menu_File.actions()[-2], w.action_save_open)   # juste avant Exit
            self.assertIsNone(w.save_open(path=str(self.root / 'missing.sav')))
            self.assertTrue(errors and 'Not readable as a save' in errors[0])
            w.project_open_path(str(project))
            self.assertTrue(wait_until(lambda: not w.task_running() and w.vfs is not None))
            v = w.save_open(path=str(self.save))
            self.assertIsNotNone(v)
            self.assertIn(v, w.save_viewers)
            v.language.setCurrentText('fr')
            self.assertEqual(v.items.item(1, 2).text(), RIFLE)
            shown = v.items.item(1, 3).text()
            self.assertTrue(shown and shown.isupper(), shown)                 # nom affiche du jeu, en francais
            self.assertEqual(v.items.item(1, 4).text(), 'weapons')            # categorie nommee par le projet
            self.assertIn('equipment_data.bin', v.status.text())
            group = v.slots.topLevelItem(0)
            self.assertEqual(group.text(0), 'InventorySlot')
            self.assertEqual(group.child(0).text(0), '[9]')
            self.assertTrue(group.child(0).child(2).text(1).startswith(SCOPE))
            self.assertEqual(v.slots.topLevelItem(1).text(0), 'loadout 0 (Deer)')
            self.assertEqual(v.weapons.rowCount(), 1)
            self.assertIn(RIFLE, v.weapons.item(0, 1).text())
            self.assertIn('2 unknown item hashes', v.status.text())
            v.close()
        finally:
            wait_until(lambda: not w.task_running(), 120)
            if w.vfs is not None:
                w.tabs_nodes_clear()
                w.vfs.db_conn.close()
            w.close()
            w.deleteLater()
            APP.processEvents()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
