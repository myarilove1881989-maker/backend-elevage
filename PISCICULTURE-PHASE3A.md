# Pisciculture phase 3A — contrat local

Base backend aec363eb19da2a580c62a403f43472805cd8483e. Branche feature/pisciculture-phase3a-20261113, copie D: existante. Développement et qualification locale uniquement ; aucune autorisation de publication ou de migration distante.

## Audit préalable et choix

Référence métier : cahier du 8 novembre 2026, section 10 (observations simples, photos facultatives, registre de soins, référentiel sanitaire), audit du 11 novembre, conception 0B–0C et contrats qualifiés 1/2A/2B/2C.

SupplyProduct VACCINE est le référentiel existant compatible : identité UUID, nom, archivage, unité physique dose. Il reste utilisé directement, sans copie/import automatique. Aucun achat n’est converti en traitement. Les autres produits ne rentrent pas dans le contrat fermé FEED/VACCINE ni dans ses projections offline : extension SanitaryProduct réservée aux types TREATMENT/OTHER, unité d’administration explicite, informations libres, sans stock séparé. Création OWNER, refus d’un nom déjà présent dans le référentiel sanitaire ou VACCINE de la même ferme. Les vaccins restent gérés dans leur référentiel existant ; aucune nouvelle marque ou posologie par défaut.

Vaccination, HealthTask, Task et les mécanismes de consommation sanitaire existants imposent un Lot terrestre et des flacons/doses. Ils ne représentent pas un soin global de bassin multicohorte sans adaptation de leurs contrats. Cette phase n’altère donc ni ces actes ni leurs tâches : uniquement des actes effectivement administrés, pas de nouveau planning/rappel. Les stocks, reçus et dépenses sont inchangés. stock_automation=false, planning_supported=false sont explicites.

Aucun stockage privé d’image, contrôle d’accès à une photo ni infrastructure d’upload sécurisée adaptée n’a été trouvé dans les modèles/configurations audités. photo_supported=false ; aucune saisie de fichier ni URL de photo acceptée. Pas de stockage public ou installation de dépendance pour contourner cette limite.

## Modèles

SanitaryProduct : TenantEntity existant (UUID, exploitation, version), name, type TREATMENT/OTHER, unit parmi dose/mL/L/g/kg/unit, information facultative, archived, created_by. Réservé aux produits incompatibles avec SupplyProduct VACCINE. Pas de quantité en stock ni prix ni indication thérapeutique inventée.

HealthRecord : fait immuable, kind OBSERVATION/TREATMENT, exploitation, acteur, UUID de commande, installation, cohorte facultative, dates effective/enregistrement, précision DATE/DATETIME, date métier éventuelle, note, instantané de population/espèces/provenance.

OBSERVATION : symptômes et comportement libres ; au moins l’un des deux décrit. Aucun diagnostic obligatoire, aucune mortalité ou correction d’effectif implicite. Les décès réellement comptés restent dans la phase 2B.

TREATMENT : exactement un produit SupplyProduct VACCINE ou SanitaryProduct, instantané de son nom/type/unité, quantité Decimal(18,6) strictement positive, unité explicite compatible, motif, observation liée facultative, ongoing déclaré. L’administration est explicitement confirmée ; PLANNED et les champs de stock/dépense ne sont pas acceptés. Aucun effet dans Depense, SupplyOperation, SupplyMovement, Vaccination, Task ou StockFact.

Précaution : withdrawal_days nullable entier 0..36500 (borne technique), withdrawal_source libre obligatoire si délai connu, withdrawal_until calculé. Inconnu n’est pas zéro. Aucune valeur réglementaire par défaut. Zéro explicitement documenté est conservé ; ce n’est pas une autorisation de vente.

Date/heure connue : date de fin = occurred_at + délai en jours de 24 heures. Date seule : occurred_at au début de la journée avec fuseau explicite ; date métier conservée ; date de fin métier = business_date + jours et borne exclusive au début du jour suivant, fin de journée conservatrice. Cette convention est exposée (END_OF_DAY_CONSERVATIVE), ne remplace pas le document applicable et ne prétend pas résoudre des règles réglementaires.

Rectification complète OWNER : nouveau HealthRecord de même type, supersedes OneToOne, root_id stable, motif obligatoire, original conservé, pas de suppression silencieuse. Une référence ne peut être rectifiée deux fois ; on rectifie sa dernière version. Les précautions et traitements en cours utilisent la dernière version seulement. L’historique renvoie aussi les versions précédentes, avec is_current/superseded_by. Pas d’annulation destructrice, de nouvelle dépense ni de second débit de stock.

## Portée et population

Appartenance des références vérifiée côté serveur. Présence de la cohorte dans le bassin à la date effective vérifiée par le journal 2B. Sans cohorte, toutes les populations présentes sont conservées dans eligible_cohorts, species_ids et scope_snapshot ; la dose reste globale et n’est pas répartie. Une population arrivée ensuite n’est pas automatiquement concernée. Saisie rétrospective possible pour une cohorte fermée/installation inactive si la présence historique est établie ; aucun soin futur au-delà de la tolérance technique de cinq minutes.

Le lien facultatif vers une observation impose même ferme, même installation, dates compatibles et absence de conflit entre cohortes identifiées. Lier une observation n’en change pas la portée, les symptômes ou l’effectif. Les références aux versions originales restent auditables après rectification.

## API additive /api/pisciculture/v1/

