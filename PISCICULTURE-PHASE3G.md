# Pisciculture — préparation du pilote Web3G

Qualification locale du 14 novembre 2026 sur la branche `feature/pisciculture-phase3g-pilote-20261114`.

Candidate backend issue de la phase3F, sources applicatives inchangées. Seul ce document est ajouté au dépôt ; aucune migration nouvelle, modification de configuration, protocole offline, clé/PIN, outbox, Android119 ou fichier de téléchargement.

Dossier local : `D:/ElevAge-Pisciculture-Phase3G-20261114/qualification/RAPPORT-PHASE3G.md`. Le guide, l’audit46 exigences, les tests, les preuves release/PDF et les plans non exécutés sont dans ce dossier sur D:.

Vérifications nouvelles3G : backend PostgreSQL245 réussites/2 ignorés sur247 ; Flutter161 réussites ; Web **release** construite ; parcours Chrome14 étapes sur données fictives et transport intercepté exclusivement loopback ; PDF réellement généré, paiement partiel, isolation entre deux fermes ; dump/restauration fictifs vérifiés sur83 tables. Les campagnes complètes3F906 backend/1402 Flutter sont des preuves antérieures distinctes, pas des totaux réexécutés3G.

Décision proposée : **NO-GO publication immédiate**, préparation locale terminée. Avant une mission ultérieure : choix explicite d’API, lecture seule du registre publié, chemin de migration supporté (gardes intermédiaires0038/0049 potentiellement bloquantes), sauvegarde réelle restaurée, configuration/TLS/Linux/cache publics qualifiés et limites du pilote acceptées. Le build conserve l’API canonique publique existante ; ne pas le lancer hors interception avant accord de cible. Les services publics n’ont pas été contactés par cette qualification.

Une navigation rapide peut laisser une carte au dernier chargement ; actualiser avant contrôle. Aucun changement applicatif ajouté pour contourner les gardes ou étendre le périmètre. Pas de phase4.

Aucun push, PR, fusion, déploiement, migration distante, publication Web/APK ou changement de données réelles. Arrêt après qualification locale ; autorisation explicite séparée nécessaire avant publication.
