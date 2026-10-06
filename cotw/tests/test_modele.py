#!/usr/bin/env python3
"""
Tests du modele 3D de cartouche declare dans l'entite d'une munition : modele_munition.py, puis --modele-de /
--modele / --modele-douille / --basculer-armes de cloner_munition et "model_from" / "model" / "casing_model" /
"convert_weapons" des fichiers de mod, sur le projet demo (entites a composants fresh_round / empty_casing comme
celles du jeu ; la .270 SP declare ses modeles ; la Ranger a les siens, la .270 aucun, la 7,62x54R des variantes a
evenements).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_modele.py
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
# Au niveau du module : en mode spawn, les processus du scan reimportent ce fichier (substitut zugbruecke).
import _projet  # noqa: E402
import demo_projet  # noqa: E402
import modele_munition as M  # noqa: E402
import sarc as S  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402

SP = 'equipment_ammo_243_sp_01'
PT = 'equipment_ammo_243_pt_01'
S270 = 'equipment_ammo_270_sp_01'
NEW = 'equipment_ammo_243_modele_01'
SLUG = 'models/hp_ammunition/ammo_bullet_12g_slug.modelc'
SOFT_270, EMPTY_270 = demo_projet.MODELS[S270]
NEW270 = 'equipment_ammo_270_modele_01'
RANGER = 'equipment_weapon_ba_rifle_243_01'
PISTOL = 'equipment_weapon_pistol_ss_243_01'
SOFT_243 = 'models/hp_ammunition/ammo_bullet_243_soft.modelc'
EMPTY_243 = 'models/hp_ammunition/ammo_bullet_243_empty.modelc'


def epe_of(ee: bytes) -> bytes:
    s = S.parse(ee)
    return s.entries[M.entree_epe(s)].data


class TestModeleEpe(unittest.TestCase):
    def test_read(self):
        self.assertEqual([(c.nom, c.point, c.modele) for c in M.lire(demo_projet.entity(SP))],
                         [('fresh_round', 'ammo_reload', None), ('empty_casing', 'ammo_used', None)])
        self.assertEqual(M.modeles_declares(demo_projet.entity(S270)), {'fresh_round': SOFT_270, 'empty_casing': EMPTY_270})
        self.assertEqual(M.modeles_declares(demo_projet.entity(SP)), {})

    def test_add_like_the_game(self):
        """Sans base_model, il est ajoute en derniere propriete : meme forme que l'entite de la .270 SP, qui en a."""
        epe = epe_of(demo_projet.entity(SP))
        out, lines = M.modifier_epe(epe, {'fresh_round': SOFT_270, 'empty_casing': EMPTY_270.replace('/', '\\')})
        self.assertEqual(len(lines), 2)
        self.assertIn('ajoute', lines[0])

        def shape(data):
            res = []

            def walk(n):
                res.append((n.name_hash, [(p.name_hash, p.type) for p in n.prop_table]))
                for c in n.child_table:
                    walk(c)
            walk(W.rtpc_from_bytes(data).root_node)
            return res
        self.assertEqual(shape(out), shape(epe_of(demo_projet.entity(S270))))
        self.assertEqual(M.modeles_declares(S.build(S.Sarc(2, [S.Entry('x.epe', out, len(out))]))[0]),
                         {'fresh_round': SOFT_270, 'empty_casing': EMPTY_270})     # '\\' ecrits en '/'

    def test_replace_and_entity(self):
        ee = demo_projet.entity(S270)
        out, lines = M.modifier(ee, {'fresh_round': SLUG})
        self.assertIn('"{}" -> "{}"'.format(SOFT_270, SLUG), lines[0])
        self.assertEqual(M.modeles_declares(out), {'fresh_round': SLUG, 'empty_casing': EMPTY_270})
        a, b = S.parse(ee), S.parse(out)
        self.assertEqual([e.path for e in a.entries], [e.path for e in b.entries])
        self.assertEqual([e.data for e in a.entries[:2]], [e.data for e in b.entries[:2]])    # autres entrees intactes

    def test_refusals(self):
        epe = epe_of(demo_projet.entity(SP))
        for models, message in (({'usedAmmo_over': SLUG}, 'inconnu'),
                                ({'fresh_round': 'models/x.meshc'}, '.modelc')):
            with self.subTest(message=message):
                with self.assertRaises(M.EModele) as ctx:
                    M.modifier_epe(epe, models)
                self.assertIn(message, str(ctx.exception))
        no_item = S.build(S.Sarc(2, [S.Entry('a.epe', W.rtpc_to_binary(W.rtpc_from_bytes(epe)).replace(
            b'CAmmunitionItem', b'CXmmunitionItem'), 0)]))[0]
        with self.assertRaises(M.EModele):
            M.modifier(no_item, {'fresh_round': SLUG})

    def test_tampering_detected(self):
        epe = epe_of(demo_projet.entity(SP))
        out, _ = M.modifier_epe(epe, {'fresh_round': SLUG})
        tree = W.rtpc_from_bytes(out)
        W.node_get_prop(tree.root_node.child_table[0], W.name_hash('item_id')).data = b'equipment_ammo_autre_01'
        with self.assertRaises(M.EModele) as ctx:
            M.verifier_epe(epe, W.rtpc_to_binary(tree), {'fresh_round': SLUG}, {'fresh_round': None})
        self.assertIn('ailleurs que sur base_model', str(ctx.exception))


