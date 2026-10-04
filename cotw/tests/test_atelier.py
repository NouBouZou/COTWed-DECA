#!/usr/bin/env python3
"""
Tests de l'atelier de munitions : format de mod et reconstruction (construire_mod.py), bibliotheque de
cloner_munition (construire), catalogue (catalogue.py) et fenetre Ammo Workshop de l'interface, hors ecran.

Reference : la ligne de commande cloner_munition.py, validee en jeu jusqu'a test14. L'atelier et construire_mod
doivent produire exactement les memes octets. Seule exception : comparer plusieurs commandes enchainees (--base) a
un mod construit en une passe ; string_ids.stringlookup y est alors plus petit (une seule copie des tableaux
tries) et on compare ses textes, cle par cle et langue par langue.

Tout part du projet demo (cotw/demo_projet.py), construit dans un dossier temporaire par un vrai scan DECA.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_atelier.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
# Au niveau du module : en mode spawn, les processus du scan reimportent ce fichier (substitut zugbruecke).
import _projet  # noqa: E402,F401
import demo_projet  # noqa: E402
import cloner_munition as CM  # noqa: E402
import construire_mod as CMOD  # noqa: E402
import catalogue as K  # noqa: E402
import stringlookup as SL  # noqa: E402

SP = 'equipment_ammo_243_sp_01'
PT = 'equipment_ammo_243_pt_01'
S270 = 'equipment_ammo_270_sp_01'
RIFLE = 'equipment_weapon_ba_rifle_243_01'
EXAMPLE = HERE.parent / 'mods' / 'test14.cotwmod.json'
STATS_CLI = ['--stats-de', SP, '--stat', 'Penetration=17', '--stat', 'Expansion=20', '--stat', 'Class=3;4;5;6']
STATS_MOD = {'Penetration': '17', 'Expansion': '20', 'Class': '3;4;5;6'}
APP = None


class Demo:
    """Projet demo construit une fois pour le module."""
    root = None
    project = None

    @classmethod
    def get(cls) -> str:
        if cls.project is None:
            cls.root = Path(tempfile.mkdtemp(prefix='cotwed_atelier_'))
            with contextlib.redirect_stdout(io.StringIO()):
                cls.project = str(demo_projet.build(cls.root / 'demo').parent)
        return cls.project

    @classmethod
    def cleanup(cls):
        if cls.root is not None:
            shutil.rmtree(cls.root, ignore_errors=True)


def tearDownModule():
    Demo.cleanup()


def run_cli(main, argv) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main([str(a) for a in argv])
    return out.getvalue()


def files_of(folder: Path) -> dict:
    root = Path(folder) / 'dropzone'
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def all_texts(data: bytes):
    vue = SL.Vue(data)
    return [sorted((h, vue.text(t)) for h, t in vue.pairs(lang)) for lang in range(vue.n_lang)]


def write_mod(path: Path, ammo, witness=None, changes=None, name='essai') -> Path:
    path.write_text(json.dumps({'format': CMOD.FORMAT, 'version': CMOD.VERSION, 'name': name,
                                'witness_prices': witness or {}, 'changes': changes or [], 'ammo': ammo}),
                    encoding='utf-8')
    return path


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='cotwed_at_'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Format du fichier de mod
# ---------------------------------------------------------------------------

class TestModFormat(Tmp):
    def test_example_round_trip(self):
        mf = CMOD.load(EXAMPLE)
        self.assertEqual(mf.name, 'test14')
        self.assertEqual([m.nom for m in mf.mod.munitions], ['equipment_ammo_243_test_03', 'equipment_ammo_243_test_02'])
        m = mf.mod.munitions[0]
        self.assertEqual((m.source, m.prix, m.entite, m.stats_de, m.stats, m.nom_affiche),
                         (SP, 88, CM.ENTITE_PROPRE, SP, STATS_MOD, ['BALLES DE TEST 88 .243']))
        self.assertEqual(mf.mod.controle_prix, [S270 + '=5432'])
        d = CMOD.to_dict(mf)
        self.assertEqual(CMOD.to_dict(CMOD.from_dict(d)), d)
        CMOD.save(mf, self.tmp / 'copie.cotwmod.json')
        self.assertEqual(CMOD.to_dict(CMOD.load(self.tmp / 'copie.cotwmod.json')), d)

    def test_texts_and_specs(self):
        self.assertEqual(CMOD.texts_to_specs({'all': 'A', 'en': 'B'}), ['A', 'en=B'])
        self.assertEqual(CMOD.specs_to_texts(['A', 'en=B']), {'all': 'A', 'en': 'B'})
        self.assertEqual(CMOD.texts_to_specs({}), [])
        # un texte "toutes langues" qui commence par fr= serait lu comme le francais : les 10 langues sont ecrites
        specs = CMOD.texts_to_specs({'all': 'fr=x', 'de': 'D'})
        self.assertEqual(len(specs), 10)
        self.assertEqual(SL.textes_par_langue(specs), ['fr=x', 'fr=x', 'D'] + ['fr=x'] * 7)

    def test_refusals(self):
        good = {'format': CMOD.FORMAT, 'version': 1, 'ammo': [{'source': SP, 'name': 'equipment_ammo_x_01'}]}
        bad = [
            ({**good, 'format': 'autre'}, 'pas un fichier de mod'),
            ({**good, 'version': 2}, 'version'),
            ({**good, 'extra': 1}, 'cle(s) inconnue(s)'),
            ({**good, 'ammo': [{'source': SP}]}, '"name" obligatoire'),
            ({**good, 'ammo': [{'source': SP, 'name': 'n', 'price': -1}]}, 'price'),
            ({**good, 'ammo': [{'source': SP, 'name': 'n', 'entity': 'autre'}]}, 'entity'),
            ({**good, 'ammo': [{'source': SP, 'name': 'n', 'nom': 'x'}]}, 'cle(s) inconnue(s)'),
            ({**good, 'ammo': [{'source': SP, 'name': 'n', 'display_name': {'xx': 'y'}}]}, 'langue'),
            ({**good, 'witness_prices': {S270: 'cher'}}, 'witness_prices'),
        ]
        CMOD.from_dict(good)
        for d, message in bad:
            with self.subTest(message=message):
                with self.assertRaises(CMOD.EMod) as ctx:
                    CMOD.from_dict(d)
                self.assertIn(message, str(ctx.exception))


# ---------------------------------------------------------------------------
# Construction : mod contre ligne de commande
# ---------------------------------------------------------------------------

class TestBuild(Tmp):
    @classmethod
    def setUpClass(cls):
        cls.project = Demo.get()

    def cli_88(self, out: Path):
        run_cli(CM.main, [self.project, '--source', SP, '--nom', 'equipment_ammo_243_test_03', '--prix', 88,
                          '--entite-propre'] + STATS_CLI + ['--nom-affiche', 'BALLES DE TEST 88 .243',
                                                            '--description', 'Munition de test 88.',
                                                            '--controle-prix', S270 + '=5432', '--sortie', out])

    AMMO_88 = {'source': SP, 'name': 'equipment_ammo_243_test_03', 'price': 88, 'entity': 'own', 'stats_from': SP,
               'stats': STATS_MOD, 'display_name': {'all': 'BALLES DE TEST 88 .243'},
               'description': {'all': 'Munition de test 88.'}}
    AMMO_77 = {'source': SP, 'name': 'equipment_ammo_243_test_02', 'price': 77, 'label': '.243 Test 77',
               'weapons': [RIFLE], 'fields': {'locked': '3'}, 'entity': 'own', 'stats_from': SP, 'stats': STATS_MOD,
               'display_name': {'all': 'BALLES DE TEST 77 .243', 'en': '.243 TEST 77 BULLET'},
               'description': {'all': 'Munition de test 77.'}}

    def test_single_ammo_mod_equals_cli(self):
        self.cli_88(self.tmp / 'cli')
        mod = write_mod(self.tmp / 'm.cotwmod.json', [self.AMMO_88], {S270: 5432})
        run_cli(CMOD.main, [self.project, mod, '--sortie', self.tmp / 'mod'])
        cli, built = files_of(self.tmp / 'cli'), files_of(self.tmp / 'mod')
        self.assertEqual(len(cli), 5)
        self.assertEqual(built, cli)

    def test_mod_equals_chained_cli(self):
        """test14 complet : deux commandes enchainees avec --base contre un mod de deux munitions."""
        self.cli_88(self.tmp / 't88')
        run_cli(CM.main, [self.project, '--source', SP, '--nom', 'equipment_ammo_243_test_02', '--prix', 77,
                          '--libelle', '.243 Test 77', '--armes', RIFLE, '--champ', 'locked=3',
                          '--modifier', PT + ':locked=0', '--entite-propre'] + STATS_CLI +
                ['--nom-affiche', 'BALLES DE TEST 77 .243', '--nom-affiche', 'en=.243 TEST 77 BULLET',
                 '--description', 'Munition de test 77.', '--base', self.tmp / 't88' / 'dropzone',
                 '--sortie', self.tmp / 't77'])
        mod = write_mod(self.tmp / 'm.cotwmod.json', [self.AMMO_88, self.AMMO_77], {S270: 5432},
                        [{'item': PT, 'field': 'locked', 'value': '0'}])
        run_cli(CMOD.main, [self.project, mod, '--sortie', self.tmp / 'mod'])
        cli = files_of(self.tmp / 't77')
        cli.update({k: v for k, v in files_of(self.tmp / 't88').items() if k.endswith('.ee')})
        built = files_of(self.tmp / 'mod')
        # --comparer : meme verdict, en une commande (verification prevue sur le PC de jeu)
        shutil.copytree(self.tmp / 't88' / 'dropzone', self.tmp / 'deploye', dirs_exist_ok=True)
        shutil.copytree(self.tmp / 't77' / 'dropzone', self.tmp / 'deploye', dirs_exist_ok=True)
        report = run_cli(CMOD.main, [self.project, mod, '--sortie', self.tmp / 'mod2', '--comparer', self.tmp / 'deploye'])
        verdicts = [line for line in report.splitlines() if line.startswith('  ')][-6:]
        self.assertEqual(sum(v.startswith('  identique') for v in verdicts), 5, verdicts)
        self.assertTrue(any(v.startswith('  different   : ' + SL.STRING_IDS) and v.endswith(': identiques')
                            for v in verdicts), verdicts)
        self.assertEqual(sorted(built), sorted(cli))
        for path in built:
            with self.subTest(path=path):
                if path == SL.STRING_IDS:
                    self.assertEqual(all_texts(built[path]), all_texts(cli[path]))
                    self.assertLess(len(built[path]), len(cli[path]))   # une passe au lieu de deux
                else:
                    self.assertEqual(built[path], cli[path])

    def test_example_builds_and_rebuilds(self):
        out = self.tmp / 'sortie'
        report = run_cli(CMOD.main, [self.project, EXAMPLE, '--sortie', out])
        self.assertIn('Mod : test14', report)
        first = files_of(out)
        self.assertEqual(len(first), 6)   # 4 fichiers communs + 2 entites
        manifest = json.loads((out / CMOD.MANIFEST).read_text(encoding='utf-8'))
        self.assertEqual(sorted(manifest['files']), sorted(first))
        # reconstruction sans --ecraser : ce sont nos fichiers, inchanges
        run_cli(CMOD.main, [self.project, EXAMPLE, '--sortie', out])
        self.assertEqual(files_of(out), first)

    def test_foreign_files_and_removed_ammo(self):
        out = self.tmp / 'sortie'
        run_cli(CMOD.main, [self.project, EXAMPLE, '--sortie', out])
        # un fichier modifie a la main n'est plus "a nous" : refuse sans --ecraser
        eq = out / 'dropzone' / Path(CM.EQUIPMENT_DATA)
        eq.write_bytes(eq.read_bytes() + b'\0')
        with self.assertRaises(SystemExit) as ctx:
            run_cli(CMOD.main, [self.project, EXAMPLE, '--sortie', out])
        self.assertIn('--ecraser', str(ctx.exception))
        run_cli(CMOD.main, [self.project, EXAMPLE, '--sortie', out, '--ecraser'])
        # munition retiree du mod : son entite, ecrite par la construction precedente, est supprimee
        mf = CMOD.load(EXAMPLE)
        removed = mf.mod.munitions.pop(1)
        CMOD.save(mf, self.tmp / 'reduit.cotwmod.json')
        ee = [p for p in files_of(out) if p.endswith(removed.nom + '.ee')]
        self.assertEqual(len(ee), 1)
        report = run_cli(CMOD.main, [self.project, self.tmp / 'reduit.cotwmod.json', '--sortie', out])
        self.assertIn('Supprime (plus produit par le mod)', report)
        self.assertNotIn(ee[0], files_of(out))
        # un autre fichier deja present, pas de nous : jamais supprime
        other = out / 'dropzone' / 'settings' / 'autre_mod.bin'
        other.write_bytes(b'x')
        run_cli(CMOD.main, [self.project, self.tmp / 'reduit.cotwmod.json', '--sortie', out])
        self.assertTrue(other.is_file())

    def test_copy_of_an_ammo_of_the_mod(self):
        """B copie A, ajoutee plus haut : entite, ligne de stats et modele de texte lus dans les fichiers en cours."""
        a = dict(self.AMMO_88, name='equipment_ammo_243_moda_01')
        b = {'source': 'equipment_ammo_243_moda_01', 'name': 'equipment_ammo_243_modb_01', 'price': 99,
             'entity': 'own', 'stats_from': 'equipment_ammo_243_moda_01', 'stats': {'Penetration': '18'},
             'display_name': {'all': 'B'}}
        mod = write_mod(self.tmp / 'm.cotwmod.json', [a, b])
        report = run_cli(CMOD.main, [self.project, mod, '--sortie', self.tmp / 'mod'])
        self.assertIn('Origine : mod : editor/entities/hp_weapons/ammunition/rifles/equipment_ammo_243_moda_01.ee',
                      report)
        self.assertIn('equipment_ammo_243_modb_01_name (modele equipment_ammo_243_sp_name)', report)
        built = files_of(self.tmp / 'mod')
        ee_b = built['editor/entities/hp_weapons/ammunition/rifles/equipment_ammo_243_modb_01.ee']
        self.assertIn(b'equipment_ammo_243_modb_01', ee_b)
        self.assertNotIn(b'equipment_ammo_243_moda_01', ee_b)
        cat_rows = [line for line in report.splitlines() if 'ligne ajoutee :' in line]
        self.assertEqual(len(cat_rows), 2)
        self.assertIn("['equipment_ammo_243_modb_01', 150.0, 18.0, 20.0, -1.0, '3;4;5;6', 'x']", cat_rows[1])
        vue = SL.Vue(built[SL.STRING_IDS])
        self.assertEqual(vue.texts('equipment_ammo_243_modb_01_name'), ['B'] * 10)

    def test_refusals(self):
        vfs = _projet.open_project(self.project)
        try:
            for ammo, message in (
                    ([CM.Munition(SP, 'equipment_ammo_x_01'), CM.Munition(SP, 'equipment_ammo_x_01')], 'plusieurs fois'),
                    ([CM.Munition(SP, PT)], 'existe deja'),
                    ([CM.Munition('equipment_ammo_absente_01', 'equipment_ammo_x_01')], 'introuvable'),
                    ([CM.Munition(SP, 'nom avec espaces')], 'ASCII'),
                    ([CM.Munition(SP, 'equipment_ammo_x_01', stats={'Penetration': '1'})], '--stats-de'),
                    ([], 'aucune munition')):
                with self.subTest(message=message):
                    with self.assertRaises(CM.EClone) as ctx:
                        CM.construire(vfs, CM.Mod(ammo))
                    self.assertIn(message, str(ctx.exception))
        finally:
            vfs.db_conn.close()


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

class TestCatalogue(unittest.TestCase):
    def test_demo_catalogue(self):
        vfs = _projet.open_project(Demo.get())
        try:
            steps = []
            cat = K.charger(vfs, 'fr', steps.append)
        finally:
            vfs.db_conn.close()
        self.assertEqual(steps[0], 'equipment_data')
        self.assertEqual(len(cat.ammo), 5)
        self.assertEqual(len(cat.weapons), 4)
        sp = cat.find(SP)
        self.assertEqual(sp.display_name, 'BALLES À TÊTE MOLLE .243')
        self.assertTrue(sp.description.startswith('Objet de démonstration'))
        self.assertEqual(sp.weapons, [RIFLE, 'equipment_weapon_pistol_ss_243_01'])
        self.assertEqual(sp.entity, 'editor/entities/hp_weapons/ammunition/rifles/{}.ee'.format(SP))
        self.assertEqual(sp.stats, [SP, 150.0, 15.0, 17.0, -1.0, '2;3;4;5;6', 'x'])
        self.assertTrue(sp.stats_read_by_game)
        self.assertEqual(cat.stats_first_empty_row, 6)
        self.assertEqual(cat.stats_header[:2], ['Name', 'Stat Range'])
        self.assertEqual((sp.availability, cat.find('equipment_ammo_762x54r_fmj_01').availability),
                         (K.VISIBLE, K.DLC))
        self.assertEqual(cat.find(RIFLE).display_name, 'CARABINE À VERROU .243')
        self.assertEqual(cat.warnings, [])

    def test_availability_rule(self):
        self.assertEqual(K.Item('ammo', 'a', dlc=['-1'], locked=5).availability, K.HIDDEN)
        self.assertEqual(K.Item('ammo', 'a', dlc=['-1'], locked=1).availability, K.VISIBLE)
        self.assertEqual(K.Item('ammo', 'a', dlc=['1095272277'], locked=3).availability, K.DLC)
        self.assertEqual(K.Item('ammo', 'a', dlc=['1', '3988076135'], locked=3).availability, K.FREE)


# ---------------------------------------------------------------------------
# Atelier : logique sans fenetre, puis fenetre hors ecran
# ---------------------------------------------------------------------------

class TestWorkshopLogic(unittest.TestCase):
    def test_suggest_name(self):
        from deca_gui.ammo_workshop import suggest_name
        self.assertEqual(suggest_name(SP, set()), 'equipment_ammo_243_sp_mod_01')
        self.assertEqual(suggest_name(SP, {'equipment_ammo_243_sp_mod_01'}), 'equipment_ammo_243_sp_mod_02')

    def test_problems_without_catalogue(self):
        from deca_gui.ammo_workshop import entry_problems
        long_src = 'equipment_ammo_' + 'x' * 20 + '_01'
        mod = CM.Mod([CM.Munition(long_src, 'equipment_ammo_' + 'x' * 20 + '_02', entite=CM.ENTITE_PROPRE),
                      CM.Munition(SP, 'bad name'),
                      CM.Munition(SP, 'equipment_ammo_ok_01', nom_affiche=['en=only english']),
                      CM.Munition(SP, 'equipment_ammo_ok_02', champs=['=3'])])
        problems = [entry_problems(mod, i, None, CM, SL) for i in range(4)]
        self.assertIn('.ammotunec instance name', problems[0][0])
        self.assertIn('ASCII', problems[1][0])
        self.assertIn('Display name', problems[2][0])
        self.assertIn('FIELD=VALUE', problems[3][0])


def wait_until(predicate, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return predicate()


class TestWorkshopWindow(Tmp):
    @classmethod
    def setUpClass(cls):
        global APP
        from PySide6.QtWidgets import QApplication
        APP = QApplication.instance() or QApplication([])
        cls.project = Demo.get()

    def setUp(self):
        super().setUp()
        from deca_gui.main import MainWindow
        self.errors = []
        self.window = MainWindow()
        self.window.error_dialog = self.errors.append
        self.window.show()
        self.window.project_open_path(self.project)
        self.assertTrue(wait_until(lambda: not self.window.task_running() and self.window.vfs is not None))
        self.assertTrue(self.window.action_ammo_workshop.isEnabled())
        self.window.ammo_workshop_open()
        self.ws = self.window.ammo_workshop
        self.ws.error_dialog = self.errors.append
        self.ws.info_dialog = self.errors.append
        self.assertTrue(wait_until(lambda: self.ws.catalogue is not None and not self.ws.task_running()),
                        'catalogue non charge : {}'.format(self.errors))

    def tearDown(self):
        wait_until(lambda: not self.ws.task_running(), 300)
        self.ws.dirty = False
        self.assertTrue(self.window.ammo_workshop_close())
        self.window.project_close()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        super().tearDown()

    def build(self, out: Path):
        self.ws.output_edit.setText(str(out))
        self.assertTrue(self.ws.build_button.isEnabled(), self.ws.problems_label.text())
        self.ws.build()
        self.assertTrue(wait_until(lambda: not self.ws.task_running(), 300))
        self.assertEqual(self.errors, [])

    def test_add_edit_build_equals_cli(self):
        ws = self.ws
        self.assertTrue(ws.select_game_item(SP))
        ws.add_from_selected()
        m = ws.current_entry()
        self.assertEqual((m.nom, m.entite, m.stats_de), ('equipment_ammo_243_sp_mod_01', CM.ENTITE_PROPRE, SP))
        ws.name_edit.setText('equipment_ammo_243_test_03')
        ws.price_check.setChecked(True)
        ws.price_spin.setValue(88)
        ws.name_texts.all_edit.setText('BALLES DE TEST 88 .243')
        ws.desc_texts.all_edit.setText('Munition de test 88.')
        header = [ws.stats_table.item(r, 0).text() for r in range(ws.stats_table.rowCount())]
        for col_name, value in (('Stat Bullet Penetration', '17'), ('Stat Bullet Expansion', '20'), ('Class', '3;4;5;6')):
            ws.stats_table.item(header.index(col_name), 2).setText(value)
        ws.add_witness()
        ws.witness_table.item(0, 0).setText(S270)
        self.assertEqual(ws.mf.mod.controle_prix, [S270 + '=5432'])
        self.assertTrue(ws.dirty)
        self.build(self.tmp / 'gui')
        TestBuild.cli_88(self, self.tmp / 'cli')
        self.assertEqual(files_of(self.tmp / 'gui'), files_of(self.tmp / 'cli'))
        self.assertIn('Verification', '\n'.join(ws.last_report))

    def test_open_example_build_equals_construire_mod(self):
        self.assertTrue(self.ws.mod_open(str(EXAMPLE)))
        self.assertEqual(self.ws.entry_list.count(), 2)
        self.assertEqual(self.ws.mod_name_edit.text(), 'test14')
        self.assertEqual(self.ws.name_texts.all_edit.text(), 'BALLES DE TEST 88 .243')
        self.assertFalse(self.ws.dirty)   # afficher un mod ne le modifie pas
        self.build(self.tmp / 'gui')
        run_cli(CMOD.main, [self.project, EXAMPLE, '--sortie', self.tmp / 'cli'])
        self.assertEqual(files_of(self.tmp / 'gui'), files_of(self.tmp / 'cli'))
        cmd = self.ws.command_line()
        self.assertTrue(cmd.startswith('python cotw\\construire_mod.py '))
        self.assertIn(str(EXAMPLE), cmd)

    def test_save_round_trip(self):
        self.assertTrue(self.ws.mod_open(str(EXAMPLE)))
        self.ws.entry_list.setCurrentRow(1)
        self.ws.same_weapons_check.setChecked(False)
        from PySide6.QtCore import Qt
        rows = {self.ws.weapons_list.item(r).data(Qt.UserRole): r for r in range(self.ws.weapons_list.count())}
        self.ws.weapons_list.item(rows['equipment_weapon_pistol_ss_243_01']).setCheckState(Qt.Unchecked)
        self.assertEqual(self.ws.current_entry().armes, [RIFLE])
        self.ws.stats_check.setChecked(False)
        self.assertIsNone(self.ws.current_entry().stats_de)
        target = self.tmp / 'enregistre.cotwmod.json'
        self.ws.ask_save_path = lambda: str(target)
        self.ws.mod_path = None
        self.assertTrue(self.ws.mod_save())
        self.assertFalse(self.ws.dirty)
        reloaded = CMOD.load(target)
        self.assertEqual(CMOD.to_dict(reloaded), CMOD.to_dict(self.ws.mf))
        self.assertEqual(reloaded.mod.munitions[1].armes, [RIFLE])
        self.assertEqual(reloaded.mod.munitions[0].stats, STATS_MOD)   # cles du fichier gardees telles quelles

    def test_problems_block_build(self):
        self.ws.select_game_item(SP)
        self.ws.add_from_selected()
        self.assertTrue(self.ws.build_button.isEnabled())
        self.ws.name_edit.setText(PT)
        self.assertIn('already exists in the game', self.ws.problems_label.text())
        self.assertFalse(self.ws.build_button.isEnabled())
        self.ws.name_edit.setText('equipment_ammo_243_ok_01')
        self.assertEqual(self.ws.problems_label.text(), '')
        self.assertTrue(self.ws.build_button.isEnabled())

    def test_unsaved_changes_on_close(self):
        self.ws.select_game_item(SP)
        self.ws.add_from_selected()
        self.ws.ask_save_changes = lambda: 'cancel'
        self.assertFalse(self.ws.close())
        self.assertFalse(self.window.ammo_workshop_close())   # la fenetre principale respecte le refus
        self.ws.ask_save_changes = lambda: 'discard'
        self.assertTrue(self.ws.close())

    def test_language_switch(self):
        self.ws.language_combo.setCurrentIndex(self.ws.language_combo.findData('fr'))
        self.assertTrue(wait_until(lambda: self.ws.catalogue.language == 'fr' and not self.ws.task_running()))
        self.assertEqual(self.ws.catalogue.find(SP).display_name, 'BALLES À TÊTE MOLLE .243')


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        import multiprocessing
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
