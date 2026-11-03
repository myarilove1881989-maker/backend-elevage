# Stocks & Santé — phase 3, qualification locale

Date : 3 novembre 2026. Aucun déploiement, push, PR ou changement de production.
Branche dédiée dans les deux dépôts : `feature/stocks-sante-phase3-20261102`.
Bases conservées : backend `222d7fd`, Flutter `b5ce178`, socle backend phase 1 `72a2d7d`.

## Résultat et périmètre

La distribution existante est reliée au registre physique des aliments. Les approvisionnements et distributions utilisent les déclarations DEPENSE et ALIMENTATION et la file chiffrée existante. Les nouveaux vaccins, formulaires de transfert, cartes du tableau de bord et rappels ne font pas partie de cette phase.

Une entrée de 500 kg puis une distribution de 80 kg produit un solde de 420 kg. La distribution crée une seule sortie, aucune dépense. La pesée reste indépendante. Aucune reprise automatique des anciennes distributions textuelles n'est effectuée.

## Django

- Service commun aux requêtes HTTP et aux déclarations signées : verrou de l'exploitation, validation exacte en kg, UUID idempotent et contrôle de l'auteur initial, des droits actuels et du contrat signé.
- Lien protégé entre ConsommationAliment, produit, position source et opération immuable. Migrations additives 0044 et 0045 ; aucune transformation des anciennes lignes. Protection PostgreSQL contre modification/suppression du lien.
- Stock partagé par espèce ou réservé au seul lot destinataire ; refus des autres lots, espèces et exploitations. Lot clôturé et stock insuffisant refusés.
- Corrections versionnées et annulations compensées, avec motif et historique conservé. Une répétition renvoie le même résultat. L'annulation générique ne peut pas contourner ce service.
- Transfert explicite propriétaire : POST `/feed-transfers/`, UUID, produit, espèce, source_lot, target_lot, quantité et motif. Réutilisation du service de transfert du socle ; pas de formulaire Flutter de transfert.
- Instantané confirmé dans le cache existant et les reçus, avec révision et intervalles des séquences déjà appliquées par appareil. Cette couverture empêche un double calcul après perte d'une réponse.
- Conservation des versions et des annulations de distributions dans les reçus, afin d'éviter leur réapparition dans un historique local ancien.
- Un conflit non appliqué peut uniquement être classé sans effet par le propriétaire. Cela ne supprime pas la déclaration, ne réapplique pas des droits révoqués et ne modifie ni argent ni stock.

## Flutter et fonctionnement hors ligne

- Formulaire de distribution existant : aliment stable du référentiel, espèce/lot du parcours Production existant, choix partagé/réservé, quantité en kg et historique.
- Présentation séparée du stock confirmé, du delta local et du disponible estimé. Les estimations utilisent des milli-kilogrammes entiers, sans arrondi flottant.
- Approvisionnement dans le formulaire Dépenses existant et création d'une distribution dépendante avant sa synchronisation, y compris avec un nouveau produit local.
- Une seule base chiffrée et une seule file de synchronisation. Version du schéma local maintenue à 9 ; aucune suppression/réinitialisation des données Android.
- UUID et contenu d'origine conservés lors des doubles clics, réponses perdues, interruptions et reprises. Les déclarations en conflit restent dans le registre ; le stock estimé n'inclut pas les opérations connues refusées.
- Les corrections hors ligne sont versionnées, conservées et soumises au serveur ; leur delta n'est pas deviné localement avant réception de l'instantané confirmé. Elles restent signalées comme à examiner.
- L'accès hors ligne doit être renouvelé pour disposer du marqueur signé `feed_stock_contract=1`. Les anciens accès ne sont pas réécrits. Un premier chargement connecté du référentiel/stock est requis.
- Si aucun référentiel compatible n'est disponible, le parcours historique affiche explicitement une distribution non rattachée et sans déduction physique ; aucune correspondance par texte n'est inventée.

## Compatibilité et limites

Calculs financiers, achats d'animaux, ponte, croissance, agenda et anciennes distributions conservés. Les catégories d'aliments restent identifiées par la liaison stable de phase 2, même après renommage.

Une estimation locale peut différer du serveur lorsqu'un autre appareil consomme le stock. Le serveur arbitre sous verrou ; le refus reste récupérable et visible. Le classement d'un conflit ne recrée pas automatiquement une opération : une nouvelle opération autorisée exige une décision explicite.

Pas de recette sur tablette physique : aucun appareil disponible. Les tests de persistance utilisent la vraie base chiffrée locale sur poste de développement, sans données d'exploitation. Pas d'installation Android ni de publication.

