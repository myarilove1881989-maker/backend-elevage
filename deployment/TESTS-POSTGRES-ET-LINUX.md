# Validation suivante : PostgreSQL synthétique et Linux/Gunicorn

Ce document ne constitue aucune autorisation d'exécution. Aucune base PostgreSQL, instance Render, VM ou machine Linux n'a été créée pendant l'étape 3.

## Périmètre source figé

Backend candidat avec 37 migrations core, versions de requirements.txt inchangées et réconciliation V2 en lecture seule. Le graphe complet comporte 67 migrations, dont 12 token_blacklist. Depuis la référence historique supposée de 33 migrations (core 0015 et 18 migrations Django communes), le plan source prévoit 34 ajouts : 22 core et 12 token_blacklist. Cette référence n'a pas été relue sur la base principale et ne prouve pas son état actuel.

Le fichier offline-migrations.json contient le graphe, l'ordre proposé, les empreintes brutes et LF des 37 sources ; aucune identité de base réelle ni credential n'y figure. Les versions des dépendances doivent être les mêmes avant toute répétition.

## Autorisation PostgreSQL distincte requise

Nommer un répertoire neuf, un port loopback libre, les noms de bases synthétiques et l'exécutant. Utiliser uniquement des binaires déjà présents, comptes et mots de passe inventés, aucun dump réel ni ancien laboratoire. Ne pas lire d'environnement réel. Bloquer les sorties réseau hors loopback, empêcher un hostaddr différent, refuser toute base/cluster préexistant. Si une dépendance manque : arrêt sans installation ou téléchargement.

L'autorisation doit couvrir explicitement la création des bases éphémères, les migrations de leurs seuls schémas, les fixtures, les sauvegardes/restaurations synthétiques, leurs fichiers de clés jetables et le nettoyage précisément ciblé. Le refus de connexion PostgreSQL du banc actuel doit rester actif jusqu'à cette nouvelle autorisation.

## Matrice PostgreSQL à exécuter ensuite

| ID | Scénario | GO mesurable |
|---|---|---|
| PG01 | Charger le graphe complet hors DB ; vérifier les empreintes et zéro dérive des modèles sans traduction | 67 noeuds attendus, 37 core, aucun conflit ; plan explicite de 34 depuis la référence supposée |
| PG02 | Créer une base neuve ; migrer uniquement core 0015 et les feuilles auth/admin/contenttypes/sessions, sans token_blacklist | 33 entrées techniques attendues et absence des tables offline/JWT nouvelles |
| PG03 | Insérer via les modèles historiques des exploitations, propriétaires/opérateurs, stocks et montants fictifs | Identités, dates, auteurs, séquences et agrégats de référence conservés, offline_policy false |
| PG04 | Préflight monétaire 0023 sur valeurs limites, centimes, négatifs, débordements, NaN/Inf, valeur à arrondir | Valeurs finies de 0 à 9999999999.99 ; tolérance de représentation 0.0000001 uniquement ; refus avant conversion d'un cas invalide |
| PG05 | Préflight membres 0017/0032/0033, propriétaire absent/inactif/ambigu et utilisateur rattaché à une autre ferme | Diagnostic explicite ; aucune réparation, réattribution ou suppression silencieuse |
| PG06 | Exécuter les 34 migrations sur la seule base synthétique, un seul exécutant sous verrou coopératif de session | Plan final vide, 67 entrées attendues ; montants et historiques inchangés ; contraintes/index valides |
| PG07 | Relever les SQL, verrous, attentes, tailles, temps et sessions avant/pendant/après | Mesures consignées ; aucun seuil de durée production déduit du banc |
| PG08 | Éprouver audit/déclarations/reçus/décisions/compensations/annulations immuables avec rôle applicatif non superuser | UPDATE/DELETE interdits refusés par les triggers ; aucune réattribution d'auteur |
| PG09 | Éprouver deux écritures concurrentes, survente, double réception même UUID, réponse perdue après commit et reprise | Une seule application par original ; conflit conservé ; isolement inter-exploitations |
| PG10 | Permissions, signatures Ed25519/P-256, corps altéré, défi réutilisé, révocation sécurité et retraits BUSINESS | Refus attendus, aucune émission fine lorsque flag False, aucune tolérance de sécurité nouvelle |
| PG11 | Réconciliation V2 : lecture permise selon rôle, toutes décisions refusées même contrat inconnu/ancien client | V2_RECONCILIATION_READ_ONLY ; historique, effets, versions et audit inchangés |
| PG12 | Révocation JWT : création/blacklist/refresh/reconnexion et ancien token après changement de credential | Droits et sessions attendus ; aucun retour silencieux à l'ancien client ou grant |
| PG13 | Santé via alias dédié, rôle non superuser, SELECT 1, timeouts et PG indisponible | 200 exact ou 503 générique, connexion fermée, read-only effectif, DDL interdit, délais mesurés |
| PG14 | TLS : require/verify-ca/verify-full, CA inconnue et nom incorrect avec CA synthétique | verify-ca/full conservés sur les deux alias ; mauvais nom/CA refusés dans les modes correspondants |
| PG15 | Sauvegarder/restaurer uniquement les fixtures dans une deuxième base neuve | UUID/auteurs/contenus/effets/contraintes/triggers/séquences comparés ; récupération de la clé de sauvegarde démontrée |
| PG16 | Suite Django complète sur PostgreSQL, avec explication des échecs de politique OWNER et des anciens tests | Aucun échec masqué ; les 403 ne sont pas corrigés en abaissant les droits ; contrat/fixtures à arbitrer explicitement |

