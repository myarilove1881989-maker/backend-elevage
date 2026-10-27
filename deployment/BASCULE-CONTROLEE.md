# Ã‰LEVâ€™AGE â€” DÃ©roulement de bascule prÃ©parÃ©, non exÃ©cutÃ©

22 octobre 2026. Candidat Ã©tape 7. **NO-GO production tant que les portes ci-dessous ne sont pas levÃ©es.** Aucun chantier Linux local. Les commandes sont des instructions futures ; leur prÃ©sence ne vaut pas autorisation dâ€™exÃ©cution.

## 0. Cibles, responsables et conditions dâ€™ouverture

Principal exclusif : `srv-d7sdmucm0tmc73cvc6u0`, My Workspace, `https://backend-elevage.onrender.com`. Secondaire `backend-elevage-lczf` : suspendu, ne pas modifier, rÃ©veiller ni rÃ©utiliser. Base inventoriÃ©e : `dpg-da2spvv40ujc73avuefg-a`, PostgreSQL 18.4, 33 migrations. Confirmer la correspondance au DATABASE_URL rÃ©ellement utilisÃ© en comparant identitÃ©/hÃ´te sans afficher mot de passe ni URL complÃ¨te.

Dernier audit du 22 octobre, non rafraÃ®chi dans cette Ã©tape : principal free, autoDeploy sur main, start affichÃ© avec migrate ; logs du dÃ©ploiement live incluant aussi deux nettoyages. DÃ©ploiement live `dep-db21j3vlot8c73dfv3d0`, commit `90ce6fe3bd1272c3603776eb22d20bd16455b4b7`. Lâ€™Ã©tat courant sera relu avant toute opÃ©ration. Un redÃ©marrage/rÃ©veil peut rÃ©exÃ©cuter sa commande attachÃ©e : ne pas lâ€™utiliser pour tester le candidat.

Ã€ renseigner et approuver avant ouverture : dÃ©cideur, opÃ©rateur unique, identifiant de livraison et SHA du commit, fenÃªtre, RPO maximal, RTO maximal, durÃ©e de gel acceptable, hÃ´tes/DB/rÃ´les vÃ©rifiÃ©s, emplacement chiffrÃ© de sauvegarde, durÃ©e de conservation, cible de restauration, environnement dâ€™exÃ©cution des migrations, artefact de repli testÃ©. Toute valeur manquante = STOP. Les rÃ´les et services libpq citÃ©s ci-dessous sont des noms de prÃ©paration, pas des ressources existantes.

## 1. Autorisations distinctes

| Porte | Autorisation et preuve exigÃ©es |
|---|---|
| A â€” laboratoire | Environnement Render/Linux distant prÃ©cisÃ©ment identifiÃ©, quotas/coÃ»ts approuvÃ©s, transfert/build de sources, bases synthÃ©tiques et tests autorisÃ©s. Aucun achat prÃ©sumÃ©, aucune production accessible. |
| B â€” accÃ¨s diagnostic | IdentitÃ© de la base et rÃ´le de lecture autorisÃ©s ; exÃ©cution des scans de montants/propriÃ©taires avec limites, aucune rÃ©paration. La crÃ©ation Ã©ventuelle dâ€™un rÃ´le nÃ©cessite son propre accord DB. |
| C â€” sauvegarde/restauration | Export de donnÃ©es rÃ©elles, emplacement chiffrÃ© et ACL, cible distincte, comptes/mÃ©dias/clefs nÃ©cessaires, restauration et rÃ©pÃ©tition sur copie expressÃ©ment autorisÃ©s. |
| D â€” bascule | Liste exacte des modifications du principal, dÃ©sactivation autoDeploy, maÃ®trise hooks/CI, gel effectif des Ã©critures, push du SHA approuvÃ©, migrations uniques et dÃ©ploiement explicitement autorisÃ©s. Aucun changement secondaire. |
| E â€” incident | Retour applicatif dÃ©jÃ  testÃ© ou restauration vers nouvelle cible et rapprochement des Ã©critures ; dÃ©cision du propriÃ©taire sur RPO/RTO. Pas de restauration automatique. |
| F â€” APK | Build/signature puis installation sÃ©parÃ©ment autorisÃ©s, versionCode et certificat validÃ©s, appareil propre dÃ©signÃ©. |

