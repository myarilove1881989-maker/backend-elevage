# ÉLEV’AGE — Mission offline autonome

| Phase | Statut | Validation | Déployée |
|---|---|---|---|
| 2C | VALIDÉE | Android API 24, Flutter/Web/APK, preuve native contre PostgreSQL | Non |
| 2D | PARTIELLE | Serveur validé ; validation mobile en cours | Non |
| 2E–2I | NON COMMENCÉES | Portes suivantes | Non |

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
attendus. La validation mobile 2D est en cours. Aucune phase 2E n'a démarré.
La phase 2D ne sera déclarée validée qu'après toutes ses portes mobiles.
