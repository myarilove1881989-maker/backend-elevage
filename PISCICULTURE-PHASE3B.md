# Pisciculture phase 3B — contrat local

Base backend 933bf9d74bccca8551747718efec5a211897bb56. Branche feature/pisciculture-phase3b-20261113, copie existante sur D:. Qualification locale uniquement, aucune publication ni migration distante autorisée.

## Audit et invariant principal

Cahier du 8 novembre 2026, section 10 ; audit du 11 novembre et conception 0B–0C ; contrats et rapports 1/2A/2B/2C/3A. Les naissances terrestres Mouvement(NAISSANCE, Lot, lot_origine) ne représentent pas une origine de cohorte piscicole : elles ne sont ni utilisées ni modifiées. Les services piscicoles d’effectifs, cohortes, audit, droits, UUID et chronologie sont réutilisés.

Créer un groupe de géniteurs, une reproduction, une ponte, une incubation ou une éclosion n’ajoute AUCUN poisson au stock vivant. La seule entrée 3B dans le vivant est une PLACEMENT explicite, quantité réellement comptée, qui crée une cohorte par le service phase 1 existant : un INITIAL et ses CountLine/Position habituels. Aucun second moteur d’effectif, Lot terrestre caché, naissance financière ou consommation de stock créée.

Une éclosion COUNTED de 100 n’ajoute rien au vivant ; deux mises en élevage 40 puis 60 ajoutent exactement 100 et épuisent la source. Une mortalité ultérieure ne restitue pas les alevins à l’éclosion. Une quantité estimée ou inconnue ne fournit aucune disponibilité confirmée ; il faut une déclaration de comptage réel distincte. Zéro constaté n’est pas null.

## Modèles

BroodGroup (TenantEntity existant) : UUID, nom, espèce existante avec profil aquatique actif, installation, cohorte d’origine nullable, date/heure et précision, mâles/femelles nullable, note, ACTIVE/INACTIVE, auteur, date d’enregistrement, instantané de référence. Groupe descriptif, sans déplacement/réserve de poissons. Si origine connue : même ferme/espèce, présence historique dans l’installation, somme des sexes connus <= brut de référence ; aucune répartition sexuée déduite. Le lien reste descriptif et ne certifie pas une partition exhaustive/disjointe de cohortes entre groupes.

Reproduction : UUID, groupe, espèce dérivée du groupe (pas de choix divergent), installation, date, technique libre facultative, note, OPEN/CLOSED, auteur et instantané de version du groupe consulté. Date >= constitution du groupe, groupe actif lors de la saisie. Une cohorte d’origine identifiée doit être présente au lieu/date indiqués. Sans origine, aucune cohorte/quantité n’est inventée. Pas de protocole propre à une espèce codé en dur.

BreedingFact append-only : SPAWN, INCUBATION, HATCH, COUNT, PLACEMENT ou VOID ; ferme, reproduction, installation, auteur, commande UUID, dates effective/enregistrement, précision, quantité entière nullable, qualité COUNTED/ESTIMATED/UNKNOWN, parent facultatif typé, motif/note et liens de cohorte/événement de stock lorsque nécessaires.

- SPAWN : œufs comptés/estimés/inconnus, aucun parent imposé.
- INCUBATION : étape facultative ; parent SPAWN facultatif, quantité d’œufs facultative selon qualité.
- HATCH : alevins observés comptés/estimés/inconnus ; parent SPAWN/INCUBATION facultatif. Plusieurs éclosions/éclosions partielles autorisées. Chaque constat est une source distincte déclarée, jamais obtenu par conversion d’œufs.
- COUNT : comptage réel total d’un lot d’éclosion, y compris les quantités déjà mises en élevage ; ce n’est pas un ajout/delta. Motif/justification et confirmation JSON true obligatoires. Original estimé/inconnu conservé. Aucun poisson vivant ajouté.
- PLACEMENT : quantité réelle >0, parent HATCH, nouvelle cohorte et lien unique vers son INITIAL. Cohort.origin={type:HATCH,reference:BREEDING:<uuid éclosion>}. La chaîne cohorte -> mise en élevage -> éclosion -> reproduction -> groupe -> cohorte d’origine éventuelle est explicite. Aucun rattachement rétrospectif d’anciennes cohortes par supposition.
- VOID : compensation datée et motivée OWNER, original conservé ; un fait ne peut être annulé qu’une fois et une annulation n’est pas annulable dans ce lot.