Les échecs historiques SQLite d'écriture propriétaire et de contrats d'affichage ne sont pas supposés résolus par PostgreSQL. Leur reproduction est un diagnostic requis. Les cas JSON contains/concurrence/contraintes SQL demandent réellement PostgreSQL. Le test de source ne remplace pas la comparaison du schéma physique.

Un verrou consultatif ne protège que les exécutants qui le prennent ; un migrate lancé autrement peut le contourner. L'ancien contrôleur P2-C, figé sur 0015, n'est pas intégré. Un futur contrôleur devra lier explicitement source, registre appliqué, identité de base approuvée, rôle, sauvegarde et exécutant unique, sans mode apply implicite ni branche --fake.

## Matrice Linux/Gunicorn

Après autorisation d'un hôte Linux identifié et déjà équipé : répertoire isolé, utilisateur non root, ressources et dépendances disponibles, aucune donnée réelle montée. Pas d'installation automatique de WSL/Docker/VM.

1. Vérifier les empreintes et versions Python/Django/Gunicorn/psycopg/libpq/OpenSSL. Utiliser uniquement un PostgreSQL synthétique autorisé.
2. Exécuter le vrai Gunicorn avec deployment/gunicorn.conf.py et config.wsgi:application : 1 worker gthread, 2 threads, bind 0.0.0.0:$PORT dans un réseau isolé, sans migration au build/start/preDeploy.
3. Prouver le refus de démarrage réel pour DB absente/invalide/SQLite et DEBUG=True. Une simple importation de settings n'est pas cette preuve.
4. Vérifier GET/HEAD santé (200 JSON exact / HEAD vide), erreur PG et reprise (503 puis 200), cookies absents, no-store et fermeture des connexions. Une sonde SELECT 1 valide seulement la connectivité, pas le schéma offline : le contrôle de migrations doit être passé séparément avant la disponibilité métier.
5. Éprouver le proxy HTTPS réel, l'écrasement des headers transmis, les headers falsifiés et les routes voisines de /api/health/. Les en-têtes injectés d'un test Django ne prouvent pas la frontière de confiance du proxy.
6. Éprouver CSRF : méthodes non sûres sans token 403, avec token valide 405 pour la santé ; aucune exemption globale.
7. Dépasser les deux threads par des sondes parallèles, ralentir/arrêter PG, mesurer latences/saturation/connexions et vérifier qu'une panne ne devient pas un faux 200.
8. Envoyer SIGTERM au maître pendant une requête, vérifier drainage, sortie, workers et sessions résiduels ; tester arrêt/reprise sans rejouer une opération métier. Le graceful_timeout local est 25 s ; son adéquation à la fenêtre de plateforme devra être validée.
9. Répéter les scénarios métiers et la suite PostgreSQL ; archiver les mesures et fermer uniquement les processus et ressources de fixture créés dans cette campagne.

La documentation actuelle indique qu'une santé HTTP Render peut accepter un 2xx ou 3xx en moins de cinq secondes. Notre recette doit exiger 200 et le contenu attendu, sans accepter une redirection comme validation. [Santé Render](https://render.com/docs/health-checks).

Render décrit un cycle build, éventuel pre-deploy, puis démarrage ; un redémarrage crée une nouvelle instance. Les contrôles de signaux et de migration séparée doivent donc être prouvés avant un futur changement de service. [Déploiements Render](https://render.com/docs/deploys).

## Passage ultérieur vers production et retour

Le principal backend-elevage est la seule cible conservée. Le secondaire lczf reste suspendu ; cette campagne ne le consulte ni ne le reprend. Aucune permission fine n'est activée. Les secrets effectifs, les deux identités de service et le registre réellement appliqué devront être relus seulement dans une mission ultérieure autorisée.

Avant toute opération réelle : sauvegarde fraîche restaurée dans une cible distincte, garde de clé indépendante, préflight des données, compatibilité des clients, gel des déclencheurs approuvé, plan de migrations unique et fenêtre validée. La signature et la garde des clés réelles relèvent d'un périmètre séparé.

Le retour applicatif doit être compatible avec le schéma après 0037 et utiliser des secrets encore valides, sans migrate au démarrage. Un ancien artefact Render peut réintroduire sa commande et ses paramètres historiques. Les triggers et conversions monétaires ne sont pas simplement des ajouts sans conséquence pour un ancien client. Aucun downgrade automatique, --fake, suppression/réinstallation Android, purge d'outbox ou retour à un secret révoqué.

Si les données sont altérées : arrêt des écritures selon procédure approuvée, restauration dans une nouvelle base, rapprochement des écritures postérieures et décision explicite de bascule. La sauvegarde P1 historique ne couvre pas les nouvelles écritures. Aucun ancien UUID Jean ou appareil LAB révoqué ne doit entrer dans les fixtures.

## Décision

GO pour préparer/autoriser une campagne PostgreSQL synthétique de diagnostic avec le périmètre ci-dessus. La qualification de livraison reste NO-GO tant que les échecs métier/politique et les contrôles PostgreSQL/Linux/Android ne sont pas résolus. STOP après l'étape 3 ; aucune ressource n'est créée par ce plan.
