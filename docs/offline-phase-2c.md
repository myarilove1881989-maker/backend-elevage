# Phase 2C — compatibilité de l'identité Android

État initial backend : `c3c5ff455ac01be61fd6992d1310368a34d0b0a2`.
Branche : `feature/offline-phase-2c`. Aucun accès à la production.

## Adaptation du protocole existant

Le contrat signé `ELEVAGE-DEVICE-V1` reste inchangé. La clé publique PEM
enregistrée détermine l'algorithme : Ed25519, ou P-256 avec ECDSA/SHA-256
et signature ASN.1 DER compatible `SHA256withECDSA` Android Keystore.
RSA, P-384 et les autres courbes sont refusés. La clé privée Android ne
doit jamais être exportée. Les grants serveur restent signés Ed25519.

Référence : https://developer.android.com/reference/android/security/keystore/KeyGenParameterSpec

## Régression, 7 octobre 2026

- 4 tests dédiés : activation P-256, replay refusé, mauvaise clé/corps
  modifié refusés, algorithmes exclus et enregistrement P-256.
- SQLite : 183 tests, 181 réussis, 2 skips PostgreSQL attendus.
- PostgreSQL 17.11, cluster local de travail, host 127.0.0.1:55437,
  bases `elevage_phase2b1_local` et `elevage_phase2b1_suite` :
  183 tests, 181 réussis, 2 skips de contrôles SQLite attendus.
- Migrations core 0016–0018 et token_blacklist appliquées ; check et
  makemigrations --check passent. Aucune nouvelle migration.
- SQL direct : UPDATE/DELETE AuditEvent refusés par trigger 0018 (P0001).
  Deuxième primary ACTIVE refusé par contrainte (23505) ; concurrence verte.
- Serveur PostgreSQL temporaire arrêté après validation.

Une preuve publique produite par le vrai Keystore Android API 24 a ensuite
été soumise à l'API Django sur le même cluster PostgreSQL local isolé :
activation acceptée, corps altéré refusé, défi réutilisé refusé. La clé
privée n'a pas été exportée. La suite PostgreSQL complète (183 tests,
181 réussis et 2 skips SQLite attendus), les migrations et les contrôles
SQL ont tous été relancés avec succès ; le serveur local a été arrêté.

Le premier lancement SQLite échouait sur deux tests d'achat à cause d'un
ancien print avec emoji non encodable sur une sortie Windows cp1252.
Les deux prints de débogage de l'endpoint achat ont été retirés ; la suite
SQLite a été relancée intégralement. Aucun test n'a été désactivé.

## Limites du checkpoint

Le cache en lecture seule expose des pages de 50 éléments par défaut
(maximum 200), filtrées par exploitation. Les opérateurs reçoivent leurs
tâches et les tâches générales. Le stock confirmé est agrégé côté serveur
sans requête supplémentaire par lot. Six tests couvrent pagination,
isolation, droits, coût des requêtes et refus des écritures.

Le socle 2C est maintenant validé au checkpoint mobile
`9e5937846340e09e51c3c287b5de576e0bb02867`, workflow `37613201556` :
126 tests Flutter, Web et APK release, parcours personnel Jean/Paul sur
API 24 x86_64, Keystore dans deux processus et fichier Android illisible
par SQLite standard. La signature native est acceptée par cette API
PostgreSQL locale ; corps altéré et rejeu sont refusés.
La phase 2D peut commencer. Aucun merge de production, déploiement,
migration production ou activation offline n'a eu lieu. Les phases
suivantes et toutes les portes finales restent nécessaires.
