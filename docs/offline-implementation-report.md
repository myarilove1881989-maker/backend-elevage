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
aussi l'événement d'audit correspondant. Relance finale : SQLite 207 tests
(202 réussis, 5 skips PG), PostgreSQL 207 tests (205 réussis, 2 skips SQLite),
serveur de test arrêté. Sources validées : serveur
`c0bc8473594e49d36a60a8415ca5346227520967`, mobile
`e64ca3f056d4c46425defbd40139d867508f37e8`. CI `37627927982` : 145 tests
Flutter, Web/APK/API 24 et formulaire client sans réseau passent.

## Phase 2E — extension opérations terrain, validation EN COURS

Ajout des dépenses, alimentation, pesées, collectes, mortalité/don/vol,
achats et naissances. Les achats réutilisent AchatSerializer ; les collectes
réutilisent save_collection et leur mouvement de stock. Les naissances
créent un lot enfant de vivants, sans modifier le parent ni inclure les
mort-nés dans le stock. Les références locales sont résolues exclusivement
par une correspondance confirmée dans la même exploitation.

Migration additive 0022 : révision métier de l'exploitation et lots touchés
du résultat terrain. Les écritures en ligne et l'application terrain
incrémentent la révision sous verrou d'exploitation. Les pages de cache et
reçus de stock utilisent le même verrou pour fournir un stock avec sa
révision, sans dépendre de l'horloge Android. Le cache conserve une révision
plus récente reçue pendant le chargement. Stock animaux et stock œufs restent
distincts. Le nombre de requêtes de page de lots reste constant, avec le
verrou d'exploitation et les savepoints atomiques désormais comptabilisés.

Tests ciblés opérations : 10 tests, 9 réussis et 1 concurrence PostgreSQL
ignorée sous SQLite. Première exécution : référence dépense recherchée avec
un champ exploitation inexistant ; corrigée vers lot__exploitation, puis
relancée avec succès. SQLite complet : 217 tests, 211 réussis et 6 skips PG.
PostgreSQL complet : 217 tests, 215 réussis et 2 skips SQLite ; toutes les
migrations, contrôles et tests de concurrence passent, serveur de test arrêté.
Validations mobiles en cours. Ventes, paiements et phases 2F
suivantes restent non commencés.

## Clôture 2E — 7 octobre 2026

Sources validées : backend `5e146d76de58658d7ec7fd95e196693389ce2b97`,
mobile `6aa7ab9179d3b3796bc8585e3ca0d97c193fb1b8`.
Clients, comptes rendus de tâches, dépenses, alimentation, pesées, collectes,
mortalités, dons, vols, achats et naissances sont inclus. Achats et enfants
ont un UUID de lot provisoire résolu dans la même exploitation. Les mort-nés
ne contribuent pas au stock et le lot parent reste inchangé.

SQLite : 217 tests, 211 réussis et 6 skips PostgreSQL. PostgreSQL 17.11
local isolé : 217 tests, 215 réussis et 2 skips SQLite, migrations jusqu'à
0022 et token_blacklist, check et makemigrations --check réussis. Les
retraits simultanés conservent deux déclarations, appliquent un seul retrait
et laissent un stock de 5. Le cluster de test est arrêté.

CI mobile `37635935971` : 160 tests réussis ; analyse sans erreur ni warning
(61 infos conservées), lockfile strict, Web release, APK debug et ARM64
release (28,4 MB). Android API 24 réelle : profils Jean/Paul et auteurs
préservés, formulaire client offline et achat de 3 à 13,01 donnant 39,03,
projection et réouverture de la file, Keystore dans deux processus et
preuve de fichier chiffré illisible par SQLite ordinaire. Les échanges HTTP
du parcours UI sont synthétiques ; le parcours métier natif avec backend
réel, redémarrage complet et mise à jour APK reste à valider en 2I.

Phase 2E validée ; ventes, FIFO et encaissements commencent ensuite en 2F.
PR backend #8 et mobile #10 restent en brouillon. Aucun merge, déploiement,
test de production ni activation de politique réelle.

