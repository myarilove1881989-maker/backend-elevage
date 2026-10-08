# Phase 2K — cible, migration, sauvegarde et retour arrière

Préparation uniquement. Aucun merge, déploiement, migration, export de données réelles ou activation offline_policy n'est autorisé par cette phase.

## Inventaire confirmé en lecture seule

My Workspace tea-d7rn0628qa3s73dkf780 : backend srv-d7sdmucm0tmc73cvc6u0, https://backend-elevage.onrender.com, dépôt backend-elevage/main, Virginia, Free ; frontend srv-da3hpa9t0dsc73fmrqg0, elevage-mobile/master. Les deux auto-déploient les commits de leur branche, previews de PR désactivées. Branche de préparation feature/offline-phase-2k vérifiée hors de ces déclencheurs avant push.

Backend start actuel : `python manage.py migrate --noinput && gunicorn config.wsgi:application`. Un merge ou redémarrage peut donc appliquer une migration. Rien n'a été modifié dans Render.

Base dpg-da2spvv40ujc73avuefg-a, elevage-db, nom logique elevage, PostgreSQL18.4 confirmé par SQL, basic_256mb/5Go, Virginia, même environnement Production, sans HA/réplica/pool. Liaison réelle service→hôte/base confirmée lors du contrôle limité 2J ; aucune nouvelle lecture du secret pendant 2K. Le connecteur de configuration ne donne pas une seconde preuve indépendante de cette liaison. Le schéma relu en 2K est core0001–0015, sans trigger applicatif. Candidat core0031 plus migrations token_blacklist.

Second espace db-elevage tea-d804dkrtqb8s73fr2mp0 : service srv-d804prjrjlhs73a0m7qg https://backend-elevage-lczf.onrender.com, main/starter/Virginia, autoDeploy et migrations au démarrage ; pas de PostgreSQL inventorié dans cet espace. Il n'est pas la cible du mobile.

PITR3jours/export visible du 5octobre2026 et rétention≥7jours constatés dans le Dashboard 2J. Pas de téléchargement ni de restauration réelle ; l'existence de l'export n'est pas une preuve de récupérabilité. Incident 2J : une sortie automatique d'interface a affiché DATABASE_URL lors du remasquage. Aucun secret dans Git/rapports. Rotation coordonnée recommandée, non exécutée pendant cette mission ; ne jamais recopier l'URL pour diagnostiquer.

## Revue core0016–0031

| Migrations | Effet / risque à contrôler |
|---|---|
| 0016 | Dispositifs, memberships, génération et politique offline false ; index partiel one_active_primary_device ; DDL et index prennent des verrous. |
| 0017 | Bootstrap propriétaire enregistré→OWNER, autres utilisateurs de l'exploitation→OPERATEUR ; conserver liens historiques ambigus pour revue explicite. Reverse noop protège les memberships futurs. |
| 0018/0020 | Audit et déclarations originelles immuables en SQL PostgreSQL ; fonctions/triggers à restaurer. |
| 0019/0021/0022 | Transport/challenges, UUID/séquences/identités, outcomes/mappings et révision métier ; vérifier unicité et relations. |
| 0023 | Scan Payment/Lettrage puis float→numeric(12,2), création encaissement ; refuse négatif, non-fini, débordement ou vrai arrondi. Pas de correction automatique. ALTER TYPE peut réécrire les tables et prendre AccessExclusiveLock. |
| 0024 | Montant reçu/origine du paiement physique et DELETE protégés ; affectation/rapprochement peuvent évoluer sans modifier la somme reçue. |
| 0025/0026 | Décisions distinctes, append-only ; conserver historique et acteur. |
| 0027/0028 | Compensations et annulation motivée, originaux conservés ; cohérence et immuabilité des compensations. |
| 0029/0030/0031 | Réversions non-stock/origines et archivage client ; ne pas réécrire ni supprimer la déclaration d'origine. |

