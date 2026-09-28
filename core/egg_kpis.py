"""Indicateurs techniques des lots de ponte, calculés depuis les écritures sources."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .egg_services import get_egg_stock, get_live_birds
from .models import CollecteOeufs, ConsommationAliment


def _one_decimal(value):
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def get_laying_kpis(exploitation, lot, *, start, end):
    """Le taux du jour utilise l'effectif actuel, faute d'heure des mouvements animaux.

    Aucun taux historique n'est reconstruit à partir de cette approximation.
    Les dates des collectes sont interprétées dans le fuseau actif Django.
    """
    today = timezone.localdate()
    collections = CollecteOeufs.objects.filter(exploitation=exploitation, lot=lot)
    daily = collections.filter(collecte_at__date=today).aggregate(
        count=Count("id"),
        collected=Sum("nombre_collecte"),
        broken=Sum("nombre_casses"),
        downgraded=Sum("nombre_declasses"),
        consumed=Sum("nombre_consommes_donnes"),
    )
    has_collection = daily["count"] > 0
    produced = daily["collected"] if has_collection else None
    marketable = (
        produced - daily["broken"] - daily["downgraded"] - daily["consumed"]
        if has_collection else None
    )
    birds = get_live_birds(lot)
    feed = ConsommationAliment.objects.filter(
        exploitation=exploitation, lot=lot, date=today,
    ).aggregate(count=Count("id"), kg=Sum("quantite_kg"))
    has_feed = feed["count"] > 0
    feed_kg = feed["kg"] if has_feed else None

    grouped = {
        item["day"]: item["collected"]
        for item in collections.filter(collecte_at__date__range=(start, end))
        .annotate(day=TruncDate("collecte_at", tzinfo=timezone.get_current_timezone()))
        .values("day").annotate(collected=Sum("nombre_collecte"))
        .order_by("day")
    }
    evolution = []
    day = start
    while day <= end:
        evolution.append({"date": day, "production": grouped.get(day)})
        day += timedelta(days=1)

    return {
        "lot": lot.pk,
        "date": today,
        "fuseau_horaire": str(timezone.get_current_timezone()),
        "date_debut": start,
        "date_fin": end,
        "effectif_actuel": birds,
        "effectif_reference": birds,
        "effectif_reference_mode": "effectif_actuel_approximation",
        "collectes_enregistrees": has_collection,
        "production_jour": produced,
        "commercialisable_jour": marketable,
        "casses_jour": daily["broken"] if has_collection else None,
        "taux_ponte": (
            _one_decimal(Decimal(produced) * 100 / birds)
            if has_collection and birds > 0 else None
        ),
        "taux_casse": (
            _one_decimal(Decimal(daily["broken"]) * 100 / produced)
            if has_collection and produced > 0 else None
        ),
        "taux_ponte_inhabituel": bool(has_collection and birds > 0 and produced > birds),
        "stock_disponible": get_egg_stock(exploitation, lot),
        "aliment_enregistre": has_feed,
        "aliment_jour_kg": float(feed_kg) if has_feed else None,
        "consommation_par_poule_g": (
            _one_decimal(feed_kg * 1000 / birds)
            if has_feed and birds > 0 else None
        ),
        "evolution": evolution,
    }
