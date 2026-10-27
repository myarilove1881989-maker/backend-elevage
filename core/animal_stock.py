"""Current animal stock from the durable ledger, never from source documents.

An adjustment row is an already committed compensation, including reversals of
SUPERSEDED submissions. Do not filter it by the original operation's outcome.
This reader has no date cutoff and must not be used for historical hen-days.
"""
from django.db.models import BigIntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce


def animal_stock_expression():
    """Correlated sums for a Lot queryset, safe from join multiplication.

    Older animal movements may have a null exploitation: their lot defines the
    tenant. An explicit conflicting exploitation is excluded, never repaired.
    """
    from .models import Mouvement, TerrainStockAdjustment

    movements = (Mouvement.objects.filter(lot_id=OuterRef('pk'))
                 .filter(Q(exploitation_id=OuterRef('exploitation_id')) |
                         Q(exploitation_id__isnull=True))
                 .order_by().values('lot_id')
                 .annotate(total=Sum('quantite_signee')).values('total')[:1])
    adjustments = (TerrainStockAdjustment.objects.filter(
        lot_id=OuterRef('pk'), exploitation_id=OuterRef('exploitation_id'), kind='ANIMAL')
        .order_by().values('lot_id')
        .annotate(total=Sum('signed_quantity')).values('total')[:1])
    return (Coalesce(Subquery(movements, output_field=BigIntegerField()),
                     Value(0), output_field=BigIntegerField()) +
            Coalesce(Subquery(adjustments, output_field=BigIntegerField()),
                     Value(0), output_field=BigIntegerField()))


def current_animal_stock(lot):
    """Read the current signed stock in one statement; retain negative anomalies.

    Purchases and births are represented by animal movements. Adding their
    source documents would count them twice. No journal row is mutated here.
    """
    from .models import Lot

    return (Lot.objects.filter(pk=lot.pk, exploitation_id=lot.exploitation_id)
            .annotate(current_stock=animal_stock_expression())
            .values_list('current_stock', flat=True).get())
