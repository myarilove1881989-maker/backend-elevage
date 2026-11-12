# Pisciculture phase 2C — contrat local

Base backend : 9c73436cba62c15404c40313e6caacb017eb5ff9.
Branche : feature/pisciculture-phase2c-20261112, copie isolée sur D:.
Qualification locale uniquement. Aucun accord de publication ni de migration réelle.

## Une vérité de stock

SupplyProduct(kind=FEED), SupplyOperation, SupplyReceipt, SupplyMovement sont les modèles existants réutilisés. FeedPurchase relie un achat à son unique reçu/dépense ; FeedDistribution ajoute les seules informations piscicoles au débit physique. Aucun second solde stocké, ni ConsommationAliment terrestre artificielle.

Position commune FARM_COMMON : species=NULL, lot=NULL, destination_lot=NULL, vial=NULL. Réserves existantes par espèce/lot conservées. Physique total = somme du même journal ; disponible pour une distribution piscicole = position commune seule. Ni prélèvement implicite d’une réserve ni conversion d’un stock virtuel historique en stock physique. Le propriétaire peut libérer ou réserver une quantité par un transfert explicite, motivé, avec deux mouvements équilibrés du même produit, sans dépense.

100 kg achetés -> 100 kg communs ; distribution 15 -> 85 ; distribution 20 -> 65. Une dépense sur l’achat, zéro dépense sur les distributions. Les restes déclarés ne retournent jamais automatiquement au stock.

## Extension financière minimale

Depense réutilisé, parent lot rendu facultatif et shared_exploitation ajouté. Contrainte XOR : exactement un parent. Un achat commun a lot=NULL, shared_exploitation=ferme, catégorie alimentaire FEED liée explicitement. Le montant, la date, la note, l’auteur, le reçu unique et l’audit suivent les mécanismes existants. La contrainte historique core_depense_void_consistent est conservée.

Aucun faux Lot terrestre. Les anciens sérialiseurs et listes restent attachés aux vrais lots. Une dépense commune ne peut être mutée via les routes anciennes de dépense/distribution. Les totaux administratifs globaux/par ferme et exports incluent son unique montant ; les indicateurs terrestres par lot restent inchangés. Un coût commun ne devient pas automatiquement un coût de cycle par lot : le rapport de cycle indique alors le coût physique non attribuable/insuffisant. Pas de nouveau système de facturation, d’affectation financière ni de FCR.

## API en ligne /pisciculture/v1/

- GET feed-products/ : catalogue FEED existant, actif/archivé, unité kg, common_kg, physical_kg, réserves explicites, aquatic_used, catégories alimentaires, espèces/lots existants, capacités d’achat et de gestion des réserves.
- GET/POST feed-purchases/ : achat/entrée/dépense atomiques. product UUID OU new_product {id UUID,name} ; category entier OU new_category texte ; quantity_kg chaîne décimale positive (14,3), amount chaîne décimale >=0 (12,2), unit=kg, occurred_at avec fuseau, note facultative 255 caractères.
- GET/POST feed-distributions/ : product UUID, installation UUID active, cohort UUID ouverte facultative/null (présence historique vérifiée), quantity_kg positive (14,3), leftovers_kg facultatif/null ou >=0 et <= quantité, unit=kg, occurred_at avec fuseau, note facultative 500 caractères. weight_reference UUID facultatif de portée exacte. Auteur et date d’enregistrement ajoutés côté serveur.
- POST feed-reserves/ : product UUID, source et target {species entier/null,lot entier/null}, quantity_kg positive, unit=kg, occurred_at avec fuseau, reason obligatoire. Un lot exige son espèce et doit être ouvert. Source différente de destination.
- GET feed-summary/ : totaux, sous-totaux connus, état EMPTY/UNKNOWN/PARTIAL/ESTIMATED, quantités journalières et par aliment, non-attribué au bassin séparé, stock commun actuel toute ferme, comparaison de biomasse indicative ou INSUFFICIENT_DATA.
- GET feed-stock-history/ : tous les mouvements du journal partagé FEED, dates effective/enregistrement, auteur, UUID, produit, réserve et quantité signée. Aucune donnée de prix dans ce journal.

Toutes les commandes portent operation_uuid et schema_version=1. Schémas stricts : champs inconnus, floats JSON pour décimaux, valeurs nulles invalides, négatives, unités différentes ou précision excessive refusés. Le numéro de schéma de commande existant est conservé ; les nouveaux chemins sont dans l’API v1, sans modifier les anciens endpoints.

Achat/distribution : time_precision=DATETIME par défaut. Si seule la date est connue, time_precision=DATE, business_date=YYYY-MM-DD, occurred_at au début de cette journée avec fuseau explicite. La date originale est conservée dans la charge immuable de SupplyOperation et renvoyée ; aucune heure connue inventée. Les contrôles de stock/présence utilisent ce début de journée conservateur et peuvent refuser si les entrées/présences ne sont établies que plus tard. La date de dépense et les groupes journaliers respectent business_date.

