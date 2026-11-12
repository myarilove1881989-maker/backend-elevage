# Pisciculture — phase 1 locale

Base : `0f05f1b1aefda04ad24df0a9027e06977398a86d`. Aucune autorisation de publication.

## Modèle et sécurité

App `pisciculture` distincte de `core.Lot`. Installation, profil aquatique de l'espèce existante, stade, cohorte, position, commande, événement et lignes d'effectif. Positions reconstruisibles à partir du journal ; aucune écriture dans les finances, stocks d'aliments ou mouvements terrestres.

Le module est inactif en l'absence d'une Activity activée par son propriétaire. Droits HTTP : `aquatic.read`, `aquatic.installations.manage`, `aquatic.cohorts.manage`, `aquatic.transfers.create`. Ils sont attribuables aux opérateurs via l'administration existante et ne sont jamais incorporés aux anciens contrats HTTP fermés ou aux grants signés offline. Sous politique offline existante, l'opérateur doit aussi prouver sa tablette principale active avec le mécanisme de challenge existant. L'audit conserve auteur, tablette, commande et états.

Réserve future A1 : `Position.reserved_count`, non modifiable par l'API phase 1 ; disponible G−R. Clôture et désactivation refusées si effectif présent. Pas de fusion, mortalité, inventaire, correction de faits ou opération piscicole offline dans cette livraison. A2 et A5 restent des contrats de capacités préparés, pas des fonctions activées.

Événements et commandes append-only : protections ORM et triggers PostgreSQL. Les commandes sont uniques par exploitation/UUID ; même auteur et contenu retournent le résultat initial. Autre contenu =409. Verrou ferme puis positions ; contrôle des versions et de tous les préfixes du journal pour une date antérieure. Aucun demi-transfert ou demi-assistant après exception.

## Routes disponibles

Préfixe `/api/pisciculture/v1/`, JWT existant et exploitation dérivée de l'utilisateur.

| Route | Méthodes |
|---|---|
| capabilities/ | GET |
| activation/ | POST, propriétaire |
| installations/ | GET, POST |
| installations/{uuid}/ | GET, PATCH avec If-Match |
| species/ | GET, POST (profil d'une espèce existante ou nouvelle espèce propre à la ferme) |
| stages/ | GET, POST |
| cohorts/ | GET, POST |
| cohorts/{uuid}/ | GET, PATCH avec If-Match |
| positions/ | GET, as_of facultatif |
| transfers/ | POST |
| events/ | GET |
| dashboard/ | GET, filtres espèce/cohorte/installation et as_of |
| commands/{operation_uuid}/ | GET, résultat de l'auteur |
| onboarding/ | POST, installation + espèce + cohorte atomiques |

Toutes les mutations : `operation_uuid`, `schema_version:1`. Création d'installation/cohorte/stade/profil : UUID `id`. Installation et cohorte exigent `code` et `name`. Les anciens IDs d'espèces restent entiers. Effectifs entiers positifs, allocations totalisant exactement l'effectif initial, masses/capacité décimales, fuseau obligatoire pour les dates. Aucun DELETE métier.

Listes : `results`, `next_cursor`, `limit` 1..100, curseur lié au tenant/route/filtres. Historique trié par date et séquence et borné à un snapshot pour sa pagination. Les UUID de fait sont exposés séparément de la séquence interne PostgreSQL. Filtres : espèce/stade/statut pour cohortes, installation/cohorte pour positions, installation/cohorte/type/from/to pour événements. Une référence extérieure est refusée ; les réponses ne divulguent pas ses détails.

### Exemple de transfert

```json
{
  "operation_uuid":"20000000-0000-4000-8000-000000000001",
  "schema_version":1,
  "cohort":"30000000-0000-4000-8000-000000000001",
  "source":"10000000-0000-4000-8000-000000000001",
  "destination":"10000000-0000-4000-8000-000000000002",
  "count":150,
  "occurred_at":"2026-11-12T10:00:00+01:00",
  "reason":"Répartition",
  "expected_versions":{"cohort":1,"source_position":1,"destination_position":1}
}
```

Réponse 201 avec `event_id`, deux positions/version, `cohort_version`, total avant/après et `business_revision`. Si destination encore absente, version attendue 0. Rejeu : même statut et résultat, `Idempotent-Replay:true`. Erreurs de validation 400, droits 403, référence 404, conflit stock/version/timeline/UUID 409 ; PATCH sans précondition 428, version différente 412. Les erreurs du module comportent `error.code/message/fields/retryable` ; l'authentification préalable garde le contrat DRF existant.

Assistant : champs `installation`, `species`, `cohort`. Chaque objet contient les champs de sa création, sans enveloppe. `species` vaut soit `{existing_species: ID}` pour un profil aquatique actif, soit `{id: UUID, name: NOM}`. La cohorte contient les affectations dont le nouvel UUID d'installation. Les sous-commandes sont dérivées de l'UUID principal et leurs effets/recettes sont committés ensemble ; une erreur ne laisse aucune entité partielle. Aucun coût ni achat implicite.

## Migrations et garde

- core 0051 : élargissement exact de la contrainte des permissions, sans changer les droits historiques.
- pisciculture 0001 : nouvelles tables et contraintes, dépend de core 0051.
- pisciculture 0002 : triggers append-only des faits.

`pisciculture/migration_manifest.json` fixe les empreintes et l'ordre exact. La garde 0050 délègue au nouveau contrôle pour le schéma additionnel ; 0051 est reconnu seulement si toutes les migrations attendues sont appliquées, leurs fichiers intacts et aucun plan inattendu. Depuis 0050, absence de drapeau propre et d'empreinte de sauvegarde = refus sans écriture. Les gardes 0049→0050 et antérieures restent strictes. Une montée depuis 0049 exige la préparation historique 0050 dans son graphe approuvé ; aucun raccourci n'est activé ici.

La garde vérifie cible PostgreSQL, manifeste, registre, plan, verrous, délais et empreintes des colonnes historiques. Les migrations historiques à empreinte fixe sont extraites dans leurs octets Git exacts ; `.gitattributes` évite leur conversion automatique selon la plateforme. Aucune empreinte historique n'a été changée ni aucun contrôle désactivé.

Les tests n'emploient une empreinte de sauvegarde synthétique que sur leur cluster isolé. Ne pas reprendre cette valeur en production. Ne lancer aucune migration distante avec ce dossier. Une éventuelle publication exigera une mission séparée et une sauvegarde réelle vérifiée. Retrait : capacité désactivée, backend explicitement compatible avec le schéma ajouté ; pas de suppression de tables.

## Qualification reproductible

Depuis ce dépôt, `tools/dev.ps1 test -Postgres -Tests pisciculture.tests` utilise le lanceur synthétique existant. Dans cet hôte Windows, le lanceur extérieur `../qualify_backend.py` réutilise ce même outil avec un cluster propre au travail et le port 57847. Connexions bloquées hors boucle locale ; serveur arrêté après les tests. Dépendances de requirements.txt inchangées.

Tests : effectifs 1000=600+400 puis 450+550, plusieurs cohortes, stock/versions, rejeu et collision UUID, deux connexions réellement concurrentes, rollback, références étrangères, rôles/révocation, signature appareil, absence de poissons/finances terrestres, assistant atomique, pagination, chronologie, immutabilité et migrations historiques. Les tests de migrations historiques bornent leur graphe à leur livraison originale sans retirer leurs assertions ; le nouveau graphe a sa propre qualification.
