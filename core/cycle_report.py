"""Read-only cycle reports. Accounting amounts never include physical use twice."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q, Sum
from django.utils import timezone

from .models import (Achat, CollecteOeufs, ConsommationAliment, Depense, Lettrage,
                     Mouvement, PeseeProduction, TerrainStockAdjustment, Vente, VenteOeufs)
from .lot_lifecycle import closed
from .supply_models import SupplyMovement, SupplyReceipt
from .vaccination_models import Vaccination

ZERO = Decimal('0')


def money(value):
    return str(Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def total(query, field):
    return query.aggregate(n=Sum(field))['n'] or ZERO


def percent(numerator, denominator):
    return money(Decimal(numerator) * 100 / Decimal(denominator)) if denominator else None


def consumption_analysis(lot, start, end):
    """Conservative moving weighted average, per physical feed pool.

    Replay all prior entries before the cutoff. Any transfer, correction,
    reversal, unpriced receipt or negative inventory invalidates that entire
    product/species history. No arbitrary reconstruction of old allocation.
    Vaccine costs follow identified vials. These costs are informational only.
    """
    farm = lot.exploitation_id
    rows = list(SupplyMovement.objects.filter(
        operation__exploitation_id=farm, product__exploitation_id=farm, species_id=lot.espece_id,
        operation__occurred_at__date__lte=end,
    ).select_related('operation', 'product', 'vial__receipt__expense__lot').order_by('operation__occurred_at', 'operation_id', 'line'))
    receipts = {r.operation_id: r for r in SupplyReceipt.objects.filter(
        operation__exploitation_id=farm).select_related('expense__lot')}
    unsupported = set(SupplyMovement.objects.filter(
        operation__exploitation_id=farm, species_id=lot.espece_id, product__exploitation_id=farm,
    ).exclude(kind__in=['ENTRY', 'DISTRIBUTION', 'LOSS', 'ADMINISTRATION']).values_list('product_id', flat=True))
    pools = defaultdict(lambda: [ZERO, ZERO])
    costs = defaultdict(lambda: {'feed': ZERO, 'vaccine': ZERO, 'loss': ZERO})
    relevant = set()
    vaccine_balances = defaultdict(lambda: ZERO)
    reserved_vials = set()
    expired_vials = set()
    for row in rows:
        day = timezone.localtime(row.operation.occurred_at).date()
        in_period = start <= day <= end
        target = row.destination_lot_id or row.lot_id
        if target == lot.pk and in_period and row.kind in {'DISTRIBUTION', 'ADMINISTRATION', 'LOSS'}:
            relevant.add(row.product_id)
        if row.product.kind == 'FEED':
            pool = pools[(row.product_id, row.lot_id)]
            if row.kind == 'ENTRY':
                receipt = receipts.get(row.operation_id)
                if (not receipt or not receipt.expense_id or receipt.expense.voided_at
                        or receipt.quantity != row.quantity or receipt.expense.montant < 0 or receipt.expense.lot.exploitation_id != farm):
                    unsupported.add(row.product_id)
                else:
                    pool[0] += row.quantity
                    pool[1] += receipt.expense.montant
            elif row.kind in {'DISTRIBUTION', 'LOSS'}:
                qty = -row.quantity
                if pool[0] < qty or pool[0] <= 0:
                    unsupported.add(row.product_id)
                else:
                    cost = pool[1] * qty / pool[0]
                    pool[0] -= qty
                    pool[1] -= cost
                    if target == lot.pk and in_period:
                        costs[row.product_id]['feed' if row.kind == 'DISTRIBUTION' else 'loss'] += cost
        elif row.kind in {'ADMINISTRATION', 'LOSS'}:
            receipt = row.vial.receipt if row.vial_id else None
            if not receipt or not receipt.expense_id or receipt.expense.voided_at or receipt.expense.montant < 0 or receipt.expense.lot.exploitation_id != farm:
                unsupported.add(row.product_id)
            elif target == lot.pk and in_period:
                costs[row.product_id]['vaccine' if row.kind == 'ADMINISTRATION' else 'loss'] += (
                    -row.quantity * receipt.expense.montant / receipt.quantity)
        if row.product.kind == 'VACCINE':
            # Physical position only; shared balances are never allocated to a cycle.
            vaccine_balances[row.vial_id] += row.quantity
            if row.kind == 'ENTRY' and row.lot_id == lot.pk:
                reserved_vials.add(row.vial_id)
            if row.vial_id and row.vial.receipt.expires_on < end:
                expired_vials.add(row.vial_id)
    known = relevant - unsupported
    missing = relevant & unsupported
    kinds = {r.product_id: r.product.kind for r in rows}
    amounts = {name: money(sum((costs[p][name] for p in known if name == 'loss' or
                                kinds[p] == ('FEED' if name == 'feed' else 'VACCINE')), ZERO))
               if any(name == 'loss' or kinds[p] == ('FEED' if name == 'feed' else 'VACCINE') for p in known) else None
               for name in ('feed', 'vaccine', 'loss')}
    return {
        'method': 'CMP mobile par produit et réserve physique ; vaccins au coût du flacon identifié',
        'status': 'unavailable' if not known else ('partial' if missing else 'documented'),
        'feed_cost': amounts['feed'], 'vaccine_cost': amounts['vaccine'], 'loss_cost': amounts['loss'],
        'priced_products': len(known), 'unpriced_products': len(missing),
        'reserved_vaccine_doses_remaining': sum((max(vaccine_balances[v], ZERO) for v in reserved_vials), ZERO) if reserved_vials else None,
        'reserved_expired_doses': sum((max(vaccine_balances[v], ZERO) for v in reserved_vials & expired_vials), ZERO) if reserved_vials else None,
        'reserved_feed_remaining_cost': money(sum((value[1] for (product, source), value in pools.items() if source == lot.pk and product not in unsupported), ZERO)) if any(source == lot.pk and product not in unsupported for product, source in pools) else None,
        'explanation': 'Analyse informative, jamais ajoutée aux charges enregistrées. Historiques corrigés, transférés, annulés ou non valorisés exclus. Stocks partagés restants non attribués au lot.',
    }


def report(lot):
    today = timezone.localdate()
    end = min(lot.date_fin or today, today)
    start = lot.date_debut
    warnings = ['Les charges saisies ne garantissent pas un coût complet : travail, bâtiments et autres charges peuvent manquer.']
    if closed(lot) and lot.date_fin is None:
        warnings.append('Lot terminé sans date de fin : date du jour utilisée, bilan à vérifier.')
    if start > end:
        warnings.append('Les dates du cycle sont incohérentes.')
    sales = Vente.objects.filter(lot=lot, date__range=(start, end))
    purchases = Achat.objects.filter(lot=lot, exploitation_id=lot.exploitation_id, date__range=(start, end))
    expenses = Depense.objects.filter(lot=lot, date__range=(start, end))
    revenue, animal_cost, expense_cost = total(sales, 'montant_total'), total(purchases, 'prix_total'), total(expenses, 'montant')
    charges = animal_cost + expense_cost
    result = revenue - charges
    payments = dict(Lettrage.objects.filter(vente__in=sales, payment__exploitation_id=lot.exploitation_id).values('vente_id').annotate(amount=Sum('montant')).values_list('vente_id', 'amount'))
    paid = sum(payments.values(), ZERO)
    # Sum per-sale debt: overpayment of one sale cannot hide another receivable.
    receivables = sum((max(s.montant_total - payments.get(s.pk, ZERO), ZERO) for s in sales), ZERO)
    movements = Mouvement.objects.filter(lot=lot).filter(Q(exploitation_id=lot.exploitation_id) | Q(exploitation__isnull=True))
    dated = movements.filter(date__range=(start, end))
    adjustments = TerrainStockAdjustment.objects.filter(lot=lot, exploitation_id=lot.exploitation_id, kind='ANIMAL', occurred_at__date__lte=end)
    before = total(movements.filter(date__lt=start), 'quantite_signee') + total(adjustments.filter(occurred_at__date__lt=start), 'signed_quantity')
    remaining = total(movements.filter(date__lte=end), 'quantite_signee') + total(adjustments, 'signed_quantity')
    entries = dated.filter(type_mouvement__in=['ACHAT', 'NAISSANCE'])
    entries_count = total(entries, 'quantite')
    initial_rows = movements.filter(date=start, type_mouvement__in=['ACHAT', 'NAISSANCE'])
    initial = before + total(initial_rows, 'quantite') if initial_rows.exists() or before else None
    deaths_rows = dated.filter(type_mouvement='MORTALITE')
    deaths = int(total(deaths_rows, 'quantite')) if deaths_rows.exists() else None
    mortality = percent(deaths, entries_count + max(before, 0)) if deaths is not None else None
    if adjustments.exists():
        mortality = None
        deaths = None
        warnings.append('Des compensations d’effectif existent : taux de mortalité non calculable sans qualification de leur motif.')
    if initial is None:
        warnings.append('Effectif de départ non documenté à la date de début du cycle.')
    if remaining < 0:
        warnings.append('Effectif restant négatif : rapprocher les mouvements et compensations.')
    elif remaining:
        warnings.append('Des animaux restent dans le lot ; ils ne sont pas valorisés en revenu.')
    feeds = ConsommationAliment.objects.filter(lot=lot, exploitation_id=lot.exploitation_id, date__range=(start, end))
    collections = CollecteOeufs.objects.filter(lot=lot, exploitation_id=lot.exploitation_id, collecte_at__date__range=(start, end))
    egg_sales = VenteOeufs.objects.filter(vente__in=sales, lot=lot, exploitation_id=lot.exploitation_id)
    weights = list(PeseeProduction.objects.filter(lot=lot, exploitation_id=lot.exploitation_id, pesee_at__date__range=(start, end)).order_by('pesee_at', 'pk'))
    gmq = None
    if len(weights) > 1:
        a, b = weights[-2:]
        days = (timezone.localtime(b.pesee_at).date() - timezone.localtime(a.pesee_at).date()).days
        if days > 0:
            gmq = money((b.poids_moyen_kg - a.poids_moyen_kg) * 1000 / days)
    vaccinations = Vaccination.objects.filter(lot=lot, exploitation_id=lot.exploitation_id, voided_at__isnull=True, vaccinated_at__date__range=(start, end))
    physical = SupplyMovement.objects.filter(operation__exploitation_id=lot.exploitation_id,
        operation_id__in=vaccinations.values('current_id'), operation__occurred_at__date__range=(start, end)).filter(Q(lot=lot) | Q(destination_lot=lot))
    receipts = SupplyReceipt.objects.filter(expense__in=expenses)
    if receipts.filter(operation__movements__lot__isnull=True).exists() or feeds.filter(physical_feed_link__source_lot__isnull=True, physical_feed_link__isnull=False).exists():
        warnings.append('Consommables partagés : le lot qui porte l’achat peut différer du lot consommateur.')
    if feeds.filter(physical_feed_link__isnull=True).exists():
        warnings.append('Anciennes distributions : traçabilité physique et valorisation potentiellement incomplètes.')
    outside = (Vente.objects.filter(lot=lot).exclude(date__range=(start, end)).exists()
               or Depense.objects.filter(lot=lot).exclude(date__range=(start, end)).exists()
               or Achat.objects.filter(lot=lot).exclude(date__range=(start, end)).exists()
               or movements.exclude(date__range=(start, end)).exists())
    if outside:
        warnings.append('Des opérations hors des dates du cycle sont exclues ; vérifier les dates et l’affectation du lot.')
    from .egg_services import get_egg_stock
    egg_stock = get_egg_stock(lot.exploitation, lot) if lot.type_production == 'OEUFS' else None
    if egg_stock:
        warnings.append('Le stock courant d’œufs restant est présenté sans valorisation financière.')
    valuation = consumption_analysis(lot, start, end)
    if feeds.filter(physical_feed_link__isnull=True).exists():
        valuation['status'] = 'partial' if valuation['priced_products'] else 'unavailable'
    if valuation['status'] != 'documented':
        warnings.append('Valorisation des consommables partielle ou indisponible : aucun coût manquant n’est estimé.')
    animal_sales = sales.filter(detail_oeufs__isnull=True)
    metrics = {
        'duration_days': max((end - start).days + 1, 0),
        'initial_animals': int(initial) if initial is not None else None,
        'live_entries': int(entries_count) if entries.exists() else None,
        'animals_sold': int(total(animal_sales, 'quantite')) if animal_sales.exists() else None,
        'deaths': deaths, 'mortality_percent': mortality, 'remaining_animals': int(remaining),
        'births': int(total(dated.filter(type_mouvement='NAISSANCE'), 'quantite')) if dated.filter(type_mouvement='NAISSANCE').exists() and not adjustments.exists() else None,
        'stillbirths': int(total(dated.filter(type_mouvement='NAISSANCE'), 'mort_nes')) if dated.filter(type_mouvement='NAISSANCE').exists() else None,
        'feed_kg': str(total(feeds, 'quantite_kg')) if feeds.exists() else None,
        'vaccination_sessions': vaccinations.count() or None,
        'administered_doses': int(-total(physical.filter(kind='ADMINISTRATION'), 'quantity')) if physical.filter(kind='ADMINISTRATION').exists() else None,
        'lost_vaccine_doses': int(-total(physical.filter(kind='LOSS', product__kind='VACCINE'), 'quantity')) if physical.filter(kind='LOSS', product__kind='VACCINE').exists() else None,
    }
    if lot.type_production == 'OEUFS':
        metrics.update(eggs_collected=int(total(collections, 'nombre_collecte')) if collections.exists() else None,
                       eggs_sold=int(total(egg_sales, 'nombre_oeufs')) if egg_sales.exists() else None,
                       eggs_broken=int(total(collections, 'nombre_casses')) if collections.exists() else None,
                       current_egg_stock=egg_stock)
    if lot.type_production == 'CHAIR':
        metrics.update(mean_weight_kg=str(weights[-1].poids_moyen_kg) if weights else None, daily_gain_g=gmq)
    return {
        'version': 1, 'lot': {'id': lot.pk, 'name': lot.nom, 'species': lot.espece_id, 'production': lot.type_production},
        'closed': closed(lot), 'start': start, 'end': end, 'generated_at': timezone.now(),
        'revision': lot.exploitation.business_revision,
        'financial': {'revenue': money(revenue), 'animal_purchases': money(animal_cost), 'expenses': money(expense_cost),
                      'charges': money(charges), 'result': money(result), 'result_percent': percent(result, charges),
                      'allocated_payments_to_date': money(paid), 'receivables_to_date': money(receivables)},
        'expense_breakdown': [{'category': x['categorie__nom'] or 'Sans catégorie', 'amount': money(x['amount'])}
                              for x in expenses.values('categorie__nom').annotate(amount=Sum('montant')).order_by('categorie__nom')],
        'technical': metrics, 'valuation': valuation, 'warnings': warnings,
        'completeness': 'unverified',
        'technical_formula': 'Durée : dates incluses. Mortalité : mortalités déclarées / (entrées vivantes du cycle + effectif antérieur documenté) × 100 ; mort-nés séparés. Exhaustivité des saisies non attestée.',
        'formula': 'Ventes actives − achats actifs d’animaux − dépenses actives du lot. Taux = résultat / charges × 100. Les règlements sont suivis à la date de consultation.',
    }


def compare(first, second):
    differences = {}
    for section in ('financial', 'technical'):
        for key, value in first[section].items():
            other = second[section].get(key)
            delta = Decimal(value) - Decimal(other) if value is not None and other is not None else None
            rate = key.endswith('_percent')
            differences[key] = {'absolute': (money(delta) if section == 'financial' or rate else str(delta)) if delta is not None else None,
                                'relative_percent': percent(delta, abs(Decimal(other))) if delta is not None and other and Decimal(other) != 0 and not rate else None,
                                'unit': 'points' if rate else 'value'}
    return {'reference': second, 'differences': differences,
            'warnings': ['Comparaison descriptive : des durées, tailles ou saisies différentes peuvent expliquer les écarts. Aucun classement automatique de rentabilité.']}
