# COTWed DECA

Outils pour lire, analyser et écrire les fichiers de **theHunter: Call of the Wild** (moteur APEX d'Avalanche Studios).
Le but est de préparer un **modloader** qui ajoute du contenu (munitions, armes…) au jeu **sans écraser** les fichiers d'origine.

Projet personnel, privé pour l'instant.

## Origine

Ce projet part de [DECA](https://github.com/kk49/deca), de Krzysztof Kamieniecki (kk49), sous licence MIT : branche `develop`, commit `9231177` (2025-12-10).
DECA n'est plus maintenu. Ce dépôt n'en suit plus les évolutions et se concentre sur COTW. Le README d'origine est conservé dans [`docs/README_DECA.md`](docs/README_DECA.md).

## État au 2026-10-06

**Une munition ajoutée fonctionne en jeu.** Elle est visible au magasin, s'achète, affiche ses propres stats (`--stats-de` et `--stat`), son nom et sa description, ainsi que le logo de possession. Elle se charge, se tire et consomme ses propres munitions. Plusieurs copies d'une même munition coexistent avec elle sans bloquer le changement de munition (test14). Pour cela, quatre fichiers sont déposés dans `dropzone`, tous écrits sans rien écraser :

| Fichier | Format | Rôle |
|---|---|---|
| `settings/hp_settings/equipment_data.bin` | RTPC | l'objet (prix, compatibilités, DLC, `locked`…) |
| `settings/spawn_ammunition_defs.bin` | RTPC | lien nom → entité |
| `editor/entities/…/<nom>.ee` | SARC | entité propre (`.epe` avec `item_id`, `tuning_file`, `_object_id` nouveaux ; instance du `.ammotunec` renommée) |
| `settings/hp_settings/equipment_stats_ui.bin` | ADF (XLSBook) | stats affichées et logo de possession |

**Nom et description propres** (`--nom-affiche`, `--description`, vérifiés en jeu) : un cinquième fichier, `settings/hp_settings/string_ids.stringlookup` (ADF StringLookup, table de traduction des 10 langues), reçoit les clés `<nom>_name` et `<nom>_description`, que désignent `display_name` et `display_description` de la nouvelle munition. Le fichier passe de 38,8 à 49,4 Mo (les anciennes copies des tableaux triés restent en place).

**Balistique propre** (`--balistique CHAMP=VALEUR`, `--classes`, ou `"ballistics"` / `"classes"` dans un fichier de mod ; à vérifier en jeu, test15) : les valeurs du `.ammotunec` de l'entité propre sont modifiées (pénétration, expansion, masse, traçante, classes d'animaux…), sans toucher la source. `python cotw\ammotune.py PROJET OBJET` liste les champs et leurs valeurs. L'Ammo Workshop ne les édite pas encore ; il les garde quand il enregistre un mod. Les stats affichées viennent de la ligne de stats : les mettre en accord.

Encore emprunté à la munition source : l'icône.

**Tout cela se fait aussi dans l'interface** (**Tools → Ammo Workshop…**, depuis le 2026-10-04). On y édite un **fichier de mod** (`.cotwmod.json`) qui liste les munitions à ajouter. **Build** reconstruit le dropzone du mod depuis les originaux du jeu, en une passe, avec exactement les mêmes octets que la ligne de commande. Exemple : [`cotw/mods/test14.cotwmod.json`](cotw/mods/test14.cotwmod.json).

## Lecture des fichiers (2026-10-05)

L'autre moitié du projet : **lire tout ce que le jeu contient**, là où DECA s'arrête.

- **Le scan ne s'arrête plus sur un fichier illisible** : le fichier est marqué `PROCESSING FAILED` (onglet Info), une ligne du journal en donne la cause, le reste du jeu est exploré.
- **Les noms trouvés par le fork sont connus dès le scan** : `cotw/dictionnaires/hp_noms.tsv` (`cash_price`, `compatibility_N`, `secondary_ids`…) est chargé avec les dictionnaires de DECA.
- **Visionneuses** de l'interface pour les formats que le fork a documentés : RTPC et ADF en arbres (noms résolus, notes tirées du journal de recherche), tables de traduction (**Strings**), feuilles XLSBook (**Sheet**), archives SARC (**SARC**), sauvegardes (**File → Open Save…**).
- **Mesure de ce qui reste à lire** : `deca_audit.py` donne, par extension, la part des fichiers qu'une visionneuse sait afficher, et prépare l'extraction d'échantillons des formats encore illisibles, à étudier ensuite.

## Contenu

### Modifications du cœur DECA (`python/deca`)
- **Scan** :
  - un nom RTPC contenant un antislash n'interrompt plus le scan ;
  - un processus de travail qui meurt sans prévenir (tué, mémoire insuffisante) fait échouer le scan, au lieu de le bloquer indéfiniment ;
  - le gestionnaire du scan n'occupe plus un cœur à 100 % pendant tout le scan (attente bloquante au lieu d'une attente active).
  - un fichier dont le traitement lève une exception n'arrête plus le scan : ses écritures en base sont annulées, il reçoit le drapeau `node_flag_processing_failed` (affiché dans l'onglet Info, jamais retenté), et le journal donne une ligne `loop_over_uid_wrapper: failed for` avec la cause et ses archives parentes ;
  - les dictionnaires de noms (`ghidra_strings`, `field_strings`) sont trouvés quel que soit le dossier courant, et `cotw/dictionnaires/<jeu>_noms.tsv` est chargé au scan ;
  - les entrées de taille 0 des `.tab` sont gardées (liens), au lieu d'être perdues.
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
- **Visionneuses** (panneau de droite, double-clic sur un fichier) ; les onglets lourds ne sont calculés qu'à l'affichage, et l'erreur d'une visionneuse s'affiche dans **Info** :
  - **RTPC** et **ADF** : arbres chargés au fur et à mesure (les grands tableaux sont découpés en tranches de 256), noms résolus par lots (`cotw/noms.py`) ou tirés des chaînes du fichier, hashes annotés, notes du journal (`secondary_ids` : jeu de base ou DLC ; `locked` 5 : caché du magasin), recherche, **Export text…**, et la vue texte d'origine de DECA à la demande (**DECA text dump**) ;
  - **Strings** (`*.stringlookup`) : clé, hash et texte dans les 10 langues, filtre ; lu directement sur les octets, sans l'arbre ADF générique (17 s pour `string_ids`, 38,8 Mo) ;
  - **Sheet** (XLSBook, `equipment_stats_ui.bin`) : une grille par feuille, cases vides en blanc, lignes après la première ligne vide grisées (le jeu n'y lit plus les stats, test12) ;
  - **SARC** : table des entrées (offset, taille, type détecté, lien) ; double-clic pour ouvrir une entrée.
- **Sauvegardes** (**File → Open Save…**, `deca_gui/save_viewer.py`) : objets possédés et quantités, sac, objets débloqués, emplacements d'inventaire, loadouts, données d'armes, et l'arbre ADF complet. Les objets sont nommés avec le projet ouvert, dans la langue choisie, et avec les mods ajoutés (**Add mod file…**) ; un hash que rien ne nomme est signalé.
- **Projet de démonstration** (`cotw/demo_projet.py`, `cotw/lancer_demo.bat`) : un faux COTW scanné par DECA, pour travailler sur l'interface sans le jeu. Il contient tout ce que demande l'atelier : entités avec `.ammotunec`, `string_ids` en 10 langues, feuille de stats terminée par deux lignes vides.
- **Ammo Workshop** (**Tools → Ammo Workshop…**, `deca_gui/ammo_workshop.py`) :
  - à gauche, les munitions du jeu (nom affiché dans la langue choisie, prix, disponibilité, ligne de stats, armes) ;
  - au centre, le mod : munitions ajoutées, prix témoins, changements d'objets existants ;
  - à droite, la munition sélectionnée : nom, source, prix, entité, armes compatibles, ligne de stats (valeurs copiées et nouvelles), nom et description par langue ;
  - les problèmes connus avant construction sont signalés en rouge (nom déjà pris, instance du `.ammotunec` identique à celle de la source…) ;
  - **Build** tourne en arrière-plan et affiche le rapport ; **Copy command line** donne la commande `construire_mod` équivalente.

### Outils COTW (`cotw/`)
| Outil | Rôle |
|---|---|
| `cloner_munition.py` | ajoute une munition copiée d'une autre (equipment_data, spawn defs, entité propre, ligne de stats, nom et description, témoin de prix) ; sa fonction `construire` sert aussi à `construire_mod` et à l'interface |
| `construire_mod.py` | reconstruit le dropzone d'un fichier de mod (`.cotwmod.json`, plusieurs munitions) depuis les originaux, en une passe ; manifeste des fichiers écrits ; `--comparer` avec un dropzone déployé |
| `catalogue.py` | munitions et armes du jeu telles que l'atelier les montre (noms affichés, disponibilité, ligne de stats, entité) |
| `entite.py`, `sarc.py` | copie d'entité `.ee` ; lecture et écriture SARC v2/v3 |
| `adf_ecriture.py`, `xlsbook.py` | agrandit des tableaux d'un ADF sans déplacer les données existantes ; ajoute une ligne à une feuille XLSBook |
| `adf_ajouter_ligne.py` | ajoute une ligne à `equipment_stats_ui.bin` (ou une autre feuille XLSBook), copiée de celle d'un objet existant |
| `stringlookup.py`, `traduction.py` | lit les textes affichés (noms, descriptions) dans les 10 langues ; ajoute des clés de traduction sans rien écraser |
| `ammotune.py` | lit et modifie le réglage balistique (`.ammotunec`) d'une munition, dans son entité |
| `adf_remplacer_chaine.py` | remplace une chaîne d'un ADF sur place |
| `rtpc_dump.py`, `adf_dump.py`, `sarc_dump.py` | affichage lisible des formats, noms résolus |
| `comparer_objets.py`, `separateurs.py`, `liste_objets.py` | comparaison d'objets d'`equipment_data` |
| `indexer_refs.py`, `differentiel.py`, `deca_refs.py`, `citations.py` | qui cite quoi, dans tout le jeu |
| `resoudre_hashes.py` | retrouve les noms en clair des hashes (dictionnaire `cotw/dictionnaires/hp_noms.tsv`) |
| `rtpc_roundtrip.py`, `sarc_roundtrip.py`, `adf_ecriture_controle.py` | validation des écrivains sur les fichiers réels |
| `sauvegarde.py` | lit une sauvegarde (conteneur SAVE/COMP/zlib, ADF) et en fait un rapport nommé ; `--charge` écrit l'ADF décompressé |
| `noms.py` | noms en clair des hashes pour l'affichage (projet, dictionnaire du fork, noms d'un mod) |
| `extraire.py` | copie octet pour octet de fichiers du jeu, pour étude : par chemin, motif, extension (`--echantillons N` : quelques fichiers répartis par taille) ou numéro de nœud (fichiers sans nom) |
| `deca_audit.py` | audit en lecture seule d'un projet DECA : fichiers sans nom, formats non lus, **couverture de lecture par extension** et commandes d'extraction d'échantillons |

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
- **Atelier** : DECA ouvert sur le projet, **Tools → Ammo Workshop…**. **Open mod…** charge un `.cotwmod.json` ; **Build** écrit `<sortie>\dropzone`, à copier dans le `dropzone` du jeu. La sortie par défaut est `..\mods\<nom du mod>`.
- **Mod en ligne de commande** : `python cotw\construire_mod.py ..\work\hp cotw\mods\test14.cotwmod.json --sortie ..\mods\test14`.
- **Sauvegarde** : **File → Open Save…** dans DECA, ou `python cotw\sauvegarde.py <fichier> --projet ..\work\hp --langue fr`.
- **Audit de lecture** : `python cotw\deca_audit.py ..\work\hp` (DECA fermé). Le dossier de sortie (`..\work\audits\…`) contient `rapport.md` (section 3 : couverture par extension et priorités), `couverture_extensions.csv` et `extraire_echantillons.bat`, qui copie quelques fichiers de chaque format non lu dans `echantillons\`. `--compare <ancien metrics.json>` montre l'évolution.
- **Outils** : `python cotw\<outil>.py --help`, DECA fermé.
- **Tests** : `python cotw\tests\test_<nom>.py`.
- **Charger les fichiers modifiés en jeu** : option de lancement Steam
  `--vfs-fs dropzone --vfs-archive archives_win64 --vfs-archive patch_win64 --vfs-archive dlc_win64 --vfs-fs .`

## Licence

MIT, voir [`LICENSE`](LICENSE). Le code de DECA reste sous le copyright de Krzysztof Kamieniecki.
