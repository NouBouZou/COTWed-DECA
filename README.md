# COTWed DECA

Outils pour lire, analyser et écrire les fichiers de **theHunter: Call of the Wild** (moteur APEX d'Avalanche Studios).
Le but est de préparer un **modloader** qui ajoute du contenu (munitions, armes…) au jeu **sans écraser** les fichiers d'origine.

Projet personnel, privé pour l'instant.

## Origine

Ce projet part de [DECA](https://github.com/kk49/deca), de Krzysztof Kamieniecki (kk49), sous licence MIT : branche `develop`, commit `9231177` (2025-12-10).
DECA n'est plus maintenu. Ce dépôt n'en suit plus les évolutions et se concentre sur COTW. Le README d'origine est conservé dans [`docs/README_DECA.md`](docs/README_DECA.md).

## État au 2026-10-07

**Une munition ajoutée fonctionne en jeu.** Elle est visible au magasin, s'achète, affiche ses propres stats (`--stats-de` et `--stat`), son nom et sa description, ainsi que le logo de possession. Elle se charge, se tire et consomme ses propres munitions. Plusieurs copies d'une même munition coexistent avec elle sans bloquer le changement de munition (test14). Pour cela, quatre fichiers sont déposés dans `dropzone`, tous écrits sans rien écraser :

| Fichier | Format | Rôle |
|---|---|---|
| `settings/hp_settings/equipment_data.bin` | RTPC | l'objet (prix, compatibilités, DLC, `locked`…) |
| `settings/spawn_ammunition_defs.bin` | RTPC | lien nom → entité |
| `editor/entities/…/<nom>.ee` | SARC | entité propre (`.epe` avec `item_id`, `tuning_file`, `_object_id` nouveaux ; instance du `.ammotunec` renommée) |
| `settings/hp_settings/equipment_stats_ui.bin` | ADF (XLSBook) | stats affichées et logo de possession |

**Nom et description propres** (`--nom-affiche`, `--description`, vérifiés en jeu) : un cinquième fichier, `settings/hp_settings/string_ids.stringlookup` (ADF StringLookup, table de traduction des 10 langues), reçoit les clés `<nom>_name` et `<nom>_description`, que désignent `display_name` et `display_description` de la nouvelle munition. Le fichier passe de 38,8 à 49,4 Mo (les anciennes copies des tableaux triés restent en place).

**Balistique propre** (`--balistique CHAMP=VALEUR`, `--classes`, ou `"ballistics"` / `"classes"` dans un fichier de mod ; test15 : réglage modifié accepté par le jeu, effet de la traçante non observé) : les valeurs du `.ammotunec` de l'entité propre sont modifiées (pénétration, expansion, masse, traçante, classes d'animaux…), sans toucher la source. `python cotw\ammotune.py PROJET OBJET` liste les champs et leurs valeurs. L'Ammo Workshop ne les édite pas encore ; il les garde quand il enregistre un mod. Les stats affichées viennent de la ligne de stats : les mettre en accord.

**Icône propre** (`--icone IMAGE` ou `--icone-de OBJET`, ou `"icon"` / `"icon_from"` dans un fichier de mod ; vérifiée en jeu, test16) : une texture `textures/ui/items/ammo/<nom>_alpha.ddsc` est ajoutée, et `display_icon` de la nouvelle munition la désigne. `--icone` la fabrique depuis une image (PNG à fond transparent de préférence, mise à 640 × 320 en gardant les proportions) : compression BC3 et 9 niveaux de détail par `cotw/icone.py`, en-tête repris de l'icône de la source, contrôle par un décodage de référence et par la lecture de DECA. `--icone-de` copie l'icône d'un autre objet. Dans un fichier de mod, le chemin de l'image est relatif au fichier. L'Ammo Workshop ne les édite pas encore ; il les garde quand il enregistre un mod.

**Modèle de cartouche** (`--modele-de OBJET`, `--modele`, `--modele-douille`, ou `"model_from"` / `"model"` / `"casing_model"` dans un fichier de mod) : l'entité d'une munition peut déclarer le modèle de la cartouche chargée et de la douille éjectée (`base_model` des composants `fresh_round` et `empty_casing` du `.epe`), et l'outil les écrit dans l'entité propre de la nouvelle munition. **L'arme a le dernier mot** (test17 : la Ranger .243 a ignoré le modèle déclaré) :
- une arme sans composant de cartouche (`CModelAttachementWeaponComponent` aux points `ammo_reload` / `ammo_used`), comme les .223 et .270 du jeu, affiche le modèle de la munition ;
- une arme qui a ses propres composants (Ranger .243…) affiche toujours les siens. L'outil refuse alors, sauf avec `--basculer-armes` (`"convert_weapons": true`) : les composants sont retirés de l'entité de l'arme, et leur modèle est déclaré dans chaque munition compatible qui n'en déclare pas, pour que leur aspect ne change pas (à vérifier en jeu, test18). Les entités de ces munitions, et celle de l'arme, sont alors des fichiers du mod ;
- une arme dont les cartouches changent sur événement (`show_event` / `hide_event` ; relevé : 12GA semi-automatique type 2, 16GA 1897, 10GA superposé et semi-automatique, .410) n'est pas prise en charge : refus.

`python cotw\modele_munition.py PROJET OBJET` affiche les modèles déclarés par une munition.

**Étiquettes de spawn** (`--etiquettes A,B,C`, ou `"tags"` dans un fichier de mod) : les étiquettes de l'entrée de `spawn_ammunition_defs.bin` (`ammo_cal_243`, `ammo_type_sp`, `ammunition`…), à la place de celles de la source ; le nom de la munition reste en tête, comme dans le jeu. Leur rôle en jeu est inconnu : une étiquette absente du jeu est signalée, le nom d'un autre objet est refusé.

**Dépendances restantes** (2026-10-07, `cotw/dependances.py`) : chaque construction (ligne de commande, `construire_mod`, atelier) se termine par une section qui dit, élément par élément, ce que chaque munition ajoutée doit encore à un objet d'origine, d'après les fichiers produits :

| Statut | Sens |
|---|---|
| `partage` | le jeu lit une ressource d'un objet d'origine : entité, clé de traduction, icône, modèle de l'arme, son (`.fmod_guids`), nom d'instance du réglage… ; « (munition du mod) » quand c'est une autre munition du même mod |
| `absent` | élément manquant : pas de ligne de stats, aucun modèle |
| `inconnu` | non déterminé (fichier illisible, ligne de stats placée après la première ligne vide…) |
| `herite` | valeur recopiée, encore égale à celle d'origine : champs d'`equipment_data`, étiquettes, valeurs balistiques… |
| `propre` | propre à la munition |

Elle signale aussi les incohérences connues, par exemple des classes affichées (ligne de stats) différentes de celles du réglage balistique. `python cotw\dependances.py PROJET MOD.cotwmod.json [--json FICHIER]` construit le mod en mémoire et n'affiche que cette analyse.

**Tout cela se fait aussi dans l'interface** (**Tools → Ammo Workshop…**, depuis le 2026-10-04). On y édite un **fichier de mod** (`.cotwmod.json`) qui liste les munitions à ajouter. **Build** reconstruit le dropzone du mod depuis les originaux du jeu, en une passe, avec exactement les mêmes octets que la ligne de commande. Exemple : [`cotw/mods/test14.cotwmod.json`](cotw/mods/test14.cotwmod.json).

## Lecture des fichiers (2026-10-05)

L'autre moitié du projet : **lire tout ce que le jeu contient**, là où DECA s'arrête.

- **Le scan ne s'arrête plus sur un fichier illisible** : le fichier est marqué `PROCESSING FAILED` (onglet Info), une ligne du journal en donne la cause, le reste du jeu est exploré.
- **Les noms trouvés par le fork sont connus dès le scan** : `cotw/dictionnaires/hp_noms.tsv` (`cash_price`, `compatibility_N`, `secondary_ids`…) est chargé avec les dictionnaires de DECA.
- **Visionneuses** de l'interface pour les formats que le fork a documentés : RTPC et ADF en arbres (noms résolus, notes tirées du journal de recherche), tables de traduction (**Strings**), feuilles XLSBook (**Sheet**), archives SARC (**SARC**), sauvegardes (**File → Open Save…**).
- **Mesure de ce qui reste à lire** : `deca_audit.py` donne, par extension, la part des fichiers qu'une visionneuse sait afficher, et prépare l'extraction d'échantillons des formats encore illisibles, à étudier ensuite.

## Nommage des fichiers (2026-10-08)

Référence du 08/10 : 78,7 % des fichiers lisibles, 10 222 fichiers sans nom.

- **Nommer sans rescanner** (`cotw/renommer.py`) : DECA n'appliquait de nouveaux noms qu'en recréant le projet (64 min). `renommer.py ajouter` donne leur nom aux seuls fichiers sans nom, avec le code du scan de DECA, après un contrôle de type ; tout est journalisé, et `renommer.py annuler` remet la base dans son état précédent.
- **Proposer des noms** (`cotw/generer_noms.py`) : variantes des noms connus (numéros, suffixes, dossiers frères, chaînes de l'exécutable). Un nom n'est gardé que si le type détecté dans le fichier est attendu pour son extension. `--temoin` mesure les faux positifs sur des cibles fictives. Sur les données du 08/10 (hors ligne) : 4 993 noms (48,8 %), environ 10 faux.
- **Chaînes de l'exécutable** : le scan lit les chaînes en forme de chemin de l'exécutable installé (`deca/exe_strings.py`), et plus seulement la liste figée `ghidra_strings`.
- **Noms internes** (`cotw/audit_noms_internes.py`) : part des hashes résolus dans les RTPC et ADF (propriétés, nœuds, StringHash, événements), énumérations mal affichées par DECA, hashes inconnus classés par fréquence ; `--compare` donne l'évolution depuis un audit précédent.
- **Retrouver les noms internes** (`resoudre_hashes.py chercher --cibles inconnus.csv`) : cherche d'un coup tous les hashes inconnus relevés par l'audit. Les cibles sont classées par nombre d'occurrences : les 303 propriétés vues 1 000 fois ou plus portent 96 % des occurrences de propriétés inconnues, et sur si peu de cibles une coïncidence est très improbable. Un nom n'est écrit que si les faux positifs attendus restent faibles pour sa passe et sa classe, ou s'il est confirmé par un autre nom de même racine (`frame_0` … `frame_98`, `show_clues` et `hide_clues`, `segment_type` par `segment_id`) ; `--temoin` mesure les faux positifs réels. Mesuré sur le PC le 08/10 : 1 117 noms ; propriétés RTPC résolues de 60,1 % à **75,8 %** des occurrences, nœuds de 14,9 % à **57,8 %**.
- **Passes par famille de fichiers** (`cotw/familles.py`) : chaque famille (`trophy_feature_data.bin`, `.asb`, `.blo`…) confronte son propre vocabulaire (noms déjà connus dans ses fichiers, `noms_par_famille.tsv` de l'audit, et `dictionnaires/vocabulaire_familles.txt`) à ses seules cibles. Contre 108 cibles, 2 millions de candidats n'attendent que 0,05 coïncidence : `antler_offset`, `keyframe_index`, `tines_length`, `circumference_upper_beam`… Estimation hors ligne : 29 % des occurrences de propriétés encore inconnues, 0,15 faux positif attendu.
- **Textures** : `deca_audit.py` ne compte plus un `.hmddsc` ou un `.atxN` comme lisible si son `.ddsc` n'est pas nommé : la visionneuse ouvre la texture par son `.ddsc` (nouveau statut `mip_orphelin`).

## Contenu

### Modifications du cœur DECA (`python/deca`)
- **Scan** :
  - un nom RTPC contenant un antislash n'interrompt plus le scan ;
  - un processus de travail qui meurt sans prévenir (tué, mémoire insuffisante) fait échouer le scan, au lieu de le bloquer indéfiniment ;
  - le gestionnaire du scan n'occupe plus un cœur à 100 % pendant tout le scan (attente bloquante au lieu d'une attente active).
  - un fichier dont le traitement lève une exception n'arrête plus le scan : ses écritures en base sont annulées, il reçoit le drapeau `node_flag_processing_failed` (affiché dans l'onglet Info, jamais retenté), et le journal donne une ligne `loop_over_uid_wrapper: failed for` avec la cause et ses archives parentes ;
  - les dictionnaires de noms (`ghidra_strings`, `field_strings`) sont trouvés quel que soit le dossier courant, et `cotw/dictionnaires/<jeu>_noms.tsv` est chargé au scan ;
  - les entrées de taille 0 des `.tab` sont gardées (liens), au lieu d'être perdues ;
  - les chaînes en forme de chemin de l'exécutable installé sont proposées comme noms (`deca/exe_strings.py`, ligne `STRINGS FROM EXE` du journal).
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
| `dependances.py` | ce qu'une munition ajoutée doit encore à un objet d'origine (partagé, hérité, propre, absent) ; appelé à la fin de chaque construction |
| `modele_munition.py` | modèles 3D de cartouche et de douille : déclarés par une munition (lecture, écriture de `base_model`) ou par une arme (lecture, retrait des composants pour la bascule) |
| `icone.py` | icônes `.ddsc` (AVTX, BC3) : lecture, export PNG, fabrication depuis une image (compression BC3 et niveaux de détail en numpy) |
| `adf_remplacer_chaine.py` | remplace une chaîne d'un ADF sur place |
| `rtpc_dump.py`, `adf_dump.py`, `sarc_dump.py` | affichage lisible des formats, noms résolus |
| `comparer_objets.py`, `separateurs.py`, `liste_objets.py` | comparaison d'objets d'`equipment_data` |
| `indexer_refs.py`, `differentiel.py`, `deca_refs.py`, `citations.py` | qui cite quoi, dans tout le jeu |
| `resoudre_hashes.py` | retrouve les noms en clair des hashes (dictionnaire `cotw/dictionnaires/hp_noms.tsv`) : hashes de fichiers choisis, ou tous les inconnus d'un audit (`--cibles`, par classe, avec corroboration et témoin, puis par famille de fichiers) |
| `rtpc_roundtrip.py`, `sarc_roundtrip.py`, `adf_ecriture_controle.py` | validation des écrivains sur les fichiers réels |
| `sauvegarde.py` | lit une sauvegarde (conteneur SAVE/COMP/zlib, ADF) et en fait un rapport nommé ; `--charge` écrit l'ADF décompressé |
| `noms.py` | noms en clair des hashes pour l'affichage (projet, dictionnaire du fork, noms d'un mod) |
| `extraire.py` | copie octet pour octet de fichiers du jeu, pour étude : par chemin, motif, extension (`--echantillons N` : quelques fichiers répartis par taille) ou numéro de nœud (fichiers sans nom) |
| `generer_noms.py` | propose des noms pour les fichiers sans nom (variantes des noms connus, chaînes de l'exécutable), avec contrôle de type et mesure des faux positifs (`--temoin`) ; `--hors-ligne` travaille sur les sorties d'un audit, sans la base |
| `renommer.py`, `noms_fichiers.py` | applique des noms à un projet déjà scanné, sans rescanner ; journal et annulation ; contrôle de type appris dans le projet |
| `audit_noms_internes.py` | part des noms internes résolus (RTPC, ADF), énumérations mal affichées, hashes inconnus par fréquence et par famille de fichiers, vocabulaire connu par famille ; `--compare` avec un audit précédent |
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
- **Nommer les fichiers sans nom** (DECA fermé) : `python cotw\generer_noms.py ..\work\hp --sortie noms.txt --temoin`, puis `python cotw\renommer.py ajouter ..\work\hp --noms noms.txt --exe` ; `python cotw\renommer.py annuler ..\work\hp` revient en arrière.
- **Noms internes** (DECA fermé) : `python cotw\audit_noms_internes.py ..\work\hp --out ..\work\audits\noms-internes`, puis `python cotw\resoudre_hashes.py chercher ..\work\hp --cibles ..\work\audits\noms-internes\inconnus.csv --temoin --out resoudre.md` (écrit `cotw\dictionnaires\hp_noms.tsv`, que les visionneuses RTPC et ADF lisent directement ; `resoudre_hashes.py injecter ..\work\hp` les ajoute aussi à la base, pour la vue texte d'origine de DECA).
- **Outils** : `python cotw\<outil>.py --help`, DECA fermé.
- **Tests** : `python cotw\tests\test_<nom>.py`.
- **Charger les fichiers modifiés en jeu** : option de lancement Steam
  `--vfs-fs dropzone --vfs-archive archives_win64 --vfs-archive patch_win64 --vfs-archive dlc_win64 --vfs-fs .`

## Licence

MIT, voir [`LICENSE`](LICENSE). Le code de DECA reste sous le copyright de Krzysztof Kamieniecki.
