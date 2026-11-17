"""
Tests du message d'environnement de _projet.open_project (module introuvable : venv absent, pas active, incomplet).

Pourquoi : lance hors du venv du depot, un outil echouait sur "No module named 'deca'" sans dire quoi faire.

Lancer : python cotw\\tests\\test_environnement.py
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _projet  # noqa: E402


class BlockDeca:
    """Chercheur de modules qui fait comme si deca n'etait pas installe."""
    def find_spec(self, name, path=None, target=None):
        if name == 'deca' or name.startswith('deca.'):
            raise ModuleNotFoundError("No module named 'deca'", name='deca')
        return None


class EnvironmentHintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.error = ModuleNotFoundError("No module named 'deca'", name='deca')

    def tearDown(self):
        self.tmp.cleanup()

    def hint(self, prefix):
        with mock.patch.object(_projet, 'REPO_ROOT', self.root), mock.patch.object(sys, 'prefix', str(prefix)):
            return _projet.environment_hint(self.error)

    def test_venv_absent(self):
        text = self.hint(self.root / 'ailleurs')
        self.assertIn("No module named 'deca'", text)
        self.assertIn("n'existe pas", text)
        self.assertIn('3.12 -m venv venv', text)
        self.assertIn('-e python', text)

    def test_venv_pas_active(self):
        (self.root / 'venv').mkdir()
        text = self.hint(self.root / 'ailleurs')
        self.assertIn("n'est pas active", text)
        self.assertIn('activate', text)
        self.assertNotIn('pip install', text)

    def test_venv_actif_incomplet(self):
        (self.root / 'venv').mkdir()
        text = self.hint(self.root / 'venv')
        self.assertIn('incomplet', text)
        self.assertIn('pip install -e python', text)

    def test_open_project_sans_deca(self):
        saved = {k: v for k, v in sys.modules.items() if k == 'deca' or k.startswith('deca.')}
        for k in saved:
            del sys.modules[k]
        blocker = BlockDeca()
        sys.meta_path.insert(0, blocker)
        try:
            with mock.patch.object(_projet, 'REPO_ROOT', self.root):
                with self.assertRaises(SystemExit) as ctx:
                    _projet.open_project(self.root)
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)
        message = str(ctx.exception.code)
        self.assertTrue(message.startswith("ERREUR : No module named 'deca'"), message)
        self.assertIn("n'existe pas", message)


if __name__ == '__main__':
    unittest.main()
