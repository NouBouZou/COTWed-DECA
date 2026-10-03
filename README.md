# COTWed DECA

Outils pour lire, analyser et écrire les fichiers de **theHunter: Call of the Wild** (moteur APEX d'Avalanche Studios).
Le but est de préparer un **modloader** qui ajoute du contenu (munitions, armes…) au jeu **sans écraser** les fichiers d'origine.

Projet personnel, privé pour l'instant.

## Origine

Ce projet part de [DECA](https://github.com/kk49/deca), de Krzysztof Kamieniecki (kk49), sous licence MIT : branche `develop`, commit `9231177` (2025-12-10).
DECA n'est plus maintenu. Ce dépôt n'en suit plus les évolutions et se concentre sur COTW. Le README d'origine est conservé dans [`docs/README_DECA.md`](docs/README_DECA.md).

## État au 2026-10-03

**Une munition ajoutée fonctionne en jeu.** Elle est visible au magasin, s'achète, affiche ses propres stats (`--stats-de` et `--stat`) et le logo de possession, se charge, se tire et consomme ses propres munitions. Pour cela, quatre fichiers sont déposés dans `dropzone`, tous écrits sans rien écraser :

| Fichier | Format | Rôle |
|---|---|---|
| `settings/hp_settings/equipment_data.bin` | RTPC | l'objet (prix, compatibilités, DLC, `locked`…) |
| `settings/spawn_ammunition_defs.bin` | RTPC | lien nom → entité |
| `editor/entities/…/<nom>.ee` | SARC | entité propre (`.epe` avec `item_id`, `tuning_file`, `_object_id` nouveaux) |
| `settings/hp_settings/equipment_stats_ui.bin` | ADF (XLSBook) | stats affichées et logo de possession |

**Nom et description propres** (`--nom-affiche`, `--description` ; nom vérifié en jeu, test13) : un cinquième fichier, `settings/hp_settings/string_ids.stringlookup` (ADF StringLookup, table de traduction des 10 langues), reçoit les clés `<nom>_name` et `<nom>_description`, que désignent `display_name` et `display_description` de la nouvelle munition. Le fichier passe de 38,8 à 49,4 Mo (les anciennes copies des tableaux triés restent en place).

Encore empruntés à la munition source : l'icône et le `.ammotunec` (balistique réelle ; les stats affichées viennent de la ligne de stats).

## Contenu

### Modifications du cœur DECA (`python/deca`)
- **Scan** :
  - un nom RTPC contenant un antislash n'interrompt plus le scan ;
  - un processus de travail qui meurt sans prévenir (tué, mémoire insuffisante) fait échouer le scan, au lieu de le bloquer indéfiniment ;
  - le gestionnaire du scan n'occupe plus un cœur à 100 % pendant tout le scan (attente bloquante au lieu d'une attente active).
- **Écriture RTPC** (`deca/ff_rtpc_write.py`) : versions 1 et 2, identique à l'octet près sur les 6 348 RTPC du jeu.
- **Vues** (`db_view.py`, `db_types.py`) : le filtre est appliqué par SQLite. Les chemins non ASCII sont acceptés. La table `core_nodes` a un index sur `parent_id`, créé à la première ouverture d'un projet existant.

### Interface (`python/deca_gui`, en anglais)
- **Ouverture et scan en arrière-plan.** La fenêtre reste utilisable. Le journal s'affiche en direct dans un panneau **Log**, et la barre d'état indique la durée écoulée.
  - **File → New Project** sur un jeu qui a déjà un projet propose de l'ouvrir, ou de le supprimer et de rescanner.
  - Un projet dont le scan a été interrompu est signalé : l'ouvrir reprend le scan.
  - Si on ferme la fenêtre pendant un scan, ses processus sont arrêtés.
- **Filtre et affichage**, mesurés sur 900 000 nœuds :
  - filtre : 4 à 6 s avant, 0,4 à 1 s maintenant ;
  - construction des vues : 5,5 s avant, 2,3 s maintenant ;
  - repeinture : plus aucune requête SQL par cellule.
- **Onglet Info** rempli : chemins, hash, type, magic, flags décodés, archives parentes, nombre d'enfants, autres copies du même fichier.
- **Tri** des listes Raw et Non-Mapped par colonne.
- **Projet de démonstration** (`cotw/demo_projet.py`, `cotw/lancer_demo.bat`) : un faux COTW scanné par DECA, pour travailler sur l'interface sans le jeu.

### Outils COTW (`cotw/`)
| Outil | Rôle |
|---|---|
| `cloner_munition.py` | ajoute une munition copiée d'une autre (equipment_data, spawn defs, entité propre, ligne de stats, nom et description, témoin de prix) |
| `entite.py`, `sarc.py` | copie d'entité `.ee` ; lecture et écriture SARC v2/v3 |
| `adf_ecriture.py`, `xlsbook.py` | agrandit des tableaux d'un ADF sans déplacer les données existantes ; ajoute une ligne à une feuille XLSBook |
| `adf_ajouter_ligne.py` | ajoute une ligne à `equipment_stats_ui.bin` (ou une autre feuille XLSBook), copiée de celle d'un objet existant |
| `stringlookup.py`, `traduction.py` | lit les textes affichés (noms, descriptions) dans les 10 langues ; ajoute des clés de traduction sans rien écraser |
| `adf_remplacer_chaine.py` | remplace une chaîne d'un ADF sur place |
| `rtpc_dump.py`, `adf_dump.py`, `sarc_dump.py` | affichage lisible des formats, noms résolus |
| `comparer_objets.py`, `separateurs.py`, `liste_objets.py` | comparaison d'objets d'`equipment_data` |
| `indexer_refs.py`, `differentiel.py`, `deca_refs.py`, `citations.py` | qui cite quoi, dans tout le jeu |
| `resoudre_hashes.py` | retrouve les noms en clair des hashes (dictionnaire `cotw/dictionnaires/hp_noms.tsv`) |
| `rtpc_roundtrip.py`, `sarc_roundtrip.py`, `adf_ecriture_controle.py` | validation des écrivains sur les fichiers réels |
| `extraire.py` | copie octet pour octet de fichiers du jeu (par chemin ou motif), pour étude |
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
- **Ouvrir un projet au lancement** : `python python\deca_gui\deca_gui\entry_point.py <project.json ou son dossier>`.
- **Sans le jeu** (portable) : `cotw\lancer_demo.bat` crée au besoin le projet de démonstration dans `..\work_demo` et l'ouvre. Le vrai projet `..\work\hp` n'est jamais touché.
- **Outils** : `python cotw\<outil>.py --help`, DECA fermé.
- **Tests** : `python cotw\tests\test_<nom>.py`.
- **Charger les fichiers modifiés en jeu** : option de lancement Steam
  `--vfs-fs dropzone --vfs-archive archives_win64 --vfs-archive patch_win64 --vfs-archive dlc_win64 --vfs-fs .`

## Licence

MIT, voir [`LICENSE`](LICENSE). Le code de DECA reste sous le copyright de Krzysztof Kamieniecki.
