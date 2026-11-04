# Bilan de cycle — contrat 1

API GET `/api/lots/{id}/cycle-report/`, comparaison optionnelle `?compare={id}`.
Lecture membre actif finance.read, lot et référence bornés à l’exploitation.
La transaction sérialise la lecture avec les écritures de l’exploitation sans changer sa révision.
Aucune migration. Aucun format signé hors ligne ou endpoint historique modifié.

## Comptabilité enregistrée
Ventes actives − achats actifs d’animaux − dépenses actives du lot.
Les dates du lot sont inclusives ; lot ouvert : aujourd’hui, lot fermé : date_fin.
Les opérations hors période sont exclues et signalées. Les annulations sont exclues
par les managers actifs. Les règlements affectés et créances sont calculés à la
consultation, y compris règlements postérieurs à la clôture. Ils ne sont pas ajoutés
aux ventes. Ce résultat ne garantit pas un bénéfice net ni une trésorerie disponible.
Les anciennes consommations valorisées sans dépense ne sont pas ajoutées aux charges.
Leur existence est signalée. Aucune dépense n’est réaffectée automatiquement.

## Effectifs et production
Stock daté = mouvements signés jusqu’à la référence + compensations datées.
Effectif de départ = solde antérieur + entrées vivantes documentées le jour de début.
Aucune entrée documentée à cette date et aucun solde antérieur : indisponible.
Mortalité = décès déclarés / (entrées vivantes du cycle + solde antérieur positif).
Les mort-nés restent séparés. En présence de compensations, mortalité et naissances
nettes restent indisponibles plutôt que d’inférer le motif d’une compensation.
Les journées sans saisie ne sont pas reconstruites. Les pesées sont des échantillons.
Pas de poids vendu, rendement carcasse, coût/kg ni indice de conversion inventé.
Stock d’œufs : explicitement stock courant, distinct du stock animal daté.

## Consommables : analyse informative indépendante
Aliments : replay chronologique de toutes les entrées et sorties antérieures à la
référence, par produit, espèce et réserve physique (partagée ou réservée).
CMP mobile : (valeur restante + montant du nouvel achat)/(kg restants + kg reçus).
Une distribution consomme au CMP de sa réserve à cet instant. Calcul Decimal,
arrondi monétaire à deux décimales uniquement à la restitution.
Le prix provient de la dépense active du reçu, et la quantité du reçu doit égaler
l’entrée. Sans reçu valorisé, stock suffisant ou historique simple : non calculable.
Tout transfert, correction ou annulation dans l’historique connu du produit/espèce, y compris après
la référence, exclut ce produit de la valorisation ; aucune reconstruction arbitraire.
Vaccins : doses administrées ou perdues × coût du reçu / doses du reçu identifié
par flacon. Les corrections/annulations rendent la valorisation indisponible mais
les doses techniques utilisent la version active de la vaccination.
Les stocks restants partagés ne sont pas répartis entre cycles. Les stocks réservés
restants sont informatifs ; doses périmées = date de péremption antérieure à la référence.
Aucun coût de consommation ou stock restant n’entre dans le résultat comptable.

## Comparaison et hors ligne
Deux lots distincts de même espèce et production ; écart = premier − référence.
Écart relatif = écart / valeur absolue de la référence ; référence nulle : indisponible.
Écarts de taux en points. Pas de classement automatique ni d’exhaustivité présumée.
Bilan mémorisé explicitement provisoire, date de dernière actualisation visible.
Les opérations locales non confirmées ne sont pas intégrées aux chiffres serveur.
Les résultats sont recalculables après correction/réouverture, jamais un arrêté immuable.

## Compatibilité et livraison
Android 118 non reconstruit et non publié. Contrats API historiques, signatures,
permissions et files inchangés. Le nouvel écran est consultatif.
Retour arrière : artefacts précédents backend 3f7cdaa et Web 212454a, sans restauration
ni migration de base. Publier backend puis Web, auto-déploiements conservés désactivés.
