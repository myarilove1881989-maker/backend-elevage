from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.shortcuts import render

from .models import Exploitation, Lot, Payment, Vente


@staff_member_required
def superadmin_dashboard(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    user_model = get_user_model()
    users = (
        user_model.objects
        .select_related("exploitation")
        .order_by("-date_joined")
    )
    exploitations = (
        Exploitation.objects
        .select_related("proprietaire")
        .annotate(
            users_count=Count("users", distinct=True),
            lots_count=Count("lots", distinct=True),
        )
        .order_by("-date_creation")
    )

    context = {
        **admin.site.each_context(request),
        "title": "Tableau de bord superutilisateur",
        "users": users,
        "exploitations": exploitations,
        "total_users": users.count(),
        "total_exploitations": exploitations.count(),
        "total_lots": Lot.objects.count(),
        "total_sales": Vente.objects.aggregate(total=Sum("montant_total"))["total"] or 0,
        "total_payments": Payment.objects.aggregate(total=Sum("montant"))["total"] or 0,
    }
    return render(request, "admin/superadmin_dashboard.html", context)
