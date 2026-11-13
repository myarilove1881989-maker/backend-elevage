# Pisciculture 3D — contrat finances et agenda

Extension locale de la base 3C 2898c53f07a30953d446e84bab89c0500afeb09e. En ligne uniquement. Aucune publication ou migration distante autorisée.

## Modèles et audit

La valeur économique reste exclusivement core.Depense / core.Vente / core.Payment / core.Lettrage, avec les Client et CategorieDepense existants. Aucun compte ou montant économique dupliqué.

Depense.shared_purpose (FEED/AQUATIC/FARM, défaut FEED) conserve la validation des achats alimentaires partagés et ouvre des catégories libres aux autres charges partagées. Le parent terrestre/partagé reste exclusif et immuable. Les anciennes colonnes et lignes sont conservées.

AquaticExpenseLink : UUID, ferme, version, expense unique, scope COHORT/INSTALLATION/AQUATIC/FARM, cohorte/site/espèce éventuels, auteur et dates techniques. Espèce reconstruite depuis la cohorte à la date de dépense. Une installation ne se déduit jamais de sa position actuelle. COHORT exige une cohorte ; INSTALLATION un site sans cohorte ; les deux scopes communs n'ont aucun de ces rattachements. Aucun montant dans ce lien. Son auteur initial est conservé ; les corrections portent leur propre auteur/motif dans AuditEvent.

AquaticTaskPlan : UUID/ferme/version, Task unique existante, type, site/cohorte facultatifs, heure facultative, rappel facultatif, couple actual_kind/actual_id facultatif, auteur et dates techniques. Titre/date/description/statut/auteur de création/compte rendu restent dans core.Task. Le statut et sa version ont une seule source, Task. Les heures renseignées doivent avoir un fuseau ; une date sans heure n'invente pas minuit comme heure planifiée.

Task.objects exclut les tâches possédant aquatic_plan par sous-requête NOT IN, sans jointure nullable dans les anciens verrous. Les routes terrestres, préparations/cache/notifications terrain continuent à utiliser ce manager et ne voient pas ces tâches. Task.all_tasks sert uniquement les lectures administratives globales. Aucune modification de champ ou transport Task hors ligne. L'administration des tâches reste en lecture seule comme avant.

AuditEvent et Command existants : avant/après, auteur/motif, UUID, empreinte du corps, résultat. Toute écriture est atomique sous le verrou de ferme ; mêmes UUID/corps/auteur donnent le même résultat, divergence refusée. Versions attendues obligatoires pour correction/statut ; révocation et preuve appareil primaire existante revérifiées avant rejeu.

## Règles financières

Période inclusive de dates civiles, défaut début de mois à aujourd'hui. Chiffre d'affaires : montant des Vente piscicoles actives dont la date de facture appartient à la période. Encaissements : seuls montants Lettrage vers ces ventes piscicoles, date du Payment dans la période, même ferme et même client. Un paiement mixte terrestre/piscicole ne compte que sa fraction réellement lettrée aux factures piscicoles. Les paiements ne s'ajoutent jamais au chiffre d'affaires.

Solde clients à la date de fin : toutes les factures piscicoles correspondant au périmètre jusqu'à cette date, moins leurs lettrages jusqu'à cette date, y compris factures antérieures au début. La liste des recettes inclut également une ancienne facture ayant un encaissement dans la période et indique invoice_in_period=false ; received_in_period permet de retrouver le montant reçu.

Coûts directs : dépenses actives datées dans la période dont les rattachements explicites correspondent à tous les filtres. Une dépense de site sans cohorte n'est pas répartie entre espèces. Une dépense de cohorte sans site ne devient pas une dépense de son bassin actuel.

Coûts AQUATIC : visibles séparément, soustraits une seule fois au résultat piscicole global sans filtre d'origine ; avec filtre espèce/cohorte/site, affichés mais exclus du résultat filtré. Coûts FARM : visibles séparément, jamais soustraits au résultat piscicole. Les totaux communs sont ceux de la période entière, sans répartition automatique. Catégories : seules dépenses retenues dans le résultat courant.

Résultat indicatif = chiffre d'affaires facturé - dépenses retenues. Ce n'est pas un bénéfice net comptable. Ni amortissements, ni affectation implicite des charges indirectes, ni conversion monétaire. Calculs Decimal serveur et montants en chaînes ; monnaie applicative existante.

Les achats alimentaires 2C sont lus par leur unique Depense déjà liée au SupplyReceipt. Les clés d'allocation de quantité du stock ne constituent pas une clé financière fiable : aucune allocation monétaire déduite. Les achats communs non désignés restent FARM jusqu'à attribution explicite par le propriétaire ; une désignation change seulement le périmètre de la même dépense. Correction de montant d'un achat avec SupplyReceipt refusée ici : utiliser le parcours de correction d'origine. Un traitement 3A ne crée aucune dépense. Les ventes/factures/paiements 3C sont simplement relus, sans nouvelle écriture.

