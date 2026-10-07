# Élev’Age — Fondations serveur Mobile 2B

Cette branche prépare le serveur. Elle ne contient aucun client offline, PIN,
Outbox, moteur de synchronisation ou endpoint TerrainSubmission. Les API métier
actuelles restent des API online sans nouveau mécanisme d’idempotence.

## Transition et identité

`User.exploitation` reste le contexte unique utilisé par les clients historiques.
La membership décrit les droits dans ce contexte ; elle ne permet pas de choisir
une autre exploitation dans une requête. `Exploitation.proprietaire` reste le
propriétaire principal. Le changement d’exploitation d’un compte existant est
refusé par `User.save()` ; un transfert devra faire l’objet d’un service dédié.

La migration 0017 crée OWNER à partir de `proprietaire` et OPERATEUR pour les
autres comptes déjà attachés à l’exploitation. Elle ne change aucun pointeur
historique, n’invente aucun auteur et n’active aucune politique. Une incohérence
historique doit être examinée avant activation. Revenir sur cette migration ne
supprime pas les memberships créées depuis.

La politique est opt-in par exploitation, `offline_policy_enabled=False`.
En mode compatible, les routes et écritures des anciens clients restent
accessibles. Une membership désactivée est refusée même dans ce mode.
Le passage au nouveau mode nécessite un principal dont la possession est prouvée
et un opérateur actif. Il est irréversible par l’API normale de cette phase.
Prévoir l’accompagnement des anciens clients avant de l’activer : ils ne savent
pas encore signer leurs écritures terrain.

## API et droits

Toutes les routes ci-dessous sont préfixées `/api/` et exigent un JWT utilisateur.

| Route | Méthodes | Droits / contrat |
|---|---|---|
| `me/capabilities/` | GET | Identité, membership, mode, capacités, principal, génération, durée offline |
| `memberships/` | GET, POST | OWNER uniquement ; créer un compte OPERATEUR dans sa propre ferme |
| `memberships/{id}/` | PATCH | OWNER ; `is_active`, `can_reconcile` ; version incrémentée, grants révoqués |
| `devices/` | GET, POST | OWNER ; enregistrer une installation PENDING avec clé publique Ed25519 PEM |
| `devices/challenge/` | POST | Membre actif ; `device_id`, `purpose` ; ACTIVATE/REPLACE réservés OWNER |
| `devices/{id}/activate/` | POST | OWNER + signature du nouveau dispositif ; échoue si un principal existe |
| `devices/{id}/replace/` | POST | OWNER + signature ; révoque l’ancien principal, incrémente la génération |
| `devices/{id}/revoke/` | POST | OWNER ; motif `reason` facultatif, 255 caractères maximum |
| `offline-policy/enable/` | POST | OWNER ; transition explicite après vérification des prérequis |
| `offline-authorizations/` | POST | OPERATEUR, mode activé, principal prouvé ; grant signé, durée configurée |
| `audit-events/` | GET | OWNER ; pagination 100, filtres entity_type/entity_id/action/category |
| `token/logout/` | POST | Utilisateur ; blacklist de son propre `refresh` |

Création opérateur : `username`, `email` facultatif, `password` validé par Django.
Ce parcours attribue l’exploitation avant la sauvegarde et ne crée aucune ferme
parasite. Aucun email n’est envoyé ; le parcours d’invitation par email et le
renouvellement obligatoire du mot de passe initial restent à concevoir.
Les propriétaires ne deviennent jamais staff/superuser.

En mode activé, OWNER lit métier et finances, gère comptes/appareils et planifie
les tâches. Il ne saisit aucune opération terrain ordinaire : achats, dépenses,
mouvements, naissance, clients, encaissement, collectes, ventes, alimentation,
pesées, suppressions/corrections. OPERATEUR saisit le terrain avec une preuve
du principal actif ; il ne gère ni comptes, ni appareils, ni Audit Trail.
Les contrôles s’appliquent au serveur, pas aux boutons du client.

Le superadmin Django reste une administration globale privilégiée. Ses
modifications métier sont auditées avec un decision_actor. Les fondations
membership/device/grant/audit sont en lecture seule dans cet admin et utilisent
les services API pour les transitions normales. Transfert de propriétaire et
changement direct de politique/génération sont désactivés dans les formulaires.