Filtres de lecture : product, installation, cohort, from, to (dates ISO avec fuseau). Les achats et mouvements communs refusent installation/cohort. Catalogue sans filtre. Historiques paginés par curseur signé lié à ferme/type/filtres/limite/snapshot, limite 1..100. Résumés bornés par les filtres ; le stock courant est toujours celui de toute la ferme. Si le tableau de bord filtre une espèce, aucun total alimentaire global n’est présenté comme attribué à celle-ci ; données insuffisantes explicites, consulter les bassins/cohortes. Un total de cohorte exclut les distributions globales et expose séparément le non-attribué dans le bassin/la période. Valeurs de quantité/prix renvoyées sous forme de chaînes exactes.

## Inconnus et biomasse

leftovers_kg absent/null : consommation inconnue, estimated_consumed_kg=null. Restes explicitement zéro : consommé estimé = distribué. Sinon consommé estimé = distribué - restes. Si un relevé manque dans la période, aucun total de consommation réelle inventé ; known_consumed_kg est un sous-total explicitement partiel. Une distribution globale n’est jamais divisée entre cohortes.

Comparaison indicative : dernière pesée antérieure de portée exacte (cohorte entière OU bassin global), effectif historique EXACT, provenance des événements/positions/faits identique à celle de la pesée, règle explicite de fraîcheur conservée avec la pesée et encore respectée. Calculs Fraction/Decimal exacts, numérateur/dénominateur et projection décimale. Aliment/biomasse = quantité *100 / biomasse kg. La comparaison périodique n’agrège que des références de biomasse identiques ; sinon INSUFFICIENT_DATA. Pertes non rapprochées, crédit incertain, changement d’effectif/provenance, pesée absente/périmée ou règle absente : données insuffisantes. Pas de moyenne arbitraire ou recommandation biologique ; fcr=null. Les analyses de distribution sont des instantanés à leur date, pas une affirmation sur la biomasse actuelle.

## Sécurité et transaction

aquatic.read et stocks.read sur les lectures ; finance.read pour les achats. Les droits de lecture de base existants restent implicites. Écriture : aquatic.feed.purchase + expenses.create pour acheter ; aquatic.feed.distribute pour distribuer. Création produit/catégorie et réserves réservées au OWNER. Révocation, tenant, activité et droits revérifiés dans le verrou de ferme, avant même le rejeu UUID. Employés sous politique appareils : mêmes preuves signées de requête/appareil primaire que les autres écritures piscicoles en ligne.

Même verrou de ferme que les services de stock existants, transaction atomique, UUID de commande + empreinte, collision avec le journal partagé refusée. Rejeu identique renvoie le résultat mémorisé sans nouveau mouvement. Charge différente : 409. Solde insuffisant/conflit de chronologie : 409 et rollback dépense, reçu, mouvements, faits et commande. Chaque préfixe temporel de la réserve débitée est non négatif. Dates et références étrangères invalides : 400/404, droits : 403. Faits immuables ORM + triggers PostgreSQL. Pas de PATCH/DELETE de ces faits ni d’annulation via REVERSE générique.

Projection signée offline historique : même schéma/contrat, mêmes réserves non communes ; seules les nouvelles positions communes non interprétables par les anciens clients sont exclues. Aucune modification des clés, PIN, appareils, outbox ou du client offline. Les réserves libérées/réservées restent prises en compte dans le journal partagé. Les clients terrestres antérieurs ne peuvent pas dépenser la position commune implicitement.

## Migrations additives

1. core/0054_shared_feed_expenses_common_pool_permissions.py : parent facultatif Depense, parent direct ferme, XOR, espèce facultative SupplyMovement, forme commune, nouveaux droits.
2. pisciculture/0008_feedpurchase_feeddistribution.py : liens immuables achat/distribution et contraintes quantités/restes.
3. pisciculture/0009_feed_facts_and_common_pool.py : triggers d’immutabilité et restriction commune FEED au niveau SQL.

Aucune réécriture des anciennes migrations ni modification des dix empreintes déjà approuvées. Manifeste et garde de démarrage ajoutent le seul suffixe 2C exact depuis 0050/phase1/2A/2B. Un nouveau schéma nécessite l’opt-in et l’empreinte de sauvegarde ; redémarrage identique sans migration. Conservation des données anciennes vérifiée pendant upgrade. Aucun upgrade exécuté sur un serveur externe.

## Qualification reproductible

D-only : tools/qualify_phase2c.py, PostgreSQL synthétique 127.0.0.1:57853, fichiers .local/phase2c-postgres, temporaires ../phase2c/tmp. Le runner interdit les connexions externes et arrête ce cluster à sa sortie. Voir rapport ../phase2c/RAPPORT-PHASE2C.md pour les séries réellement exécutées et leurs limites. Pas d’installation, push, PR, déploiement, APK ou build Web.
