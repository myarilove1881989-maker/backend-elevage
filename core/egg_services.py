from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    Achat,
    Client,
    CollecteOeufs,
    Lot,
    Mouvement,
    MouvementOeufs,
    Vente,
    VenteOeufs,
)


def get_live_birds(lot):
    total_achats = Achat.objects.filter(lot=lot).aggregate(total=Sum("quantite"))["total"] or 0
    total_sorties = Mouvement.objects.filter(
        lot=lot,
        type_mouvement__in=["VENTE", "MORTALITE", "DON", "VOL"],
    ).aggregate(total=Sum("quantite"))["total"] or 0
    return max(total_achats - total_sorties, 0)


def get_hen_days(lot, start, end):
    """Effectif par jour, mouvements datés inclus, pour la période sélectionnée."""
    events = {}
    for row in Achat.objects.filter(lot=lot, date__lte=end).values('date').annotate(n=Sum('quantite')):
        events[row['date']] = events.get(row['date'], 0) + row['n']
    for row in Mouvement.objects.filter(
        lot=lot, date__lte=end, type_mouvement__in=['VENTE', 'MORTALITE', 'DON', 'VOL']
    ).values('date').annotate(n=Sum('quantite')):
        events[row['date']] = events.get(row['date'], 0) - row['n']
    birds = sum(n for day, n in events.items() if day < start)
    cursor = start
    total = 0
    for day in sorted(day for day in events if day >= start):
        total += max(birds, 0) * (day - cursor).days
        birds += events[day]
        cursor = day
    return total + max(birds, 0) * ((end - cursor).days + 1)


def get_egg_stock(exploitation, lot=None):
    mouvements = MouvementOeufs.objects.filter(exploitation=exploitation)
    if lot is not None:
        mouvements = mouvements.filter(lot=lot)
    return mouvements.aggregate(total=Sum("quantite_signee"))["total"] or 0


def sync_collection_stock_movement(collection):
    commercialisable = collection.nombre_commercialisable
    if commercialisable <= 0:
        MouvementOeufs.objects.filter(collecte=collection).delete()
        return None

    mouvement, _ = MouvementOeufs.objects.update_or_create(
        collecte=collection,
        defaults={
            "exploitation": collection.exploitation,
            "lot": collection.lot,
            "type_mouvement": "PRODUCTION",
            "quantite": commercialisable,
            "date": collection.collecte_at,
            "created_by": collection.created_by,
            "note": f"Production issue de la collecte #{collection.pk}",
        },
    )
    return mouvement


@transaction.atomic
def save_collection(*, serializer, user):
    Lot.objects.select_for_update().get(pk=serializer.validated_data['lot'].pk)
    collection = serializer.save(
        exploitation=user.exploitation,
        created_by=user,
    )
    sync_collection_stock_movement(collection)
    return collection


@transaction.atomic
def update_collection(*, serializer):
    original = serializer.instance
    Lot.objects.select_for_update().get(pk=original.lot_id)
    original.refresh_from_db()
    if serializer.validated_data.get('lot', original.lot).pk != original.lot_id:
        raise ValidationError({'lot': 'Une collecte ne peut pas changer de lot.'})
    previous_quantity = original.nombre_commercialisable
    collection = serializer.save()
    if get_egg_stock(collection.exploitation, collection.lot) + collection.nombre_commercialisable - previous_quantity < 0:
        raise ValidationError('Cette correction rendrait le stock d’œufs négatif.')
    sync_collection_stock_movement(collection)
    return collection


@transaction.atomic
def delete_collection(collection):
    Lot.objects.select_for_update().get(pk=collection.lot_id)
    collection.refresh_from_db()
    if get_egg_stock(collection.exploitation, collection.lot) < collection.nombre_commercialisable:
        raise ValidationError('Cette collecte contient des œufs déjà sortis du stock.')
    collection.delete()


@transaction.atomic
def create_egg_sale(*, validated_data, user):
    requested_lot = validated_data.pop("lot")
    lot = Lot.objects.select_for_update().get(
        id=requested_lot.id,
        exploitation=user.exploitation,
        type_production="OEUFS",
    )
    client = Client.objects.get(
        id=validated_data.pop("client"),
        exploitation=user.exploitation,
    )
    vente_data = validated_data.pop("vente", {})
    nombre_conditionnements = validated_data["nombre_conditionnements"]
    oeufs_par_conditionnement = validated_data["oeufs_par_conditionnement"]
    nombre_oeufs = nombre_conditionnements * oeufs_par_conditionnement
    prix_conditionnement = validated_data["prix_unitaire_conditionnement"]
    montant_total = nombre_conditionnements * prix_conditionnement

    stock_disponible = get_egg_stock(user.exploitation, lot)
    if nombre_oeufs > stock_disponible:
        raise ValueError(f"Stock d'œufs insuffisant ({stock_disponible})")

    vente = Vente.objects.create(
        lot=lot,
        client=client,
        date=vente_data.get("date", None) or timezone.now().date(),
        quantite=nombre_conditionnements,
        prix_unitaire=prix_conditionnement,
    )
    vente_oeufs = VenteOeufs.objects.create(
        vente=vente,
        exploitation=user.exploitation,
        lot=lot,
        nombre_oeufs=nombre_oeufs,
        montant_total=montant_total,
        **validated_data,
    )
    MouvementOeufs.objects.create(
        exploitation=user.exploitation,
        lot=lot,
        type_mouvement="VENTE",
        quantite=nombre_oeufs,
        date=timezone.now(),
        vente_oeufs=vente_oeufs,
        created_by=user,
        note=f"Vente d'œufs #{vente.pk}",
    )
    return vente_oeufs


@transaction.atomic
def delete_egg_sale(vente_oeufs):
    if vente_oeufs.vente.lettrages.exists():
        raise ValueError("Impossible de supprimer une vente ayant déjà reçu un paiement.")
    vente_oeufs.vente.delete()