## Agenda

`title` et `date` gardent leur sens et restent acceptés. Champs ajoutés :
`description`, `priority` LOW/NORMAL/HIGH, `status` TODO/IN_PROGRESS/DONE/CANCELLED,
`assigned_to`, auteur, réalisation, compte rendu, timestamps et `version`.
`assigned_to=null` désigne une tâche générale de l’exploitation.

OWNER crée, modifie, attribue à un opérateur actif de sa ferme, annule, remet à
TODO et consulte. L’exécution IN_PROGRESS/DONE appartient aux opérateurs.
Jean voit ses tâches et les tâches générales ; Paul ne voit pas les tâches de
Jean. Un opérateur modifie seulement `status` et `report` : TODO → IN_PROGRESS
→ DONE, sans annulation ni réattribution. DONE accepte encore un compte rendu.
CANCELLED ne peut plus être modifiée par l’opérateur. Les tâches générales ont
un état partagé, pas une réalisation individuelle par opérateur.

La réalisation est horodatée par le serveur avec `completed_by` ; aucune date
réelle déclarée offline n’est encore acceptée. `expected_version` facultatif
permet de détecter une modification concurrente ; absence conservée pour les
anciens clients. Les mises à jour verrouillent la tâche et l’exploitation.
La suppression legacy reste compatible et auditée ; en mode activé, annuler
au lieu de supprimer. Aucune preuve de tablette n’est exigée pour l’agenda.

## Preuve de possession de l’appareil

Installation UUID aléatoire et clé Ed25519 ; aucun IMEI ni secret serveur côté
Android. La clé privée de l’appareil devra être protégée par Android en 2C.
Le challenge serveur est lié au dispositif, utilisateur, purpose et expire après
cinq minutes. Les purposes sont WRITE, ACTIVATE, REPLACE, GRANT.

Headers : `X-Elevage-Device`, `X-Elevage-Challenge`, `X-Elevage-Signature`.
La dernière valeur est la signature Ed25519 encodée en Base64 standard du message
UTF-8 suivant, sans saut de ligne final :

```text
ELEVAGE-DEVICE-V1
{challenge UUID}
{device id decimal}
{user id decimal}
{purpose}
{HTTP method uppercase}
{get_full_path() exact, query string included}
{SHA256 exact body bytes, lowercase hexadecimal}
```

Signer après sérialisation JSON et envoyer exactement ces octets. Un autre
corps, chemin, purpose, utilisateur ou challenge consommé ne convient pas.
Un challenge WRITE peut être consommé même si la validation métier échoue :
obtenir un nouveau challenge pour réessayer. Une transition/grant invalide
annule sa consommation avec sa transaction. Le `last_seen_at` est un heartbeat
opérationnel sans événement métier.

Les transitions verrouillent la ligne exploitation. Une contrainte partielle
en base interdit deux dispositifs ACTIVE + primary_writer pour une ferme ;
une contrainte impose qu’un primary_writer soit ACTIVE. Une installation révoquée
ne se réactive pas : enregistrer une nouvelle installation puis remplacer.
La révocation bloque les futures requêtes protégées et révoque les grants.
Le remplacement incrémente `write_generation` et rend les grants anciens invalides.

## Autorisation offline et auteur / transport

Configuration : `OFFLINE_AUTHORIZATION_DAYS=7` (1–30),
`OFFLINE_SIGNING_PRIVATE_KEY` PEM Ed25519, `OFFLINE_SIGNING_KEY_ID=offline-v1`.
Aucune clé n’est générée automatiquement, aucune valeur réelle n’est versionnée.
Sans clé correctement configurée, émission refusée avec 503.

L’autorisation conserve membership/device/capacités/rights_version/génération,
issued_at/expires_at/revoked_at. Le token EdDSA utilise issuer `elevage-offline`,
audience `elevage-device`, typ `offline-authorization`, jti UUID et sub auteur.
Il ne remplace pas le JWT login. Le client futur devra disposer d’une clé publique
serveur issue d’un provisioning HTTPS de confiance et vérifier algorithme,
issuer, audience, kid, dates et type. Ne pas faire confiance à une clé accompagnant
un token reçu d’une source inconnue. La rotation des clés et le provisioning
Android restent à préciser avant mise en service offline.

