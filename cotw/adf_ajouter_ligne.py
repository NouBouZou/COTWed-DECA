#!/usr/bin/env python3
"""
adf_ajouter_ligne.py -- ajoute a une feuille XLSBook (ex : equipment_stats_ui.bin) une ligne copiee de celle d'un
objet existant, sous un nouveau nom. Rien n'est ecrase : les lignes existantes restent intactes.

Pourquoi : la ligne d'equipment_stats_ui d'un objet donne ses stats affichees au magasin ET le logo de possession
(constat en jeu, test8). Jusqu'ici un objet ajoute ne l'obtenait qu'en detournant la ligne d'un autre objet
(adf_remplacer_chaine.py). Methode : voir xlsbook.py (ajout de ligne) et adf_ecriture.py (agrandissement des
tableaux d'un ADF sans deplacer les donnees existantes).

Controle avant ecriture : DECA relit le fichier produit ; la lecture doit etre exactement l'originale plus les
lignes ajoutees, chaque ligne ajoutee doit montrer les valeurs de son modele, au nom pres ; la chaine de
relocation doit couvrir tous les champs pointeurs.

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\adf_ajouter_ligne.py D:\\dev\\work\\hp settings/hp_settings/equipment_stats_ui.bin ^
        --ajouter equipment_ammo_243_sp_01=equipment_ammo_243_test_03 --sortie D:\\dev\\mods\\test9
    ... --valeur "Expansion=20" --valeur "Penetration=17" --valeur "Class=3;4;5;6"
"""
import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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
             sheet: Optional[str] = None, values: Optional[Dict[str, str]] = None,
             own_cells: bool = False) -> Tuple[bytes, List[str]]:
    """Ajoute une ligne par couple (modele, nouveau nom), l'une apres l'autre, chacune relue par DECA. values et
    own_cells s'appliquent a chaque ligne ajoutee (voir xlsbook.add_row)."""
    report = []
    for template, new_name in pairs:
        adf = decode(data)
        if adf is None:
            raise X.EXls('ADF illisible par DECA (types manquants ?)')
        new_data, plan = X.add_row(adf, data, template, new_name, sheet, values=values, own_cells=own_cells)
        X.verify(decode, data, new_data, plan)
        report.append('  "{}" -> nouvelle ligne "{}" : feuille "{}", ligne {} copiee en ligne {} ; chaine n.{}, '
                      'cellule n.{} ; chaine de relocation : {} ; {} -> {} octets'.format(
                          template, new_name, plan.match.sheet_name, plan.match.row, plan.new_row,
                          plan.string_index, plan.cell_index, plan.chain, len(data), len(new_data)))
        if plan.changed:
            report.append('    valeurs donnees : ' + ' ; '.join(plan.changed))
        report.append('    ligne ajoutee : {}'.format(plan.expected_row))
        data = new_data
    return data, report


def parse_values(specs: List[str]) -> Dict[str, str]:
    """COLONNE=VALEUR (option repetable) -> {colonne: valeur} ; la colonne s'arrete au premier '='."""
    out = {}
    for spec in specs:
        col, sep, val = spec.partition('=')
        if not sep or not col.strip():
            raise SystemExit('ERREUR : attendu COLONNE=VALEUR, recu "{}"'.format(spec))
        out[col.strip()] = val.strip()
    return out


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
    ap.add_argument('--valeur', action='append', default=[], metavar='COLONNE=VALEUR',
                    help='valeur d\'une colonne de la nouvelle ligne (option repetable) ; COLONNE : numero, en-tete, ou '
                         'morceau unique d\'en-tete (ex : "Penetration=17", "Class=3;4;5;6")')
    ap.add_argument('--cellules-propres', action='store_true',
                    help='donner a chaque case de la nouvelle ligne sa propre cellule, meme sans valeur changee')
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
        out, lines = add_rows(decoder(RH.FileReader(vfs)), data, pairs, args.feuille,
                              parse_values(args.valeur), args.cellules_propres)
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
