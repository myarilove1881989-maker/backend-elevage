# Pisciculture — phase3H : préparation de l’intégration commune

Audit du14 novembre2026. Architecture commune validée : application/backend/PostgreSQL/frontend existants, sélecteur d’activité et isolation des exploitations. Aucun changement de production autorisé par cette mission.

Copie isolée backend, branche `feature/pisciculture-phase3h-integration-20261114`. Sources applicatives, dépendances, migrations, fichiers publics et mécanismes hors ligne identiques à la base3G. Seul ce document est ajouté au dépôt.

Dossier : `D:/ElevAge-Pisciculture-Phase3H-20261114/qualification/RAPPORT-PHASE3H.md`. Métadonnées Render et schéma lus en lecture seule ; backend live0f05f1b1aefda04ad24df0a9027e06977398a86d, Web livee4cd64a4d421fdbc8a267aca093cd3330afb3ba5. Registre réel80 entrées/core0050, sans pisciculture ; PostgreSQL18.4. Configuration effective/runtime et sauvegardes réelles partiellement inaccessibles, preuves manquantes explicites.

Répétition locale depuis le registre exact :24 migrations vers core0058/pisciculture0016, garde inchangé, digest d’un vrai dump fictif préalablement restauré,51 tables core/49 lignes historiques conservées, garde rejoué sans drapeau. Contraintes des60 tables publiques reproduites exactement. Tests nouveaux3H :51 backend et25 Flutter réussis. Release/Chrome/PDF3G repris comme preuves antérieures, sans nouvelle exécution.

Recommandation : **NO-GO publication immédiate**. Conditions : sauvegarde/restauration réelle autorisée et représentative ; Linux/versions/configuration ; anciens onglets Web ne sachant pas présenter POISSONS et preuve de compatibilité119 ; artefact/cache canonique préservant les liens publics ; récupération compatible0058. L’ancien backend publié ne doit pas être redéployé aveuglément après migration : il refuse le nouveau schéma. Aucun intermédiaire0038/0049 nécessaire pour0050 observé.

Tous les fichiers contrôlés surD:, copies précédentes conservées. Aucun push/PR/fusion, déploiement, migration distante, backup/export/restauration réel, réglage Render, écriture métier réelle ou publication Web/APK. Android119, clés/PIN/outbox, /telecharger et /android-version.json non modifiés. Arrêt après audit/préparation ; autorisation explicite séparée avant toute intervention.
