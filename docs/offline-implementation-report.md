# ÉLEV’AGE — Mission offline autonome

| Phase | Statut | Validation | Déployée |
|---|---|---|---|
| 2C | VALIDÉE | Android API 24, Flutter/Web/APK, preuve native contre PostgreSQL | Non |
| 2D | VALIDÉE | 139 tests Flutter, Web/APK/API 24 ; 197 tests par moteur serveur | Non |
| 2E | PARTIELLE | Clients et comptes rendus de tâches : validation en cours | Non |
| 2F–2I | NON COMMENCÉES | Portes suivantes | Non |

## Checkpoint serveur 2D

La déclaration TerrainSubmission conserve le payload original, l'auteur,
le membre, l'appareil, sa génération, la référence UUID du grant, les
dépendances, les dates et la version attendue. L'état métier appartient
à TerrainOutcome : recevoir ne signifie pas confirmer. Le transport
est authentifié par l'appareil avec un défi signé, indépendamment d'un
JWT personnel expiré ou du profil actuellement ouvert.

Migrations additives 0019–0020 : tables de transport/déclarations/résultats,
unicité exploitation/UUID et appareil/séquence, puis trigger PostgreSQL
refusant UPDATE/DELETE des déclarations originales. Aucun état de
production ni politique offline existante n'a été modifié.

PostgreSQL 17.11 local, 127.0.0.1:55437, bases elevage_phase2b1_local et
elevage_phase2b1_suite, cluster isolé vérifié : migrations, check,
makemigrations --check, audit SQL, unicité primary writer et suite complète
passent. 197 tests, 195 réussis et 2 skips propres à SQLite.
Deux requêtes simultanées du même UUID donnent une seule déclaration,
un seul résultat et un seul événement TERRAIN_RECEIVED. Les tests SQL
directs vérifient le refus P0001. Le serveur local est arrêté après les tests.

Le premier lancement PostgreSQL 2D avait un échec et une erreur : le
test de migration 2B restaurait seulement le schéma 0018 avant le test
concurrent, et l'assertion SQLSTATE utilisait uniquement le nom psycopg3
avec un pilote psycopg2. La fixture restaure maintenant tous les derniers
nœuds core sans changer la migration 2B testée ; l'assertion accepte les
deux noms de propriété et vérifie toujours P0001. La suite a été relancée
intégralement avec succès. Aucun test n'a été supprimé.

La suite SQLite finale compte 197 tests, 193 réussis et 4 skips PostgreSQL
attendus. Ces nombres décrivent le checkpoint serveur initial ; la clôture
mobile et l'ouverture de 2E sont documentées plus bas.
## Clôture 2D

Source serveur `eab29d096d3b72bad06fc1f36f415bd493553ccf`, source mobile
`930f4462cd3c380a630c04e0a7d5d860e234ebef` : phase 2D validée.
CI mobile `37620191629` verte : suite Flutter, analyse sans erreur/warning,
Web et APK, vraie API 24 avec file Jean/Paul réouverte et chiffrement natif.
61 infos de style/dépréciation visibles, dont 5 conseils d'accolades nouveaux.
Suites serveur 197 SQLite / 197 PostgreSQL de test passent selon les skips
spécifiques au moteur décrits ci-dessous. PR #7 et mobile #9 en brouillon.
Aucune application métier terrain n'est encore mise en œuvre à ce jalon.
Production, branches principales et politique réelle inchangées.

## Phase 2E — premier checkpoint clients et tâches, EN COURS

Migration additive 0021 : correspondance exploitation/type/UUID local vers
identifiant serveur. La réception immuable termine sa transaction avant
l'application métier ; une panne d'application laisse le reçu à reprendre.
L'application utilise une transaction et des savepoints : payload invalide,
version ou affectation de tâche divergente deviennent NEEDS_RECONCILIATION.
La création client et les états/comptes rendus de tâches gardent leur auteur,
leur date métier et leur provenance OFFLINE dans les événements d'audit.
Les dépendances reçues dans l'ordre inverse attendent leur parent confirmé.

22 tests ciblés initiaux passés (2 skips PostgreSQL) avant extension des
scénarios. Suites complètes SQLite/PostgreSQL et CI mobile en cours : ce
checkpoint ne valide pas la phase 2E entière. Les autres modules terrain,
ventes et paiements ne sont pas inclus à ce stade.

Première suite PostgreSQL de ce checkpoint : 207 tests, deux erreurs de
longueur des nouveaux codes de conflit dans AuditEvent.reason_code (30).
SQLite n'impose pas la longueur VARCHAR. Les codes ont été raccourcis sans
tronquer l'historique ni modifier le schéma d'audit. Les assertions vérifient
aussi l'événement d'audit correspondant. Relance complète en cours.
