# COTWed DECA : documentation détaillée

Outils pour lire, analyser et écrire les fichiers de **theHunter: Call of the Wild** (moteur APEX d'Avalanche Studios).
Le but est de préparer un **modloader** qui ajoute du contenu (munitions, armes…) au jeu **sans écraser** les fichiers d'origine.

Ces pages reprennent le détail de l'ancien README du dépôt (état au 2026-10-10), complété des outils et formats ajoutés depuis. Le [README du dépôt](../../README.md), en anglais, en donne la vue d'ensemble.

| Page | Contenu |
|---|---|
| [Ajouter une munition](munitions.md) | ce qui marche en jeu, fichiers écrits dans `dropzone`, balistique, icône, modèle de cartouche, dépendances restantes |
| [Lire et nommer les fichiers du jeu](lecture-et-nommage.md) | visionneuses, formats lus, nommage des fichiers et des hashes internes, ajouts depuis le 2026-10-11 |
| [Contenu du fork](contenu.md) | modifications du cœur DECA, de l'interface, et liste des outils de `cotw/` |
| [Utilisation](utilisation.md) | installation et commandes sous Windows |

## Origine

Ce projet part de [DECA](https://github.com/kk49/deca), de Krzysztof Kamieniecki (kk49), sous licence MIT : branche `develop`, commit `9231177` (2025-12-10).
DECA n'est plus maintenu. Ce dépôt n'en suit plus les évolutions et se concentre sur COTW. Le README d'origine est conservé dans [`docs/deca/README.md`](../deca/README.md).
