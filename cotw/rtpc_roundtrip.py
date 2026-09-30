#!/usr/bin/env python3
"""
rtpc_roundtrip.py -- verifie l'ecriture RTPC de DECA (deca.ff_rtpc_write) sur les vrais fichiers du jeu.

Pour chaque fichier RTPC :
  1. lecture avec le lecteur de DECA ;
  2. reecriture avec deca.ff_rtpc_write ;
  3. relecture du resultat et comparaison des deux arbres (noms, types, valeurs) : c'est la condition
     indispensable, un ecart ici est un bug de l'ecrivain ;
  4. comparaison octet par octet avec l'original : l'egalite prouve que l'ecrivain reproduit exactement
     celui du jeu. En cas d'ecart, l'outil indique la premiere structure placee differemment et mesure
     les regles de l'original (octets de bourrage, chaines partagees, valeurs partagees, fin de fichier).
  5. option --explorer : essaie d'autres regles de disposition sur les fichiers en ecart et indique
     celle qui en reproduit le plus.

Sources possibles :
  - un projet DECA (dossier contenant db\\core.db) : tous les RTPC du jeu, un par hash. DECA doit etre ferme ;
  - un dossier de fichiers extraits : tous les fichiers qui commencent par 'RTPC'.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\rtpc_roundtrip.py D:\\dev\\work\\hp --out D:\\dev\\audits\\rtpc-01 --explorer
    python cotw\\rtpc_roundtrip.py D:\\extraits --out D:\\dev\\audits\\rtpc-extraits

Aucun fichier du jeu ni la base ne sont modifies. Lire un fichier compresse peut ajouter des fichiers
dans le cache de DECA (work\\hp\\__CACHE__), comme quand on parcourt le projet dans l'interface.
"""
import argparse
import collections
import csv
import hashlib
import itertools
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if not sys.platform.startswith('win'):
    # Sous Linux, deca importe zugbruecke (pont vers les DLL Windows, qui exige Wine) : inutile pour COTW.
    import ctypes
    sys.modules['zugbruecke.ctypes'] = ctypes

import deca.ff_rtpc_write as W
from deca.ff_rtpc import k_type_str


# ---------------------------------------------------------------------------
# Sources de fichiers
# ---------------------------------------------------------------------------

class SilentLogger:
    """Journal muet : on ne veut pas ecrire dans le journal du projet DECA."""
    def log(self, *a, **k): pass
    trace = debug = warning = error = log


def iter_project_files(project_dir, limit=None):
    """Tous les RTPC d'un projet DECA, un par v_hash (DECA stocke plusieurs copies d'un meme fichier)."""
    from deca.db_core import VfsDatabase
    project_file = project_dir / 'project.json'
    if not (project_dir / 'db' / 'core.db').is_file() or not project_file.is_file():
        raise SystemExit('ERREUR : {} n\'est pas un projet DECA (db\\core.db ou project.json absent)'.format(project_dir))
    working_dir = str(project_dir).replace('\\', '/') + '/'
    vfs = VfsDatabase(str(project_file).replace('\\', '/'), working_dir, SilentLogger())
    rows = vfs.db_query_all(
        "SELECT node_id, v_hash, v_path FROM core_nodes WHERE file_type = 'rtpc' ORDER BY v_path IS NULL, node_id")
    seen = set()
    selected = []
    for node_id, v_hash, v_path in rows:
        key = v_hash if v_hash is not None else ('uid', node_id)
        if key in seen:
            continue
        seen.add(key)
        selected.append((node_id, v_hash, v_path))
    if limit:
        selected = selected[:limit]
    yield len(selected)
    for node_id, v_hash, v_path in selected:
        node = vfs.node_where_uid(node_id)
        name = v_path.decode('utf-8', 'replace') if isinstance(v_path, bytes) else v_path
        label = name or '(sans nom) {:08x}'.format(v_hash & 0xFFFFFFFF if v_hash is not None else 0)
        try:
            with vfs.file_obj_from(node) as f:
                data = f.read(node.size_u)
        except Exception as e:
            yield label, None, 'extraction impossible : {!r}'.format(e)
            continue
        yield label, data, None


def iter_folder_files(folder, limit=None):
    files = []
    for p in sorted(folder.rglob('*')):
        if p.is_file():
            try:
                with open(p, 'rb') as f:
                    if f.read(4) == W.RTPC_MAGIC:
                        files.append(p)
            except OSError:
                continue
    if limit:
        files = files[:limit]
    yield len(files)
    for p in files:
        yield str(p.relative_to(folder)).replace('\\', '/'), p.read_bytes(), None


