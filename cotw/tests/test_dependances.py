#!/usr/bin/env python3
"""
Tests de dependances.py (ce qu'une munition ajoutee doit encore a un objet d'origine) et des etiquettes de spawn
(--etiquettes, "tags"), sur le projet demo : .243 SP et PT, .270 SP (qui declare ses modeles), Ranger (composants de
cartouche propres), pistolet .243 (composants a d'autres points), .270 (aucun composant).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_dependances.py
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
import adf_fabrique  # noqa: E402
import demo_projet  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402

SP = 'equipment_ammo_243_sp_01'
PT = 'equipment_ammo_243_pt_01'
S270 = 'equipment_ammo_270_sp_01'
RANGER = 'equipment_weapon_ba_rifle_243_01'
PISTOL = 'equipment_weapon_pistol_ss_243_01'
RIFLE270 = 'equipment_weapon_ba_rifle_270_01'
NEW = 'equipment_ammo_243_dep_01'
NEW2 = 'equipment_ammo_243_dep_02'


class Demo:
    root = None
    project = None

    @classmethod
    def get(cls) -> str:
        if cls.project is None:
            cls.root = Path(tempfile.mkdtemp(prefix='cotwed_dep_demo_'))
            with contextlib.redirect_stdout(io.StringIO()):
                cls.project = str(demo_projet.build(cls.root / 'demo').parent)
        return cls.project


def tearDownModule():
    if Demo.root is not None:
        shutil.rmtree(Demo.root, ignore_errors=True)


def find(bilan, rubrique, statut=None):
    """Elements dont la rubrique commence par ``rubrique`` (et de ce statut)."""
    return [e for e in bilan.elements if e.rubrique.startswith(rubrique) and (statut is None or e.statut == statut)]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cloner_munition as CM
        import construire_mod as CMOD
        import dependances as DEP
        cls.CM, cls.CMOD, cls.DEP = CM, CMOD, DEP
        cls.project = Demo.get()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='cotwed_dep_'))
        self.vfs = _projet.open_project(self.project)
        self.addCleanup(self.vfs.db_conn.close)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, *munitions, **kw):
        return self.CM.construire(self.vfs, self.CM.Mod(list(munitions), **kw))

    def ammo(self, source=SP, nom=NEW, **kw):
        kw.setdefault('entite', self.CM.ENTITE_PROPRE)
        return self.CM.Munition(source, nom, **kw)

    def bilan(self, result, nom=NEW):
        return [b for b in result.dependances if b.munition == nom][0]

    def statut(self, bilan, rubrique):
        found = find(bilan, rubrique)
        self.assertEqual(len({e.statut for e in found}), 1, [(e.rubrique, e.statut) for e in found])
        return found[0].statut


class TestAnalyse(Base):
    def test_test14_settings(self):
        """Reglages de test14 : entite propre, ligne de stats, nom et description propres."""
        DEP = self.DEP
        result = self.build(self.ammo(prix=88, stats_de=SP, stats={'Penetration': '17', 'Expansion': '20',
                                                                     'Class': '3;4;5;6'},
                                      nom_affiche=['BALLES DEP .243'], description=['Test.']))
        b = self.bilan(result)
        self.assertEqual((b.source, b.racine, b.ancetres), (SP, SP, []))
        for rubrique, statut in (('nom affiche', DEP.PROPRE), ('description', DEP.PROPRE), ('icone', DEP.PARTAGE),
                                 ('entite', DEP.PROPRE), ('item_id', DEP.PROPRE), ('tuning_file', DEP.PROPRE),
                                 ('_object_id', DEP.PROPRE), ('reglage, nom d\'instance', DEP.PROPRE),
                                 ('son', DEP.PARTAGE), ('etiquettes de spawn', DEP.HERITE),
                                 ('armes compatibles', DEP.HERITE), ('classes d\'animaux', DEP.HERITE)):
            with self.subTest(rubrique=rubrique):
                self.assertEqual(self.statut(b, rubrique), statut)
        # Ranger : ses composants l'emportent (test17) ; pistolet : ses modeles a d'autres points
        ranger = find(b, 'modele, cartouche chargee ({})'.format(RANGER))
        self.assertEqual((ranger[0].statut, ranger[0].origine), (DEP.PARTAGE, RANGER))
        self.assertIn('ammo_bullet_243_soft', ranger[0].detail)
        pistol = find(b, 'modele, cartouche chargee ({})'.format(PISTOL))[0]
        self.assertEqual((pistol.statut, pistol.origine), (DEP.PARTAGE, PISTOL))
        self.assertIn('ammo_bullet, ammo_empty', pistol.detail)
        # equipment_data : prix propre, le reste herite
        self.assertIn('cash_price : 0 -> 88', find(b, 'equipment_data, champs', DEP.PROPRE)[0].detail)
        self.assertIn('locked = 1', find(b, 'equipment_data, champs', DEP.HERITE)[0].detail)
        # ligne de stats : 3 colonnes propres, les autres reprises de la .243 SP ; classes affichees != reglage
        stats = find(b, 'ligne de stats', DEP.PROPRE)[0].detail
        self.assertIn('Penetration : 15 -> 17', stats)
        self.assertIn('Class : 2;3;4;5;6 -> 3;4;5;6', stats)
        self.assertEqual(find(b, 'ligne de stats', DEP.HERITE)[0].origine, SP)
        self.assertTrue(any('classes affichees au magasin (3;4;5;6)' in n for n in b.notes))
        self.assertFalse(b.independante())
        report = '\n'.join(result.rapport)
        self.assertIn('# Dependances restantes', report)
        self.assertIn('## {} (copie de {})'.format(NEW, SP), report)
        self.assertLess(report.index('Verification'), report.index('# Dependances restantes'))

    def test_source_entity(self):
        """Entite de la source : tout ce qui passe par l'entite est partage, pas de ligne de stats."""
        DEP = self.DEP
        b = self.bilan(self.build(self.ammo(source=PT, entite=self.CM.ENTITE_SOURCE)))
        entity = find(b, 'entite')[0]
        self.assertEqual((entity.statut, entity.origine), (DEP.PARTAGE, PT))
        self.assertEqual(self.statut(b, 'nom affiche'), DEP.PARTAGE)
        self.assertEqual(self.statut(b, 'ligne de stats'), DEP.ABSENT)
        self.assertEqual(find(b, 'item_id'), [])          # entite non analysee : c'est celle de la PT
        self.assertIn('cash_price = 45', find(b, 'equipment_data, champs', DEP.HERITE)[0].detail)

    def test_byte_copy_entity(self):
        """Copie a l'octet pres (diagnostic) : item_id, _object_id et nom d'instance de la source (test4, test13)."""
        DEP = self.DEP
        b = self.bilan(self.build(self.ammo(entite=self.CM.ENTITE_COPIE)))
        self.assertEqual(self.statut(b, 'entite'), DEP.HERITE)
        item = find(b, 'item_id')[0]
        self.assertEqual((item.statut, item.origine), (DEP.PARTAGE, SP))
        self.assertEqual(self.statut(b, '_object_id'), DEP.PARTAGE)
        self.assertEqual(self.statut(b, 'reglage, nom d\'instance'), DEP.PARTAGE)

    def test_chain_in_mod(self):
        """Munition copiee d'une munition du mod : comparee a la racine (la .243 SP) ; ce qu'elle reprend de la
        munition du mod est partage avec le mod, pas avec le jeu."""
        DEP = self.DEP
        result = self.build(self.ammo(nom_affiche=['BALLES DEP .243'], description=['Test.'], icone_de=S270,
                                      balistique={'tracer_settings.enabled': '1'}),
                            self.ammo(source=NEW, nom=NEW2))
        first, second = self.bilan(result), self.bilan(result, NEW2)
        self.assertEqual((second.source, second.racine, second.ancetres), (NEW, SP, [NEW]))
        for rubrique in ('nom affiche', 'description', 'icone'):
            with self.subTest(rubrique=rubrique):
                e = find(second, rubrique)[0]
                self.assertEqual((e.statut, e.origine, e.du_mod), (DEP.PARTAGE, NEW, True))
        icon = find(first, 'icone')[0]
        self.assertEqual((icon.statut, icon.origine), (DEP.HERITE, S270))   # copie de l'icone de la .270
        self.assertIn('tracer_settings.enabled : 0 -> 1', find(second, 'balistique', DEP.PROPRE)[0].detail)
        self.assertEqual(self.statut(second, 'item_id'), DEP.PROPRE)
        self.assertEqual(self.statut(second, '_object_id'), DEP.PROPRE)
        self.assertIn('(dont 3 avec le mod)', '\n'.join(result.rapport))

    def test_independent_when_only_mod_shares(self):
        b = self.DEP.Bilan('x', 'y', 'z')
        b.partage_avec_le_mod('icone', 'y', 'texture de y')
        b.ajouter('balistique', self.DEP.HERITE, 'valeurs')
        self.assertTrue(b.independante())
        b.ajouter('son (.fmod_guids)', self.DEP.PARTAGE, 'GUID de z', 'z')
        self.assertFalse(b.independante())

    def test_converted_weapon_and_models(self):
        """Modeles repris de la .270 SP, Ranger basculee : modele du jeu (partage), arme signalee basculee."""
        DEP = self.DEP
        b = self.bilan(self.build(self.ammo(modele_de=S270, basculer_armes=True)))
        e = find(b, 'modele, cartouche chargee ({})'.format(RANGER))[0]
        self.assertEqual(e.statut, DEP.PARTAGE)
        self.assertIn('ammo_bullet_270_soft', e.detail)
        self.assertIn('arme basculee par le mod', e.detail)
        self.assertTrue(any(PISTOL in n and '[a verifier en jeu]' in n for n in b.notes))

    def test_model_of_the_mod_is_own(self):
        """Un modele fourni par le mod, avec une arme sans composant (.270) : propre."""
        import modele_munition as MM
        DEP, CM = self.DEP, self.CM
        nom = 'equipment_ammo_270_dep_01'
        m = self.ammo(source=S270, nom=nom)
        result = self.build(m)
        files = dict(result.fichiers)
        path = [v for v in files if v.endswith(nom + '.ee')][0]
        own = 'models/mod/ma_cartouche.modelc'
        files[path], _ = MM.modifier(files[path], {MM.CARTOUCHE: own})
        files[own] = b'modele du mod'
        b = DEP.analyser(self.vfs, [m], files, lambda v: CM.load(self.vfs, None, v)[0])[0]
        loaded = find(b, 'modele, cartouche chargee ({})'.format(RIFLE270))[0]
        self.assertEqual((loaded.statut, loaded.detail), (DEP.PROPRE, 'modele du mod : ' + own))
        casing = find(b, 'modele, douille ejectee ({})'.format(RIFLE270))[0]
        self.assertEqual((casing.statut, casing.origine), (DEP.PARTAGE, S270))   # douille de la .270 SP, fichier du jeu
        self.assertIn('comme ' + S270, casing.detail)

    def test_stats_row_after_empty_rows(self):
        """Ligne placee apres la premiere ligne vide : le jeu ne la lit pas (test11) -> inconnu."""
        DEP, CM = self.DEP, self.CM
        m = self.ammo(stats_de=SP)
        result = self.build(m)
        rows = [list(demo_projet.STATS_HEADER)] + [[n] + list(demo_projet.STATS[n]) for n, *_ in demo_projet.AMMO]
        rows += [[True] * len(demo_projet.STATS_HEADER)] * 2 + [[NEW] + list(demo_projet.STATS[SP])]
        files = dict(result.fichiers)
        files['settings/hp_settings/equipment_stats_ui.bin'] = adf_fabrique.xlsbook([('ammo', rows)], order='donnees_avant')
        b = DEP.analyser(self.vfs, [m], files, lambda v: CM.load(self.vfs, None, v)[0])[0]
        self.assertTrue(any('apres la premiere ligne vide' in e.detail for e in find(b, 'ligne de stats', DEP.INCONNU)))

    def test_missing_files_become_unknown(self):
        """Sans spawn_weapon_defs (projets plus anciens) : modeles inconnus, le reste est analyse."""
        DEP, CM = self.DEP, self.CM
        m = self.ammo()
        result = self.build(m)

        def start(v):
            if v == CM.SPAWN_WEAPON:
                raise SystemExit('ERREUR : absent')
            return CM.load(self.vfs, None, v)[0]
        b = DEP.analyser(self.vfs, [m], dict(result.fichiers), start)[0]
        self.assertEqual(find(b, 'modele')[0].statut, DEP.INCONNU)
        self.assertEqual(self.statut(b, 'item_id'), DEP.PROPRE)

    def test_analysis_never_breaks_a_build(self):
        DEP = self.DEP
        saved = DEP.analyser

        def broken(*_a, **_k):
            raise RuntimeError('panne simulee')
        DEP.analyser = broken
        try:
            result = self.build(self.ammo())
        finally:
            DEP.analyser = saved
        self.assertEqual(len(result.fichiers), 3)
        self.assertEqual(result.dependances, [])
        self.assertIn('Dependances restantes : analyse impossible (RuntimeError: panne simulee)', result.rapport)

    def test_files_unchanged_by_analysis(self):
        result = self.build(self.ammo(stats_de=SP, nom_affiche=['X']))
        before = [(v, bytes(d)) for v, d in result.fichiers]
        self.DEP.analyser(self.vfs, [self.ammo(stats_de=SP, nom_affiche=['X'])], dict(result.fichiers),
                          lambda v: self.CM.load(self.vfs, None, v)[0])
        self.assertEqual([(v, bytes(d)) for v, d in result.fichiers], before)

    def test_command_line(self):
        DEP, CMOD = self.DEP, self.CMOD
        mod = self.tmp / 'dep.cotwmod.json'
        mod.write_text(json.dumps({'format': CMOD.FORMAT, 'version': CMOD.VERSION, 'name': 'dep', 'ammo': [
            {'source': SP, 'name': NEW, 'entity': 'own', 'display_name': {'all': 'X'}}]}), encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            DEP.main([self.project, str(mod), '--json', str(self.tmp / 'dep.json')])
        text = out.getvalue()
        self.assertTrue(text.lstrip().startswith('# Dependances restantes'))
        self.assertNotIn('Ecrit :', text)                 # rien n'est ecrit dans un dropzone
        data = json.loads((self.tmp / 'dep.json').read_text(encoding='utf-8'))
        self.assertEqual((data[0]['munition'], data[0]['racine']), (NEW, SP))
        self.assertIn('statut', data[0]['elements'][0])
        self.assertFalse((self.tmp / 'dropzone').exists())


class TestEtiquettes(Base):
    def spawn_tags(self, result, nom=NEW):
        CM = self.CM
        tree = W.rtpc_from_bytes(dict(result.fichiers)[CM.SPAWN_AMMO])
        _, node = CM.find_item(tree.root_node, nom, CM.H_SPAWN_NAME)
        return bytes(W.node_get_prop(node, CM.H_TAGS).data).decode()

    def test_default_keeps_source_tags(self):
        result = self.build(self.ammo())
        self.assertEqual(self.spawn_tags(result), '{},ammo_cal_243,ammo_type_sp,ammunition'.format(NEW))

    def test_own_tags(self):
        DEP = self.DEP
        result = self.build(self.ammo(etiquettes=['ammo_cal_243', 'ammo_type_dep', 'ammunition']))
        self.assertEqual(self.spawn_tags(result), '{},ammo_cal_243,ammo_type_dep,ammunition'.format(NEW))
        report = '\n'.join(result.rapport)
        self.assertIn('etiquette(s) absente(s) du jeu, role inconnu : ammo_type_dep', report)
        self.assertEqual(self.statut(self.bilan(result), 'etiquettes de spawn'), DEP.PROPRE)
        empty = self.build(self.ammo(etiquettes=[]))
        self.assertEqual(self.spawn_tags(empty), NEW)

    def test_refusals(self):
        CM = self.CM
        for tags, message in ((['ammo cal'], 'refusee'), (['ammo_cal_243', 'ammo_cal_243'], 'deux fois'),
                              ([NEW], 'ajoute en tete'), ([PT], 'nom d\'un objet du jeu'), (['é'], 'refusee')):
            with self.subTest(tags=tags):
                with self.assertRaises(CM.EClone) as ctx:
                    self.build(self.ammo(etiquettes=tags))
                self.assertIn(message, str(ctx.exception))

    def test_mod_file_and_command_line(self):
        CM, CMOD = self.CM, self.CMOD
        mod = self.tmp / 'tags.cotwmod.json'
        mod.write_text(json.dumps({'format': CMOD.FORMAT, 'version': CMOD.VERSION, 'name': 'tags', 'ammo': [
            {'source': SP, 'name': NEW, 'entity': 'own', 'tags': 'ammo_cal_243, ammo_type_dep,ammunition'}]}),
            encoding='utf-8')
        mf = CMOD.load(mod)
        self.assertEqual(mf.mod.munitions[0].etiquettes, ['ammo_cal_243', 'ammo_type_dep', 'ammunition'])
        d = CMOD.to_dict(mf)
        self.assertEqual(d['ammo'][0]['tags'], ['ammo_cal_243', 'ammo_type_dep', 'ammunition'])
        self.assertEqual(CMOD.to_dict(CMOD.from_dict(d)), d)
        self.assertNotIn('tags', CMOD.munition_to_dict(self.ammo()))
        with self.assertRaises(CMOD.EMod):
            CMOD.from_dict(dict(d, ammo=[dict(d['ammo'][0], tags=3)]))
        with contextlib.redirect_stdout(io.StringIO()):
            CMOD.main([self.project, str(mod), '--sortie', str(self.tmp / 'built')])
            CM.main([self.project, '--source', SP, '--nom', NEW, '--entite-propre',
                     '--etiquettes', 'ammo_cal_243,ammo_type_dep,ammunition', '--sortie', str(self.tmp / 'cli')])

        def files(folder):
            root = self.tmp / folder / 'dropzone'
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.assertEqual(files('built'), files('cli'))


if __name__ == '__main__':
    unittest.main()
