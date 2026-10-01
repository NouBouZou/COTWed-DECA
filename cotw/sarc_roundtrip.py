#!/usr/bin/env python3
"""
sarc_roundtrip.py -- verifie que l'ecriture SARC (deca.ff_sarc, via cotw/sarc.py) reproduit les archives du jeu.

Pour chaque SARC du projet (un par hash) : lecture, reecriture avec la disposition de DECA, comparaison octet
par octet. Un ecart est classe :
  decalage : une entree n'est pas placee au meme offset (regle d'alignement differente) ;
  fin      : tout est identique sauf la longueur (bourrage en fin de fichier) ;
  en-tete  : le repertoire differe a offsets identiques ;
  donnees  : le contenu differe (ne devrait jamais arriver : les donnees sont recopiees).
C'est le prealable a la fabrication d'une entite (.ee) pour un objet ajoute.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\sarc_roundtrip.py D:\\dev\\work\\hp --out D:\\dev\\audits\\sarc_roundtrip.md
"""
import argparse
import collections
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import sarc as S  # noqa: E402

MAX_EXAMPLES = 15


def project_sarcs(vfs, limit=None):
    rows = vfs.db_query_all("SELECT node_id, v_hash, v_path FROM core_nodes WHERE file_type = 'sarc' "
                            "AND size_u > 0 ORDER BY v_path IS NULL, node_id", [], dbg='cotw_sarc_select')
    seen, selected = set(), []
    for node_id, v_hash, v_path in rows:
        key = v_hash if v_hash is not None else ('uid', node_id)
        if key not in seen:
            seen.add(key)
            selected.append((node_id, v_hash, v_path))
    return selected[:limit] if limit else selected


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Aller-retour lecture/ecriture sur tous les SARC du projet.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--limite', type=int, help='ne traiter que les N premiers SARC (essai)')
    ap.add_argument('--out', help='rapport markdown (sinon : affichage a l\'ecran)')
    args = ap.parse_args(argv)

    vfs = _projet.open_project(args.projet)
    selected = project_sarcs(vfs, args.limite)
    print('{} SARC a verifier'.format(len(selected)))
    by_cat = collections.Counter()
    by_version = collections.Counter()
    examples = collections.defaultdict(list)
    errors = []
    entries = links = 0
    t0 = last = time.time()
    for i, (node_id, v_hash, v_path) in enumerate(selected, 1):
        node = vfs.node_where_uid(node_id)
        label = _projet.node_label(node)
        try:
            data = _projet.read_node(vfs, node)
            r = S.roundtrip(data)
        except Exception as e:  # SARC illisible : on le note et on continue
            errors.append((label, '{}: {}'.format(type(e).__name__, e)))
            continue
        by_cat[r.category] += 1
        by_version[(r.version, r.identical)] += 1
        entries += r.entries
        links += r.links
        if not r.identical and len(examples[r.category]) < MAX_EXAMPLES:
            examples[r.category].append((label, r.detail))
        if time.time() - last > 10 or i == len(selected):
            last = time.time()
            print('  {}/{} ({:.0f} s), identiques : {}'.format(i, len(selected), last - t0, by_cat['identique']))

    total = sum(by_cat.values())
    L = ['# Aller-retour SARC', '',
         '- SARC lus : {} ; illisibles : {} ; entrées : {} (dont {} liens)'.format(total, len(errors), entries, links),
         '- **Identiques à l\'octet près : {} / {}**'.format(by_cat['identique'], total), '',
         '| Sous-version | Identiques | En écart |', '|---|---:|---:|']
    for v in sorted({v for v, _ok in by_version}):
        L.append('| {} | {} | {} |'.format(v, by_version[(v, True)], by_version[(v, False)]))
    L += ['', '| Catégorie | Fichiers |', '|---|---:|']
    for cat, n in by_cat.most_common():
        L.append('| {} | {} |'.format(cat, n))
    for cat, ex in examples.items():
        L += ['', '## Exemples : {}'.format(cat), '']
        L += ['- `{}` : {}'.format(label, detail) for label, detail in ex]
    if errors:
        L += ['', '## Illisibles', ''] + ['- `{}` : {}'.format(l, e) for l, e in errors[:MAX_EXAMPLES]]
    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding='utf-8')
        print('Ecrit : {}'.format(args.out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
