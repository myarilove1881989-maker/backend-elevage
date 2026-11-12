# Pisciculture phase 2B — contrat local

Base : edb7dcedc46f1566dfca3fd153445f43c4d277e4. Développement sur
feature/pisciculture-phase2b-20261112 dans la copie D: existante.
Aucune autorisation de publication ou de migration réelle.

## Journal et conservation

StockFact est append-only (ORM et trigger PostgreSQL). Auteur, installation,
cohorte connue, date effective, date d’enregistrement, motif, observations,
UUID de commande et liens de parenté/compensation sont conservés.
Types distincts : MORTALITY, SUSPICION, RECONCILE, INVENTORY, ASSIGN_CREDIT, CANCEL.

Le moteur rejoue le journal phase 1 et les nouveaux faits dans l’ordre
occurred_at, recorded_at, identifiant stable. G(c,i) reste le compte attribué
à une cohorte ; U(i) contient les pertes/corrections négatives non rapprochées ;
P(i) contient les crédits positifs non affectés.
Vivant physique installation/ferme = somme G - U + P.
P est exposé séparément et ne donne aucune disponibilité de transfert.

Mortalité connue : débit de la position identifiée. Mortalité inconnue :
augmentation de U sans débit arbitraire de cohorte. Suspicion : aucun débit.
Rapprochement : débit des cohortes éligibles et réduction simultanée de U,
sans seconde baisse du vivant. Attribution partielle permise.

Chaque attente conserve les cohortes présentes à son instant effectif.
Une cohorte arrivée après ne devient pas éligible. Chaque préfixe est vérifié :
soldes non négatifs, réserve conservatrice et faisabilité intégrale de toutes
les attentes par flot maximal. R(c,i)=somme des attentes éligibles, plafonnée
à G pour la projection ; disponibilité=max(0,G-somme des attentes éligibles).
Aucun transfert ne déplace une attente vers son bassin destinataire.

Une création de cohorte ou un transfert antidaté est aussi rejoué avec tous
les faits suivants. Conflit de préfixe ou modification de l’éligibilité
historique : 409 TIMELINE_CONFLICT et rollback intégral. Les versions des
positions/cohortes sont mises à jour lorsque la projection change. Le journal
est la source ; aucun stock terrestre ou financier n’est écrit.

## Inventaires et annulations

Le comptage est comparé au vivant physique à la date du constat, incluant les
attentes et crédits déjà enregistrés. 1000 brut - 10 en attente, constat 990 :
écart 0 ; constat 985 : correction -5 distincte des 10 mortalités.
Un écart négatif devient une attente de type INVENTORY à rapprocher.
Un écart positif devient P, puis ASSIGN_CREDIT l’affecte explicitement à une
cohorte ouverte existante, sans changer le vivant physique.

Un crédit non affecté doit d’abord être affecté ou annulé avant un nouvel
inventaire différent ou une mortalité inconnue. Ce refus explicite évite de
supposer l’origine de poissons non identifiés. Un inventaire identique, sans
écart, reste enregistrable. Pas de création implicite de cohorte/naissance.

CANCEL ajoute un fait compensatoire daté : il ne supprime ni ne modifie
l’original. Une attente ne peut être annulée qu’après compensation des
rapprochements dépendants ; même règle pour un crédit et ses affectations.
L’annulation d’un rapprochement restaure le brut et l’attente, avec vivant
inchangé. Une annulation ne peut être annulée à son tour ; une nouvelle
opération justifiée reste nécessaire. Un constat sans écart n’a aucun effet
de stock à compenser (NO_EFFECT_TO_CANCEL). Deux compensations d’un même fait
sont interdites. Une réintroduction dans un bassin inactif ou une cohorte
clôturée est refusée. Le contrôle de tous les préfixes s’applique aussi aux
compensations antidatées.

## Droits et routes v1

Toutes les écritures : enveloppe operation_uuid, schema_version=1, occurred_at
avec fuseau, reason obligatoire, note facultative. Date future au-delà de la
tolérance technique existante refusée. Entiers stricts, références du tenant,
module activé, vérification des permissions et preuve appareil réutilisées.
Verrou de ferme, nouveau contrôle d’accès, commande et effets atomiques ;
un rejeu du même auteur/UUID/payload retourne le résultat original.

- GET/POST /api/pisciculture/v1/mortalities/ : installation, quantity >0,
  cohort facultative, suspected booléen. Droit aquatic.losses.create.
- POST loss-reconciliations/ : pending, allocations [{cohort,quantity}].
- GET/POST inventories/ : installation, observed_count >=0,
  expected_revision obligatoire.
- POST inventory-credits/assign/ : credit, cohort, quantity.
- POST stock-cancellations/ : target.
- GET stock-state/ : état courant ou as_of ; installation/cohort facultatifs.
- GET stock-history/ : faits chronologiques paginés ; installation, cohort,
  from, to, as_of et curseur signé lié au tenant/filtres.

Lecture aquatic.read. Rapprochement, inventaire, affectation et compensation :
rôle OWNER uniquement, même si toutes les permissions sont déléguées à un
opérateur. expected_revision facultatif sur les autres mutations permet un
contrôle optimiste supplémentaire. Erreurs de validation 400, droits 403,
références 404 et conflits métier 409. Aucun DELETE/PATCH des faits.

L’historique conserve les valeurs originales et expose séparément l’état
courant canceled/remaining. Pour l’effectif historique, utiliser stock-state
avec as_of ; les états courants d’un fait ne sont pas un instantané passé.

## Lecture des indicateurs et pesées

Le tableau de bord ferme/bassin utilise le vivant ajusté, avec U et P visibles.
Un filtre cohorte/espèce ne ventile jamais U ou P arbitrairement : brut,
quality=UNRECONCILED et bornes conservatrices si une attente le concerne.
Les ventilations par espèce/installation signalent aussi cette incertitude.
Mortalités confirmées et écarts d’inventaire restent distincts.

Les nouvelles références de pesée utilisent le journal unifié à leur date.
L’instantané d’une ancienne pesée ne change pas. La lecture signale séparément
un effectif actuel incertain ou une référence modifiée. Aucun nouveau seuil
d’eau ni calcul flottant de biomasse ajouté.

## Schéma et qualification

Migrations additives : core 0053 (permission), pisciculture 0006 (StockFact),
0007 (immutabilité SQL). Anciennes migrations et empreintes préservées.
Manifeste/registre/plan exacts ; montée depuis 0050, phase 1 ou phase 2A
seulement avec autorisation et empreinte de sauvegarde explicites.
Le dispatcher connaît le couple exact core 53/0053, sans élargissement général.

Lanceur tools/qualify_phase2b.py : D: obligatoire, PostgreSQL synthétique
127.0.0.1:57851, cluster backend/.local/phase2b-postgres, temporaires dans
phase2b/tmp. Connexions externes interdites et arrêt dans finally.
Voir phase2b/RAPPORT-PHASE2B.md parent pour les tests réellement exécutés.

Limites : pas de fusion exposée, création d’une nouvelle cohorte depuis crédit
non implémentée, pas de point de contrôle de performance à grand volume. Le
rejeu complet sous verrou privilégie la cohérence ; benchmark requis avant
publication. Aucune nouvelle synchronisation hors ligne, alimentation,
finance, reproduction ou récolte.