- GET/POST health-products/ : catalogue combiné avec source SUPPLY/SANITARY et UUID, unités explicites, informations connues, archivage, capacités. POST OWNER crée uniquement SanitaryProduct : id UUID, name, type TREATMENT/OTHER, unit, information facultative.
- GET/POST health-observations/ : installation UUID, cohort UUID/null facultatif, occurred_at avec fuseau, time_precision DATETIME par défaut ou DATE avec business_date, symptoms, behavior, note facultative. Pas de diagnostic/photo/mortalité implicite.
- GET/POST health-treatments/ : même portée/date ; supply_product UUID OU sanitary_product UUID ; quantity chaîne décimale strictement positive ; unit ; administration_status=ADMINISTERED obligatoire ; reason ; note ; observation UUID/null facultative ; ongoing facultatif false ; withdrawal_days null par défaut et withdrawal_source facultative sauf délai connu.
- GET health-history/ : historique chronologique complet observations/traitements et rectifications, versions originales incluses.
- GET health-records/{uuid}/ : détail immuable avec population, références, dates et auteur.
- POST health-corrections/ OWNER : target UUID, reason, exactement un remplacement observation ou treatment complet, de même type que la cible. Le remplacement utilise les champs métier de création sans enveloppe UUID imbriquée.
- GET health-state/ : traitements actuels déclarés en cours et précautions actives/inconnues, toutes dates indépendantes de la période de l’historique, paginés.
- GET health-sale-check/ : contrat de vérification préparatoire 3C décrit ci-dessous. Aucun raccord aux ventes existantes.

Toutes les commandes : operation_uuid et schema_version=1. Champs inconnus refusés proprement avec 400. Quantités sous forme de chaînes exactes, entiers explicites pour les jours (bool/float refusés), unités sensibles à la casse et sans conversion implicite. Symptômes/comportement/note <=2000 caractères ; motif/source/informations <=1000 ; nom produit <=120.

Lecture : aquatic.read, exploitation dérivée de l’utilisateur. Écritures : aquatic.health.observe / aquatic.health.treat distincts ; catalogue et rectification OWNER seulement. Droits/révocation réévalués dans le verrou de ferme avant rejeu UUID ; mêmes preuves d’appareil primaire/signature que les autres commandes piscicoles pour les opérateurs soumis à cette politique. Aucun ajout aux contrats signés offline.

Historiques : filtres installation, cohort, species, from/to ISO avec fuseau, kind OBSERVATION/TREATMENT pour l’historique global, limit 1..100 et cursor signé lié au tenant/route/filtres/snapshot. Les filtres cohorte/espèce incluent les actes globaux dont l’instantané comprend cette population, sans attribuer une dose individuelle. Le curseur des précautions actuelles est aussi lié à la révision métier : en cas de modification pendant la pagination, le chargement échoue et exige une actualisation au lieu de masquer une rectification. Le contrat de vente lit sous verrou de ferme pour une révision cohérente. Le registre de précautions courantes accepte installation/cohort/species et pagination mais refuse from/to : une période d’historique ne masque pas un délai ouvert.

Même transaction, verrou de ferme, audit, commande et empreinte que les phases précédentes. Rejeu identique = même résultat, même UUID sans duplication. Autre contenu =409. Stock/argent/effectifs jamais modifiés. Dates/références invalides 400/404 ; droits 403 ; cible déjà rectifiée 409 ; mutation PATCH/DELETE des faits indisponible. Immutabilité ORM et trigger PostgreSQL.

## Contrat préparatoire de commercialisation

GET health-sale-check/?installation=UUID&cohort=UUID facultatif&as_of=ISO facultatif (maintenant par défaut). Contrôle de présence à la date pour la cohorte sélectionnée. Réponse contract_version=1, périmètre, date, révision métier, matches référencés aux versions courantes, source/date de délai, statut, sale_authorized=null et verification_required=true. Jamais une autorisation de vente, même sans traitement enregistré ou après délai écoulé.

États : NO_RECORDED_TREATMENT, DOCUMENTED_PERIOD_ELAPSED, RESTRICTION_RECORDED, UNKNOWN_WITHDRAWAL, ONGOING_REVIEW, TRACEABILITY_REVIEW. Message inconnu exact : « Délai avant commercialisation non renseigné — vérification nécessaire ».

La portée enregistrée est le bassin et les populations présentes au moment du soin. Un transfert ultérieur d’une cohorte exposée rend la traçabilité des fractions nécessaire : parcours des transferts vers les installations atteignables, sans transformer toutes les fractions de cohorte en poissons sûrement traités et sans toucher les autres cohortes. scope_match=TRACEABILITY_REQUIRED et revue requise. Une arrivée antidatée modifiant les populations connues à la date d’un acte global produit HISTORICAL_CONTEXT_CHANGED, sans réécriture du constat original ni attribution automatique à la nouvelle cohorte. Une vérification vétérinaire/documentaire et la traçabilité de récolte 3C resteront à établir dans la phase concernée.

## Migrations et qualification

- core/0055_remove_membershippermission_membership_action_known_and_more.py : seuls droits sanitaires HTTP ajoutés, après 0054 et pisciculture 0009.
- pisciculture/0010_sanitaryproduct_healthrecord_and_more.py : nouveaux modèles, index et contraintes, dépend de 0055.
- pisciculture/0011_health_record_immutable.py : trigger append-only, réutilise la fonction de rejet phase 1.

Treize anciennes empreintes conservées et trois nouvelles ajoutées au manifeste. Garde exact étendu depuis le préfixe 2C (13 migrations), sans élargir ni désactiver les gardes précédents ; opt-in et empreinte de sauvegarde nécessaires uniquement pour une montée de schéma. Aucune migration distante.

Runner tools/qualify_phase3a.py : base synthétique PostgreSQL sur 127.0.0.1:57855, fichiers backend/.local/phase3a-postgres, temporaires ../phase3a/tmp ; connexions externes interdites, cluster arrêté à la sortie. Voir ../phase3a/RAPPORT-PHASE3A.md pour les résultats effectivement exécutés et limites.