La répétition synthétique commence à0015 avec20 exploitations,60 utilisateurs,20 lots/ventes/lettrages et2000 paiements. Elle compare tous les montants et toutes les lignes après restauration, contraintes logiques et définitions (exception de représentation PostgreSQL du CHECK de statut, vérifiée par huit cas), fonctions/triggers et séquences. Échantillonnage des verrous chaque10ms et temps par migration. Ce corpus ne représente pas la volumétrie, la concurrence ou les données réelles ; aucun RTO production ne peut être déduit de ses durées.

## Déploiement futur, sous autorisation distincte

1. Figer SHAs et manifeste artefacts/API ; désigner responsable, fenêtre, RPO/RTO et coffre chiffré. Exiger CI verte et recette B10K, clé Android durable récupérée depuis deux copies.
2. Autoriser explicitement la sauvegarde de la base réelle et sa restauration contrôlée ; ne pas utiliser les données des éleveurs dans les tests synthétiques actuels. Vérifier les droits, rétention et accès au coffre. Export custom pg_dump18.4 avec TLS pour accès externe et credentials via PGPASSFILE/coffre, jamais en arguments. Chiffrer immédiatement, vérifier SHA256, inventaire versions/heures/schéma/rôles/privilèges/extensions. no-owner/no-acl nécessite de restituer les privilèges séparément.
3. Restaurer sur base isolée vide autorisée, application/jobs/notifications absents, credentials distincts, pas de lien vers production. pg_restore exit-on-error/single-transaction/no-owner/no-acl ; pas de clean sur une cible existante. Comparer données, UUID/auteurs, montants, FK, séquences, contraintes, triggers et refus de mutation ; prouver récupération puis chiffrement/conservation. Restauration réelle non effectuée.
4. Vérifier sur une base autorisée les données legacy avant0023, y compris montants incompatibles et ambiguïtés de membership. Toute anomalie impose rapprochement explicite, pas de fake ou d'arrondi silencieux. Planifier fenêtre et budget de verrouillage après estimation de volume.
5. Sous autorisation de changement Render, contrôler autoDeploy pour éviter une migration concurrente, séparer une migration unique surveillée du lancement des workers web. Vérifier les capacités du plan Free avant de choisir un mécanisme preDeploy/job/manuel ; aucun changement de commande effectué maintenant. Exiger verrou d'orchestration, identifiant de base et inventaire des migrations au début/fin, sans secrets.
6. Déployer backend compatible avec clients existants, offline_policy reste false. Smoke tests anciens parcours sur cible autorisée, permissions et montants. Déployer Web ensuite depuis source/Flutter/lockfile épinglés ; URL API explicite et cache/version contrôlés. Distribution APK durable seulement après manifeste signé vérifié et update sans perte.
7. Activation d'une unique exploitation pilote nécessite encore une autorisation distincte ; aucun rollout global implicite. Surveiller erreurs, conflits/rapprochements, duplications, stock, encaissements physiques et horloges, avec critères d'arrêt écrits.

## Retour arrière sans perdre les déclarations

Conserver les branches/SHA historiques et artefacts, mais ne pas présumer qu'ancien code et nouveau schéma sont compatibles. Ne pas renverser0023 vers float ou retirer les triggers pour résoudre un incident. Préférer correction en avant quand le schéma ou les opérations déjà créées l'imposent.

Si restauration nécessaire : suspendre les écritures/synchronisations selon procédure autorisée, inventorier originaux serveur depuis la sauvegarde et ceux en attente sur chaque tablette, préserver UUID/auteur/audit/encaissements. Restaurer vers une nouvelle base et rapprocher tous les écarts avant une bascule expressément autorisée. Une restauration à l'instant T ne récupère pas les déclarations postérieures à T ; PITR/RPO ne remplace pas ce rapprochement.

APK : même certificat et versionCode supérieur ; aucune désinstallation, pm clear, downgrade forcé ou suppression de PIN/Keystore. Une base locale chiffrée sans clés récupérables n'est pas un plan de sauvegarde.