`validated_offline_context` est un service interne préparé, sans route d’ingestion.
Il reçoit un dispositif dont la preuve vient d’être vérifiée par le serveur,
contrôle le grant enregistré, les dates, appartenance active, rôle, version et
génération. Il retourne un `ActorContext` séparant author/device/transport/
decision actor ; il ne remplace jamais `request.user`. Ce service doit être appelé
dans la future transaction d’ingestion après verrouillage de l’exploitation.
Une autorisation expirée/révoquée est refusée ; le futur circuit « à vérifier »
et l’idempotence par operation_id ne sont pas implémentés ici.

## Audit et conservation

Scopes explicites et transactions, sans signaux : API métier principales,
agenda, administration et transitions de sécurité. `AuditedModel` capture
avant/après pendant ces scopes ; les créations FIFO en lot sont aussi capturées.
Les cascades de suppression et SET_NULL sont recensés par le Collector Django.
Un correlation_id relie les écritures composites. Une réponse d’erreur annule
les changements ; une panne d’audit annule l’opération.

Les événements conservent des IDs de contexte sans FK : suppression d’objet,
compte ou ferme ne réécrit pas leur histoire. Date métier actuelle dans les
snapshots, dates serveur dans received/applied ; business_occurred_at reste
disponible pour les futures déclarations. Pas de soft-delete généralisé.

Les champs secrets connus sont supprimés récursivement des snapshots. Les
headers de signature/JWT, tokens de refresh et clés privées ne sont jamais
transmis à l’audit. Payloads avant/après/metadata limités à 64 KiB chacun.
Les textes métier saisis librement restent des textes utilisateur : éviter
d’y copier des secrets. AuditEvent refuse update/delete/bulk_update/bulk_create ;
création interne validée uniquement, API et admin en lecture seule.
PostgreSQL ajoute un trigger rejetant UPDATE/DELETE. SQLite n’offre ici que les
protections applicatives. Les droits SQL et sauvegardes restent à configurer :
un propriétaire de base privilégié peut supprimer un trigger ou TRUNCATE.
Il ne s’agit pas d’une immutabilité cryptographique absolue.

Un service direct/shell hors scope n’est pas automatiquement audité : tout
nouveau parcours doit ouvrir un scope explicite. Dans un scope, queryset update
et bulk_update sont refusés pour éviter une écriture silencieuse. L’audit est un
journal de traçabilité, pas une reconstruction complète de l’état métier.

## JWT et migrations

Login et schéma access/refresh restent identiques : 60 minutes / 7 jours.
Rotation de refresh désactivée pour préserver les clients. Blacklist activée,
logout additionnel, refus de refresh pour compte/membership inactifs. La
désactivation blacklist les refresh déjà connus du serveur et révoque les grants.
Les vieux refresh créés avant l’installation de la blacklist peuvent ne pas
être enregistrés : le contrôle membership les refuse tant que le compte est
désactivé ; après réactivation ils peuvent rester utilisables jusqu’à expiration.
Les JWT access existants restent soumis aux contrôles de membership sur les
routes protégées. Un mécanisme de cutoff/auth_version global peut compléter
cette transition ultérieurement. Programmer `flushexpiredtokens` côté exploitation
du serveur avant usage intensif ; aucun job de production n’est créé ici.

Migrations core : 0016 schéma, 0017 bootstrap, 0018 trigger PostgreSQL.
La blacklist ajoute ses migrations tierces. Vérifier migration plan, sauvegarde,
comptes historiques et privilèges SQL avant toute application en production.
Aucun déploiement ni migration de production n’est autorisé par cette branche.

## Validation

Suite historique + tests roles/tenant/agenda/device/audit/grant/JWT et migrations.
`test_mobile_concurrency` teste deux activations via threads sur PostgreSQL.
`test_postgresql_audit_trigger` teste le rejet SQL direct. Ces deux tests sont
explicitement ignorés sur SQLite : une suite verte sur SQLite ne valide ni le
trigger PostgreSQL ni ses verrouillages concurrents.
