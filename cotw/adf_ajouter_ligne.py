#!/usr/bin/env python3
"""
adf_ajouter_ligne.py -- ajoute a une feuille XLSBook (ex : equipment_stats_ui.bin) une ligne copiee de celle d'un
objet existant, sous un nouveau nom. Rien n'est ecrase : les lignes existantes restent intactes.

Pourquoi : la ligne d'equipment_stats_ui d'un objet donne ses stats affichees au magasin ET le logo de possession
(constat en jeu, test8). Jusqu'ici un objet ajoute ne l'obtenait qu'en detournant la ligne d'un autre objet
(adf_remplacer_chaine.py). Methode : voir xlsbook.py (ajout de ligne) et adf_ecriture.py (agrandissement des
tableaux d'un ADF sans deplacer les donnees existantes).

Controle avant ecriture : DECA relit le fichier produit ; la lecture doit etre exactement l'originale plus les
lignes ajoutees, chaque ligne ajoutee doit montrer les valeurs de son modele, au nom pres.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\adf_ajouter_ligne.py D:\\dev\\work\\hp settings/hp_settings/equipment_stats_ui.bin ^
        --ajouter equipment_ammo_243_sp_01=equipment_ammo_243_test_03 --sortie D:\\dev\\mods\\test9
"""
import argparse
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402
import adf_ecriture as AE  # noqa: E402
import xlsbook as X  # noqa: E402

STATS_UI = 'settings/hp_settings/equipment_stats_ui.bin'


def decoder(reader) -> Callable[[bytes], object]:
    """Octets d'un fichier ADF -> Adf lu par DECA, avec les types du projet (ceux de l'exe)."""
    def decode(data: bytes):
        return reader.adf_db._load_adf(data[AE.adf_base(data):])
    return decode


def add_rows(decode: Callable[[bytes], object], data: bytes, pairs: List[Tuple[str, str]],
             sheet: Optional[str] = None) -> Tuple[bytes, List[str]]:
    """Ajoute une ligne par couple (modele, nouveau nom), l'une apres l'autre, chacune relue par DECA."""
    report = []
    for template, new_name in pairs:
        adf = decode(data)
        if adf is None:
            raise X.EXls('ADF illisible par DECA (types manquants ?)')
        new_data, plan = X.add_row(adf, data, template, new_name, sheet)
        X.verify(decode, data, new_data, plan)
        report.append('  "{}" -> nouvelle ligne "{}" : feuille "{}", ligne {} copiee en ligne {} ; chaine n.{}, '
                      'cellule n.{} ; second champ des chaines : {} ; {} -> {} octets'.format(
                          template, new_name, plan.match.sheet_name, plan.match.row, plan.new_row,
                          plan.string_index, plan.cell_index, plan.second_rule, len(data), len(new_data)))
        data = new_data
    return data, report


def parse_pairs(specs: List[str]) -> List[Tuple[str, str]]:
    pairs = []
    for spec in specs:
        old, sep, new = spec.partition('=')
        if not sep or not old.strip() or not new.strip():
            raise SystemExit('ERREUR : attendu MODELE=NOUVEAU, recu "{}"'.format(spec))
        pairs.append((old.strip(), new.strip()))
    return pairs


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Ajoute a une feuille XLSBook une ligne copiee d\'un objet existant.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('fichier', nargs='?', default=STATS_UI, help='chemin virtuel (defaut : {})'.format(STATS_UI))
    ap.add_argument('--ajouter', action='append', required=True, metavar='MODELE=NOUVEAU',
                    help='ligne a copier et nom de la copie (option repetable)')
    ap.add_argument('--feuille', help='nom de la feuille, si le modele figure dans plusieurs feuilles')
    ap.add_argument('--sortie', required=True, help='dossier de sortie ; le fichier est ecrit dans SORTIE\\dropzone')
    ap.add_argument('--base', help='dossier au format dropzone dont partir, s\'il contient deja ce fichier')
    ap.add_argument('--ecraser', action='store_true', help='remplacer le fichier s\'il existe deja dans la sortie')
    args = ap.parse_args(argv)

    import cloner_munition as C
    import resoudre_hashes as RH
    pairs = parse_pairs(args.ajouter)
    target = Path(args.sortie).expanduser().resolve() / 'dropzone' / Path(args.fichier)
    if target.exists() and not args.ecraser:
        raise SystemExit('ERREUR : {} existe deja (option --ecraser pour le remplacer)'.format(target))
    vfs = _projet.open_project(args.projet)
    base = Path(args.base).expanduser().resolve() if args.base else None
    data, src = C.load(vfs, base, args.fichier)
    try:
        out, lines = add_rows(decoder(RH.FileReader(vfs)), data, pairs, args.feuille)
    except (X.EXls, AE.EAdfEcriture) as e:
        raise SystemExit('ERREUR : {}'.format(e))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(out)
    print('\n'.join(['Origine : {}'.format(src)] + lines + [
        'Verification : relu par DECA : l\'original plus {} ligne(s), rien d\'autre.'.format(len(pairs)),
        'Ecrit : {}'.format(target)]))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
