# Élev’Age — développement durable

Dépôt officiel : https://github.com/myarilove1881989-maker/backend-elevage
Emplacement local courant : D:/ElevAge-Dev/backend. GitHub est la référence ; les anciennes copies sont des références de livraison à préserver.

Lire DEVELOPPEMENT.md et PUBLICATION.md avant de travailler. `develop` est l’intégration, `feature/...` le travail courant. Ne jamais pousser, fusionner ou changer les branches de production sans accord explicite. Web `master` auto-déploie ; backend `codex/android-users-compatibility-20261027` est la branche Render actuelle. Aucune modification Render, aucun appel métier en écriture à la production, aucune migration production, aucune installation Android sans accord.

Ordinaire autorisé : lire, modifier, tester localement, créer des branches feature, committer, pousser les branches de développement et préparer des PR vers develop. Ne pas demander de confirmation pour ces opérations. Pas de push forcé, suppression de branche/dossier, changement de secrets, permissions sensibles ou protection GitHub.

Préserver Android signé 113, clés privées externes, données appareil, offline V2, preuve appareil, cloisonnement exploitation et droits propriétaire/opérateur. Aucune refonte ni contournement d’autorisation. Les opérateurs utilisent Android ; le candidat Web refuse les opérateurs.

Utiliser tools/dev.ps1 ; temporaires, caches et grosses compilations sur D:. Tests ciblés selon le diff ; réutiliser les preuves du candidat si les sources testées sont identiques. Ne jamais versionner .local/, .env, secrets, bases, APK, clés de signature ni journaux privés. Aucun secret SMTP à l’écran ou dans les rapports.

Les hooks locaux bloquent les pushs hors develop/feature et les secrets courants. Ne pas les contourner. Ne pas créer d’autre copie courante ; garder ces deux clones. Un changement production doit être présenté avec commit, artefact, migration/configuration, impacts et retour arrière, puis approuvé.
