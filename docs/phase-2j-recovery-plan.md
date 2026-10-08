# Phase 2J — sauvegarde, schéma et retour arrière

## Cible Render à identifier avant toute opération

Render authentifié ; audit des deux espaces demandé par l’utilisateur. L’URL API mobile correspond à `backend-elevage` dans `My Workspace` (tea-d7rn0628qa3s73dkf780), service `srv-d7sdmucm0tmc73cvc6u0`, main, virginia, free, SHA live90ce6fe3bd1272c3603776eb22d20bd16455b4b7. Base candidate du même environnement Production : `elevage-db`, IDdpg-da2spvv40ujc73avuefg-a, nom logique elevage, version SQL18.4, basic_256mb/5GB, sans HA/replica. Liaison DATABASE_URL non confirmée : lecture du secret refusée par le contrôle automatique. Ne pas la considérer démontrée par le seul environnement commun.

Migrations réelles de cette base : core0001–0015 seulement, pas de token_blacklist ni triggers applicatifs. PITR3jours et un export du5octobre2026 à22h27 Europe/Paris visibles, rétention exports au moins7jours ; archive non téléchargée et restauration non testée. Version locale18.1 distincte de18.4 réelle : nouveau contrôle18.4 requis.

Le backend principal et le site mobile (srv-da3hpa9t0dsc73fmrqg0/master) auto-déploient sur commit. Le backend applique migrate au démarrage. L’autre espace `db-elevage` (tea-d804dkrtqb8s73fr2mp0) contient backend-elevage-lczf (srv-d804prjrjlhs73a0m7qg/main), même SHA live, migrations au démarrage et autoDeploy actif, sans Postgres listé. Aucun push/merge/redémarrage/déploiement n’est permis par cette phase. Audit en lecture seule, aucun secret recopié.

Inventaire après sélection : service ID, URL, dépôt, branche, SHA réellement déployé, région, autoDeploy, commandes build/start/preDeploy ; base ID, nom logique, version PostgreSQL, région, plan, sauvegardes/PITR et rétention effectivement disponibles. Confirmer la liaison service→base par référence Render/Dashboard, sans afficher DATABASE_URL. Le connecteur ne proposant pas de lecture des variables, une confirmation Dashboard peut être nécessaire. Ne jamais pousser sur une branche surveillée avant identification de l’auto-déploiement.

Lecture de métadonnées uniquement : django_migrations (noms), pg_constraint, pg_trigger et version serveur. Ne pas lire les déclarations, clients ou utilisateurs de production pour les tests. Le schéma local cible core0031 plus token_blacklist est une cible, pas une preuve du schéma déployé.

## Revue des migrations

0001–0031 : 0016 ajoute memberships/dispositifs, génération et politique offline désactivée par défaut ; index partiel `one_active_primary_device`. 0017 bootstrap selon propriétaire enregistré, reverse=noop pour préserver les memberships. 0018 protège AuditEvent, 0020 originaux terrain, 0024 encaissement physique, 0026 décisions et 0028 compensations. Restaurer aussi fonctions PL/pgSQL, triggers, contraintes et séquences ; SQLite ne démontre pas ces garanties.

0023 valide les montants avant float→numeric(12,2), refuse valeurs non finies/hors limites/arrondi incompatible. Aucun ajustement automatique des montants. Évaluer temps de scan et verrous sur volumétrie synthétique représentative. Le retour à FloatField ou l’annulation des triggers ne constitue pas un retour arrière sûr. Vérifier le plan depuis les migrations réellement présentes ; pas de `--fake` pour contourner une divergence.

## Répétition isolée

`tool/phase2j_isolated_postgres.py` crée un cluster local dédié en écoute 127.0.0.1:55440, sans reprendre DATABASE_URL. PostgreSQL18.1 disponible localement, distinct du PostgreSQL17.11 testé en 2I ; répéter ensuite sur la version exacte Render. Données entièrement synthétiques. Suite backend, migrations, pg_dump custom, pg_restore atomique vers une seconde base dédiée, comparaison migrations/contraintes/triggers et UUID/montant fictifs, refus SQL UPDATE/DELETE AuditEvent. Les logs et dump restent ignorés par Git. Cette répétition ne valide pas les backups Render.

## Sauvegarde avant opération future autorisée

1. Désigner responsable, cible exacte, fenêtre, RPO/RTO, stockage chiffré durable, rétention et procédure de restitution. Inventaire des originaux locaux à préserver séparément ; une restauration serveur ne remet pas automatiquement les clients au même instant.
2. Vérifier snapshot/PITR existants dans Render selon plan. Aucun export production ni test avec données production exécuté pendant cette phase.
3. Après autorisation distincte de sauvegarde production : pg_dump -Fc --no-owner --no-acl vers coffre chiffré, credentials via coffre/PGPASSFILE protégé, TLS externe requis. Ne jamais passer l’URL avec mot de passe dans les arguments ou logs. Hacher l’archive et conserver versions outils, heure de référence et schéma. Inventorier rôles/privilèges/extensions séparément, car no-owner/no-acl ne les restitue pas.
4. La validation actuelle utilise une archive synthétique seulement. Toute restauration future contenant de la production exige une autorisation distincte compatible avec l’interdiction actuelle d’utiliser ces données pour tester. Une répétition synthétique ne suffit pas à déclarer l’archive réelle restaurable.
5. Restaurer la répétition dans une instance/base isolée vide, credentials distincts, application et jobs absents, egress/notifications désactivés, aucun lien production. pg_restore --exit-on-error --single-transaction --no-owner --no-acl. Pas de --clean sur une cible existante.
6. Comparer comptes/UUID/auteurs/générations/autorisation, montants décimaux, relations FK, séquences, contraintes et fonctions/triggers ; vérifier interdictions de mutation, idempotence et schéma exact ; relever durée et preuves. Réappliquer privilèges minimaux validés. Chiffrer/archive puis supprimer les ressources de test seulement après décision séparée.

## Retour arrière

Avant tout déploiement futur : conserver l’APK compatible signé et l’image/SHA backend réellement en service, ainsi que la sauvegarde vérifiée. Après incident : suspendre les nouvelles écritures et la synchronisation sous procédure autorisée, inventorier tous les originaux reçus depuis la sauvegarde et ceux en attente côté tablette. Préserver audit et UUID, rapprocher avant reprise. Revenir au code précédent seulement si compatible avec le schéma et les déclarations déjà créées ; sinon correction en avant. Restaurer sur une nouvelle base et examiner les écarts avant bascule explicitement autorisée. Aucun downgrade APK ni suppression des données client.

Documentation : https://www.postgresql.org/docs/17/app-pgrestore.html ; https://render.com/docs/postgresql-backups