# ---------------------------------------------------------------------------
# Analyse de la disposition d'un fichier original
# ---------------------------------------------------------------------------

def align(value, a):
    return value + (-value) % a


def analyze_layout(data, tree):
    """
    Reconstitue les zones occupees par chaque structure de l'original et mesure ses regles :
    bourrage, partage de valeurs, chaines dupliquees, octets en fin de fichier, tri des hashes.
    """
    covered = bytearray(len(data))
    spans_by_offset = collections.defaultdict(list)
    string_offsets = collections.defaultdict(set)
    stats = collections.Counter()
    trailer_examples = []
    is_v2 = tree.version == 2

    def mark(start, end):
        start, end = max(start, 0), min(end, len(data))
        if end > start:
            covered[start:end] = b'\x01' * (end - start)

    mark(0, 20)

    def walk(node):
        off = node.data_offset
        n_props, n_children = len(node.prop_table), len(node.child_table)
        mark(off, off + W.PROP_HEADER_SIZE * n_props)
        child_headers = align(off + W.PROP_HEADER_SIZE * n_props, 4)
        mark(child_headers, child_headers + W.NODE_HEADER_SIZE * n_children)
        if is_v2:
            # u32 apres les en-tetes des enfants : on compare sa valeur aux regles candidates
            t_off = W.node_trailer_offset(node)
            mark(t_off, t_off + 4)
            value = int.from_bytes(data[t_off:t_off + 4], 'little')
            valid = W.node_trailer_value(node, 'valid_props')
            stats['v2_noeuds'] += 1
            stats['v2_compteur_egal_valides'] += int(value == valid)
            stats['v2_compteur_egal_total'] += int(value == n_props)
            if value != valid and len(trailer_examples) < 3:
                trailer_examples.append('noeud 0x{:08x} a 0x{:x} : lu {}, {} proprietes dont {} non-none'.format(
                    node.name_hash, t_off, value, n_props, valid))
        hashes = [p.name_hash for p in node.prop_table]
        stats['noeuds'] += 1
        stats['noeuds_props_triees'] += int(all(hashes[i] <= hashes[i + 1] for i in range(len(hashes) - 1)))
        chashes = [c.name_hash for c in node.child_table]
        stats['noeuds_enfants_tries'] += int(all(chashes[i] <= chashes[i + 1] for i in range(len(chashes) - 1)))
        for p in node.prop_table:
            if p.type in W.INLINE_TYPES:
                continue
            size = len(W.encode_deferred(p))
            mark(p.data_raw, p.data_raw + size)
            spans_by_offset[p.data_raw].append(p.type)
            if p.type == k_type_str:
                string_offsets[bytes(p.data)].add(p.data_raw)
        for c in node.child_table:
            walk(c)

    walk(tree.root_node)

    last_covered = covered.rfind(b'\x01')
    gap_bytes = collections.Counter()
    for run in re.finditer(b'\x00+', bytes(covered[:last_covered + 1])):
        gap_bytes.update(data[run.start():run.end()])
    trailing = data[last_covered + 1:]
    shared_str = sum(1 for types in spans_by_offset.values() if len(types) > 1 and types[0] == k_type_str)
    shared_other = sum(1 for types in spans_by_offset.values() if len(types) > 1 and types[0] != k_type_str)
    dup_strings = sum(1 for offs in string_offsets.values() if len(offs) > 1)
    return {
        'octets_bourrage': dict(gap_bytes),
        'octets_fin': len(trailing),
        'octets_fin_valeurs': sorted(set(trailing)),
        'chaines_partagees': shared_str,
        'valeurs_partagees_autres': shared_other,
        'chaines_identiques_offsets_distincts': dup_strings,
        'noeuds': stats['noeuds'],
        'noeuds_props_triees': stats['noeuds_props_triees'],
        'noeuds_enfants_tries': stats['noeuds_enfants_tries'],
        'v2_noeuds': stats['v2_noeuds'],
        'v2_compteur_egal_valides': stats['v2_compteur_egal_valides'],
        'v2_compteur_egal_total': stats['v2_compteur_egal_total'],
        'v2_exemples_ecart': trailer_examples,
    }


