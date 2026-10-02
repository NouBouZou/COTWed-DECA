# COTWed DECA

Outils pour lire, analyser et écrire les fichiers de **theHunter: Call of the Wild** (moteur APEX d'Avalanche Studios).
Le but est de préparer un **modloader** qui ajoute du contenu (munitions, armes…) au jeu **sans écraser** les fichiers d'origine.

Projet personnel, privé pour l'instant.

## Origine

Ce projet part de [DECA](https://github.com/kk49/deca), de Krzysztof Kamieniecki (kk49), sous licence MIT : branche `develop`, commit `9231177` (2025-12-10).
DECA n'est plus maintenu. Ce dépôt n'en suit plus les évolutions et se concentre sur COTW. Le README d'origine est conservé dans [`docs/README_DECA.md`](docs/README_DECA.md).

## État au 2026-10-01

**Une munition ajoutée fonctionne en jeu.** Elle est visible au magasin, s'achète, affiche ses stats et le logo de possession, se charge, se tire et consomme ses propres munitions. Pour cela, quatre fichiers sont déposés dans `dropzone` :

| Fichier | Format | Rôle |
|---|---|---|
| `settings/hp_settings/equipment_data.bin` | RTPC | l'objet (prix, compatibilités, DLC, `locked`…) |
| `settings/spawn_ammunition_defs.bin` | RTPC | lien nom → entité |
| `editor/entities/…/<nom>.ee` | SARC | entité propre (`.epe` avec `item_id`, `tuning_file`, `_object_id` nouveaux) |
| `settings/hp_settings/equipment_stats_ui.bin` | ADF (XLSBook) | stats affichées et logo de possession |

Encore empruntés à la munition source : le nom affiché, l'icône et le `.ammotunec` (balistique). La ligne de stats, jusqu'ici réaffectée depuis un autre objet, peut désormais être **ajoutée** (`--stats-de`) ; reste à le confirmer en jeu (essai test9).

## Contenu

### Modifications du cœur DECA (`python/deca`)
- **Scan** : un nom RTPC contenant un antislash n'interrompt plus le scan.
- **Écriture RTPC** (`deca/ff_rtpc_write.py`) : versions 1 et 2, identique à l'octet près sur les 6 348 RTPC du jeu.

### Outils COTW (`cotw/`)
| Outil | Rôle |
|---|---|
| `cloner_munition.py` | ajoute une munition copiée d'une autre (equipment_data, spawn defs, entité propre, ligne de stats, témoin de prix) |
| `entite.py`, `sarc.py` | copie d'entité `.ee` ; lecture et écriture SARC v2/v3 |
| `adf_ecriture.py`, `xlsbook.py` | agrandit des tableaux d'un ADF sans déplacer les données existantes ; ajoute une ligne à une feuille XLSBook |
| `adf_ajouter_ligne.py` | ajoute une ligne à `equipment_stats_ui.bin` (ou une autre feuille XLSBook), copiée de celle d'un objet existant |
| `adf_remplacer_chaine.py` | remplace une chaîne d'un ADF sur place |
| `rtpc_dump.py`, `adf_dump.py`, `sarc_dump.py` | affichage lisible des formats, noms résolus |
| `comparer_objets.py`, `separateurs.py`, `liste_objets.py` | comparaison d'objets d'`equipment_data` |
| `indexer_refs.py`, `differentiel.py`, `deca_refs.py`, `citations.py` | qui cite quoi, dans tout le jeu |
| `resoudre_hashes.py` | retrouve les noms en clair des hashes (dictionnaire `cotw/dictionnaires/hp_noms.tsv`) |
| `rtpc_roundtrip.py`, `sarc_roundtrip.py`, `adf_ecriture_controle.py` | validation des écrivains sur les fichiers réels |
| `deca_audit.py` | audit en lecture seule d'un projet DECA |

## Utilisation (Windows, Invite de commandes)

Prérequis : Python **3.12.10** 64 bits (numba 0.60.0 n'a pas de paquet Windows pour 3.13+), Git.

```
py -3.12 -m venv venv
venv\Scripts\activate.bat
pip install -r python\requirements.txt
pip install -e python\deca -e python\deca_gui
```

- **Scan du jeu** : `cotw\lancer_deca.bat`, puis **File → New Project…** et `theHunterCotW_F.exe`. Le projet est créé dans `..\work\hp`.
- **Outils** : `python cotw\<outil>.py --help`, DECA fermé.
- **Tests** : `python cotw\tests\test_<nom>.py`.
- **Charger les fichiers modifiés en jeu** : option de lancement Steam
  `--vfs-fs dropzone --vfs-archive archives_win64 --vfs-archive patch_win64 --vfs-archive dlc_win64 --vfs-fs .`

## Licence

MIT, voir [`LICENSE`](LICENSE). Le code de DECA reste sous le copyright de Krzysztof Kamieniecki.