class TestArme(unittest.TestCase):
    def test_read_weapon(self):
        self.assertEqual([(c.nom, c.point, c.modele, c.evenements) for c in M.lire_arme(demo_projet.weapon_entity(RANGER))],
                         [('model.bullet_used', 'ammo_used', EMPTY_243, False),
                          ('model.bullet_new', 'ammo_reload', SOFT_243, False)])
        self.assertEqual(M.lire_arme(demo_projet.weapon_entity('equipment_weapon_ba_rifle_270_01')), [])
        with self.assertRaises(M.EModele):
            M.lire_arme(demo_projet.entity(SP))          # entite de munition, pas d'arme

    def test_remove_points(self):
        ee = demo_projet.weapon_entity(RANGER)
        out, lines, removed = M.retirer_points(ee, ['ammo_reload', 'ammo_used'])
        self.assertEqual(removed, {'ammo_reload': SOFT_243, 'ammo_used': EMPTY_243})
        self.assertEqual(M.lire_arme(out), [])
        self.assertEqual(len(lines), 2)
        # le rail (composant sans point d'attache) et l'objet arme restent
        tree = W.rtpc_from_bytes(S.parse(out).entries[0].data)
        self.assertEqual([bytes(W.node_get_prop(c, W.name_hash('name')).data) for c in tree.root_node.child_table[0].child_table],
                         [b'model.rail'])
        out1, _, removed1 = M.retirer_points(ee, ['ammo_used'])
        self.assertEqual([c.point for c in M.lire_arme(out1)], ['ammo_reload'])

    def test_remove_refusals(self):
        for name, point, message in ((RANGER, 'ammo_bullet', 'aucun composant'),
                                     ('equipment_weapon_ba_rifle_762x54r_01', 'ammo_reload', 'evenements')):
            with self.subTest(message=message):
                with self.assertRaises(M.EModele) as ctx:
                    M.retirer_points(demo_projet.weapon_entity(name), [point])
                self.assertIn(message, str(ctx.exception))


class Demo:
    root = None
    project = None

    @classmethod
    def get(cls) -> str:
        if cls.project is None:
            cls.root = Path(tempfile.mkdtemp(prefix='cotwed_modele_demo_'))
            with contextlib.redirect_stdout(io.StringIO()):
                cls.project = str(demo_projet.build(cls.root / 'demo').parent)
        return cls.project


def tearDownModule():
    if Demo.root is not None:
        shutil.rmtree(Demo.root, ignore_errors=True)


