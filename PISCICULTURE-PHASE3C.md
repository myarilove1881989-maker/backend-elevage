# Pisciculture phase 3C — récoltes, ventes et post-récolte

Base backend : afd301716fd8f0098c8ddcf6f52752206d5c4ae4. Branche locale feature/pisciculture-phase3c-20261113. Aucune autorisation de publication ou de migration distante.

## Audit et décisions d’intégration

Le cahier du 8 novembre, sections 11 et 12, la conception 0B–0C et les contrats/rapports 1–3B sont les références. Les cohortes, positions, événements, lignes, verrou de ferme, commandes UUID et audit existants sont réutilisés. Un événement HARVEST et une ligne négative débitent le vivant ; jamais un StockFact MORTALITY. Le rejouage complet vérifie tous les préfixes, réserves de pertes non rapprochées et événements futurs. HARVEST a le même contrôle conservateur de réserve que TRANSFER. Une masse ne produit aucun nombre de poissons : live_count entier positif et counted=true strictement sont obligatoires au débit. L’utilisateur doit réellement compter pour disposer de cette information. Pas de reprise silencieuse d’une biomasse estimée.

Vente impose historiquement lot et quantité entière, et calcule quantite*prix_unitaire. Client, Payment, Lettrage et les soldes sont déjà indépendants du Lot. La facture est une représentation PDF d’une Vente, pas une deuxième créance ni une table Facture. Extension minimale de Vente : lot et quantite nullable, shared_exploitation nullable, priced_quantity Decimal(18,6) et price_unit KG/PIECE. XOR SQL : vraie vente terrestre avec lot et ancienne quantité, ou vente piscicole avec ferme et quantité facturée. Anciennes lignes/IDs/montants inchangés ; aucun faux Lot.

Pour une vente aquatique, montant_total = priced_quantity × prix_unitaire avec Decimal et arrondi monétaire ROUND_HALF_UP au centime, une seule fois à la fin. Prix Decimal(10,2), total Decimal(12,2), zéro de prix explicitement permis, quantité positive. quantite conserve seulement le nombre réellement renseigné, nullable lorsque la vente est au kg et le nombre inconnu. Les ventes aquatiques existantes ne sont pas modifiables par save : correction explicite requise. Les actes nouveaux sont liés au journal et audités sous le même UUID de commande.

Un paiement crée exactement un Payment du client existant et un Lettrage de cette même Vente. Les montants réduisent la même créance dans les API habituelles de solde/dettes et de ventes du client. Verrou ferme, puis client, puis vente : même ordre client/vente que le paiement classique. Refus au-delà du reste et avant la date de vente. Les routes classiques lisent les ventes piscicoles et restent disponibles pour leurs propres parcours ; le nouveau parcours utilise commerce-payments/ pour garantir les reprises UUID. Aucun nouveau mode de paiement, compte ou moteur de comptabilité.

Audit des usages de Lot : indicateurs terrestres par Lot/cycle, services de vente terrestre/œufs, rapprochement terrain et offline conservent leurs scopes terrestres. Les vrais totaux clients et administratifs par ferme incluent lot__exploitation OU shared_exploitation, sans multiplication de lignes. Exports/admin traitent le parent partagé et l’unité. La fiche client et le PDF distinguent POISSONS, kg ou poissons ; aucune quantité inconnue affichée comme zéro animaux. PDF piscicole : centimes conservés et référence FAC-année de vente-ID stable. Les formats de factures terrestres restent identiques. La monnaie reste celle du mécanisme applicatif existant ; aucun change ou nouveau grand livre multidevise.

## Récolte et journal de garde post-récolte

Harvest immuable : origine cohorte/installation, espèce historique déduite, auteur, instant avec fuseau, nombre confirmé, masse kg nullable et qualité UNKNOWN/ESTIMATED/MEASURED, destination DIRECT/STOCK, unité de suivi KG/PIECE, lieu/mode/information de conservation, note et preuve sanitaire. Masse estimée conservée comme estimation ; seule une masse réellement mesurée peut ouvrir un suivi en kg. PIECE s’appuie sur le comptage confirmé. Aucune conversion kg/poissons, aucun changement d’unité après entrée.

HarvestMovement immuable : SALE, LOSS, VOID, CORRECTION. SALE possède un lien unique à core.Vente. Le stock post-récolte est un journal de garde distinct du vivant et non une seconde comptabilité. Son solde primaire est la quantité initiale moins ventes/pertes actives, rejouées par instant/date d’enregistrement/UUID. Aucun solde négatif à aucun préfixe. La vente directe crée récolte, débit vivant, mouvement et Vente dans une transaction ; même date/heure pour récolte et vente. Échec de client, sanitaire, stock ou finance annule tout. Une récolte directe est entièrement consommée et ne peut être revendue. Pour récolte puis vente à des dates différentes, choisir STOCK et vendre ensuite.

