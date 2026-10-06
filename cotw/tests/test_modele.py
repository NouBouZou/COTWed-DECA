#!/usr/bin/env python3
"""
Tests du modele 3D de cartouche declare dans l'entite d'une munition : modele_munition.py, puis --modele-de /
--modele / --modele-douille de cloner_munition et "model_from" / "model" / "casing_model" des fichiers de mod, sur le
projet demo (entites a composants fresh_round / empty_casing comme celles du jeu ; la .270 SP declare ses modeles).

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

    def build(self, **kw):
        CM = self.CM
        kw.setdefault('entite', CM.ENTITE_PROPRE)
        return CM.construire(self.vfs, CM.Mod([CM.Munition(SP, NEW, **kw)]))

    def entity(self, result):
        return [d for v, d in result.fichiers if v.endswith(NEW + '.ee')][0]

    def test_model_from(self):
        result = self.build(modele_de=S270)
        self.assertEqual(M.modeles_declares(self.entity(result)), {'fresh_round': SOFT_270, 'empty_casing': EMPTY_270})
        self.assertIn('modeles repris de {}'.format(S270), '\n'.join(result.rapport))
        self.assertEqual(len(result.fichiers), 3)      # equipment_data, spawn, entite : aucun fichier de modele

    def test_explicit_models(self):
        result = self.build(modele=SOFT_270)
        self.assertEqual(M.modeles_declares(self.entity(result)), {'fresh_round': SOFT_270})
        result = self.build(modele_douille=EMPTY_270.replace('/', '\\'))
        self.assertEqual(M.modeles_declares(self.entity(result)), {'empty_casing': EMPTY_270})

    def test_without_option_entity_unchanged(self):
        self.assertEqual(M.modeles_declares(self.entity(self.build())), {})

    def test_refusals(self):
        CM = self.CM
        for kw, message in (({'modele_de': PT}, 'ne declare aucun modele'),
                            ({'modele': 'models/hp_ammunition/absent.modelc'}, 'introuvable'),
                            ({'modele_de': S270, 'modele': SOFT_270}, 'incompatible'),
                            ({'modele_de': S270, 'entite': CM.ENTITE_SOURCE}, '--entite-propre'),
                            ({'modele_de': 'equipment_ammo_absente_01'}, 'introuvable')):
            with self.subTest(message=message):
                with self.assertRaises(CM.EClone) as ctx:
                    self.build(**kw)
                self.assertIn(message, str(ctx.exception))

    def test_cli_equals_mod(self):
        CM, CMOD = self.CM, self.CMOD
        with contextlib.redirect_stdout(io.StringIO()):
            CM.main([self.project, '--source', SP, '--nom', NEW, '--entite-propre', '--modele-de', S270,
                     '--sortie', str(self.tmp / 'cli')])
            CM.main([self.project, '--source', SP, '--nom', 'equipment_ammo_243_modele_02', '--entite-propre',
                     '--modele', SOFT_270, '--modele-douille', EMPTY_270, '--base', str(self.tmp / 'cli' / 'dropzone'),
                     '--sortie', str(self.tmp / 'cli2')])
        mod = self.tmp / 'm.cotwmod.json'
        mod.write_text(json.dumps({'format': CMOD.FORMAT, 'version': CMOD.VERSION, 'name': 'modeles', 'ammo': [
            {'source': SP, 'name': NEW, 'entity': 'own', 'model_from': S270},
            {'source': SP, 'name': 'equipment_ammo_243_modele_02', 'entity': 'own', 'model': SOFT_270,
             'casing_model': EMPTY_270}]}), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            CMOD.main([self.project, str(mod), '--sortie', str(self.tmp / 'built')])

        def files(folder):
            root = self.tmp / folder / 'dropzone'
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

        cli = files('cli')
        cli.update(files('cli2'))
        self.assertEqual(files('built'), cli)
        d = CMOD.to_dict(CMOD.load(mod))
        self.assertEqual(d['ammo'][0]['model_from'], S270)
        self.assertEqual((d['ammo'][1]['model'], d['ammo'][1]['casing_model']), (SOFT_270, EMPTY_270))
        self.assertEqual(CMOD.to_dict(CMOD.from_dict(d)), d)

    def test_command_line_reader(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            M.main([self.project, S270])
        self.assertIn(SOFT_270, out.getvalue())
        self.assertIn('ammo_reload', out.getvalue())


if __name__ == '__main__':
    unittest.main()
