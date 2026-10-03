#!/usr/bin/env python3
"""
traduction.py -- lit les textes affiches par le jeu (noms, descriptions), ou en ajoute de nouveaux, sans rien
ecraser, dans settings/hp_settings/string_ids.stringlookup (format : voir stringlookup.py).

  lire     : texte d'une ou plusieurs cles dans les 10 langues (en, fr, de, es, ru, pl, ja, pt, cs, zh) ;
  ajouter  : cles nouvelles, un texte pour toutes les langues ("CLE=TEXTE"), et au besoin un texte par langue
             ("CLE:en=TEXT") ; le fichier est ecrit dans SORTIE\\dropzone, puis relu et controle.

Une cle est la valeur de display_name ou display_description d'un objet d'equipment_data.bin, par exemple
equipment_ammo_243_sp_name. cloner_munition.py --nom-affiche / --description s'en sert directement.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\traduction.py lire D:\\dev\\work\\hp equipment_ammo_243_sp_name equipment_ammo_243_sp_description
    python cotw\\traduction.py ajouter D:\\dev\\work\\hp --ajouter "mon_objet_name=MON OBJET" ^
        --texte "mon_objet_name:en=MY ITEM" --modele equipment_ammo_243_sp_name --sortie D:\\dev\\mods\\essai
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import stringlookup as SL  # noqa: E402


def load(projet: str, base: str, fichier: str):
    import cloner_munition as C
    vfs = _projet.open_project(projet)
    return C.load(vfs, _projet.base_dir(base), fichier)


def entries_from_args(adds: List[str], texts: List[str], model: str) -> List[SL.Entree]:
    """--ajouter CLE=TEXTE (toutes les langues), --texte CLE:langue=TEXTE (une langue)."""
    per: Dict[str, List[str]] = {}
    order = []
    for spec in adds:
        key, sep, text = spec.partition('=')
        if not sep or not key.strip():
            raise SystemExit('ERREUR : attendu CLE=TEXTE, recu "{}"'.format(spec))
        key = key.strip()
        if key in per:
            raise SystemExit('ERREUR : cle "{}" donnee deux fois'.format(key))
        per[key] = [text]
        order.append(key)
    for spec in texts:
        key, sep, rest = spec.partition(':')
        if not sep or key.strip() not in per:
            raise SystemExit('ERREUR : --texte attend CLE:langue=TEXTE pour une cle de --ajouter, recu "{}"'.format(spec))
        code, sep, _text = rest.partition('=')
        try:
            SL.language_index(code)
        except SL.EStringLookup as e:
            raise SystemExit('ERREUR : --texte "{}" : {}'.format(spec, e))
        if not sep:
            raise SystemExit('ERREUR : --texte attend CLE:langue=TEXTE, recu "{}"'.format(spec))
        per[key.strip()].append(rest)
    try:
        return [SL.Entree(k, SL.textes_par_langue(per[k]), model) for k in order]
    except SL.EStringLookup as e:
        raise SystemExit('ERREUR : {}'.format(e))


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Textes affiches par le jeu (string_ids.stringlookup).')
    sub = ap.add_subparsers(dest='commande', required=True)
    lire = sub.add_parser('lire', help='texte de cles dans toutes les langues')
    lire.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    lire.add_argument('cles', nargs='+', help='cles, ex : equipment_ammo_243_sp_name')
    lire.add_argument('--fichier', default=SL.STRING_IDS, help='chemin virtuel (defaut : {})'.format(SL.STRING_IDS))
    lire.add_argument('--base', help='dossier au format dropzone dont lire le fichier s\'il y est')
    add = sub.add_parser('ajouter', help='ajoute des cles nouvelles')
    add.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    add.add_argument('--ajouter', action='append', required=True, metavar='CLE=TEXTE',
                     help='cle nouvelle et son texte pour toutes les langues (option repetable)')
    add.add_argument('--texte', action='append', default=[], metavar='CLE:LANGUE=TEXTE',
                     help='texte propre a une langue (en, fr, de, es, ru, pl, ja, pt, cs, zh) ; option repetable')
    add.add_argument('--modele', help='cle existante dont la ligne de dialogue est copiee (defaut : la premiere)')
    add.add_argument('--fichier', default=SL.STRING_IDS, help='chemin virtuel (defaut : {})'.format(SL.STRING_IDS))
    add.add_argument('--sortie', required=True, help='dossier de sortie ; le fichier est ecrit dans SORTIE\\dropzone')
    add.add_argument('--base', help='dossier au format dropzone dont partir, s\'il contient deja ce fichier')
    add.add_argument('--ecraser', action='store_true', help='remplacer le fichier s\'il existe deja dans la sortie')
    args = ap.parse_args(argv)

    if args.commande == 'lire':
        data, src = load(args.projet, args.base, args.fichier)
        vue = SL.Vue(data)
        print('Origine : {} ({} langues)'.format(src, vue.n_lang))
        for key in args.cles:
            print('{} (hash 0x{:08x}) :'.format(key, SL.key_hash(key)))
            for code, text in zip(SL.LANGUES, vue.texts(key)):
                print('  {} : {}'.format(code, text if text is not None else '(absente)'))
        return 0

    entries = entries_from_args(args.ajouter, args.texte, args.modele)
    target = Path(args.sortie).expanduser().resolve() / 'dropzone' / Path(args.fichier)
    if target.exists() and not args.ecraser:
        raise SystemExit('ERREUR : {} existe deja (option --ecraser pour le remplacer)'.format(target))
    data, src = load(args.projet, args.base, args.fichier)
    try:
        out, plan = SL.add(SL.decode, data, entries)
        SL.verify(SL.decode, data, out, plan)
    except SL.EStringLookup as e:
        raise SystemExit('ERREUR : {}'.format(e))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(out)
    lines = ['Origine : {}'.format(src)]
    for e, h in zip(plan.entrees, plan.hashes):
        lines.append('  {} (hash 0x{:08x}) : {}'.format(e.cle, h, ' | '.join(
            '{}={}'.format(c, t) for c, t in zip(SL.LANGUES, e.textes))))
    lines += ['Verification : relu ; paires triees, textes d\'origine intacts, chaine de relocation {}.'.format(plan.chain),
              'Ecrit : {} ({} -> {} octets)'.format(target, len(data), len(out))]
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