Entrée en stock = date de récolte, dans la même transaction. Lieu et mode de conservation sont requis pour STOCK, libres et sans recommandation universelle ; conservation_note peut contenir date/info facultative. Pas de chambre froide avancée, péremption automatique ou autorisation alimentaire inventée.

La dimension primaire choisie pilote les consommations. KG : quantity décimale en kg et mass_kg=quantity ; count secondaire facultatif. PIECE : quantity entière et count=quantity ; masse secondaire facultative. Mesures secondaires réelles uniquement ; lorsqu’un mouvement ne mesure pas une dimension secondaire, son solde devient inconnu et reste inconnu, sans approximation ni retour à zéro. Le fait initial mesuré est conservé. Les dimensions secondaires intégralement connues sont également vérifiées ; un épuisement primaire ne peut laisser un reliquat secondaire connu contradictoire. Un comptage secondaire inconnu ne crée jamais une capacité supplémentaire : toutes les ventes restent bornées par la dimension primaire verrouillée.

Exemple testé : 100 kg mesurés, 30 puis 45 vendus = 25 kg ; pas de nombre vendu imposé. Le nombre restant est alors inconnu. La récolte a déjà débité le vivant une fois. Une perte post-récolte n’affecte que ce journal, ne crée ni mortalité vivante ni dépense.

## Santé : réutilisation du contrat 3A

commerce-check/ appelle le même sale_check utilisé par health-sale-check/. Prévisualisation tenant sous verrou : précautions, sources, dates, traitements en cours, statut des délais et contexte après transfert. sale_authorized reste toujours null. Aucune règle réglementaire ni délai par défaut.

Pour une vente post-récolte, l’origine est la cohorte/installation de la récolte, avec exposition et transferts arrêtés à cette date, y compris après récolte totale sans poisson restant dans le bassin. Les traitements actuellement rectifiés mais effectifs avant la récolte sont réévalués ; les précautions sont évaluées à la date de vente. L’instantané d’origine reste consultable, une nouvelle vérification est requise pour chaque vente.

Le résultat fournit un health_token signé, lié à utilisateur/ferme/origine/date, aux précautions et à la révision métier. Durée 30 minutes. Mutation : recomposition sous le même verrou, comparaison exacte, sinon 409 HEALTH_REVIEW_STALE. Date normalisée UTC et ordre stable des précautions. La preuve comporte human_verified=true strictement, motif/document vérifié, auteur et date serveur. Un délai documenté ACTIVE ou un traitement déclaré ongoing bloque, même avec case cochée. Un délai inconnu, une source absente, un contexte ambigu ou l’absence de traitement trouvé exigent une vérification humaine explicite avec motif ; le serveur ne certifie pas cette vérification documentaire et ne donne aucune autorisation réglementaire. Rejeu d’une commande déjà réussie : résultat initial après revalidation des droits, sans deuxième acte.

## Annulations et corrections

Pas de PATCH/DELETE des faits, triggers PostgreSQL append-only. Propriétaire seulement : annulation motivée d’une perte, unique, après son instant, avec replay de tous les préfixes. Récolte restée sans vente ni perte : compensation HARVEST_REVERSAL au temps de correction, retour de l’effectif au vivant, conservation de l’original. Récolte déjà consommée : refus conservateur, y compris si une perte a ensuite été annulée.

Une vente/facture ou un paiement n’est jamais annulé automatiquement. Une demande CORRECTION immuable peut viser la récolte ou un mouvement, avec motif/auteur/date et décision REVIEW_REQUIRED ; aucun effet financier, remboursement, avoir ou libération de stock. Le contrat de résolution financière reste à valider dans une mission distincte. Cela empêche qu’une correction de stock efface une créance ou un paiement. Les routes de correction terrestres restent bornées à leurs vrais lots ou à leurs faits classiques déjà non corrélés.

## API additive /api/pisciculture/v1/

| Route | Méthode | Usage |
|---|---|---|
| harvests/ | GET/POST | Liste, récolte STOCK ou DIRECT atomique |
| harvests/{uuid}/ | GET | Origine, solde, mouvements et factures/paiements |
| harvest-sales/ | GET/POST | Ventes partielles |
| postharvest-losses/ | POST | Perte post-récolte |
| commerce-history/ | GET | Mouvements y compris correction/annulation |
| commerce-check/ | GET | Revue sanitaire signée |
| commerce-clients/ | GET/POST | Référentiel Client existant |
| commerce-invoices/{id}/ | GET | Même créance Vente et mêmes paiements |
| commerce-payments/ | POST | Payment + Lettrage idempotents |
| commerce-cancellations/ | POST | Annulation non financière conservatrice |
| commerce-corrections/ | POST | Demande de correction sans effet automatique |

