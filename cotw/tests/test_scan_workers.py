#!/usr/bin/env python3
"""
Robustesse du scan DECA face a un processus de travail qui meurt sans prevenir.

Avant le correctif (deca/db_commands.py, MultiProcessControl.mp_issue_commands), un processus tue (memoire
insuffisante, gestionnaire des taches, erreur d'import au demarrage en mode spawn) n'envoyait jamais 'process_done' :
le scan attendait indefiniment en ecrivant seulement "ELAPSED TIME" dans le journal. Dans l'interface, la fenetre
affichait un scan sans fin et sans erreur.

Le scan tourne ici dans un fil, comme dans l'interface (deca_gui/project_task.py).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_scan_workers.py
"""
import multiprocessing
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
# Au niveau du module, pas sous __main__ : en mode spawn, chaque processus du scan reimporte ce module, et sous Linux
# il a besoin du substitut de zugbruecke avant d'importer deca.
import _projet  # noqa: E402,F401
import demo_projet  # noqa: E402
import faux_projet  # noqa: E402

SCAN_TIMEOUT = 300


class TestWorkerDeath(unittest.TestCase):
    def test_killed_workers_fail_the_scan_instead_of_hanging(self):
        from deca.db_processor import vfs_structure_open
        root = Path(tempfile.mkdtemp(prefix='cotwed_workers_'))
        outcome = {}

        def scan(project_file):
            try:
                outcome['vfs'] = vfs_structure_open(project_file)
            except Exception as exc:  # noqa: BLE001 - on veut l'exception exacte
                outcome['error'] = exc

        try:
            project_file = faux_projet.build_fake_game(str(root), demo_projet.files())
            thread = threading.Thread(target=scan, args=(project_file,), daemon=True)
            thread.start()

            end = time.time() + SCAN_TIMEOUT
            while not multiprocessing.active_children() and thread.is_alive() and time.time() < end:
                time.sleep(0.01)
            workers = multiprocessing.active_children()
            self.assertTrue(workers, 'aucun processus de scan observe : {}'.format(outcome))
            for worker in workers:
                worker.terminate()
            killed_at = time.time()

            thread.join(60)
            self.assertFalse(thread.is_alive(), 'le scan attend toujours les processus tues')
            self.assertLess(time.time() - killed_at, 60)
            self.assertIn('error', outcome)
            self.assertIn('PROCESSING FAILED', str(outcome['error']))

            # vfs_structure_open ecrit le journal dans work/hp/log.txt (vfs_structure_new : work/hplog.txt, anomalie A1)
            log = (root / 'work' / 'hp' / 'log.txt').read_text(encoding='utf-8', errors='replace')
            self.assertIn('ended without reporting', log)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
