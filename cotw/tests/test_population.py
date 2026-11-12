#!/usr/bin/env python3
"""
Tests de cotw/population.py : lecture d'un fichier animal_population_N et comparaison de deux instantanes.

Les populations sont fabriquees (adf_fabrique.population / save_container) avec les tailles et les offsets releves
le 2026-11-12 sur les sauvegardes du PC ; le scenario reprend ce qui y a ete observe : un male preleve quitte son
groupe et entre dans la file de repopulation, puis l'entree est consommee et un animal neuf apparait ailleurs.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_population.py
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401
import adf_fabrique as F  # noqa: E402
import noms as N  # noqa: E402
import population as P  # noqa: E402

WHITETAIL = N.hash32('whitetail_population')
UNKNOWN = 0x0BADF00D
KILLED = (1, 82.588, 188.961, 0, 195423497)
BORN = (1, 81.641, 186.67, 0, 835354302)
GREAT_ONE = (1, 105.5, 310.25, P.FLAG_GREAT_ONE, 42)
DOE = (2, 51.25, 0.0, 0, 7)


def snapshot(groups, queue=(), other=True):
    pops = [(WHITETAIL, groups, queue)]
    if other:
        pops.append((UNKNOWN, [[DOE]], ()))
    return F.save_container(F.population(pops, pressure=bytes([0, 50, 0, 140])))


class TestPopulation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data) -> Path:
        path = Path(self.tmp.name) / name
        path.write_bytes(data)
        return path

    def test_lecture(self):
        r = P.lire(self.write('t0', snapshot([[KILLED, DOE], [GREAT_ONE]], queue=[(1, 70.5, 131.75)])))
        self.assertTrue(r.type.startswith(P.RESERVE_TYPE))
        self.assertEqual((r.graine, r.pression), (2534, (2, 140)))
        deer, other = r.populations
        self.assertEqual((deer.nom, other.nom), ('whitetail', '0x0badf00d'))
        self.assertEqual((deer.nombre(), deer.nombre(1), deer.nombre(2), len(deer.groupes)), (3, 2, 1, 2))
        self.assertEqual(deer.file, [(1, 70.5, 131.75)])
        self.assertEqual(deer.compteurs['Revision'], 6)
        animal = deer.animaux[0]
        self.assertEqual((animal.genre, animal.graine, animal.groupe, animal.rang), (1, 195423497, 0, 0))
        self.assertAlmostEqual(animal.poids, 82.588, places=3)
        self.assertEqual([a.great_one for a in deer.animaux], [False, False, True])
        text = '\n'.join(P.resume(r))
        self.assertIn('whitetail', text)
        self.assertIn('GREAT ONE', '\n'.join(P.animaux(r, 'whitetail')))
        self.assertIn('absente', P.animaux(r, 'moose')[0])

    def test_comparaison_prelevement_puis_repopulation(self):
        t0 = P.lire(self.write('t0', snapshot([[KILLED, DOE], []])))
        t1 = P.lire(self.write('t1', snapshot([[DOE], []], queue=[KILLED[:3]])))
        t2 = P.lire(self.write('t2', snapshot([[DOE], [BORN]])))
        kill = '\n'.join(P.comparer(t0, t1))
        self.assertIn('whitetail : 2 -> 1 animaux ; file 0 -> 1', kill)
        self.assertIn('retire   M poids   82.588', kill)
        self.assertIn('file +   M poids   82.588', kill)
        self.assertNotIn('apparu', kill)
        self.assertNotIn('0x0badf00d', kill)                 # population inchangee : pas de ligne
        born = '\n'.join(P.comparer(t1, t2))
        self.assertIn('apparu   M poids   81.641', born)
        self.assertIn('groupe   1', born)
        self.assertIn('file -   M poids   82.588', born)
        self.assertIn('aucune difference', '\n'.join(P.comparer(t2, t2)))

    def test_population_nouvelle_ou_disparue(self):
        a = P.lire(self.write('a', snapshot([[DOE]], other=False)))
        b = P.lire(self.write('b', snapshot([[DOE]])))
        self.assertIn('0x0badf00d : population nouvelle (1 animaux)', '\n'.join(P.comparer(a, b)))
        self.assertIn('0x0badf00d : population disparue', '\n'.join(P.comparer(b, a)))

    def test_ligne_de_commande(self):
        t0 = self.write('t0', snapshot([[KILLED, DOE], []]))
        t1 = self.write('t1', snapshot([[DOE], []], queue=[KILLED[:3]]))
        out = Path(self.tmp.name) / 'rapport.txt'
        self.assertEqual(P.main([str(t0), str(t1), '--animaux', 'whitetail', '--out', str(out)]), 0)
        text = out.read_text(encoding='utf-8')
        self.assertIn('Comparaison :', text)
        self.assertIn('whitetail : 2 animaux', text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(P.main([str(self.write('bad', b'pas une sauvegarde'))]), 1)
        self.assertIn('erreur :', err.getvalue())
        with contextlib.redirect_stderr(err):                 # un profil n'est pas une population
            self.assertEqual(P.main([str(self.write('profil', F.save_container(F.profile(owned=[(1, 1)]))))]), 1)


if __name__ == '__main__':
    unittest.main()