Tous POST : operation_uuid UUID, schema_version=1, occurred_at avec fuseau, note facultative. Champs inconnus refusés, entiers JSON explicites, décimaux en chaînes. Pas de champ exploitation accepté. HarvestInput : cohort, installation, live_count, counted=true, mass_kg/null, mass_quality, stock_unit, destination, storage_place/mode/conservation_note, review. DIRECT exige direct_sale={client entier,unit_price chaîne}. Review={health_token,human_verified:true,verification_reason}. Aucune quantité ou prix prérempli arbitrairement.

Vente depuis stock : harvest UUID, quantity chaîne, count/null et mass_kg/null cohérents avec l’unité, price={client,unit_price}, review. Perte : mêmes mesures sans price/review, reason requis. Paiement : sale entier core.Vente, amount chaîne au centime, date/instant, note. Client : customer={nom,telephone,pays,ville}, réutilise ClientSerializer et le tenant dérivé. Annulation/correction : harvest, target UUID facultatif (mouvement), reason.

GET commerce-check/ exige as_of et soit harvest, soit installation+cohort. Listes historiques : installation, cohort, species entier, client entier, harvest, status AVAILABLE/EXHAUSTED/CANCELED, from/to avec fuseau ; kind SALE/LOSS/VOID/CORRECTION pour mouvements. limit 1..100 et cursor signé tenant/route/filtres/snapshot/révision. Changement de révision impose actualisation. Les détails présentent tout le dossier ; leurs volumes et les filtres de solde rejoués restent à mesurer à grande échelle. Liste clients : pagination par ID avec curseur signé tenant, pas de recherche distante nouvelle.

Droits : aquatic.read, aquatic.harvest.create, aquatic.sales.create, aquatic.postharvest.loss. Vente directe exige récolte ET vente ; lecture financière finance.read. Paiement cash.create, client clients.create ; nouvelles capacités commerce_write exposent seulement ces droits existants, sans ajout au contrat offline. Annulation/correction OWNER. Appartenance tenant des cohortes/installations/clients/factures vérifiée ; erreur étrangère 404, permissions 403, validation 400, conflit/reprise divergente/stock/sanitaire périmé 409. Signature appareil existante inchangée pour opérateurs lorsque la politique l’exige. Rien dans l’outbox.

Commande sous verrou ferme, révocation recontrôlée avant rejeu UUID. Même UUID/auteur/charge = même réponse ; autre charge =409 ; autres auteurs masqués. Transaction unique pour débit, stock de garde, vente et audit ; concurrence sérialisée. Pas d’appel réseau dans les transactions. Les contrôles monétaires et de stock sont serveur, jamais confiés à l’aperçu Flutter.

## Migrations et qualification

- core/0057_remove_membershippermission_membership_action_known_and_more.py : extension Vente et droits, dépendance après pisciculture 0013.
- pisciculture/0014_harvest_harvestmovement_and_more.py : deux faits et contraintes.
- pisciculture/0015_harvest_facts_immutable.py : triggers SQL.

Dix-neuf migrations antérieures inchangées ; manifeste de vingt-deux empreintes exactes. Garde de schéma/plan étendu au préfixe 3B (19), avec opt-in et preuve de sauvegarde exigés, anciennes portes maintenues. Test de migration 3B→3C avec géniteurs/reproduction/éclosion et ventes/paiements terrestres synthétiques, conservation de toutes les anciennes colonnes/lignes et redémarrage sans flag. Aucun script ni empreinte synthétique utilisables comme autorisation de production.

Runner tools/qualify_phase3c.py : D: obligatoire, PostgreSQL isolé 127.0.0.1:57859, backend/.local/phase3c-postgres, temporaires ../phase3c/tmp, réseau externe interdit et arrêt dans finally. Les résultats réels et les limites sont dans ../phase3c/RAPPORT-PHASE3C.md. Qualification locale seulement ; aucun benchmark de très gros historique, recette physique, APK ou Web. Copies antérieures et fichiers protégés préservés.

Qualification complémentaire : la jointure nullable du paiement classique verrouille Vente seule (FOR UPDATE OF self), client déjà verrouillé. Le test monétaire historique conserve ses assertions et retire uniquement les droits synthétiques inexistants dans son ancien schéma, avec restauration systématique du graphe complet. Les mesures secondaires facultatives sont accessibles dans le formulaire ; le suivi par nombre est proposé par défaut, sans quantité inventée. Après un résultat inconnu, fermer ne peut annuler une opération éventuellement reçue : vérifier l’historique avant toute nouvelle saisie.