## 2. Paquet et qualification distante

VÃ©rifier les SHA-256 du paquet et son inventaire avant extraction. Lâ€™archive backend contient sa racine directement (`manage.py`, `config`, `core`, `deployment`) ; rootDir Render vide si seule cette archive constitue le dÃ©pÃ´t. Dans un dÃ©pÃ´t backend+mobile, choisir explicitement `backend` et vÃ©rifier toutes les commandes depuis cette racine. Ne pas mÃ©langer les deux dispositions.

Le paquet nâ€™est ni un commit poussÃ© ni un build qualifiÃ©. Il ne contient pas de `.validation`, fixtures/tests, DB, donnÃ©es sauvegardÃ©es, caches, keystore ou certificat LAB. Le paquet mobile vise uniquement production ; les flavors LAB ne sont pas livrables avec cette sÃ©lection.

Dans le laboratoire autorisÃ©, sans secret rÃ©el ni accÃ¨s Ã  la production : installer depuis les dÃ©pendances Ã©pinglÃ©es, relever OS/Python/paquets/PostgreSQL, reproduire les migrations et la restauration synthÃ©tiques, utiliser `sh deployment/render-start.sh`, vÃ©rifier Gunicorn gthread (1 worker, 2 threads), dÃ©passement de capacitÃ© des deux threads, connexions, timeouts, SIGTERM en charge, reprise et UUID rÃ©pÃ©tÃ©. Rejouer les contrÃ´les sÃ©curitÃ©/offline et la suite qualifiÃ©e depuis le candidat de test, pas depuis lâ€™archive runtime qui exclut les tests. Aligner PostgreSQL 18.4 ; documenter tout Ã©cart.

Aucun essai Gunicorn sur le principal ni son pre-deploy. Le service gratuit ne fournit pas le pre-deploy nÃ©cessaire Ã  un migrateur dÃ©diÃ© ; choisir un exÃ©cutant distant dÃ©jÃ  autorisÃ©, sans acheter de plan implicitement. Sans laboratoire disponible, rester BLOCKED. Le propriÃ©taire peut arbitrer un risque rÃ©siduel, mais cela ne rend pas les tests non exÃ©cutÃ©s PASS.

## 3. Diagnostics prÃ©parÃ©s, avant toute migration

`read_only_preflight.py` ne charge pas Django, ne prend que des identifiants de cible explicites et un service libpq protÃ©gÃ©. Password/CA/passfile restent hors paquet. Il impose TLS verify-full, transaction repeatable-read/read-only, rollback et fermeture, requÃªtes de cinq secondes, verrou dâ€™une seconde, budget global de 60 secondes et limite dâ€™un million de lignes par table par dÃ©faut. Aucun identifiant mÃ©tier ni montant individuel nâ€™est imprimÃ©. Le rÃ´le superuser/BYPASSRLS est refusÃ© ; RLS susceptible de masquer des lignes provoque une erreur. Tout code de sortie non nul = STOP ; 0 nâ€™est un PASS que pour la phase demandÃ©e.

Les variables ci-dessous doivent Ãªtre renseignÃ©es Ã  partir des cibles approuvÃ©es. Dans chaque nouvelle session, vÃ©rifier quâ€™elles existent ; aucun secret dans lâ€™historique shell. Commandes de rÃ©fÃ©rence pour lâ€™exÃ©cutant futur, depuis la racine backend :

```sh
set -eu
: "${AUDIT_SERVICE:?service libpq approuvÃ© requis}"
: "${DB_HOST:?hÃ´te vÃ©rifiÃ© requis}"
: "${DB_NAME:?base vÃ©rifiÃ©e requise}"
: "${AUDIT_USER:?rÃ´le lecture vÃ©rifiÃ© requis}"
python -B deployment/read_only_preflight.py --phase baseline --service "$AUDIT_SERVICE" --host "$DB_HOST" --database "$DB_NAME" --user "$AUDIT_USER"
```

`baseline` exige exactement les 33 noms historiques, contrÃ´le les montants et les propriÃ©taires rÃ©fÃ©rencÃ©s. 0023 : `Decimal(str(value))`, fini, 0..9999999999.99, Ã©cart au centime â‰¤ 0.0000001. Aucun arrondi correctif. NaN/infini/NULL/invalide/nÃ©gatif/dÃ©passement = STOP. Les float Python sont conservÃ©s ; ne pas remplacer cette logique par un cast SQL approximatif.

