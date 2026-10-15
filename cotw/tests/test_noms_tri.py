#!/usr/bin/env python3
"""
Tests des noms guides par l'ordre des archives : hash_lot.py (hash en lot), voisins_orphelins.bornes (intervalles),
noms_tri.py (passes greffe et grammaire) et leur branchement dans generer_noms.py (controle d'intervalle, temoin).

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_noms_tri.py
"""
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import generer_noms as G  # noqa: E402
import hash_lot as HL  # noqa: E402
import noms_fichiers as NF  # noqa: E402
import noms_tri as NT  # noqa: E402
import voisins_orphelins as VO  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402


def h32(s):
    return int(hash32_func(s.encode())) & 0xFFFFFFFF


class TestHashLot(unittest.TestCase):
    def test_same_hash_as_deca(self):
        rng = random.Random(7)
        alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789_/.'
        noms = [''.join(rng.choice(alphabet) for _ in range(n)) for n in range(0, 60)] * 3
        noms += ['textures/camo/hirschfelden/camo_h_t1_01_dif.ddsc', 'a', 'x' * 12, 'y' * 24, 'z' * 25]
        self.assertEqual(HL.hash32_lot(noms), [h32(n) for n in noms])

    def test_batch_finds_exactly_the_targets(self):
        rng = random.Random(3)
        suffixes = HL.Suffixes('dir{}/name_{:04d}.ddsc'.format(rng.randrange(50), i) for i in range(3000))
        prefixe = 'textures/'
        vrais = sorted(rng.sample(range(len(suffixes)), 25))
        cibles = [h32(prefixe + suffixes.textes[k]) for k in vrais] + [rng.getrandbits(32) for _ in range(500)]
        hh = HL.Hacheur(cibles)
        self.assertEqual(sorted(hh.touches(prefixe, suffixes)), vrais)
        self.assertEqual(sorted(hh.touches(prefixe, suffixes, 100, 2000)), [k for k in vrais if 100 <= k < 2000])
        self.assertEqual(hh.touches(prefixe, suffixes, 5, 5), [])