L'instantané est borné à 1 000 produits et 10 000 intervalles de couverture. Un dépassement échoue explicitement et nécessitera pagination/compaction étudiée avant une exploitation à cette échelle. Le transfert hors ligne et l'approvisionnement d'un lot créé localement non encore identifiable ne sont pas de nouveaux parcours fournis ici.

## Vérification

Qualification sur données synthétiques, sans requête de production :

- PostgreSQL : **189 tests réussis** dans la suite finale (116,085 s), migrations du schéma de test appliquées, aucune dérive détectée et contrôle Django sans anomalie. Modules : test_feed_stock, test_feed_purchases, test_supply_foundation, test_feed_distribution_api, test_egg_feed_api, test_owner_http_maintenance, test_terrain_reconciliation, test_terrain_reversal, test_terrain_transport, test_terrain_operations, test_offline_permissions, test_permission_foundations. Commande : `./tools/dev.ps1 test -Postgres -Tests <modules core.tests ci-dessus>`.
- Flutter natif sur poste : **146 tests de régression réussis**, puis **6 tests ciblés** des validations finales et **8 tests ciblés** après ajustement de la confirmation d'annulation. Ces suites se recouvrent ; les nombres ne sont pas additionnés. Commande : `./tools/dev.ps1 test -Tests <fichiers test/...dart>` ; voir les fichiers de tests ajoutés et les modules existants dépenses, permissions, indicateurs financiers, sécurité réseau, transport, synchronisation et supervision.
- Chrome : **19 tests réussis** de calcul exact, formulaires d'achat/distribution, parcours historiques, dépendances et conflits, puis **4 tests ciblés réussis** après l'ajustement final d'annulation (dont trois déjà présents dans la première suite, soit 20 scénarios distincts). Tests automatisés sur réponses synthétiques, pas de recette complète contre un serveur publié.
- Analyse Dart ciblée : aucune erreur ni avertissement ; restent des indications de style de niveau information (accolades et syntaxe nullable). Le SDK Flutter local incomplet impose l'appel direct à l'analyseur Dart épinglé plutôt que son enveloppe Flutter.
- Les tests Chrome ont nécessité de servir uniquement `/canvaskit/*` depuis les fichiers du SDK épinglé au navigateur de test local. Aucune modification du SDK ou du produit. SHA-256 : JS `6018b12bd8ed37e6952eee72d59c1cf7ad707ccdb74a0a45190010f77edd9068` ; WASM `ae8ff1d858140f7b1300ced3fa89fb8c9dce0a400a0f4f1e11f6dcfb3315fdcf`.

Les scénarios couvrent 500/80/420 kg, distributions successives, insuffisance, répétition, achat local avec produit nouveau, dépendance reçue dans le désordre, interruption/redémarrage de la base chiffrée, réponse perdue et couverture d'instantané, conflit Web/appareil, révocation, clôture de lot, correction et annulation tracées, ancien texte non repris, isolation et finances. Un test de l'annulation HTTP répétée garantit aussi qu'une ligne déjà annulée retrouve son résultat historique sans deuxième mouvement, tout en conservant la confirmation explicite existante.

Les journaux sont locaux et ignorés par Git : `.local/phase3-postgres-verified.log`, `.local/phase3-regression.log`, `.local/phase3-final-guards.log`, `.local/phase3-cancel-final.log`, `.local/phase3-chrome.log`, `.local/phase3-chrome-cancel.log`. Le dossier Android `android/build/`, déjà non suivi avant cette phase, est exclu de la sauvegarde Git.

## Retour arrière

Avant mise en service, conserver la qualification isolée ; aucune migration de production n'a été effectuée. Pour revenir au code de phase 2 en qualification, utiliser les références de base dans un environnement distinct.

Après création de mouvements réels, ne pas supprimer les tables, liens, mouvements ou déclarations en attente et ne pas rétrograder la base utilisateur. Suspendre les nouveaux parcours concernés et livrer un correctif compatible conservant les faits. Garder le traitement/consultation des originaux en attente jusqu'à leur issue ; ne pas remettre un ancien client devant des opérations qu'il ne sait pas représenter. Une restauration de sauvegarde nécessiterait une procédure séparée incluant la réconciliation des appareils. Les migrations inverses sont réservées aux bases synthétiques de qualification sans faits métier à conserver.

La phase suivante attend la validation de l'utilisateur ; aucune publication n'est autorisée par cette qualification.
