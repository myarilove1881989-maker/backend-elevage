# Preuves Phase 2J — 8 octobre 2026

Code métier candidat : 3c2d1fdcb530827c0d0a46b2ff0ec109e11d40e1. Les modifications2J n’altèrent pas les migrations ni les services métier.

SQLite : check sans anomalie, makemigrations --check --dry-run sans modification ; 284 tests, 367.328 secondes, OK/skipped15,269 réussis.

PostgreSQL18.1 synthétique dédié : migrations core0001–0031 et token_blacklist jusqu’à0013 réussies ; absence de dérive ; 284 tests,349.141 secondes, OK/skipped2,282 réussis. Log : phase2j-evidence/.phase2j-postgres-406f362a583d4aa49d224d096aedb908/postgres-tests.log. Base de test Django détruite et cluster arrêté.

Restauration finale : phase2j-evidence/.phase2j-postgres-3dceefe6290040b381d05460b622eb33/restore-result.json. Archive custom SHA256 `89800e427ccced7a6101ec4e3cb91b1c1152d00c3bce3d9e82930aa4266d27df`. Source et cible : deux bases neuves distinctes du même cluster jetable en127.0.0.1:55440. pg_restore atomique avec exit-on-error, no-owner/no-acl. Migrations, toutes les autres définitions de contraintes, triggers et UUID/montant de la sonde fictive égaux ; aucune exploitation avec offline_policy active. UPDATE et DELETE SQL sur AuditEvent refusés. Cluster arrêté, journaux conservés hors Git. Cette répétition ne démontre pas une restauration d’un corpus métier complet ou d’une archive Render réelle, ni la reprise sur une version Render inconnue.

Échecs conservés : accès Windows sandbox aux chemins Python/Flutter ; lancement PostgreSQL bloqué par héritage de pipe puis refus d’accès au journal partagé, corrigé avec journal/cluster uniques. Premier serveur synthétique arrêté après vérification du PID/chemin, sans toucher aux serveurs PostgreSQL préexistants. Premier contrôle restauration refusait la représentation SQL reformatée de `terrain_business_status_valid` : cast varchar[]→text[] re-décomposé en casts par élément. Le contrôle garde toutes les autres définitions exactes et compare huit probes sur cette contrainte validée de TerrainOutcome avant/après (six statuts autorisés vrais, INVALID/chaîne vide faux). Une première requête visait à tort TerrainSubmission ; corrigée vers TerrainOutcome. Relance finale `--restore-only`, sans répéter la suite déjà passée. Aucun échec ignoré pour obtenir le verdict.

Mobile : CI historique2I37728095062 success ; tests2J bloqués par Flutter3.41.6 local incompatible avec le lockfile (CI2I3.47.6). Signature Gradle préparée mais compilation/refus effectif à tester avec3.47.6. Aucun APK signé pérenne créé.

Les fichiers pyc préexistants suivis dans ce dépôt ont été remis à leur état candidat après les tests. Les logs/dumps/clusters locaux sont ignorés. Aucun secret ni donnée production dans les commits ; aucun push/deploy/merge/activation.