class TestBornes(unittest.TestCase):
    def test_intervals_between(self):
        self.assertEqual(VO.intervalles_entre(None, 'b'), [('', 'b')])
        self.assertEqual(VO.intervalles_entre('a', None), [('a', None)])
        self.assertEqual(VO.intervalles_entre('a', 'b'), [('a', 'b')])
        # fin d'une suite triee entre les deux voisins : apres le premier OU avant le second
        self.assertEqual(VO.intervalles_entre('t/z', 'c/a'), [('t/z', None), ('', 'c/a')])
        self.assertTrue(VO.dans('t/zz', [('t/z', None), ('', 'c/a')]))
        self.assertTrue(VO.dans('b', [('t/z', None), ('', 'c/a')]))
        self.assertFalse(VO.dans('m', [('t/z', None), ('', 'c/a')]))
        self.assertFalse(VO.dans('a', [('a', 'b')]))   # bornes exclues

    @staticmethod
    def archive(noms):
        """Entrees d'une archive dans l'ordre : un nom, ou un hash (int) pour un fichier sans nom."""
        out = []
        for i, n in enumerate(noms):
            if isinstance(n, int):
                out.append(VO.Entree(i, i * 16, n, None, None, 1, 0))
            else:
                out.append(VO.Entree(i, i * 16, h32(n), n, None, 1, 0))
        return out

    def test_bounds_from_sorted_archive(self):
        arch = self.archive(['a/1', 101, 102, 'a/5', 'b/1', 103])
        b, tri = VO.bornes({1: arch}, nommes=set())
        self.assertEqual(b[101], [('a/1', 'a/5')])
        self.assertEqual(b[102], [('a/1', 'a/5')])
        self.assertEqual(b[103], [('b/1', None)])
        self.assertEqual(tri[1], (2, 2))

    def test_unsorted_archive_gives_no_bounds(self):
        arch = self.archive(['c/1', 101, 'a/1', 'b/1', 'a/0'])
        b, tri = VO.bornes({1: arch}, nommes=set())
        self.assertEqual(tri[1], (1, 3))
        self.assertNotIn(101, b)

    def test_hash_named_elsewhere_is_not_a_target(self):
        arch = self.archive(['a/1', 101, 'a/5'])
        b, _tri = VO.bornes({1: arch}, nommes={101})
        self.assertNotIn(101, b)

    def test_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, 'v.csv')
            with open(p, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('hash;archive;position;taille;type;nommes_avant;nommes_apres;sans_nom_depuis_le_precedent\n')
                f.write('0000abcd;game0.tab;0;1;avtx;a/2 | a/1;a/5 | a/6;0\n')
                f.write('0000abce;game0.tab;0;1;avtx;;a/1;0\n')
            b = VO.bornes_depuis_csv(p)
        self.assertEqual(b[0xabcd], [('a/2', 'a/5')])
        self.assertEqual(b[0xabce], [('', 'a/1')])


class TestOutils(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(NT.prefixes('textures/camo/camo_basic_wrap_mpm.ddsc', 'textures/camo/trophy_trim/p.ddsc'),
                         {'textures/camo/', 'textures/camo/trophy_trim/'})
        self.assertEqual(NT.prefixes('ui/x.ddsc', 'worlds/a.c'), {'', 'ui/', 'worlds/'})
        self.assertEqual(NT.prefixes('a/b/c/x', 'a/d/y'), {'a/', 'a/b/', 'a/b/c/', 'a/d/'})

    def test_plage_sous(self):
        textes = sorted(['a.x', 'b.x', 'c.x', 'd/e.x', 'z.x'])
        self.assertEqual(NT.plage_sous('t/', 't/a.x', 't/d', textes), (1, 3))       # b.x, c.x
        self.assertEqual(NT.plage_sous('t/', 's/q', 't/c', textes), (0, 2))         # bas avant le dossier
        self.assertEqual(NT.plage_sous('t/', 't/c.x', 'u/a', textes), (3, 5))       # haut apres le dossier
        self.assertIsNone(NT.plage_sous('t/', 'u/a', 'v/a', textes))                # dossier avant l'intervalle
        self.assertIsNone(NT.plage_sous('t/', 'r/a', 's/a', textes))                # dossier apres l'intervalle

    def test_grammar_expands_only_series(self):
        formes = NT.apprendre(['camo_t1_01_dif.ddsc', 'camo_t2_01_dif.ddsc', 'camo_t2_01_nrm.ddsc'])
        (f,) = formes
        self.assertEqual(f.n, 4)
        self.assertIn('t7', f.vocab[1])            # t1, t2 : serie -> t0 .. t9
        self.assertEqual(f.vocab[2], ['01'])       # 01 seul : reste tel quel
        self.assertIn('dif.ddsc', f.derniers.textes)
        self.assertIn('nrm.ddsc', f.derniers.textes)


class TestProche(unittest.TestCase):
    def test_close_to_a_known_name(self):
        assoc = G.extensions_associees(G._file_assoc_hp())
        connus = [x for f in NT.apprendre(['mm4100_ale_m_xx_10.wavc', 'mm4100_ale_m_xx_20.wavc',
                                           'spruce_foliage_tree_01_dif.ddsc']) for x in f.connus]
        self.assertTrue(NT.proche('d/mm4100_ale_m_xx_35.wavc', connus, assoc))             # un nombre
        self.assertTrue(NT.proche('d/spruce_foliage_tree_02_mpm.hmddsc', connus, assoc))   # 2 mots, ext. associee
        self.assertFalse(NT.proche('d/mm4100_ale_m_87_832.wavc', connus, assoc))           # "xx" -> "87" : pas un mot
        self.assertFalse(NT.proche('d/spruce_foliage_tree_02_mpm.wavc', connus, assoc))    # 3 differences
        self.assertTrue(NT.proche('d/mm4100_ale_m_xx_100.wavc', connus, assoc))            # 10 -> 100
        self.assertTrue(NT.proche('d/mm4100_ale_m_xx_010.wavc', connus, assoc))            # 10 -> 010 : 2 (retrait + ajout)
        self.assertFalse(NT.proche('d/mm4100_bob_m_87_10.wavc', connus, assoc))            # 1 + 2
        self.assertTrue(NT.proche('d/mm4100_ale_xx_10.wavc', connus, assoc))               # un mot retire
        self.assertTrue(NT.proche('d/spruce_foliage_tree_02_01_dif.ddsc', connus, assoc))  # un numero insere (x2)
        self.assertFalse(NT.proche('d/spruce_big_old_foliage_x_tree_01_dif.ddsc', connus, assoc))  # 3 insertions

    def test_word_distance(self):
        self.assertEqual(NT.distance_mots(['a', 'b'], ['a', 'b'], 2), 0)
        self.assertEqual(NT.distance_mots(['a', '01'], ['a', '02'], 2), 1)
        self.assertEqual(NT.distance_mots(['a', 'xx'], ['a', '02'], 2), 2)        # incompatible : retirer + inserer
        self.assertEqual(NT.distance_mots(['a', 'xx', 'b'], ['a', '02', 'c'], 2), 3)
        self.assertEqual(NT.distance_mots(['a', 'b'], ['a', 'x', 'b'], 2), 1)
        self.assertEqual(NT.distance_mots(['a'], ['a', 'b', 'c', 'd'], 2), 3)


def etat_de(nommes_types, cibles):
    """Etat de generer_noms : nommes_types = [(nom, type)], cibles = {nom vrai: (type, intervalles)}."""
    rows = [(h32(n), n, t) for n, t in nommes_types] + [(h32(n), None, t) for n, (t, _b) in cibles.items()]
    etat = NF.Etat.depuis_lignes(rows)
    etat.bornes = {h32(n): b for n, (_t, b) in cibles.items()}
    return etat


DDSC = [('textures/ui/thumbs/pack_a/camo_a_t{}_0{}_dif.ddsc'.format(t, i), 'avtx') for t in (1, 2) for i in (1, 2, 3)]
WAVC = [('sound/dialogue/eng/mm1150_conni_f_xx_{}.wavc'.format(n), 'FSB5c') for n in (10, 20)] + \
       [('sound/dialogue/eng/mm1210_conni_f_xx_{}.wavc'.format(n), 'FSB5c') for n in (10, 15, 40)] + \
       [('sound/dialogue/eng/mm1210_bob_m_{}_10.wavc'.format(n), 'FSB5c') for n in (12, 13)]
CAMO_BAS, CAMO_HAUT = 'textures/camo/camo_basic.ddsc', 'textures/camo/trophy/x_dif.ddsc'
DIA_BAS, DIA_HAUT = 'sound/dialogue/eng/mm1150_conni_f_xx_20.wavc', 'sound/dialogue/eng/mm1210_conni_f_xx_10.wavc'


class TestPasses(unittest.TestCase):
    def generer(self, cibles, passes=('greffe', 'grammaire')):
        etat = etat_de(DDSC + WAVC + [(CAMO_BAS, 'avtx'), (CAMO_HAUT, 'avtx')], cibles)
        modele = NF.ModeleTypes(etat, G._file_assoc_hp())
        return G.generer(etat, modele, G._file_assoc_hp(), passes, iterations=2), etat, modele

    def test_greffe_and_grammaire(self):
        cibles = {
            # greffe : textures/ui/thumbs/X -> textures/camo/X (comme les camouflages du jeu)
            'textures/camo/pack_a/camo_a_t2_03_dif.ddsc': ('avtx', [(CAMO_BAS, CAMO_HAUT)]),
            # grammaire : serie 10, 15, 20, 40 -> 35
            'sound/dialogue/eng/mm1150_conni_f_xx_35.wavc': ('FSB5c', [(DIA_BAS, DIA_HAUT)]),
            # hors de son intervalle : jamais accepte
            'sound/dialogue/eng/mm1150_conni_f_xx_05.wavc': ('FSB5c', [(DIA_BAS, DIA_HAUT)]),
            # dans l'intervalle, produit par la grammaire (mots de mm1150_conni_f_xx_20 et mm1210_bob_m_12_10), mais
            # loin de tout nom connu : refuse
            'sound/dialogue/eng/mm1150_conni_m_15_40.wavc': ('FSB5c', [(DIA_BAS, DIA_HAUT)]),
        }
        ch, _etat, _m = self.generer(cibles)
        retenus, ambigus = ch.resultats()
        noms = {n: p for n, p in retenus.values()}
        self.assertEqual(noms.get('textures/camo/pack_a/camo_a_t2_03_dif.ddsc'), 'greffe')
        self.assertEqual(noms.get('sound/dialogue/eng/mm1150_conni_f_xx_35.wavc'), 'grammaire')
        self.assertNotIn('sound/dialogue/eng/mm1150_conni_f_xx_05.wavc', noms)
        self.assertNotIn('sound/dialogue/eng/mm1150_conni_m_15_40.wavc', noms)
        self.assertGreaterEqual(sum(s.notes['loin des noms connus'] for s in ch.stats), 1)
        self.assertFalse(ambigus)

    def test_chaines(self):
        cibles = {
            'sound/dialogue/eng/mm1150_conni_f_xx_77.wavc': ('FSB5c', [(DIA_BAS, DIA_HAUT)]),
            'sound/dialogue/eng/mm1150_conni_f_xx_78.wavc': ('FSB5c', [(DIA_BAS, DIA_HAUT)]),
            'sound/dialogue/eng/mm1150_conni_f_xx_79.wavc': ('FSB5c', [(DIA_BAS, DIA_HAUT)]),
        }
        etat = etat_de(DDSC + WAVC, cibles)
        modele = NF.ModeleTypes(etat, G._file_assoc_hp())
        chaines = ['MM1150_Conni_F_XX_77',                          # identifiant (casse ignoree)
                   'sound\\dialogue\\eng\\mm1150_conni_f_xx_78.wav',   # chemin : son nom de base sans extension
                   'mm1150_conni_f_xx-79', 'ab', None, '']        # caracteres hors [a-z0-9_], trop court, vide
        ch = G.generer(etat, modele, G._file_assoc_hp(), ['chaines'], chaines=chaines)
        noms = {n: p for n, p in ch.resultats()[0].values()}
        self.assertEqual(noms, {'sound/dialogue/eng/mm1150_conni_f_xx_77.wavc': 'chaines',
                                'sound/dialogue/eng/mm1150_conni_f_xx_78.wavc': 'chaines'})

    def test_plage_cle_keeps_order(self):
        textes = sorted(['ab', 'ab_c', 'ab0', 'abc', 'b'])
        self.assertEqual([t + '.x' for t in textes], sorted(t + '.x' for t in textes))
        k0, k1 = NT.plage_cle('d/', 'd/ab.x', 'd/abc.x', textes, '.x')
        self.assertEqual(textes[k0:k1], ['ab0', 'ab_c'])

    def test_no_bounds_no_guided_pass(self):
        etat = etat_de(DDSC, {'textures/camo/pack_a/camo_a_t2_03_dif.ddsc': ('avtx', [])})
        etat.bornes = {}
        modele = NF.ModeleTypes(etat, G._file_assoc_hp())
        ch = G.generer(etat, modele, G._file_assoc_hp(), ['greffe', 'grammaire'])
        self.assertEqual([s.nom for s in ch.stats], [])

    def test_interval_control_in_essayer(self):
        nom = 'textures/camo/pack_a/camo_a_t2_03_dif.ddsc'
        etat = etat_de(DDSC, {nom: ('avtx', [('textures/d', 'textures/e')])})
        ch = G.Chercheur(etat, NF.ModeleTypes(etat, G._file_assoc_hp()), {})
        st = G.Stat('t')
        ch.essayer(nom, st)
        self.assertEqual((st.touches, st.hors_intervalle, st.acceptes), (1, 1, 0))
        etat.bornes[h32(nom)] = [('textures/camo/a', None)]   # bord d'archive
        ch = G.Chercheur(etat, NF.ModeleTypes(etat, G._file_assoc_hp()), {})
        st = G.Stat('t')
        ch.essayer(nom, st, guide=True)                      # passe guidee : bord d'archive refuse
        self.assertEqual((st.hors_intervalle, st.acceptes), (1, 0))
        ch.essayer(nom, st)                                  # autre passe : accepte
        self.assertEqual(st.acceptes, 1)

    def test_witness_keeps_intervals(self):
        nom = 'textures/camo/pack_a/camo_a_t2_03_dif.ddsc'
        etat = etat_de(DDSC, {nom: ('avtx', [(CAMO_BAS, CAMO_HAUT)])})
        fictif = G.cibles_fictives(etat)
        (h,) = fictif.sans_nom
        self.assertNotEqual(h, h32(nom))
        self.assertEqual(fictif.bornes[h], [(CAMO_BAS, CAMO_HAUT)])


if __name__ == '__main__':
    unittest.main()
