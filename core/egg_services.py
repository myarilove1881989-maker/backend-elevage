from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    Achat,
    AffectationMouvementOeufs,
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
    total_naissances = Mouvement.objects.filter(
        lot=lot, type_mouvement="NAISSANCE",
    ).aggregate(total=Sum("quantite"))["total"] or 0
    total_sorties = Mouvement.objects.filter(
        lot=lot,
        type_mouvement__in=["VENTE", "MORTALITE", "DON", "VOL"],
    ).aggregate(total=Sum("quantite"))["total"] or 0
    return max(total_achats + total_naissances - total_sorties, 0)


def get_hen_days(lot, start, end):
    """Effectif par jour, mouvements datés inclus, pour la période sélectionnée."""
    events = {}
    for row in Achat.objects.filter(lot=lot, date__lte=end).values('date').annotate(n=Sum('quantite')):
        events[row['date']] = events.get(row['date'], 0) + row['n']
    for row in Mouvement.objects.filter(
        lot=lot, date__lte=end, type_mouvement='NAISSANCE',
    ).values('date').annotate(n=Sum('quantite')):
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


def get_dated_egg_stock(exploitation, lot):
    """Expose la provenance uniquement si toutes les écritures sont réconciliées."""
    collections = list(
        CollecteOeufs.objects.filter(exploitation=exploitation, lot=lot)
        .order_by("-collecte_at", "-id")
        .values(
            "id", "collecte_at", "nombre_collecte", "nombre_casses",
            "nombre_declasses", "nombre_consommes_donnes",
        )
    )
    allocated_by_collection = dict(
        AffectationMouvementOeufs.objects.filter(
            collecte__exploitation=exploitation,
            collecte__lot=lot,
            mouvement__exploitation=exploitation,
            mouvement__lot=lot,
        ).values("collecte_id").annotate(total=Sum("quantite"))
        .values_list("collecte_id", "total")
    )
    production_by_collection = {
        source_id: (amount, movement_type)
        for source_id, amount, movement_type in MouvementOeufs.objects.filter(
            exploitation=exploitation, lot=lot, collecte__isnull=False,
        ).values_list("collecte_id", "quantite_signee", "type_mouvement")
    }
    allocated_total = sum(allocated_by_collection.values())
    movements = MouvementOeufs.objects.filter(exploitation=exploitation, lot=lot)
    exits = -(
        movements.filter(quantite_signee__lt=0)
        .aggregate(total=Sum("quantite_signee"))["total"] or 0
    )
    entries_without_collection = (
        movements.filter(quantite_signee__gt=0, collecte__isnull=True)
        .aggregate(total=Sum("quantite_signee"))["total"] or 0
    )
    global_stock = get_egg_stock(exploitation, lot)
    candidate_total = 0
    for item in collections:
        commercialisable = item["nombre_collecte"] - (
            item["nombre_casses"] + item["nombre_declasses"]
            + item["nombre_consommes_donnes"]
        )
        attributed = allocated_by_collection.get(item["id"], 0)
        item["nombre_commercialisable"] = commercialisable
        item["sorties_affectees"] = attributed
        candidate_total += commercialisable - attributed

    unallocated_exits = exits - allocated_total
    complete = (
        unallocated_exits == 0 and entries_without_collection == 0
        and global_stock == candidate_total
        and all(production_by_collection.get(item["id"], (0, "PRODUCTION"))
                == (item["nombre_commercialisable"], "PRODUCTION")
                for item in collections)
        and all(item["nombre_commercialisable"] >= item["sorties_affectees"]
                for item in collections)
    )
    for item in collections:
        item["restant"] = (
            item["nombre_commercialisable"] - item["sorties_affectees"]
            if complete else None
        )
    return {
        "lot": lot.pk,
        "stock_global": global_stock,
        "origines_completes": complete,
        "sorties_non_attribuees": unallocated_exits,
        "entrees_hors_collecte": entries_without_collection,
        "collectes": collections,
    }


def plan_fifo_egg_sale(*, stock_state, quantity, sale_date, movement_at):
    """Prépare le FIFO sans écriture, sous le verrou du lot appelant."""
    if not stock_state["origines_completes"]:
        raise ValueError(
            "Origine du stock d'œufs indéterminée pour ce lot : "
            "régularisez les anciennes sorties avant une nouvelle vente."
        )

    remaining = quantity
    allocations = []
    eligible_total = 0
    for collection in sorted(
        stock_state["collectes"], key=lambda item: (item["collecte_at"], item["id"]),
    ):
        available = collection["restant"]
        if available <= 0:
            continue
        # Vente.date n'a pas d'heure : toutes les collectes du jour sont
        # admissibles, sauf celles enregistrées dans le futur réel.
        if (timezone.localdate(collection["collecte_at"]) > sale_date
                or collection["collecte_at"] > movement_at):
            continue
        eligible_total += available
        taken = min(available, remaining)
        if taken:
            allocations.append({"collecte": collection["id"], "quantite": taken})
            remaining -= taken

    if remaining:
        raise ValueError(
            f"Stock d'œufs insuffisant à la date de vente : "
            f"{eligible_total} œufs disponibles."
        )
    return allocations