Mises à jour de groupe : nom, installation, sexes connus/null, note et statut, avec version attendue et motif OWNER. Identité, espèce, cohorte d’origine et constitution ne sont pas réécrites. Le changement de statut/nom seul n’exige pas de retrouver des poissons au lieu initial. Révision d’installation/sexes liée à une cohorte : présence actuelle vérifiée. Reproduction : seul statut OPEN/CLOSED révisable avec version/motif OWNER. Les états précédents et motifs restent dans AuditEvent existant, consultables par API paginée. Fermer ne supprime ni ne déduit un solde d’éclosion ; rouvrir explicitement avant une nouvelle affectation.

## Comptabilité de source et chronologie

Le solde pré-mise-en-élevage est une preuve d’origine/allocation, pas un deuxième stock vivant :

confirmed_total = quantité initiale d’éclosion seulement si COUNTED, puis dernier COUNT réel actif ; sinon null.
placed_total = somme des PLACEMENT actives, indépendamment du vivant actuel des cohortes.
available_confirmed = confirmed_total - placed_total ; null si aucun comptage confirmé.

Rejeu de tous les faits dans l’ordre occurred_at, recorded_at, identifiant stable. Parents de la même reproduction, déjà présents et non annulés à la date ; toutes les étapes >= date de reproduction. Chaque préfixe respecte placed_total <= confirmed_total ; un comptage antidaté ne peut rendre une affectation future impossible. Une confirmation future ne finance pas une affectation antérieure. Un VOID restitue une allocation seulement à sa date effective et ne réécrit pas les préfixes anciens.

Pour plusieurs éclosions d’une reproduction, chaque source possède son comptage et ses affectations. Aucune quantité d’alevins calculée à partir des œufs, aucun plafond biologique ou taux universel supposé. Les déclarations d’éclosions distinctes doivent correspondre à des constats distincts : l’UUID protège la répétition technique, il ne certifie pas l’identité physique d’un lot saisi manuellement deux fois sous des opérations différentes.

Quantités entières explicites, 0..2 147 483 647 pour les constats/comptages, >0 pour mise en élevage ; bool/float refusés comme quantités. Qualité UNKNOWN exige quantity=null ; COUNTED/ESTIMATED exigent un entier. actually_counted accepte exclusivement le booléen JSON true, pas 1 ni une chaîne. Date/heure avec fuseau, futur au-delà de la tolérance de cinq minutes refusé. Date seule : time_precision=DATE, business_date et occurred_at au début de cette journée avec fuseau explicite ; convention conservée et contrôles de début de journée conservateurs. Pas de taux de fécondation/éclosion dans cette livraison : valeurs API null, jamais zéro inventé.

## Mise en élevage atomique

Verrou de ferme existant ; contrôle de droits, source, disponibilité à la date, absence de réutilisation d’un ID de cohorte ou de l’UUID enfant ; création de cohorte via execute(cohort) avec UUID enfant déterministe ; une affectation à l’installation choisie, un INITIAL, puis BreedingFact PLACEMENT unique. La source et le journal 2B sont rejoués, y compris leurs préfixes ultérieurs, dans la même transaction. Une incohérence ou exception annule cohorte, positions, événements, confirmation inline éventuelle et toutes les commandes. La révision de ferme est rafraîchie entre commande enfant et parent.

Une confirmation de comptage peut être fournie dans la mise en élevage par confirm_total et confirmation_reason, explicitement saisis et jamais dérivés de l’estimation. Elle et l’entrée sont atomiques. Les champs stage (facultatif) et code/nom de cohorte suivent le contrat phase 1 ; stade de la même espèce, aucune étape biologique choisie par défaut.

Ensuite : mêmes écrans et services de transferts, pertes, inventaires, pesées, alimentation et santé. Aucun traitement particulier des effectifs d’alevins n’est ajouté à ces moteurs. Une entrée antidatée modifiant l’éligibilité d’une perte déjà enregistrée est refusée par le rejeu existant.

## Corrections conservatrices

VOID d’une ponte/incubation/éclosion avec dépendances actives : refus ; annuler d’abord les dépendances explicitement. VOID d’un COUNT : retour à la confirmation active précédente, seulement si toutes les affectations restent couvertes. Les originaux demeurent dans l’historique.

