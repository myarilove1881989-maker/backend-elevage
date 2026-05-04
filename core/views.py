from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from django.db import transaction
import traceback

# Django ORM
from django.db.models import (
    IntegerField, Sum, F, Value, CharField,
    Case, When, DecimalField, ExpressionWrapper
)
from django.db.models.functions import Coalesce

# Django utils
from django.utils.timezone import now
from datetime import timedelta

from .models import (
    Lot, Mouvement, Depense, Vente, Achat, Espece,
    CategorieDepense, Client, Task, Payment, Lettrage
)

from .serializers import (
    RegisterSerializer,
    LotSerializer,
    MouvementSerializer,
    DepenseSerializer,
    AchatSerializer,
    LotDetailSerializer,
    ClientSerializer,
    TaskSerializer
)

# ===============================
# 🔐 PERMISSION CUSTOM
# ===============================
class HasExploitation(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "exploitation", None) is not None
        )


# ===============================
# LOT VIEWSET
# ===============================
class LotViewSet(ModelViewSet):
    queryset = Lot.objects.all()
    serializer_class = LotSerializer
    permission_classes = [IsAuthenticated, HasExploitation]  # 🔥 AJOUT

    def get_queryset(self):
        return Lot.objects.filter(exploitation=self.request.user.exploitation)

    def perform_create(self, serializer):
        serializer.save(exploitation=self.request.user.exploitation)


# ===============================
# TASK VIEWSET
# ===============================
class TaskViewSet(ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated, HasExploitation]  # 🔥 AJOUT

    def get_queryset(self):
        return Task.objects.filter(exploitation=self.request.user.exploitation)

    def perform_create(self, serializer):
        serializer.save(exploitation=self.request.user.exploitation)
# ===============================
# UTILS (STOCK)
# ===============================
def get_lot_stock(lot):
    total_achats = Achat.objects.filter(lot=lot).aggregate(
        total=Sum('quantite')
    )['total'] or 0

    total_sorties = Mouvement.objects.filter(
        lot=lot,
        type_mouvement__in=['VENTE', 'MORTALITE', 'DON', 'VOL']
    ).aggregate(
        total=Sum('quantite')
    )['total'] or 0

    return total_achats - total_sorties

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def client_list(request):
    clients = Client.objects.filter(
        exploitation=request.user.exploitation
    )
    serializer = ClientSerializer(clients, many=True)
    return Response(serializer.data)

# ===============================
# REGISTER
# ===============================
@api_view(['POST'])
@permission_classes([AllowAny])
def api_register(request):
    serializer = RegisterSerializer(data=request.data)

    if serializer.is_valid():
        serializer.save()
        return Response({"message": "Utilisateur créé"}, status=201)

    return Response(serializer.errors, status=400)


# ===============================
# ESPECES
# ===============================
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_especes(request):

    if request.method == 'GET':
        especes = Espece.objects.all()
        data = [{"id": e.id, "nom": e.nom} for e in especes]
        return Response(data)

    if request.method == 'POST':
        nom = request.data.get("nom")

        if not nom:
            return Response({"error": "Nom requis"}, status=400)

        espece = Espece.objects.create(nom=nom)

        return Response({
            "id": espece.id,
            "nom": espece.nom
        }, status=201)


# ===============================
# DASHBOARD
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_dashboard(request):

    exploitation = request.user.exploitation
    espece_id = request.GET.get("espece")

    lots = Lot.objects.filter(exploitation=exploitation)

    if espece_id:
        lots = lots.filter(espece_id=espece_id)

    ventes = Vente.objects.filter(lot__in=lots)
    depenses = Depense.objects.filter(lot__in=lots)

    total_ca = ventes.aggregate(total=Sum('montant_total'))['total'] or 0
    total_depenses = depenses.aggregate(total=Sum('montant'))['total'] or 0

    total_achats = Achat.objects.filter(
        exploitation=exploitation,
        lot__in=lots
    ).aggregate(total=Sum('quantite'))['total'] or 0

    marge = total_ca - total_depenses
    stock_total = sum(get_lot_stock(lot) for lot in lots) if lots.exists() else 0
    pertes = Mouvement.objects.filter(
        lot__in=lots,
        type_mouvement__in=['MORTALITE', 'DON', 'VOL']
    ).aggregate(total=Sum('quantite'))['total'] or 0

    return Response({
        "kpis": {
            "stock": stock_total,
            "chiffre_affaires": total_ca,
            "depenses": total_depenses,
            "marge": marge,
            "pertes": pertes,
            "performance": total_achats # optionnel
            
        }
    })