Ã€ 0015, les memberships nâ€™existent pas. Le contrÃ´le des propriÃ©taires historiques indique seulement les prÃ©conditions du bootstrap 0017. Il arrÃªte aussi sur compte propriÃ©taire inactif, une vÃ©rification opÃ©rationnelle supplÃ©mentaire distincte du prÃ©dicat 0032. La migration 0032 vÃ©rifie lâ€™activitÃ© de lâ€™adhÃ©sion OWNER, pas directement celle du compte utilisateur. `pre0032`, exÃ©cutÃ© Ã  0031, exige exactement une adhÃ©sion OWNER par exploitation, y compris les OWNER inactifs dans le dÃ©compte, correspondant au propriÃ©taire et active. Aucun transfert de propriÃ©tÃ© ou rÃ©paration automatique.

Avant scans rÃ©els : tester le connecteur/timeout/lecture seule et privilÃ¨ges sur une base synthÃ©tique. Les tests Windows de cette Ã©tape utilisent des doubles de connexion et SQLite mÃ©moire ; ils ne constituent pas un essai PostgreSQL rÃ©el de ce nouvel outil.

## 4. Sauvegarde fraÃ®che, rÃ©pÃ©tition et retour prÃ©parÃ©

AprÃ¨s porte C seulement. Inventorier les sauvegardes/PITR disponibles sans prÃ©sumer leur couverture. Inventorier mÃ©dias/fichiers externes : la base ne couvre pas les fichiers dâ€™un service Ã©phÃ©mÃ¨re. PrÃ©parer hors dÃ©pÃ´t une conservation sÃ©curisÃ©e des clÃ©s de signature et de configuration existantes ; aucune rotation ni secret dans le rapport. Relever versions `pg_dump --version` et `pg_restore --version` ; utiliser outils majeurs 18, idÃ©alement 18.4, validÃ©s par restauration.

Choisir une premiÃ¨re copie de rÃ©pÃ©tition approuvÃ©e, puis un dump final aprÃ¨s gel effectif de tous les writers. Un pg_dump est cohÃ©rent mÃªme en activitÃ©, mais ne contient pas les Ã©critures postÃ©rieures Ã  son snapshot. Le gel reste nÃ©cessaire Ã  la stratÃ©gie de bascule retenue.

Commandes futures, destination privÃ©e approuvÃ©e, jamais le candidat ni Git :

```sh
set -eu
: "${BACKUP_SERVICE:?service source vÃ©rifiÃ© requis}"
: "${RESTORE_SERVICE:?service de cible NEUVE vÃ©rifiÃ©e requis}"
: "${BACKUP_FILE:?chemin chiffrÃ© approuvÃ© requis}"
test ! -e "$BACKUP_FILE" || exit 2
pg_dump --dbname="service=$BACKUP_SERVICE" --format=custom --lock-wait-timeout=5s --file="$BACKUP_FILE"
# STOP sur code non nul ou avertissement non expliquÃ© ; journal stderr privÃ©.
sha256sum "$BACKUP_FILE"
pg_restore --list "$BACKUP_FILE"
# VÃ©rifier encore identitÃ© et vacuitÃ© de RESTORE_SERVICE avant la commande suivante.
pg_restore --dbname="service=$RESTORE_SERVICE" --exit-on-error --single-transaction --no-owner --no-acl "$BACKUP_FILE"
```

Ni `--clean` ni `--create` : cible neuve prÃ©parÃ©e sÃ©parÃ©ment, impossible de restaurer Ã  lâ€™aveugle dans le nom embarquÃ© dans lâ€™archive. `--no-owner --no-acl` est un choix pour la copie isolÃ©e : il ne prouve pas la restauration des privilÃ¨ges de production. PrÃ©parer, approuver et vÃ©rifier une correspondance de rÃ´les et permissions Ã  part ; pas de dump de secrets de rÃ´les dans le dossier public. Le libpq de backup/restore ne doit pas hÃ©riter dâ€™une destination gÃ©nÃ©rale PGHOST/PGDATABASE inattendue ; comparer ses paramÃ¨tres rÃ©solus de maniÃ¨re expurgÃ©e avant usage.

