# Pisciculture 3F — recette locale du 14 novembre 2026

Base backend : e3d2de318be251778848457491183f9bf78884cf. Copie isolée D:/ElevAge-Pisciculture-Phase3F-20261114/backend, branche feature/pisciculture-phase3f-recette-20261114. Copies, branches et preuves 3E conservées.

Aucun code applicatif, calcul, modèle, migration, permission, clé/PIN, outbox ou mécanisme hors ligne modifié. La qualification est exécutée depuis les HEAD 3E ; le commit 3F ajoute uniquement ce document. Les sources d’exécution restent identiques.

Flutter : suite entière test/, --no-pub --concurrency=1, 1 420 recensés, 1 402 réussis, 18 ignorés, aucun échec ; 53 min 30 s. Ce passage complet est distinct des anciens résultats 3E et de leur confirmation ciblée 290. Aucun timeout ou test relâché. Préparation offline limitée par les liens symboliques Windows/exFAT ; configurations générées et dépendances D: existantes utilisées sans build Windows ni nouvelle installation.

Backend : core.tests et pisciculture.tests, PostgreSQL synthétique 127.0.0.1:57883, 908 recensés, 906 réussis, 2 ignorés (gardes SQLite), aucun échec, 3306.866 s. Manifeste des 24 migrations inchangé, graphe core0058/pisciculture0016. Tests d’isolation, droits/révocation, UUID, concurrence, transactions, historiques/corrections, finances, migrations, terrestre et hors ligne existant repris.

Chrome réel, build debug de la source 3E, API 8081 / interface 8082 et PostgreSQL 57881 : 25 catégories d’interactions, 39 validations serveur réussies par les formulaires, trois cas négatifs. Seuls compte, seconde ferme témoin, activation et espèce sont préparés avant connexion. Aucun acte métier du scénario prérempli. Aucun widget assimilé à un parcours Chrome.

Onze panneaux aux quatre configurations 360/1080 et texte normal/doublé ; huit confirmations après chargement. Dix-huit formulaires ouverts, défilés et annulés aux quatre configurations, soit 72 observations sans nouvel acte. Le texte doublé est un réglage typographique lu par le moteur Flutter Web, pas un agrandissement d’image.

Conservation finale : vivant 1000−15+100−300=785 (Nord 135, Sud 550, nurserie 100), aliment 47 kg, garde 20 kg/80 poissons. Le transfert réel est 50 puis 100 après la première saisie tronquée par le pilote ; les deux actes sont conservés. Biomasse estimée, eau partielle avec null distinct de zéro. Facturé 150.25, payé 75.00, dû 75.25, charges retenues 145, commun ferme 30 exclu, résultat indicatif 5.25. Le paiement mixte complémentaire de 3E n’est pas reproduit dans l’interface 3F.

Contrôle PostgreSQL final strictement en lecture seule : comptes exacts et montants vérifiés. Eau vide, clôture sans motif et récolte excessive (409) refusées ; aucun nouveau fait physique ou économique. Tâche DONE avec auteur, motif et historique, business_effect_created=false.

PDF : chute reproductible de l’onglet en mode sans fenêtre, malgré génération du document. Une impression minimale hors Flutter ne reproduit pas la chute ; cause profonde non établie. Chrome visible génère et ouvre le PDF sans chute ; montants et rendu vérifiés. Aucune impression papier. Aucun correctif spéculatif de facturation.

Android non réalisé : aucun appareil dédié détecté ni AVD vierge prêt sur D:. SDK et anciens profils C: examinés en métadonnées seulement, ni copiés ni démarrés ; pas de dépendance lourde installée. Procédure manuelle fournie. Aucun retour d’un éleveur réel inventé ; petits/mixte distincts proposés au guide, pas déclarés exécutés dans Chrome.

Livrables dans ../qualification : RAPPORT-PHASE3F.md, GUIDE-RECETTE-HUMAINE.md, RECETTE-ANDROID-MANUELLE.md, ECARTS-ARBITRAGES.md, COHERENCE-DONNEES.md, PARCOURS-CHROME.md, TESTS-SECURITE-NON-REGRESSION.md, ANOMALIES-3F.md, REPRODUCTION-LOCALE.md, captures.html, qualification.json et evidence/. Widgets, Chrome, diagnostics et PDF sont distingués.

Conclusion : prêt sous réserves pour préparer une publication ultérieure du périmètre livré. Courbes d’eau, investissement simple, photos, anglais, KPI, fusion/tri et ventilations restent à arbitrer ; aucune extension automatique ni phase 4. Recette humaine et Android dédiées, impression matérielle et conditions release/TLS restent à qualifier selon le périmètre futur.

Android 119, clés/PIN/outbox, /telecharger et /android-version.json préservés. Tous les nouveaux fichiers et temporaires contrôlables sur D:. Aucun push, PR, fusion, déploiement, migration distante, donnée réelle ou artefact publié. Arrêt après qualification locale ; attendre validation.
