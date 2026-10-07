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
- SQLite : 177 tests, 175 réussis, 2 skips PostgreSQL attendus.
- PostgreSQL 17.11, cluster local de travail, host 127.0.0.1:55437,
  bases `elevage_phase2b1_local` et `elevage_phase2b1_suite` :
  177 tests, 175 réussis, 2 skips de contrôles SQLite attendus.
- Migrations core 0016–0018 et token_blacklist appliquées ; check et
  makemigrations --check passent. Aucune nouvelle migration.
- SQL direct : UPDATE/DELETE AuditEvent refusés par trigger 0018 (P0001).
  Deuxième primary ACTIVE refusé par contrainte (23505) ; concurrence verte.
- Serveur PostgreSQL temporaire arrêté après validation.

Le premier lancement SQLite échouait sur deux tests d'achat à cause d'un
ancien print avec emoji non encodable sur une sortie Windows cp1252.
Les deux prints de débogage de l'endpoint achat ont été retirés ; la suite
SQLite a été relancée intégralement. Aucun test n'a été désactivé.

## Limites du checkpoint

Cette adaptation backend est validée, mais **la phase 2C complète ne l'est
pas encore**. L'enrôlement Flutter, le cache, les sessions personnelles et
la preuve Keystore/chiffrement sur Android doivent encore être validés.
Aucune phase 2D, intégration main, migration production ou activation
offline n'est autorisée par ce seul checkpoint.
