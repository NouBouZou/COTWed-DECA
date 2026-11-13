# Contenu du fork

[← Documentation détaillée](README.md) · [README du dépôt (anglais)](../../README.md)

## Modifications du cœur DECA (`python/deca`)
- **Scan** :
  - un nom RTPC contenant un antislash n'interrompt plus le scan ;
  - un processus de travail qui meurt sans prévenir (tué, mémoire insuffisante) fait échouer le scan, au lieu de le bloquer indéfiniment ;
  - le gestionnaire du scan n'occupe plus un cœur à 100 % pendant tout le scan (attente bloquante au lieu d'une attente active).
  - un fichier dont le traitement lève une exception n'arrête plus le scan : ses écritures en base sont annulées, il reçoit le drapeau `node_flag_processing_failed` (affiché dans l'onglet Info, jamais retenté), et le journal donne une ligne `loop_over_uid_wrapper: failed for` avec la cause et ses archives parentes ;
  - les dictionnaires de noms (`ghidra_strings`, `field_strings`) sont trouvés quel que soit le dossier courant, et `cotw/dictionnaires/<jeu>_noms.tsv` est chargé au scan ;
  - les entrées de taille 0 des `.tab` sont gardées (liens), au lieu d'être perdues ;
  - les chaînes en forme de chemin de l'exécutable installé sont proposées comme noms (`deca/exe_strings.py`, ligne `STRINGS FROM EXE` du journal).
  - la banque de chaînes FMOD propose les fichiers des banques (`sound/fmod_banks/<nom>.fmod_bankc`), en ne lisant que ses en-têtes de chunks et sa table `STDT`.
- **Écriture RTPC** (`deca/ff_rtpc_write.py`) : versions 1 et 2, identique à l'octet près sur les 6 348 RTPC du jeu.
- **Formats ajoutés** (`deca/ff_rtpc_inline.py`, `ff_fmod_guids.py`, `ff_ttf.py`, `ff_rawc.py`, `ff_obc.py`, `ff_btc.py`, `ff_havok.py`, `ff_fmod_bank.py`, `ff_fsb5.py` et `ff_fsb5_vorbis.py`, `ff_pfx.py`, `ff_mdi.py`, `ff_gfx.py`, `ff_bink.py`, `ff_bc_raw.py`, `ff_hikcc.py`) : nouveaux types `rtpci`, `fmodguids`, `ttf`, `rawc`, `btc`, OBC version 1, reconnus au scan (`ff_determine`) ; packfiles Havok 2014 (type `h2014` de DECA) lus objet par objet.
- **Vues** (`db_view.py`, `db_types.py`) : le filtre est appliqué par SQLite. Les chemins non ASCII sont acceptés. La table `core_nodes` a un index sur `parent_id`, créé à la première ouverture d'un projet existant.

## Interface (`python/deca_gui`, en anglais)
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
  - **SARC** : table des entrées (offset, taille, type détecté, lien) ; double-clic pour ouvrir une entrée ;
  - **Havok** (`.ban`, `.bsk`, `.brd`, `.navmeshc`, `.physicsc`, `.pfxc`, `.pfs`) : arbre des objets du packfile et de leurs membres, os nommés avec leur parent et leur pose, pistes d'animation rattachées à leurs os ;
  - **Structure** : GUID des événements FMOD (`.fmod_guids`, avec le nom de chaque événement), banques FMOD (`.fmod_bankc` : chunks, noms de la banque de chaînes, liste des sons), sons `.wavc`, instances de modèles (`.nl.mdic`, `.fl.mdic`), films Scaleform (`.gfx`), vidéos Bink, description d'une police (`.ttfc`), arbre de comportement (`.btc` : paramètres nommés, chaînes) ; **OBC** : boîtes d'occultation de la version 1 ; **Image** : tableaux bruts `.rawc` ; **RTPC** : aussi le RTPC en ligne (`world.bin`, `.smodc`).
