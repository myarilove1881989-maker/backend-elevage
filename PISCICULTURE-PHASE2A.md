# Pisciculture phase 2A — qualification locale

Base backend : 353ac7595bd41d980b9034d3c97ab95b4e62128b.
Branche locale : feature/pisciculture-phase1-20261112. Aucun push ni déploiement autorisé.

## API additive v1

Préfixe /api/pisciculture/v1/. GET/POST weights/, water-readings/, observation-rules/.
Enveloppe de mutation : operation_uuid et schema_version=1. Même mécanisme atomique
de verrou ferme, audit, contrôle de révocation, preuve appareil opérateur, commande
idempotente et résultat que la phase 1. Aucun PATCH/DELETE d’observation ou de règle.

Droits distincts : aquatic.weights.create, aquatic.water.create,
aquatic.water.rules.manage. Lecture aquatic.read. Propriétaire ou permission
explicitement attribuée ; aucun ajout au contrat signé offline historique.

Pesée : exactement une cible cohort ou installation, occurred_at avec fuseau,
count_reference_at <= occurred_at ; soit sample_count entier positif et mass_kg
en chaîne décimale, soit direct_mean_g en chaîne décimale. Un bassin ne modifie
jamais la cohorte. Échantillon <= effectif de référence, effectif nul refusé.
Stockage des entrées Decimal, calcul Fraction exact, représentation numerator /
denominator canonique et decimal calculé à 60 chiffres significatifs pour affichage.
Une division périodique ne peut pas avoir une écriture décimale finie exacte :
la fraction conservée est alors la valeur exacte. Aucun arrondi intermédiaire
dans mean_g=mass_kg*1000/n ou biomass_kg=count*mean_g/1000.

L’instantané contient la date, les positions, les événements du journal, la
révision et les sources d’audit des métadonnées. Le recalcul d’une observation
future ne réécrit pas une ancienne observation ; les mouvements antidatés
ultérieurs ne révisent donc pas silencieusement son instantané. La biomasse
reste une estimation de population issue de l’échantillon, même si le journal
d’effectif est exact. Note libre pour documenter la représentativité/incertitude.

La réserve phase 1 est un crochet conservateur actuel, pas un journal de pertes
historique. Si elle touche une position de la référence, quality=UNRECONCILED,
biomass_kg=null ; gross_biomass_estimate_kg reste explicitement hypothétique.
Aucune distribution d’une perte inconnue, aucune fourchette inventée.
Les commandes de pertes/rapprochement/inventaire ne sont pas implémentées.

Eau : installation, occurred_at ; température temperature_c en °C, ph sans unité,
oxygen_mg_l en mg/L. Au moins une valeur non null ; zéro reste zéro. Seules les
valeurs mesurées sont évaluées, population par population à la date de mesure.
Les stades et codes système sont reconstruits depuis les modifications auditées.
Les modifications directes non auditées hors API ne sont pas reconstructibles.

## Règles datées

parameter : temperature_c / ph / oxygen_mg_l / weight_age_days.
scope : INSTALLATION + installation ; SPECIES_STAGE + species + stage exact ;
SYSTEM + system_code. lower/upper nullable, au moins une borne, lower<=upper ;
valid_from avec fuseau, valid_to facultatif et exclusif.
Pas de seuil universel. La fraîcheur utilise uniquement upper strictement positif
en jours, sans borne basse ni durée par défaut.

Une règle existante peut être remplacée avec supersedes=UUID et un valid_from
strictement ultérieur. La cible et le paramètre doivent rester identiques.
Le remplacement ferme logiquement l’ancienne règle à son début, sans la réécrire.
Une seule succession, chevauchements concurrents refusés sous verrou de ferme.
Les anciennes évaluations conservent la règle/version originale et ne sont pas
recalculées implicitement. Une règle sans successeur/fin reste applicable.

Résolution par paramètre : installation > espèce/stade exact > système. Pas de
population : seules les règles installation/système sont pertinentes. Toutes
les populations présentes sont évaluées séparément ; intervalles incompatibles
signalés, aucune moyenne. has_alert exige une règle pertinente et LOW ou HIGH.
UNCONFIGURED et CONFIG_CONFLICT ne produisent pas d’alerte biologique.
La politique de fraîcheur retenue à l’enregistrement est conservée ; l’âge
d’affichage évolue, la politique et la pesée restent inchangées.

Historiques weights/water : curseur signé lié au tenant/type/filtres, limite
1..100, ordre occurred_at/UUID et snapshot recorded_at. Filtres installation,
cohort, from, to. Aucun agrégat mélangeant observations de bassin et de cohorte.

## Migrations nouvelles

- core 0052 : extension exacte de la contrainte du catalogue de permissions.
- pisciculture 0003 : Observation et ObservationRule.
- pisciculture 0004 : triggers PostgreSQL append-only.
- pisciculture 0005 : référence de remplacement et contraintes NULL explicites.

Aucune migration historique modifiée. Manifeste SHA256 exact et plan ordonné.
Garde : état 0050 complet ou phase 1 exacte, autorisation explicite et empreinte
de sauvegarde exigées avant toute montée ; schéma final exact accepté sans écriture.
Le dispatcher de démarrage reconnaît le couple exact core 52/0052 et délègue
au même manifeste. Les autres états restent refusés. Ne jamais utiliser les
empreintes synthétiques des tests sur une base réelle.

## Vérification sur cet hôte

D:/ElevAge-Dev/.venv/Scripts/python.exe tools/qualify_phase2a.py pisciculture.tests

Lanceur D: uniquement, cluster synthétique backend/.local/phase2a-postgres,
127.0.0.1:57849, connexions externes interdites, arrêt dans finally.
Les journaux et résultats réels de la mission sont dans le rapport parent.
Pas de migration distante, d’intégration finances/alimentation, ni d’opération
offline piscicole. Une publication exige une mission et une validation séparées.