## Phase 2F — ventes et encaissements, checkpoint EN COURS

Ventes animaux sous verrou de lot/exploitation, survente conservée à
rapprocher sans stock confirmé négatif. Ventes d'œufs via le service existant,
FIFO et AffectationMouvementOeufs avec la date/heure métier comme borne :
une collecte postérieure, même du même jour, est inéligible.

EncaissementTerrain distingue le montant physiquement reçu, le montant
lettré et le reliquat à rapprocher. Payment garde tout le montant reconnu ;
seule la vente explicitement visée est lettrée, sans allocation silencieuse
à d'autres dettes. 50 000 reçus pour 30 000 dus conserve 50 000, affecte
30 000 et signale 20 000. Auteur, date métier et déclaration restent tracés.
Migration 0023 : modèle reconnu et conversion Payment/Lettrage en Decimal,
précédée d'un contrôle refusant arrondi réel, dépassement ou montant non
fini. Cette évolution des anciens champs est nécessaire au lettrage exact ;
elle reste soumise à sauvegarde et contrôle des valeurs réelles avant toute
migration de production. Test de migration historique valide et refusée.

Mobile : schéma local 5 additif, ventes/projections et chaîne UUID
client → vente → encaissement. Reçus monétaires contrôlés et persistés,
montants en chaînes décimales/BigInt, affectation distincte du fait physique.
Formulaires et parcours natif supplémentaires en attente de CI.

Premières suites complètes : 229 tests sur chaque moteur, deux erreurs
identiques dans l'export Excel mélangeant Decimal et float ; calculs corrigés.
Les tests de concurrence PostgreSQL passent, dont deux ventes concurrentes
et deux encaissements sur une dette unique. Un premier appel SQLite depuis
le mauvais répertoire n'avait découvert aucun test : résultat non retenu,
relancé depuis le dépôt. Test de cache ajouté : erreur de chemin de fixture
corrigée, puis format de dette harmonisé à deux décimales. 21 tests ciblés
ventes/exports passent avec deux skips PG. Suites complètes corrigées et CI
mobile restent requises avant clôture 2F. Aucune production touchée.

### Vérifications supplémentaires 2F

Checkpoint serveur `edd5a4a6500bb71ebb45e585f042af4fff06ff8d` : suites
corrigées 230 SQLite (222 réussis, 8 skips PG) et 230 PostgreSQL (228 réussis,
2 skips SQLite), migrations et contrôles passent ; cluster local arrêté.
Le montant physique reconnu est désormais aussi protégé dans le modèle
et par le trigger PostgreSQL additif 0024 : UPDATE de l'origine ou DELETE
refusés, affectations évolutives conservées. Quinze tests ciblés passent
sous SQLite avec trois skips PG ; régression complète de cet ajout requise.

CI mobile initiale `37642626542` : analyse réussie, 166 tests réussis et un
échec de fixture. Le test supposait le mauvais ordre de listOutbox à la
réouverture ; il recherche désormais l'encaissement par son UUID et vérifie
toujours ses deux dépendances et l'auteur. Le parcours natif de ce run
reste en cours ; aucune porte 2F n'est considérée entièrement validée.

### Critères de survente et conditionnements 2F

La relecture du paragraphe 19 a ajouté l'alerte forte et le motif requis
lorsque la quantité dépasse le stock projeté. Le fait réel reste saisissable ;
le stock confirmé ne devient pas négatif. Le motif est conservé dans la
déclaration originale et les mouvements applicables. Le parcours natif
vérifie la demande du motif puis la conservation de la survente.
Conditionnement composé existant préservé : alvéoles de 30, supplément
0–29 et prix total exact. Les dépassements de quantité SQL et de montant
sont classés à rapprocher avant toute écriture métier, sans boucle de retry.
18 tests ciblés serveur passent sous SQLite avec 3 skips PG ; nouvelle
régression complète et CI mobile requises pour ce dernier complément.
