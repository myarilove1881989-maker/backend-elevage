from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.shortcuts import render

from .models import Achat, Client, Depense, Espece, Exploitation, Lettrage, Lot, Mouvement, Payment, Task, Vente


@staff_member_required
def superadmin_dashboard(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    user_model = get_user_model()
    users = user_model.objects.select_related("exploitation").order_by("-date_joined")[:10]
    exploitations = list(
        Exploitation.objects.select_related("proprietaire")
        .annotate(
            users_count=Count("users", distinct=True),
            lots_count=Count("lots", distinct=True),
            clients_count=Count("clients", distinct=True),
        )
        .order_by("-date_creation")
    )

    for farm in exploitations:
        sales = Vente.objects.filter(lot__exploitation=farm).aggregate(total=Sum("montant_total"))["total"] or 0
        payments = Payment.objects.filter(exploitation=farm).aggregate(total=Sum("montant"))["total"] or 0
        expenses = Depense.objects.filter(lot__exploitation=farm).aggregate(total=Sum("montant"))["total"] or 0
        farm.sales_total = sales
        farm.payments_total = payments
        farm.balance_total = float(sales) - float(payments)
        farm.expenses_total = expenses

    total_sales = Vente.objects.aggregate(total=Sum("montant_total"))["total"] or 0
    total_payments = Payment.objects.aggregate(total=Sum("montant"))["total"] or 0
    total_expenses = Depense.objects.aggregate(total=Sum("montant"))["total"] or 0

    modules = (
        ("Utilisateurs", user_model.objects.count(), "admin:core_user_changelist"),
        ("Exploitations", len(exploitations), "admin:core_exploitation_changelist"),
        ("Espèces", Espece.objects.count(), "admin:core_espece_changelist"),
        ("Lots", Lot.objects.count(), "admin:core_lot_changelist"),
        ("Achats", Achat.objects.count(), "admin:core_achat_changelist"),
        ("Mouvements", Mouvement.objects.count(), "admin:core_mouvement_changelist"),
        ("Ventes", Vente.objects.count(), "admin:core_vente_changelist"),
        ("Dépenses", Depense.objects.count(), "admin:core_depense_changelist"),
        ("Clients", Client.objects.count(), "admin:core_client_changelist"),
        ("Paiements", Payment.objects.count(), "admin:core_payment_changelist"),
        ("Lettrages", Lettrage.objects.count(), "admin:core_lettrage_changelist"),
        ("Tâches", Task.objects.count(), "admin:core_task_changelist"),
    )

    context = {
        **admin.site.each_context(request),
        "title": "Tableau de bord superutilisateur",
        "users": users,
        "exploitations": exploitations,
        "modules": modules,
        "total_users": user_model.objects.count(),
        "total_exploitations": len(exploitations),
        "total_clients": Client.objects.count(),
        "total_lots": Lot.objects.count(),
        "total_sales": total_sales,
        "total_payments": total_payments,
        "total_balance": float(total_sales) - float(total_payments),
        "total_expenses": total_expenses,
        "recent_sales": Vente.objects.select_related("client", "lot", "lot__exploitation").order_by("-date", "-id")[:10],
        "recent_payments": Payment.objects.select_related("client", "exploitation").order_by("-date", "-id")[:10],
        "recent_allocations": Lettrage.objects.select_related("vente", "vente__client", "vente__lot", "payment").order_by("-created_at")[:10],
    }
    return render(request, "admin/superadmin_dashboard.html", context)