VÃ©rification de restauration : codes et avertissements, schÃ©ma et registre exacts, listes et dÃ©finitions de tables/colonnes/types/defaults/nullabilitÃ©, index, contraintes, fonctions, triggers actifs et sÃ©quences. Comparer nombres de lignes et empreintes canoniques par table, UUID/auteurs, montants et agrÃ©gats mÃ©tier, sans exporter les lignes dans les preuves. DÃ©finir sÃ©rialisation canonique et ordre stable avant de comparer ; analyser toute diffÃ©rence de cast au lieu de lâ€™ignorer. VÃ©rifier positions des sÃ©quences sans nextval sur source. Tester insert/refus de mutation et idempotence avec fixtures inventÃ©es sur cible autorisÃ©e, sans les rÃ©pliquer en production. Mesurer durÃ©e totale et vÃ©rifier RTO.

Sur la copie rÃ©pÃ©tÃ©e : rejouer la procÃ©dure complÃ¨te de migrations, prÃ©flights, contraintes et clients ; tester application de repli contre le schÃ©ma 0037. AprÃ¨s rÃ©pÃ©tition et avant bascule, gel des Ã©critures, nouvelle sauvegarde fraÃ®che, restauration et contrÃ´les de cette sauvegarde finale. Si le temps dÃ©passe la fenÃªtre acceptÃ©e : STOP, ne pas sauter la restauration. Reprendre une sauvegarde si des Ã©critures ont repris.

## 5. MaÃ®triser les dÃ©marrages et geler les Ã©critures

AprÃ¨s porte D seulement : relire Ã©tat, logs, config courante et config attachÃ©e au live. DÃ©sactiver autoDeploy du principal et maÃ®triser les dÃ©clencheurs CI/hooks avant tout push ; vÃ©rifier absence de dÃ©ploiement en cours ou en file. Ne pas toucher au secondaire : vÃ©rifier sa suspension en lecture seule. La suspension ne prouve pas quâ€™aucun tiers ne peut le rÃ©activer ; rÃ©server la fenÃªtre avec son propriÃ©taire et STOP sâ€™il devient actif.

PrÃ©parer une transition sans effets de dÃ©marrage qui soit compatible avec 0015, si nÃ©cessaire, avant migration du schÃ©ma. Cette Ã©tape 7 ne fournit pas un ancien build rÃ©Ã©crit : il faut qualifier cette transition sÃ©parÃ©ment. Un changement enregistrÃ© du start ne garantit pas quâ€™un simple restart lâ€™appliquera. Le live historique contient des nettoyages ; ne pas supposer quâ€™il constitue un repli sÃ»r.

Ã‰tablir et Ã©prouver un mÃ©canisme bloquant rÃ©ellement toutes les Ã©critures : utilisateurs, synchronisation/outbox, admin, scripts, jobs, autres connexions. Une annonce de maintenance ne suffit pas. Ne pas supposer quâ€™un mode maintenance Render gratuit ou une suspension du principal rÃ©sout automatiquement ce problÃ¨me. Choisir le mÃ©canisme exact et faire approuver ses effets ; sans gel dÃ©montrÃ©, STOP. Ne pas lancer de DDL pendant que lâ€™ancien backend continue Ã  Ã©crire selon son ancien contrat.

## 6. ExÃ©cutant unique et migrations par points de contrÃ´le

Un opÃ©rateur, un processus de migration Ã  la fois, une base identifiÃ©e, autoDeploy neutralisÃ© et writers bloquÃ©s. Les commandes sont rÃ©servÃ©es Ã  lâ€™exÃ©cutant autorisÃ© avec environnement de production vÃ©rifiÃ© et accÃ¨s migrateur distinct de lâ€™auditeur. DATABASE_URL est injectÃ© par stockage sÃ©curisÃ© ; ne jamais le recopier dans le terminal ou le rapport. Pas de shell de production pour y lancer des fixtures.

