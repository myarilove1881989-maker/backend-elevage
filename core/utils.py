from django.db.models import Sum, Case, When, IntegerField, F

def get_lot_stock(lot):
    result = lot.mouvements.aggregate(
        total=Sum(
            Case(
                When(type_mouvement='entree', then=F('quantite')),
                When(type_mouvement='sortie', then=F('quantite') * -1),
                output_field=IntegerField(),
            )
        )
    )
    return result['total'] or 0