VOID d’une PLACEMENT : seulement si la cohorte est intacte dans sa position initiale, sans autre événement, perte/inventaire/réserve, pesée, alimentation, soin ou lien de géniteurs dépendant. Contrôles conservateurs aussi pour les observations globales du bassin après l’entrée. Ajout d’un événement FRY_REVERSAL et d’une CountLine négative dans le journal existant, remise à zéro par sync et clôture de la cohorte. L’allocation redevient disponible à l’éclosion à la date de compensation. Aucune mortalité fictive, suppression de cohorte ou effacement d’événement. Après transfert, perte ou autre usage, l’annulation est refusée ; pas de moteur de compensation de tout un graphe aval dans ce lot.

## API additive /api/pisciculture/v1/

- GET/POST brood-groups/ ; GET brood-groups/{uuid}/.
- POST brood-group-revisions/ : target, expected_version, reason et seuls champs modifiables.
- GET/POST reproductions/ ; GET reproductions/{uuid}/ (étapes, éclosions et disponibilités).
- POST reproduction-revisions/ : target, expected_version, status OPEN/CLOSED, reason.
- GET/POST breeding-facts/ : historique paginé / création SPAWN, INCUBATION ou HATCH.
- POST hatch-counts/ : hatch, installation, occurred_at/précision, quantity totale, actually_counted=true, reason, note.
- POST fry-placements/ : hatch, installation destination, date/précision, quantity, actually_counted=true, id UUID/code/nom de nouvelle cohorte, stage facultatif, reason/note, confirm_total+confirmation_reason facultatifs mais indissociables.
- POST breeding-voids/ OWNER : target, occurred_at avec fuseau, reason.
- GET breeding-revisions/?group=UUID OU reproduction=UUID : AuditEvent de révision, auteur/date/motif, champs avant/après métier sélectionnés, paginé.

Toutes les mutations : operation_uuid, schema_version=1. Champs inconnus refusés avec 400. Les créations de groupe/reproduction exigent aussi id UUID. GroupInput : name, species entier, installation, cohort nullable, date/précision, males/females nullable et note. ReproductionInput : group, installation, date/précision, technique/note facultatives. StepInput : reproduction, kind, installation, date/précision, parent nullable, quality et quantity nullable cohérentes.

Lecture aquatic.read. Création groupe aquatic.breeders.manage ; reproduction/étapes aquatic.reproduction.create ; comptage et affectation aquatic.fry.stock. Affectation exige EN PLUS aquatic.cohorts.manage, puisque le service existant de création de cohorte est réutilisé avec ses propres contrôles. Révisions et annulations OWNER. Révocation et droits revérifiés avant rejeu ; preuve appareil primaire/signature existante pour opérateurs soumis à cette politique, propagée à la commande enfant. Aucun type/grant offline ajouté.

Listes : limit 1..100, cursor signé lié à ferme/route/filtres/snapshot. Filtres species, group, reproduction, installation, from/to avec fuseau ; statut ACTIVE/INACTIVE pour groupes et OPEN/CLOSED pour reproductions/historique lié ; kind pour historique. Les groupes et reproductions sont des métadonnées modifiables : curseur lié aussi à la révision de ferme, actualisation nécessaire si modification pendant pagination. Révisions : exactement un groupe/reproduction et pagination seulement. Les faits sont immuables et l’historique expose canceled sans les supprimer.

Rejeu même UUID/auteur/charge : résultat initial, aucune nouvelle cohorte ni déduction. Charge différente/capacité insuffisante/préfixe invalide/version périmée : 409 et rollback. Données invalides : 400 ; références hors ferme/inconnues : 404 ; droits : 403. Tous les liens sont tenant-scopés ; espèce dérivée de la reproduction pour les alevins.

## Migrations et garde

1. core/0056_remove_membershippermission_membership_action_known_and_more.py : seuls droits HTTP 3B, après 0055 et pisciculture 0011.
2. pisciculture/0012_broodgroup_reproduction_breedingfact_and_more.py : trois modèles et contraintes/liens/index, dépend de 0056.
3. pisciculture/0013_breeding_facts_immutable.py : trigger append-only, fonction de rejet existante phase 1.

Seize anciennes migrations et empreintes conservées ; trois nouvelles, dix-neuf empreintes vérifiées. Garde exact étendu au suffixe 3B depuis le préfixe 3A (16), sans relâcher les gardes précédents ; opt-in et empreinte de sauvegarde exigés pour une montée, pas pour redémarrage identique. Aucune migration distante exécutée.

Runner tools/qualify_phase3b.py : PostgreSQL synthétique isolé 127.0.0.1:57857, fichiers backend/.local/phase3b-postgres et temporaires ../phase3b/tmp. Réseau externe interdit, cluster arrêté à la sortie. Résultats réels, inventaire et limites : ../phase3b/RAPPORT-PHASE3B.md.