- **Sauvegardes** (**File → Open Save…**, `deca_gui/save_viewer.py`) : objets possédés et quantités, sac, objets débloqués, emplacements d'inventaire, loadouts, données d'armes, et l'arbre ADF complet. Les objets sont nommés avec le projet ouvert, dans la langue choisie, et avec les mods ajoutés (**Add mod file…**) ; un hash que rien ne nomme est signalé.
- **Projet de démonstration** (`cotw/demo_projet.py`, `cotw/lancer_demo.bat`) : un faux COTW scanné par DECA, pour travailler sur l'interface sans le jeu. Il contient tout ce que demande l'atelier : entités avec `.ammotunec`, `string_ids` en 10 langues, feuille de stats terminée par deux lignes vides.
- **Ammo Workshop** (**Tools → Ammo Workshop…**, `deca_gui/ammo_workshop.py`) :
  - à gauche, les munitions du jeu (nom affiché dans la langue choisie, prix, disponibilité, ligne de stats, armes) ;
  - au centre, le mod : munitions ajoutées, prix témoins, changements d'objets existants ;
  - à droite, la munition sélectionnée : nom, source, prix, entité, armes compatibles, ligne de stats (valeurs copiées et nouvelles), nom et description par langue ;
  - les problèmes connus avant construction sont signalés en rouge (nom déjà pris, instance du `.ammotunec` identique à celle de la source…) ;
  - **Build** tourne en arrière-plan et affiche le rapport ; **Copy command line** donne la commande `construire_mod` équivalente.

## Outils COTW (`cotw/`)
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
| `retyper.py` | donne leur type aux fichiers sans type d'un projet déjà scanné, avec la détection actuelle (journal, annulation) |
| `renommer.py`, `noms_fichiers.py` | applique des noms à un projet déjà scanné, sans rescanner ; journal et annulation ; contrôle de type appris dans le projet |
| `audit_noms_internes.py` | part des noms internes résolus (RTPC, ADF), énumérations mal affichées, hashes inconnus par fréquence et par famille de fichiers, vocabulaire connu par famille ; `--compare` avec un audit précédent |
| `deca_audit.py` | audit en lecture seule d'un projet DECA : fichiers sans nom, formats non lus, **couverture de lecture par extension** et commandes d'extraction d'échantillons |
| `voisins_orphelins.py` | voisins nommés de chaque fichier sans nom dans son archive, matière des passes guidées de `generer_noms.py` |
| `noms_tri.py`, `hash_lot.py`, `familles.py` | modules de `generer_noms.py` et `resoudre_hashes.py` : passes guidées par l'ordre des archives, hash32 en lot compilé par numba, famille de fichiers d'un chemin |
| `audit_maillages.py`, `geometrie_maillages.py` | les `.meshc` passent-ils par l'export glTF de DECA ; vraisemblance des sommets décodés (positions, normales, tangentes) ; `--modeles`, `--materiaux` |
| `audit_squelettes.py` | les maillages à os suivent-ils leur squelette (`.bsk`) ; pièces rapportées des armes mesurées sur leur os d'attache |
| `audit_animations.py`, `exporter_animation.py` | contrôle du décodage des animations `.ban` sur tout le jeu ; export glTF d'un squelette, de ses modèles et d'animations, pour Blender |
| `audit_collisions.py`, `exporter_collisions.py` | contrôle du décodage des formes de collision Havok Physics ; export OBJ (`--modeles` : glTF du `.modelc` associé, pour les superposer) |
| `audit_textures.py` | textures `.ddsc` : formats DXGI employés, en-têtes AVTX, effet des corrections du décodeur |
| `population.py` | populations d'animaux d'une sauvegarde (`animal_population_N`) ; comparaison de deux instantanés : animaux retirés, apparus, file de repopulation |
| `_projet.py` | accès en lecture à un projet DECA, partagé par les outils |