Verrou coopÃ©ratif complÃ©mentaire : dans une session psql **persistante**, sur la base exacte, `SELECT pg_try_advisory_lock(20261021, 7);` doit renvoyer true. Garder cette session ouverte jusquâ€™Ã  la fin ; ne pas utiliser un `psql -c` qui fermerait immÃ©diatement le verrou. Tous les opÃ©rateurs doivent respecter ce mÃªme verrou ; il ne bloque pas un Django migrate non coopÃ©ratif et ne remplace donc pas lâ€™exclusivitÃ© vÃ©rifiÃ©e. Si la session tombe ou si le verrou renvoie false : STOP. LibÃ©rer Ã  la fin via `SELECT pg_advisory_unlock(20261021, 7);` puis fermer. Ce verrou de session est une opÃ©ration future, non prise aujourdâ€™hui.

Pour chaque groupe : afficher le plan, comparer les noms au graphe, approuver, exÃ©cuter une seule fois, relever code et dernier registre commis. Les commandes suivantes sont sÃ©quentielles, jamais un script de relance automatique :

```sh
set -eu
python manage.py migrate core 0022 --plan --settings=config.settings_production
# Attendu depuis 0015 : 0016..0022 (7 core), dÃ©pendances dÃ©jÃ  prÃ©sentes.
python manage.py migrate core 0022 --noinput --settings=config.settings_production
python -B deployment/read_only_preflight.py --phase pre0023 --service "$AUDIT_SERVICE" --host "$DB_HOST" --database "$DB_NAME" --user "$AUDIT_USER"
# STOP si diagnostic != 0, aucune correction automatique.
python manage.py migrate core 0031 --plan --settings=config.settings_production
python manage.py migrate core 0031 --noinput --settings=config.settings_production
python -B deployment/read_only_preflight.py --phase pre0032 --service "$AUDIT_SERVICE" --host "$DB_HOST" --database "$DB_NAME" --user "$AUDIT_USER"
# STOP si diagnostic != 0, conserver le schÃ©ma atteint et dÃ©cider.
python manage.py migrate core 0037 --plan --settings=config.settings_production
python manage.py migrate core 0037 --noinput --settings=config.settings_production
python manage.py migrate token_blacklist --plan --settings=config.settings_production
python manage.py migrate token_blacklist --noinput --settings=config.settings_production
python manage.py migrate --check --settings=config.settings_production
python -B deployment/read_only_preflight.py --phase final --service "$AUDIT_SERVICE" --host "$DB_HOST" --database "$DB_NAME" --user "$AUDIT_USER"
```

Ordre rÃ©el respectÃ© : 0023 aprÃ¨s 0022 ; 0032 aprÃ¨s 0031 et bootstrap 0017. Total attendu 22 core + 12 token_blacklist = 34 ajouts, 67 entrÃ©es finales. Token_blacklist saute le numÃ©ro 0009 dans la version Ã©pinglÃ©e. Les commandes Django partielles peuvent exÃ©cuter post_migrate : les rÃ©pÃ©ter telles quelles en laboratoire, ne pas supposer quâ€™elles nâ€™Ã©crivent que django_migrations.

Fixer les timeouts DDL depuis les mesures de rÃ©pÃ©tition, avec lock_timeout court et durÃ©e globale approuvÃ©e ; ne pas rÃ©utiliser le timeout de cinq secondes des diagnostics pour le DDL. Une migration peut Ãªtre atomique sans rendre toute la chaÃ®ne atomique. Sur erreur, blocage, incohÃ©rence ou durÃ©e dÃ©passÃ©e : STOP, relever Ã©tat et transaction, dÃ©cision opÃ©rateur ; aucune relance aveugle, `--fake`, modification des anciennes migrations ni migration inverse.

Le diagnostic final vÃ©rifie registre et six noms de triggers actifs, pas toute leur dÃ©finition ni tout le schÃ©ma. Le contrÃ´le physique approfondi et les comparaisons de la copie restaurÃ©e restent obligatoires avant GO.

## 7. DÃ©marrage, santÃ© et ouverture des clients

RÃ©glages proposÃ©s : voir render-settings.json et production-environment.md. Start `sh deployment/render-start.sh` (exec Gunicorn, aucun migrate/cleanup/audit). Build collectstatic explicitement sous settings_production. PrÃ©-dÃ©ploiement vide. SantÃ© `/api/health/`. Confirmer les valeurs de sÃ©curitÃ© rÃ©elles avant dâ€™appliquer ; une ancienne ALLOWED_HOSTS=* est dÃ©sormais refusÃ©e.