## Agenda

Types WATER, WEIGHT, FEED, OBSERVATION, TREATMENT, REPRODUCTION, HATCH_CHECK, HARVEST, OTHER. Statuts TODO, DONE, CANCELLED de Task. Modification de contenu seulement TODO ; DONE peut être rouvert explicitement par le propriétaire ; CANCELLED est conservé et ne peut être réactivé. Modifications et statuts exigent motif et version. Un employé ne modifie que sa propre tâche ; progression seulement par l'assigné, annulation/réouverture par le propriétaire.

Aucun changement de statut ne crée de distribution, traitement, récolte, vente, mouvement, mortalité, dépense ou paiement. business_effect_created=false. Facultativement, DONE référence un fait déjà existant même ferme, de type et périmètre compatibles : Observation eau/pesée, FeedDistribution, HealthRecord observation/traitement, Reproduction, BreedingFact HATCH, Harvest. Les contextes de population enregistrés des observations permettent de confirmer un rattachement bassin/cohorte sans position actuelle arbitraire. La référence ne certifie pas l'absence de correction ultérieure du fait ; consulter son historique métier.

Rappel : instant explicite choisi, jamais de fréquence biologique par défaut. reminder_at <= scheduled_at si l'heure existe. Rappels dus TODO visibles dans l'application, indépendamment de la période courante ; aucune notification push, OS ou infrastructure nouvelle. Mine=true filtre l'auteur de création. Espèce d'agenda filtrée par la cohorte renseignée, pas de déduction depuis un site multi-populations.

## API /api/pisciculture/v1/

| Route | Méthode | Contrat |
|---|---|---|
| finance-summary/ | GET | synthèse, basis, limits, business_revision |
| finance-expenses/ | GET, POST | liste ou création d'une Depense existante + lien |
| finance-expense-revisions/ | POST | correction de la même dépense, propriétaire |
| finance-assignments/ | POST | attribution de dépense partagée existante, propriétaire |
| finance-categories/ | GET | catégories existantes de la ferme, aucune liste fermée |
| finance-revenues/ | GET | factures/lettrages existants et soldes à la date de fin |
| finance-history/ | GET | target UUID de lien, avant/après dépense + attribution |
| agenda/ | GET, POST | liste ou création Task + métadonnées |
| agenda-revisions/ | POST | correction TODO, target/version/motif |
| agenda-status/ | POST | statut, compte rendu, référence d'acte facultative |
| agenda-history/ | GET | target UUID de plan, avant/après Task + métadonnées |

Écritures : schema_version=1, operation_uuid UUID, décimaux en chaînes, références UUID aquatiques et entiers des catalogues financiers. Les identifiants étrangers sont masqués par 404. Montant strictement positif à deux décimales, pas de date de dépense future. category ou new_category exclusifs ; création de catégorie libre réservée OWNER. Affectation d'un achat existant : expense entier, expected_version=0 sans lien, sinon version courante, scope/cibles/motif ; aucun montant ni stock accepté.

Lectures : aquatic.read ; finances ajoutent finance.read. Dépenses : aquatic.finances.manage + expenses.create + droits de lecture ; correction/attribution OWNER. Agenda : aquatic.agenda.plan ou aquatic.agenda.progress + aquatic.read ; restrictions d'auteur et statut supplémentaires. Le propriétaire conserve la politique HTTP existante. Droits aquatiques exclus des grants offline. Capabilities ajoute finance_write sans changer commerce_write.

Filtres : from/to dates, species entier, cohort/installation UUID ; dépenses scope ; agenda status/mine/reminders. Paramètres inconnus refusés. limit 1..100, curseurs signés liés ferme/route/filtres ; listes mutables liées à business_revision. Historique AuditEvent paginé avec instant technique et snapshot. Ordre stable ; recharger si révision changée. Le client limite explicitement ses chargements à 10 000 résultats et demande un filtre plus ciblé.

## Migrations et qualification

core 0058 : shared_purpose + catalogue de permissions ; pisciculture 0016 : deux métadonnées, contraintes de scope/type/couple, clés uniques et étrangères. Additif ; 22 anciennes migrations inchangées, manifeste SHA256 étendu à 24. Porte de montée 3C précise, sauvegarde et opt-in requis, anciennes portes conservées. Aucune migration distante exécutée.

Test PostgreSQL 3C→3D : anciennes lignes/colonnes de charge alimentaire commune, vente/paiement/lettrage et tâche terrestre identiques ; défaut FEED, refus sans opt-in, montée synthétique et redémarrage normal. Les rapports externes phase3d contiennent les journaux effectivement exécutés, inventaires, captures, empreintes et limites. Qualification locale uniquement ; aucun APK/Web ou appareil réel.