class TestClone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cloner_munition as CM
        import construire_mod as CMOD
        cls.CM, cls.CMOD = CM, CMOD
        cls.project = Demo.get()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='cotwed_modele_'))
        self.vfs = _projet.open_project(self.project)
        self.addCleanup(self.vfs.db_conn.close)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, source=SP, nom=NEW, **kw):
        CM = self.CM
        kw.setdefault('entite', CM.ENTITE_PROPRE)
        return CM.construire(self.vfs, CM.Mod([CM.Munition(source, nom, **kw)]))

    def entity(self, result, nom=NEW):
        return [d for v, d in result.fichiers if v.endswith(nom + '.ee')][0]

    def test_weapon_with_own_model_refused(self):
        """La Ranger a ses composants de cartouche : sans --basculer-armes, refus explicite (constat test17)."""
        with self.assertRaises(self.CM.EClone) as ctx:
            self.build(modele_de=S270)
        self.assertIn('{} declare son propre modele au point'.format(RANGER), str(ctx.exception))
        self.assertIn('--basculer-armes', str(ctx.exception))

    def test_weapon_without_model(self):
        """La .270 n'a pas de composant de cartouche : le modele de la munition s'applique, aucune arme touchee."""
        result = self.build(source=S270, nom=NEW270, modele=EMPTY_270)
        self.assertEqual(M.modeles_declares(self.entity(result, NEW270)), {'fresh_round': EMPTY_270, 'empty_casing': EMPTY_270})
        self.assertEqual(len(result.fichiers), 3)      # equipment_data, spawn, entite
        self.assertIn('aucun modele propre au point ammo_reload', '\n'.join(result.rapport))

    def test_convert_weapon(self):
        result = self.build(modele_de=S270, basculer_armes=True)
        files = dict(result.fichiers)
        ranger = demo_projet.weapon_entity_path(RANGER)
        self.assertEqual([c.point for c in M.lire_arme(files[ranger])], [])            # composants retires
        self.assertEqual(M.lire_arme(demo_projet.weapon_entity(RANGER))[0].point, 'ammo_used')   # original intact
        for ammo in (SP, PT):           # munitions de la Ranger : son ancien modele, aspect inchange
            with self.subTest(ammo=ammo):
                self.assertEqual(M.modeles_declares(files[demo_projet.entity_path(ammo)]),
                                 {'fresh_round': SOFT_243, 'empty_casing': EMPTY_243})
        self.assertEqual(M.modeles_declares(self.entity(result)), {'fresh_round': SOFT_270, 'empty_casing': EMPTY_270})
        self.assertEqual(len(files), 6)
        self.assertEqual(list(files)[3:], [ranger, demo_projet.entity_path(SP), demo_projet.entity_path(PT)])
        report = '\n'.join(result.rapport)
        self.assertIn('{} basculee'.format(RANGER), report)
        self.assertIn('note : {} n\'a pas de composant aux points'.format(PISTOL), report)

    def test_convert_gives_weapon_model_to_copies_without_model(self):
        """Une autre copie de la .243 SP, sans modele, recoit celui de la Ranger : son aspect ne change pas."""
        CM = self.CM
        result = CM.construire(self.vfs, CM.Mod([
            CM.Munition(SP, NEW, entite=CM.ENTITE_PROPRE, modele_de=S270, basculer_armes=True),
            CM.Munition(SP, 'equipment_ammo_243_modele_02', entite=CM.ENTITE_PROPRE)]))
        self.assertEqual(M.modeles_declares(self.entity(result, 'equipment_ammo_243_modele_02')),
                         {'fresh_round': SOFT_243, 'empty_casing': EMPTY_243})
        self.assertEqual(M.modeles_declares(self.entity(result)), {'fresh_round': SOFT_270, 'empty_casing': EMPTY_270})

    def test_event_variants_refused(self):
        with self.assertRaises(self.CM.EClone) as ctx:
            self.build(source='equipment_ammo_762x54r_sp_01', nom='equipment_ammo_762x54r_mo_01', modele=SOFT_270,
                       basculer_armes=True)
        self.assertIn('evenements', str(ctx.exception))

    def test_explicit_models(self):
        """Un seul modele donne : seul ce point de l'arme est bascule, l'autre reste a l'arme."""
        ranger = demo_projet.weapon_entity_path(RANGER)
        result = self.build(modele=SOFT_270, basculer_armes=True)
        self.assertEqual(M.modeles_declares(self.entity(result)), {'fresh_round': SOFT_270})
        self.assertEqual([c.point for c in M.lire_arme(dict(result.fichiers)[ranger])], ['ammo_used'])
        self.assertEqual(M.modeles_declares(dict(result.fichiers)[demo_projet.entity_path(SP)]), {'fresh_round': SOFT_243})
        result = self.build(modele_douille=EMPTY_270.replace('/', '\\'), basculer_armes=True)
        self.assertEqual(M.modeles_declares(self.entity(result)), {'empty_casing': EMPTY_270})
        self.assertEqual([c.point for c in M.lire_arme(dict(result.fichiers)[ranger])], ['ammo_reload'])

    def test_without_option_entity_unchanged(self):
        result = self.build()
        self.assertEqual(M.modeles_declares(self.entity(result)), {})
        self.assertEqual(len(result.fichiers), 3)

    def test_refusals(self):
        CM = self.CM
        for kw, message in (({'modele_de': PT}, 'ne declare aucun modele'),
                            ({'modele': 'models/hp_ammunition/absent.modelc'}, 'introuvable'),
                            ({'modele_de': S270, 'modele': SOFT_270}, 'incompatible'),
                            ({'modele_de': S270, 'entite': CM.ENTITE_SOURCE}, '--entite-propre'),
                            ({'basculer_armes': True}, 'demande --modele-de'),
                            ({'modele_de': 'equipment_ammo_absente_01'}, 'introuvable')):
            with self.subTest(message=message):
                with self.assertRaises(CM.EClone) as ctx:
                    self.build(**kw)
                self.assertIn(message, str(ctx.exception))

    def test_cli_equals_mod(self):
        CM, CMOD = self.CM, self.CMOD
        with contextlib.redirect_stdout(io.StringIO()):
            CM.main([self.project, '--source', SP, '--nom', NEW, '--entite-propre', '--modele-de', S270,
                     '--basculer-armes', '--sortie', str(self.tmp / 'cli')])
            CM.main([self.project, '--source', S270, '--nom', NEW270, '--entite-propre',
                     '--modele', SOFT_270, '--modele-douille', EMPTY_270, '--base', str(self.tmp / 'cli' / 'dropzone'),
                     '--sortie', str(self.tmp / 'cli2')])
        mod = self.tmp / 'm.cotwmod.json'
        mod.write_text(json.dumps({'format': CMOD.FORMAT, 'version': CMOD.VERSION, 'name': 'modeles', 'ammo': [
            {'source': SP, 'name': NEW, 'entity': 'own', 'model_from': S270, 'convert_weapons': True},
            {'source': S270, 'name': NEW270, 'entity': 'own', 'model': SOFT_270, 'casing_model': EMPTY_270}]}),
            encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            CMOD.main([self.project, str(mod), '--sortie', str(self.tmp / 'built')])

        def files(folder):
            root = self.tmp / folder / 'dropzone'
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

        cli = files('cli')
        cli.update(files('cli2'))
        self.assertEqual(files('built'), cli)
        d = CMOD.to_dict(CMOD.load(mod))
        self.assertEqual((d['ammo'][0]['model_from'], d['ammo'][0]['convert_weapons']), (S270, True))
        self.assertEqual((d['ammo'][1]['model'], d['ammo'][1]['casing_model']), (SOFT_270, EMPTY_270))
        self.assertNotIn('convert_weapons', d['ammo'][1])
        self.assertEqual(CMOD.to_dict(CMOD.from_dict(d)), d)

    def test_command_line_reader(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            M.main([self.project, S270])
        self.assertIn(SOFT_270, out.getvalue())
        self.assertIn('ammo_reload', out.getvalue())


if __name__ == '__main__':
    unittest.main()