@transaction.atomic
def affect_egg_exit(mouvement, allocations):
    """Affecte une sortie entière aux collectes explicitement désignées."""
    if mouvement.lot_id is None:
        raise ValidationError({"affectations": "La sortie doit appartenir à un lot."})
    Lot.objects.select_for_update().get(
        pk=mouvement.lot_id, exploitation_id=mouvement.exploitation_id,
    )
    mouvement = MouvementOeufs.objects.select_for_update().get(pk=mouvement.pk)
    if mouvement.quantite_signee >= 0 or mouvement.type_mouvement == "PRODUCTION":
        raise ValidationError({"affectations": "Seules les sorties peuvent être affectées."})
    if mouvement.affectations.exists():
        raise ValidationError({"affectations": "Cette sortie possède déjà des affectations."})
    if not allocations or sum(item["quantite"] for item in allocations) != -mouvement.quantite_signee:
        raise ValidationError({"affectations": "La totalité de la sortie doit être affectée."})
    ids = [item["collecte"] for item in allocations]
    if len(ids) != len(set(ids)):
        raise ValidationError({"affectations": "Une collecte ne peut figurer qu'une fois."})
    sources = {
        item.pk: item for item in CollecteOeufs.objects.filter(
            pk__in=ids, lot_id=mouvement.lot_id,
            exploitation_id=mouvement.exploitation_id,
        )
    }
    if len(sources) != len(ids):
        raise ValidationError({"affectations": "Collecte introuvable dans cette exploitation et ce lot."})
    previous = dict(
        AffectationMouvementOeufs.objects.filter(collecte_id__in=ids)
        .values("collecte_id").annotate(total=Sum("quantite"))
        .values_list("collecte_id", "total")
    )
    for item in allocations:
        source = sources[item["collecte"]]
        if item["quantite"] <= 0:
            raise ValidationError({"affectations": "Chaque quantité doit être positive."})
        if source.collecte_at > mouvement.date or (
            mouvement.vente_oeufs_id and
            timezone.localdate(source.collecte_at) > mouvement.vente_oeufs.vente.date
        ):
            raise ValidationError({"affectations": "La collecte doit précéder la sortie et sa vente."})
        if previous.get(source.pk, 0) + item["quantite"] > source.nombre_commercialisable:
            raise ValidationError({"affectations": "Quantité insuffisante dans la collecte indiquée."})
    return AffectationMouvementOeufs.objects.bulk_create([
        AffectationMouvementOeufs(
            mouvement=mouvement, collecte_id=item["collecte"], quantite=item["quantite"],
        ) for item in allocations
    ])


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
    attributed = original.affectations_sortie.aggregate(total=Sum("quantite"))["total"] or 0
    if attributed and serializer.validated_data.get("collecte_at", original.collecte_at) != original.collecte_at:
        raise ValidationError({"collecte_at": "Une collecte déjà utilisée ne peut pas être redatée."})
    previous_quantity = original.nombre_commercialisable
    collection = serializer.save()
    if collection.nombre_commercialisable < attributed:
        raise ValidationError("Cette correction dépasse les sorties déjà affectées à la collecte.")
    if get_egg_stock(collection.exploitation, collection.lot) + collection.nombre_commercialisable - previous_quantity < 0:
        raise ValidationError('Cette correction rendrait le stock d’œufs négatif.')
    sync_collection_stock_movement(collection)
    return collection


@transaction.atomic
def delete_collection(collection):
    Lot.objects.select_for_update().get(pk=collection.lot_id)
    collection.refresh_from_db()
    if collection.affectations_sortie.exists():
        raise ValidationError("Cette collecte est liée à des sorties déjà enregistrées.")
    if get_egg_stock(collection.exploitation, collection.lot) < collection.nombre_commercialisable:
        raise ValidationError('Cette collecte contient des œufs déjà sortis du stock.')
    collection.delete()


@transaction.atomic
def create_egg_sale(*, validated_data, user):
    requested_lot = validated_data.pop("lot")
    allocations = validated_data.pop("affectations", None)
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
    sale_date = vente_data.get("date", None) or timezone.now().date()
    movement_at = timezone.now()

    # Toutes les écritures de stock par API verrouillent ce lot. La lecture
    # des restants et la vente appartiennent à la même transaction.
    stock_state = get_dated_egg_stock(user.exploitation, lot)
    stock_disponible = stock_state["stock_global"]
    if nombre_oeufs > stock_disponible:
        raise ValueError(f"Stock d'œufs insuffisant ({stock_disponible})")
    if not stock_state["origines_completes"]:
        raise ValueError(
            "Origine du stock d'œufs indéterminée pour ce lot : "
            "régularisez les anciennes sorties avant une nouvelle vente."
        )
    if allocations is None:
        allocations = plan_fifo_egg_sale(
            stock_state=stock_state, quantity=nombre_oeufs,
            sale_date=sale_date, movement_at=movement_at,
        )

    vente = Vente.objects.create(
        lot=lot,
        client=client,
        date=sale_date,
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
    mouvement = MouvementOeufs.objects.create(
        exploitation=user.exploitation,
        lot=lot,
        type_mouvement="VENTE",
        quantite=nombre_oeufs,
        date=movement_at,
        vente_oeufs=vente_oeufs,
        created_by=user,
        note=f"Vente d'œufs #{vente.pk}",
    )
    affect_egg_exit(mouvement, allocations)
    return vente_oeufs


@transaction.atomic
def delete_egg_sale(vente_oeufs):
    client_id = vente_oeufs.vente.client_id
    if client_id:
        Client.objects.select_for_update().get(pk=client_id)
    vente = Vente.objects.select_for_update().get(pk=vente_oeufs.vente_id)
    if vente.lettrages.exists():
        raise ValueError("Impossible de supprimer une vente ayant déjà reçu un paiement.")
    vente.delete()