# ===============================
# LOT DETAIL
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_lot_detail(request, pk):

    try:
        lot = Lot.objects.get(id=pk)
    except Lot.DoesNotExist:
        return Response({"error": "Lot introuvable"}, status=404)

    if lot.exploitation != request.user.exploitation:
        return Response({"error": "Accès interdit"}, status=403)

    total_depenses = Depense.objects.filter(lot=lot).aggregate(
        total=Sum('montant')
    )['total'] or 0

    total_ca = Vente.objects.filter(lot=lot).aggregate(
        total=Sum('montant_total')
    )['total'] or 0

    stock = get_lot_stock(lot)
    marge = total_ca - total_depenses

    serializer = LotDetailSerializer(lot)

    return Response({
        **serializer.data,
        "kpis": {
            "stock": stock,
            "stock_initial": stock,
            "chiffre_affaires": total_ca,
            "depenses": total_depenses,
            "marge": marge,
        }
    })


# ===============================
# CREATE MOUVEMENT
# ===============================
@api_view(['POST'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_create_mouvement(request):

    lot_id = request.data.get("lot")

    if not lot_id:
        return Response({"error": "Lot requis"}, status=400)

    try:
        lot = Lot.objects.get(id=lot_id)
    except Lot.DoesNotExist:
        return Response({"error": "Lot introuvable"}, status=404)

    if lot.exploitation != request.user.exploitation:
        return Response({"error": "Accès interdit"}, status=403)

    try:
        quantite = int(request.data.get("quantite", 0))
    except (TypeError, ValueError):
        return Response({"error": "Quantité invalide"}, status=400)

    type_mouvement = request.data.get("type_mouvement")

    if type_mouvement not in ["ACHAT", "VENTE", "MORTALITE", "DON", "VOL"]:
        return Response({"error": "Type invalide"}, status=400)

    stock = get_lot_stock(lot)
    if type_mouvement != "ACHAT" and quantite > stock:
        return Response({"error": f"Stock insuffisant ({stock})"}, status=400)

    serializer = MouvementSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    mouvement = serializer.save(
        lot=lot,
        exploitation=request.user.exploitation,
        created_by=request.user
    )

    # 🔥 GESTION VENTE
    if mouvement.type_mouvement == "VENTE":
        try:
            client = Client.objects.get(
                id=request.data.get("client"),
                exploitation=request.user.exploitation
            )
        except Client.DoesNotExist:
            return Response({"error": "Client invalide"}, status=400)

        prix_unitaire = float(request.data.get("prix_unitaire", 0))

        Vente.objects.create(
            lot=lot,
            client=client,
            quantite=mouvement.quantite,
            prix_unitaire=prix_unitaire,
            montant_total=prix_unitaire * mouvement.quantite,
            date=mouvement.date
        )

    return Response(serializer.data, status=201)
# ===============================
# DELETE MOUVEMENT
# ===============================
@api_view(['DELETE'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_delete_mouvement(request, pk):

    try:
        mouvement = Mouvement.objects.get(id=pk)
    except Mouvement.DoesNotExist:
        return Response({"error": "Introuvable"}, status=404)

    if mouvement.lot.exploitation != request.user.exploitation:
        return Response({"error": "Accès interdit"}, status=403)

    lot = mouvement.lot

    if mouvement.type_mouvement == "VENTE":
        Vente.objects.filter(
            lot=lot,
            quantite=mouvement.quantite,
            date=mouvement.date
        ).delete()

    mouvement.delete()

    return Response({"message": "Supprimé"}, status=200)


# ===============================
# CLIENT BALANCE UTILS
# ===============================
def get_client_balance(client):
    total_ventes = client.ventes.aggregate(
        total=Sum('montant_total')
    )['total'] or 0

    total_payments = client.payments.aggregate(
        total=Sum('montant')
    )['total'] or 0

    return float(total_ventes) - float(total_payments)


# ===============================
# LETTRAGE AUTO 🔥🔥🔥
# ===============================
def auto_lettrage(client, payment):

    restant = float(payment.montant)

    ventes = client.ventes.order_by("date")

    for vente in ventes:

        total_lettre = vente.lettrages.aggregate(
            total=Sum('montant')
        )['total'] or 0

        reste_vente = float(vente.montant_total) - float(total_lettre)

        if reste_vente <= 0:
            continue

        montant_a_lettre = min(restant, reste_vente)

        Lettrage.objects.create(
            vente=vente,
            payment=payment,
            montant=montant_a_lettre
        )

        restant -= montant_a_lettre

        if restant <= 0:
            break


# ===============================
# CLIENT BALANCE API
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_client_balance(request, client_id):

    try:
        client = Client.objects.get(
            id=client_id,
            exploitation=request.user.exploitation
        )
    except Client.DoesNotExist:
        return Response({"error": "Client introuvable"}, status=404)

    balance = get_client_balance(client)

    return Response({
        "client": client.nom,
        "balance": balance
    })


# ===============================
# CREATE PAYMENT + LETTRAGE
# ===============================
@api_view(['POST'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_create_payment(request):

    client_id = request.data.get("client")
    montant = request.data.get("montant")

    if not client_id or not montant:
        return Response({"error": "Client et montant requis"}, status=400)

    try:
        client = Client.objects.get(
            id=client_id,
            exploitation=request.user.exploitation
        )
    except Client.DoesNotExist:
        return Response({"error": "Client introuvable"}, status=404)
    try:
        montant = float(montant)
    except (TypeError, ValueError):
        return Response({"error": "Montant invalide"}, status=400)

    # 🔥 CHECK DETTE
    balance = get_client_balance(client)

    if montant > balance:
        return Response({"error": "Montant supérieur à la dette"}, status=400)

    payment = Payment.objects.create(
        client=client,
        montant=montant,
        date=now().date(),
        exploitation=request.user.exploitation,
        note=request.data.get("note")
    )

    # 🔥 LETTRAGE AUTO
    auto_lettrage(client, payment)

    return Response({
        "id": payment.id,
        "montant": payment.montant
    }, status=201)


# ===============================

# ===============================
# LIST VENTES CLIENT 🔥
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_client_ventes(request, client_id):

    try:
        client = Client.objects.get(
            id=client_id,
            exploitation=request.user.exploitation
        )
    except Client.DoesNotExist:
        return Response({"error": "Client introuvable"}, status=404)

    ventes = Vente.objects.filter(
    client=client,
    lot__exploitation=request.user.exploitation
    ).order_by("-date")

    data = []

    for v in ventes:
        total_lettre = v.lettrages.aggregate(
            total=Sum('montant')
        )['total'] or 0

        reste = float(v.montant_total) - float(total_lettre)

        # 🔥 STATUT
        if reste == 0:
            statut = "PAYE"
        elif reste < float(v.montant_total):
            statut = "PARTIEL"
        else:
            statut = "IMPAYE"

        data.append({
            "id": v.id,
            "date": v.date,
            "quantite": v.quantite,
            "montant_total": float(v.montant_total),
            "montant_paye": float(total_lettre),
            "reste": reste,
            "statut": statut,
            # 🔥 AJOUT IMPORTANT
            "lot_nom": v.lot.nom,
            "espece": v.lot.espece.nom,
        })

    return Response(data)


# ===============================
# CREATE PAYMENT
# ===============================

# ===============================
# LIST PAYMENTS CLIENT
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_client_payments(request, client_id):

    try:
        client = Client.objects.get(
            id=client_id,
            exploitation=request.user.exploitation
        )
    except Client.DoesNotExist:
        return Response({"error": "Client introuvable"}, status=404)

    payments = Payment.objects.filter(client=client)

    data = [
        {
            "id": p.id,
            "montant": p.montant,
            "date": p.date,
            "note": p.note
        }
        for p in payments
    ]

    return Response(data)


# ===============================
# ANALYTICS 🔥
# ===============================

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_stock_detail(request):
    lot_id = request.GET.get("lot")
    if not lot_id:
        return Response({"error": "Lot requis"}, status=400)
    
    mouvements = Mouvement.objects.filter(
    lot_id=lot_id,
    lot__exploitation=request.user.exploitation
)

    data = mouvements.aggregate(
        stock_initial=Sum(Case(When(type_mouvement='ACHAT', then=F('quantite')))),
        stock_restant=Sum('quantite_signee'),
        vendu=Sum(Case(When(type_mouvement='VENTE', then=F('quantite')))),
        mortalite=Sum(Case(When(type_mouvement='MORTALITE', then=F('quantite')))),
        vol=Sum(Case(When(type_mouvement='VOL', then=F('quantite')))),
        don=Sum(Case(When(type_mouvement='DON', then=F('quantite')))),
    )

    data = {k: v or 0 for k, v in data.items()}
    data["perdu"] = data["mortalite"] + data["vol"] + data["don"]

    return Response(data)

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_ca_par_lot(request):
    espece = request.GET.get("espece")

    lots = Lot.objects.filter(
    espece_id=espece,
    exploitation=request.user.exploitation
)

    result = []

    for lot in lots:

        total_ca = lot.ventes.aggregate(
            total=Sum('montant_total')
        )['total'] or 0

        total_depense = lot.depenses.aggregate(
            total=Sum('montant')
        )['total'] or 0

        total_achat = lot.achats.aggregate(
            total=Sum('prix_total')
        )['total'] or 0

        investissement = total_depense + total_achat

        result.append({
            "id": lot.id,
            "nom": lot.nom,
            "total_ca": float(total_ca),
            "total_depense": float(total_depense),
            "total_achat": float(total_achat),
            "investissement": float(investissement),
        })

    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_depenses_detail(request):
    lot_id = request.GET.get("lot")

    data = (
        Depense.objects
        .filter(
            lot_id=lot_id,
            lot__exploitation=request.user.exploitation
        )
        .values('categorie__nom')
        .annotate(total=Sum('montant'))
        .order_by('-total')
    )

    data = [
        {
            "categorie__nom": item["categorie__nom"] or "Sans catégorie",
            "total": item["total"]
        }
        for item in data
    ]

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_marge_par_lot(request):
    espece = request.GET.get("espece")

    data = (
        Lot.objects
        .filter(
            espece_id=espece,
            exploitation=request.user.exploitation
        )
        .annotate(
            total_ca=Coalesce(
                Sum('ventes__montant_total'),
                Value(0, output_field=DecimalField())
            ),
            total_depenses=Coalesce(
                Sum('depenses__montant'),
                Value(0, output_field=DecimalField())
            ),
            total_achats=Coalesce(
                Sum('achats__prix_total'),
                Value(0, output_field=DecimalField())
            ),
        )
        .annotate(
            investissement=ExpressionWrapper(
                F('total_depenses') + F('total_achats'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
        )
        .annotate(
            marge=ExpressionWrapper(
                F('total_ca') - F('investissement'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
        )
        .annotate(
            rentabilite=Case(
                When(
                    investissement=0,
                    then=Value(0, output_field=DecimalField())
                ),
                default=ExpressionWrapper(
                    (F('marge') * Value(100)) / F('investissement'),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                ),
            )
        )
        .values('id', 'nom', 'marge', 'rentabilite')
        .order_by('-rentabilite')
    )

    return Response(data)
# ===============================
# LISTES (FIX FINAL - NE CASSE RIEN)
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_lots(request):
    lots = Lot.objects.filter(exploitation=request.user.exploitation)
    return Response(LotSerializer(lots, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_mouvements(request):
    mouvements = Mouvement.objects.filter(
        lot__exploitation=request.user.exploitation
    )
    return Response(MouvementSerializer(mouvements, many=True).data)

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_depenses(request):
    depenses = Depense.objects.filter(
        lot__exploitation=request.user.exploitation
    ).order_by('-date')
    return Response(DepenseSerializer(depenses, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_achats(request):
    achats = Achat.objects.filter(
        exploitation=request.user.exploitation
    ).order_by('-date')
    return Response(AchatSerializer(achats, many=True).data)


# ===============================
# CATEGORIES (FIX)
# ===============================
@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_categories_depense(request):
    categories = CategorieDepense.objects.filter(
        exploitation=request.user.exploitation
    )
    data = [{"id": c.id, "nom": c.nom} for c in categories]
    return Response(data)
# ===============================
# CREATE DEPENSE (FIX)
# ===============================
@api_view(['POST'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_create_depense(request):

    lot_id = request.data.get("lot")

    if not lot_id:
        return Response({"error": "Lot requis"}, status=400)

    try:
        lot = Lot.objects.get(id=lot_id)
    except Lot.DoesNotExist:
        return Response({"error": "Lot introuvable"}, status=404)

    if lot.exploitation != request.user.exploitation:
        return Response({"error": "Accès interdit"}, status=403)

    serializer = DepenseSerializer(data=request.data)

    if serializer.is_valid():
        serializer.save(
            lot=lot,
            categorie_id=request.data.get("categorie")
        )
        return Response(serializer.data, status=201)

    return Response(serializer.errors, status=400)
# ===============================
# DELETE DEPENSE (FIX)
# ===============================
@api_view(['DELETE'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_delete_depense(request, pk):

    try:
        depense = Depense.objects.get(id=pk)
    except Depense.DoesNotExist:
        return Response({"error": "Introuvable"}, status=404)

    if depense.lot.exploitation != request.user.exploitation:
        return Response({"error": "Accès interdit"}, status=403)

    depense.delete()

    return Response({"message": "Supprimé"}, status=200)
# ===============================
# CREATE ACHAT (FIX)
# ===============================
# ===============================
# CREATE ACHAT (FIX FINAL PROPRE)
# ===============================
# views.py

@api_view(['POST'])
@permission_classes([])
def api_create_achat(request):

    serializer = AchatSerializer(
        data=request.data,
        context={"request": request}
    )

    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    try:
        with transaction.atomic():
            achat = serializer.save()

        return Response({
            "success": True,
            "achat_id": achat.id,
            "lot_id": achat.lot.id
        }, status=201)

    except Exception as e:
        print("🔥 ERREUR DJANGO:", str(e))
        traceback.print_exc()

        return Response({
            "error": "Erreur serveur"
        }, status=500)
# ===============================
# DELETE ACHAT (FIX FINAL)
# ===============================
@api_view(['DELETE'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_delete_achat(request, pk):

    try:
        achat = Achat.objects.get(id=pk)
    except Achat.DoesNotExist:
        return Response({"error": "Introuvable"}, status=404)

    if achat.lot.exploitation != request.user.exploitation:
        return Response({"error": "Accès interdit"}, status=403)

    lot = achat.lot

    # suppression complète du lot (cohérent avec ton système)
    Vente.objects.filter(lot=lot).delete()
    Mouvement.objects.filter(lot=lot).delete()
    Depense.objects.filter(lot=lot).delete()
    Achat.objects.filter(lot=lot).delete()

    lot.delete()

    return Response({"message": "Lot supprimé avec succès"}, status=200)

@api_view(['POST'])
@permission_classes([IsAuthenticated, HasExploitation])
def create_client(request):
    nom = request.data.get("nom")
    telephone = request.data.get("telephone")

    # ✅ validation AVANT création
    if not nom or not telephone:
        return Response({"error": "Nom et téléphone obligatoires"}, status=400)

    client = Client.objects.create(
        nom=nom,
        telephone=telephone,
        exploitation=request.user.exploitation
    )

    return Response({
        "id": client.id,
        "nom": client.nom,
        "telephone": client.telephone
    }, status=201)
# ===============================
# TOTAL DETTES (FIX FINAL)
# ===============================

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_total_dettes(request):

    exploitation = request.user.exploitation

    total_ventes = Vente.objects.filter(
        lot__exploitation=exploitation
    ).aggregate(
        total=Sum("montant_total")
    )["total"] or 0

    total_payments = Payment.objects.filter(
        exploitation=exploitation
    ).aggregate(
        total=Sum("montant")
    )["total"] or 0

    return Response({
        "total_dettes": float(total_ventes) - float(total_payments)
    })

# ===============================
# DETTES CLIENTS (FIX FINAL)
# ===============================

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_dettes_clients(request):

    exploitation = request.user.exploitation

    clients = Client.objects.filter(
        ventes__lot__exploitation=exploitation
    ).distinct()

    data = []

    for c in clients:
        total_facture = c.ventes.filter(
            lot__exploitation=exploitation
        ).aggregate(
            total=Sum('montant_total')
        )['total'] or 0

        total_paye = c.payments.filter(
            exploitation=exploitation
        ).aggregate(
            total=Sum('montant')
        )['total'] or 0

        total_facture = float(total_facture)
        total_paye = float(total_paye)

        reste = total_facture - total_paye

        if reste == 0:
            statut = "PAYE"
        elif total_paye == 0:
            statut = "IMPAYE"
        else:
            statut = "PARTIEL"

        data.append({
            "id": c.id,
            "nom": c.nom,
            "total_facture": total_facture,
            "total_paye": total_paye,
            "reste": reste,
            "statut": statut,
            "telephone": c.telephone,  # 🔥 AJOUT
        })

    return Response(data)

# ===============================
# PERFORMANCE LOTS (FIX FINAL)
# ===============================


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def api_performance_lots(request):

    from django.db.models import Sum, F, Value, Case, When, DecimalField
    from django.db.models.functions import Coalesce
    from django.db.models import ExpressionWrapper

    exploitation = request.user.exploitation

    data = (
        Lot.objects
        .filter(exploitation=exploitation)
        .annotate(
            total_ca=Coalesce(
                Sum('ventes__montant_total'),
                Value(0, output_field=DecimalField())
            ),
            total_depenses=Coalesce(
                Sum('depenses__montant'),
                Value(0, output_field=DecimalField())
            ),
        )
        .annotate(
            marge=ExpressionWrapper(
                F('total_ca') - F('total_depenses'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
            rentabilite=Case(
                When(total_depenses=0, then=Value(0)),
                default=ExpressionWrapper(
                    (F('marge') * 100) / F('total_depenses'),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                ),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )
        .values('id', 'nom', 'marge', 'rentabilite')
        .order_by('-rentabilite')
    )

    return Response(data)