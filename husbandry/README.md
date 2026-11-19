# Technique et reproduction terrestres — contrat en ligne V1

Périmètre : propriétaires authentifiés, exploitation active, Poule/Poulet, Vache/Bovin, Porc et Lapin. La reproduction concerne uniquement les trois dernières familles. Aucun nouveau droit signé Android, stockage hors ligne ou outbox n'est ajouté.

API : `/api/elevage/v1/`. `capabilities/` expose le périmètre effectif. Les autres lectures et commandes sont réservées au propriétaire. Toute référence est résolue dans l'exploitation authentifiée ; le client ne choisit jamais son tenant.

Commandes POST : `technical/setup/`, `technical/parameters/`, `technical/rules/`, `technical/readings/`, `reproduction/identities/`, `reproduction/cycles/`, `reproduction/events/`. Enveloppe : `schema_version: 1`, UUID `operation_uuid`, champs métier stricts. Une répétition identique renvoie le reçu d'origine (200) ; une réutilisation divergente ou une version périmée renvoie 409. Écritures atomiques sérialisées par exploitation, droits revérifiés après verrouillage. Aucun retry automatique de transport.

GET paginés : paramètres par espèce, règles/relevés par lot, évolution par lot et paramètre numérique, identités par espèce, cycles par lot/identité/statut et événements par cycle/lot/période. `limit` 1–100 ; `cursor` offset. Les listes Flutter sont bornées à 2 000 résultats et demandent une période plus courte au-delà.

Relevés : nombres décimaux finis transmis sous forme de chaînes (6 décimales au maximum), texte ou booléen strict. Au moins une valeur présente ; 0 et false sont conservés. Dates avec fuseau et heure d'enregistrement serveur. Paramètres prédéfinis non supprimables par l'API. Masquage par règles de lot versionnées et datées. Les snapshots conservent nom, unité, type, règle, valeur et évaluation ; aucun seuil universel. Les graphiques ne transforment pas les absences en zéro.

Reproduction : identifiant réutilisable ou groupe, cycles, dates prévues facultatives saisies sans durée biologique inventée ; événements souples, sans étapes préalables obligatoires. D1 : un événement entièrement mort-né ne crée ni lot vivant ni mouvement. Une naissance vivante réutilise `core.births.create_birth`, ou rattache explicitement une naissance existante compatible, sans double comptage. D2 : le sevrage ne crée aucun mouvement. Les pertes ne sont dérivées que des mortalités existantes sur un lot de naissance traçable, sans correction de stock ni autre entrée ambiguë. Aucun nouveau circuit financier.

Historique : faits et reçus immuables au niveau ORM et PostgreSQL. Les références protégées ne sont pas supprimées en cascade ; les anciennes routes de suppression répondent 409 lorsqu'un nouvel historique est lié. Aucun parcours de correction historique n'est ajouté dans ce lot.

Migrations : baseline exacte de 104 entrées (core 0058 + pisciculture 0016), puis husbandry 0001 et 0002, total 106. `migration_manifest.json` fige registre et SHA-256 ; `upgrade.py` vérifie manifeste, plan atomique, gardes explicites, verrou partagé et préservation des tables core/pisciculture. Le point de démarrage demeure `core.production_upgrade` avec ses contrôles de destination/TLS existants. Ne jamais utiliser un migrate distant direct pour contourner cette garde.

Gardes à renseigner uniquement après validation de publication et sauvegarde de production vérifiée : `ELEVAGE_ALLOW_HUSBANDRY_UPGRADE=true` et `ELEVAGE_HUSBANDRY_BACKUP_SHA256=<SHA-256 réel de cette sauvegarde>`. Un SHA de sauvegarde synthétique ne vaut pas sauvegarde de production. Après commit de migration, ne pas redémarrer un ancien backend dont la garde ne reconnaît pas le registre 106 ; récupération compatible ou correctif en avant.

Tests locaux : `tools/test_local.py --postgres husbandry.tests husbandry.test_concurrency husbandry.test_upgrade core.tests.test_birth_movement_api`. La qualification de cette livraison utilise des ports et chemins D: spécifiques documentés dans son dossier externe.
