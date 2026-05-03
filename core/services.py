from django.db.models import Sum, F, Case, When, FloatField
from .models import Mouvement, Depense


def dashboard_global(user):

    mouvements = Mouvement.objects.filter(
        lot__exploitation__proprietaire=user
    )

    depenses = Depense.objects.filter(
        lot__exploitation__proprietaire=user
    )

    # ===============================
    # AGRÉGATS MOUVEMENTS (OPTIMISÉ)
    # ===============================

    aggregates = mouvements.aggregate(
        achats=Sum(
            Case(When(type_mouvement='ACHAT', then=F('quantite')))
        ),
        ventes=Sum(
            Case(When(type_mouvement='VENTE', then=F('quantite')))
        ),
        mortalite=Sum(
            Case(When(type_mouvement='MORTALITE', then=F('quantite')))
        ),
        vols=Sum(
            Case(When(type_mouvement='VOL', then=F('quantite')))
        ),
        dons=Sum(
            Case(When(type_mouvement='DON', then=F('quantite')))
        ),
    )

    achats = aggregates['achats'] or 0
    ventes = aggregates['ventes'] or 0
    mortalite = aggregates['mortalite'] or 0
    vols = aggregates['vols'] or 0
    dons = aggregates['dons'] or 0

    # ===============================
    # STOCK RÉEL
    # ===============================

    stock_total = achats - ventes - mortalite - vols - dons

    if stock_total < 0:
        stock_total = 0

    # ===============================
    # FINANCIER (OPTIMISÉ)
    # ===============================

    revenu_total = mouvements.filter(
        type_mouvement='VENTE'
    ).aggregate(total=Sum('montant_total'))['total'] or 0

    depenses_totales = depenses.aggregate(
        total=Sum('montant')
    )['total'] or 0

    marge = revenu_total - depenses_totales

    # ===============================
    # INDICATEURS
    # ===============================

    rentabilite = 0
    if depenses_totales > 0:
        rentabilite = round((marge / depenses_totales) * 100, 2)

    taux_mortalite = 0
    if achats > 0:
        taux_mortalite = round((mortalite / achats) * 100, 2)

    # ===============================
    # STATS PAR ESPECE (OPTIMISÉ)
    # ===============================

    stats_queryset = mouvements.values(
        'lot__espece__nom'
    ).annotate(
        total_achat=Sum(
            Case(When(type_mouvement='ACHAT', then=F('quantite')))
        ),
        total_sortie=Sum(
            Case(
                When(type_mouvement__in=['VENTE', 'MORTALITE', 'VOL', 'DON'],
                     then=F('quantite'))
            )
        )
    )

    stats = {}

    for item in stats_queryset:
        espece = item['lot__espece__nom'] or "Inconnu"
        stock = (item['total_achat'] or 0) - (item['total_sortie'] or 0)
        stats[espece] = stock

    # ===============================
    # RESPONSE
    # ===============================

    return {
        "kpis": {
            "stock_total": stock_total,
            "revenu_total": revenu_total,
            "depenses_totales": depenses_totales,
            "marge": marge,
            "rentabilite": rentabilite,
            "taux_mortalite": taux_mortalite,
        },
        "mouvements": {
            "achats": achats,
            "ventes": ventes,
            "mortalite": mortalite,
            "vols": vols,
            "dons": dons,
        },
        "stats_especes": stats
    }