def first_placement_difference(orig_tree, new_tree):
    """Premiere structure (bloc de noeud ou valeur) placee a un offset different dans la reecriture."""
    def walk(a, b, path):
        if a.data_offset != b.data_offset:
            return '{} : bloc du noeud a 0x{:x} dans l\'original, 0x{:x} dans la reecriture'.format(
                path, a.data_offset, b.data_offset)
        for i, (pa, pb) in enumerate(zip(a.prop_table, b.prop_table)):
            if pa.type not in W.INLINE_TYPES and pa.data_raw != pb.data_raw:
                return '{}/prop[{}] (type {}) : valeur a 0x{:x} dans l\'original, 0x{:x} dans la reecriture'.format(
                    path, i, pa.type, pa.data_raw, pb.data_raw)
        for i, (ca, cb) in enumerate(zip(a.child_table, b.child_table)):
            r = walk(ca, cb, '{}/{}'.format(path, i))
            if r:
                return r
        return None
    return walk(orig_tree.root_node, new_tree.root_node, 'racine')


def roundtrip_one(data, layout=None):
    """
    Renvoie (statut, details, arbre original ou None, octets reecrits ou None).
    ``layout`` None : regles par defaut de la version du fichier (W.layout_for_version).
    """
    try:
        tree = W.rtpc_from_bytes(data)
    except Exception as e:
        return 'lecture_impossible', repr(e), None, None
    try:
        out = W.rtpc_to_binary(tree, layout)
    except Exception as e:
        return 'ecriture_impossible', repr(e), tree, None
    try:
        reread = W.rtpc_from_bytes(out)
    except Exception as e:
        return 'arbre_different', 'reecriture illisible : {!r}'.format(e), tree, out
    diff = W.rtpc_diff(tree, reread)
    if diff:
        return 'arbre_different', diff, tree, out
    if out == data:
        return 'identique', '', tree, out
    first = next((i for i, (x, y) in enumerate(zip(data, out)) if x != y), min(len(data), len(out)))
    where = first_placement_difference(tree, reread) or 'meme placement, octets de bourrage ou de fin differents'
    details = 'taille {} -> {} ; premier octet different a 0x{:x} ; {}'.format(len(data), len(out), first, where)
    return 'octets_differents', details, tree, out


# ---------------------------------------------------------------------------
# Exploration de regles de disposition alternatives
# ---------------------------------------------------------------------------

def candidate_layouts(evidence):
    # 0x50 et 0x00 toujours ; plus l'octet "de bourrage" le plus frequent s'il est different
    pads = sorted(set([0x50, 0x00] + [b for b, _ in evidence.most_common(1)]))
    for trailer, pad, dedup_s, trailing, str_align in itertools.product(
            W.NODE_TRAILER_MODES, pads, (True, False), (False, True), (1, 4)):
        alignments = dict(W.DEFAULT_ALIGNMENTS)
        alignments[k_type_str] = str_align
        yield W.RtpcLayout(pad_byte=pad, dedup_strings=dedup_s, trailing_pad=trailing,
                           alignments=alignments, node_trailer=trailer)


def describe_layout(layout):
    return ('bourrage=0x{:02x} dedup_chaines={} dedup_autres={} bourrage_final={} align_chaines={} '
            'compteur_v2={}').format(
        layout.pad_byte, layout.dedup_strings, layout.dedup_values, layout.trailing_pad,
        layout.alignments.get(k_type_str, 1), layout.node_trailer)