DÃ©ployer manuellement le SHA approuvÃ© sur le principal seulement aprÃ¨s portes prÃ©cÃ©dentes. VÃ©rifier identifiant du nouveau deploy, succÃ¨s live, commande et version rÃ©ellement exÃ©cutÃ©es. Ne pas utiliser Â« latest commit Â» sans contrÃ´ler son SHA. Garder les Ã©critures fermÃ©es durant validation. Lâ€™absence de downtime Render nâ€™est pas une garantie de compatibilitÃ© de schÃ©ma.

AprÃ¨s autorisation de rÃ©veil du service :

```sh
set -eu
python -B deployment/check_health.py --origin https://backend-elevage.onrender.com
```

Deux requÃªtes seulement, GET et HEAD, HTTPS vÃ©rifiÃ©, pas de redirection suivie, cinq secondes par opÃ©ration rÃ©seau, corps bornÃ©, 200 exact et GET `{"status":"ok"}`. Un dÃ©marrage Ã  froid gratuit peut Ã©chouer au premier essai : journaliser, attendre selon fenÃªtre approuvÃ©e, vÃ©rifier logs et readiness, ne pas appeler en boucle ni augmenter silencieusement les seuils. Le timeout rÃ©seau nâ€™est pas un chronomÃ¨tre global absolu en cas de flux lent ; ce petit endpoint doit rÃ©pondre immÃ©diatement.

Ce PASS santÃ© confirme seulement la connexion DB. Exiger sÃ©parÃ©ment registre/contraintes/versions, clÃ©s et signature vÃ©rifiÃ©es sans les exposer, flags fins False, flux clients compatibles, absence dâ€™erreur inattendue et marge de ressources mesurÃ©e. Tester des Ã©critures mÃ©tier en production exige un pÃ©rimÃ¨tre explicitement approuvÃ© ; ne pas inventer de fixtures de production pour valider le dÃ©ploiement. Ouvrir par groupe pilote autorisÃ©, observer selon fenÃªtre et seuils approuvÃ©s, puis Ã©largir. Toute double application, dÃ©rive dâ€™auteur/montant, fuite inter-exploitation, 5xx inattendu ou readiness incohÃ©rente = arrÃªt dâ€™ouverture et dÃ©cision dâ€™incident.

## 8. Retour et clÃ´ture

Retour applicatif seulement vers un artefact retenu, avec start sans effets, secrets valides et compatibilitÃ© avec le schÃ©ma atteint dÃ©montrÃ©e. Un rollback Render rÃ©utilise notamment start/env/sonde de sa cible ; ne pas se fier au start affichÃ© aujourdâ€™hui. La disponibilitÃ© dâ€™un ancien artefact doit Ãªtre contrÃ´lÃ©e avant migration. Les limites du service gratuit rendent la rÃ©tention de retour particuliÃ¨rement importante.

Si retour applicatif incompatible : conserver gel, prÃ©server Ã©critures post-snapshot, restaurer vers une nouvelle base autorisÃ©e, contrÃ´ler et rapprocher avant bascule approuvÃ©e. Aucun Ã©crasement in-place, downgrade, --fake, purge dâ€™outbox ou rÃ©activation dâ€™une clÃ©/appareil rÃ©voquÃ©. RPO/RTO et perte Ã©ventuelle relÃ¨vent du propriÃ©taire.

ClÃ´turer uniquement aprÃ¨s dÃ©cision Ã©crite : SHA/appareil/version, registre, diagnostic final, empreinte sauvegarde et restauration testÃ©e, observations, configuration autoDeploy finale volontaire. Le secondaire reste suspendu. APK soumis Ã  sa propre porte F.

## RÃ©fÃ©rences vÃ©rifiÃ©es pendant la prÃ©paration

[Render : dÃ©ploiements](https://render.com/docs/deploys), [santÃ©](https://render.com/docs/health-checks), [rollback](https://render.com/docs/rollbacks), [limitations gratuites](https://render.com/docs/free). Le plan gratuit comporte rÃ©veils et limitations ; aucun changement de plan nâ€™est engagÃ©.

[PostgreSQL 18 : pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html) et [pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html). Un dump de base ne couvre pas tous les objets globaux ; la restauration doit Ãªtre rÃ©ellement vÃ©rifiÃ©e sur cible distincte.