def explore(samples, evidence, max_samples):
    # les plus petits fichiers d'abord : l'exploration reecrit chaque echantillon pour chaque regle
    samples = sorted(samples, key=len)[:max_samples]
    results = []
    for layout in candidate_layouts(evidence):
        ok = 0
        for data in samples:
            try:
                ok += int(W.rtpc_to_binary(W.rtpc_from_bytes(data), layout) == data)
            except Exception:
                pass
        results.append((ok, describe_layout(layout)))
    results.sort(key=lambda r: -r[0])
    return len(samples), results


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Aller-retour lecture/ecriture sur les RTPC du jeu.')
    ap.add_argument('source', help='projet DECA (dossier contenant db\\core.db) ou dossier de fichiers extraits')
    ap.add_argument('--out', required=True, help='dossier de sortie')
    ap.add_argument('--limit', type=int, help='ne traiter que les N premiers fichiers')
    ap.add_argument('--explorer', action='store_true',
                    help='sur les fichiers en ecart, essayer d\'autres regles de disposition')
    ap.add_argument('--echantillon', type=int, default=100,
                    help='nombre maximal de fichiers en ecart utilises par --explorer, les plus petits (defaut 100)')
    args = ap.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if (source / 'db' / 'core.db').is_file():
        gen = iter_project_files(source, args.limit)
        kind = 'projet DECA'
    elif source.is_dir():
        gen = iter_folder_files(source, args.limit)
        kind = 'dossier'
    else:
        raise SystemExit('ERREUR : source introuvable : {}'.format(source))

    total = next(gen)
    print('Source : {} ({}), {} fichiers RTPC'.format(source, kind, total))
    layout = None  # regles choisies d'apres la version de chaque fichier
    rows = []
    status_count = collections.Counter()
    status_by_version = collections.defaultdict(collections.Counter)
    v2_examples = []
    pad_evidence = collections.Counter()
    agg = collections.Counter()
    versions = collections.Counter()
    failing_samples = []
    t0 = time.time()

    for i, (label, data, error) in enumerate(gen, 1):
        if error:
            status, details, tree = 'extraction_impossible', error, None
            size = 0
        else:
            size = len(data)
            status, details, tree, _ = roundtrip_one(data, layout)
            if tree is not None:
                versions[tree.version] += 1
                status_by_version[tree.version][status] += 1
                try:
                    lay = analyze_layout(data, tree)
                except Exception as e:
                    lay = None
                    details = (details + ' ; ' if details else '') + 'analyse impossible : {!r}'.format(e)
                if lay:
                    pad_evidence.update(lay['octets_bourrage'])
                    for k in ('chaines_partagees', 'valeurs_partagees_autres', 'chaines_identiques_offsets_distincts',
                              'noeuds', 'noeuds_props_triees', 'noeuds_enfants_tries',
                              'v2_noeuds', 'v2_compteur_egal_valides', 'v2_compteur_egal_total'):
                        agg[k] += lay[k]
                    if len(v2_examples) < 10:
                        v2_examples.extend('{} : {}'.format(label, e) for e in lay['v2_exemples_ecart'])
                    agg['fichiers_avec_octets_fin'] += int(lay['octets_fin'] > 0)
                    agg['fichiers_chaines_partagees'] += int(lay['chaines_partagees'] > 0)
                    agg['fichiers_valeurs_partagees_autres'] += int(lay['valeurs_partagees_autres'] > 0)
                    agg['fichiers_chaines_dupliquees'] += int(lay['chaines_identiques_offsets_distincts'] > 0)
            if status == 'octets_differents':
                failing_samples.append(data)
        status_count[status] += 1
        rows.append((label, size, status, details, hashlib.sha1(data).hexdigest() if data else ''))
        if i % 250 == 0 or i == total:
            print('  {}/{}  identiques : {}  ({:.0f} s)'.format(i, total, status_count['identique'], time.time() - t0))

    exploration = None
    if args.explorer and failing_samples:
        print('Exploration de regles de disposition sur {} fichiers en ecart...'.format(
            min(len(failing_samples), args.echantillon)))
        exploration = explore(failing_samples, pad_evidence, args.echantillon)

    with open(out_dir / 'rtpc_roundtrip.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['fichier', 'taille', 'statut', 'details', 'sha1_original'])
        w.writerows(rows)

    metrics = {
        'date': datetime.now().isoformat(timespec='seconds'),
        'source': str(source),
        'fichiers': total,
        'statuts': dict(status_count),
        'versions_rtpc': {str(k): v for k, v in versions.items()},
        'octets_de_bourrage_observes': {'0x{:02x}'.format(k): v for k, v in pad_evidence.most_common()},
        'mesures': dict(agg),
        'regles_utilisees': 'v1 : {} | v2 : {}'.format(describe_layout(W.layout_for_version(1)),
                                                      describe_layout(W.layout_for_version(2))),
        'statuts_par_version': {str(v): dict(c) for v, c in status_by_version.items()},
        'v2_exemples_compteur_different': v2_examples,
        'exploration': None if exploration is None else {
            'echantillon': exploration[0],
            'resultats': [{'identiques': ok, 'regles': d} for ok, d in exploration[1]],
        },
    }
    with open(out_dir / 'rtpc_roundtrip.json', 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    write_report(out_dir / 'rtpc_roundtrip.md', metrics, rows)

    print('')
    print('=== Aller-retour RTPC ===')
    for k in ('identique', 'octets_differents', 'arbre_different', 'lecture_impossible',
              'ecriture_impossible', 'extraction_impossible'):
        print('{:<24} {}'.format(k, status_count.get(k, 0)))
    if exploration:
        print('Meilleure regle sur l\'echantillon : {} / {} identiques avec {}'.format(
            exploration[1][0][0], exploration[0], exploration[1][0][1]))
    print('Resultats : {}'.format(out_dir))
    return 1 if status_count.get('arbre_different', 0) or status_count.get('ecriture_impossible', 0) else 0


def write_report(path, m, rows):
    L = ['# Aller-retour RTPC -- theHunter: Call of the Wild', '',
         '- Source : `{}`'.format(m['source']),
         '- Date : {}'.format(m['date']),
         '- Regles d\'ecriture : `{}`'.format(m['regles_utilisees']),
         '- Versions RTPC rencontrees : {}'.format(m['versions_rtpc']),
         '- Statuts par version : {}'.format(m['statuts_par_version']), '',
         '## Resultats', '', '| Statut | Fichiers | Signification |', '|---|---:|---|']
    meaning = {
        'identique': 'reecriture identique a l\'octet pres',
        'octets_differents': 'meme arbre, disposition differente (voir details)',
        'arbre_different': '**bug de l\'ecrivain** : contenu modifie par l\'aller-retour',
        'lecture_impossible': 'le lecteur DECA echoue sur l\'original',
        'ecriture_impossible': '**l\'ecrivain refuse l\'arbre** (type ou valeur non gere)',
        'extraction_impossible': 'DECA n\'a pas pu extraire le fichier du jeu',
    }
    for k, text in meaning.items():
        L.append('| `{}` | {} | {} |'.format(k, m['statuts'].get(k, 0), text))
    L += ['', '## Regles observees dans les originaux', '']
    mes0 = m['mesures']
    if mes0.get('v2_noeuds', 0):
        L.append('- Version 2, u32 apres les en-tetes des enfants : egal au nombre de proprietes non-none pour '
                 '{} / {} noeuds, au nombre total de proprietes pour {} / {}'.format(
                     mes0.get('v2_compteur_egal_valides', 0), mes0['v2_noeuds'],
                     mes0.get('v2_compteur_egal_total', 0), mes0['v2_noeuds']))
        for e in m['v2_exemples_compteur_different'][:5]:
            L.append('  - ecart : {}'.format(e))
    mes = m['mesures']
    n = max(mes.get('noeuds', 0), 1)
    L.append('- Octets de bourrage (hors fin de fichier) : {}'.format(
        ', '.join('{} x{}'.format(k, v) for k, v in list(m['octets_de_bourrage_observes'].items())[:6]) or 'aucun'))
    L.append('- Fichiers avec des octets apres la derniere structure : {}'.format(mes.get('fichiers_avec_octets_fin', 0)))
    L.append('- Fichiers dont plusieurs proprietes partagent une chaine : {}'.format(mes.get('fichiers_chaines_partagees', 0)))
    L.append('- Fichiers dont plusieurs proprietes partagent une autre valeur : {}'.format(
        mes.get('fichiers_valeurs_partagees_autres', 0)))
    L.append('- Fichiers contenant une meme chaine a plusieurs offsets (pas de deduplication) : {}'.format(
        mes.get('fichiers_chaines_dupliquees', 0)))
    L.append('- Noeuds aux proprietes triees par hash croissant : {:.1f} %'.format(
        100.0 * mes.get('noeuds_props_triees', 0) / n))
    L.append('- Noeuds aux enfants tries par hash croissant : {:.1f} %'.format(
        100.0 * mes.get('noeuds_enfants_tries', 0) / n))
    if m['exploration']:
        L += ['', '## Exploration de regles ({} fichiers en ecart)'.format(m['exploration']['echantillon']), '',
              '| Identiques | Regles |', '|---:|---|']
        for r in m['exploration']['resultats'][:10]:
            L.append('| {} | `{}` |'.format(r['identiques'], r['regles']))
    problems = [r for r in rows if r[2] != 'identique'][:40]
    if problems:
        L += ['', '## Premiers ecarts (liste complete : `rtpc_roundtrip.csv`)', '',
              '| Fichier | Statut | Details |', '|---|---|---|']
        for label, size, status, details, _ in problems:
            L.append('| `{}` | {} | {} |'.format(label, status, details.replace('|', '/')))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
