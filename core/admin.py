from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Sum

from .models import (
    Achat, CategorieDepense, Client, Depense, Espece, Exploitation,
    Lettrage, Lot, Mouvement, Payment, Task, User, Vente,
)

admin.site.site_header = "Administration Elev'Age"
admin.site.site_title = "Elev'Age"
admin.site.index_title = "Gestion complète des exploitations"


class SuperuserOnlyAdminMixin:
    """Reserve les données globales aux seuls superutilisateurs."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


class LettrageInline(admin.TabularInline):
    model = Lettrage
    extra = 0
    fields = ("vente", "payment", "montant", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("vente", "payment")


@admin.register(User)
class CustomUserAdmin(SuperuserOnlyAdminMixin, UserAdmin):
    list_display = (
        "username", "email", "exploitation", "is_active", "is_staff",
        "is_superuser", "date_joined", "last_login",
    )
    list_filter = ("is_active", "is_staff", "is_superuser", "exploitation")
    search_fields = ("username", "email", "first_name", "last_name", "exploitation__nom")
    list_select_related = ("exploitation",)
    ordering = ("-date_joined",)
    fieldsets = UserAdmin.fieldsets + (("Exploitation Elev'Age", {"fields": ("exploitation",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Exploitation Elev'Age", {"fields": ("exploitation",)}),)


@admin.register(Exploitation)
class ExploitationAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "nom", "proprietaire", "utilisateurs", "lots", "clients",
        "chiffre_affaires", "paiements", "date_creation",
    )
    search_fields = ("nom", "proprietaire__username", "proprietaire__email")
    list_select_related = ("proprietaire",)
    date_hierarchy = "date_creation"

    @admin.display(description="Utilisateurs")
    def utilisateurs(self, obj):
        return obj.users.count()

    @admin.display(description="Lots")
    def lots(self, obj):
        return obj.lots.count()

    @admin.display(description="Clients")
    def clients(self, obj):
        return obj.clients.count()

    @admin.display(description="CA (FCFA)")
    def chiffre_affaires(self, obj):
        return obj.lots.aggregate(total=Sum("ventes__montant_total"))["total"] or 0

    @admin.display(description="Payé (FCFA)")
    def paiements(self, obj):
        return obj.payment_set.aggregate(total=Sum("montant"))["total"] or 0


@admin.register(Espece)
class EspeceAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("nom", "exploitation", "nombre_lots")
    list_filter = ("exploitation",)
    search_fields = ("nom", "exploitation__nom")
    list_select_related = ("exploitation",)

    @admin.display(description="Lots")
    def nombre_lots(self, obj):
        return obj.lot_set.count()


@admin.register(Lot)
class LotAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "nom", "espece", "exploitation", "stock_display",
        "total_achats_display", "total_ventes_display", "date_debut", "date_fin",
    )
    list_filter = ("exploitation", "espece", "date_debut", "date_fin")
    search_fields = ("nom", "espece__nom", "exploitation__nom")
    list_select_related = ("espece", "exploitation", "created_by")
    readonly_fields = ("stock_display", "date_creation")
    date_hierarchy = "date_debut"

    @admin.display(description="Stock")
    def stock_display(self, obj):
        return obj.stock

    @admin.display(description="Achats")
    def total_achats_display(self, obj):
        return obj.mouvements.filter(type_mouvement="ACHAT").aggregate(total=Sum("quantite"))["total"] or 0

    @admin.display(description="Ventes")
    def total_ventes_display(self, obj):
        return obj.mouvements.filter(type_mouvement="VENTE").aggregate(total=Sum("quantite"))["total"] or 0


@admin.register(Mouvement)
class MouvementAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "lot", "exploitation", "type_mouvement", "client", "date",
        "quantite", "quantite_signee", "prix_unitaire", "montant_total", "created_by",
    )
    list_filter = ("type_mouvement", "exploitation", "lot__espece", "date")
    search_fields = ("lot__nom", "client__nom", "exploitation__nom", "created_by__username")
    list_select_related = ("lot", "lot__espece", "exploitation", "client", "created_by")
    readonly_fields = ("quantite_signee", "montant_total")
    date_hierarchy = "date"


@admin.register(Vente)
class VenteAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "client", "lot", "exploitation_display", "date", "quantite",
        "prix_unitaire", "montant_total", "montant_paye_display",
        "reste_a_payer_display", "statut_display",
    )
    list_filter = ("lot__exploitation", "lot__espece", "date")
    search_fields = ("=id", "client__nom", "lot__nom", "lot__exploitation__nom")
    list_select_related = ("client", "lot", "lot__espece", "lot__exploitation")
    readonly_fields = ("montant_total", "montant_paye_display", "reste_a_payer_display", "statut_display")
    date_hierarchy = "date"
    inlines = (LettrageInline,)

    @admin.display(description="Exploitation")
    def exploitation_display(self, obj):
        return obj.lot.exploitation

    @admin.display(description="Payé (FCFA)")
    def montant_paye_display(self, obj):
        return obj.montant_paye

    @admin.display(description="Reste (FCFA)")
    def reste_a_payer_display(self, obj):
        return obj.reste_a_payer

    @admin.display(description="Statut")
    def statut_display(self, obj):
        return obj.statut


@admin.register(CategorieDepense)
class CategorieDepenseAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("nom", "exploitation", "nombre_depenses", "total_depenses")
    list_filter = ("exploitation",)
    search_fields = ("nom", "exploitation__nom")
    list_select_related = ("exploitation",)

    @admin.display(description="Nombre")
    def nombre_depenses(self, obj):
        return obj.depense_set.count()

    @admin.display(description="Total (FCFA)")
    def total_depenses(self, obj):
        return obj.depense_set.aggregate(total=Sum("montant"))["total"] or 0


@admin.register(Depense)
class DepenseAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("lot", "exploitation_display", "categorie", "date", "montant", "note")
    list_filter = ("lot__exploitation", "categorie", "lot__espece", "date")
    search_fields = ("lot__nom", "lot__exploitation__nom", "categorie__nom", "note")
    list_select_related = ("lot", "lot__exploitation", "lot__espece", "categorie")
    date_hierarchy = "date"

    @admin.display(description="Exploitation")
    def exploitation_display(self, obj):
        return obj.lot.exploitation


@admin.register(Achat)
class AchatAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "lot", "exploitation", "date", "quantite", "prix_unitaire",
        "prix_total", "fournisseur", "created_by",
    )
    list_filter = ("exploitation", "lot__espece", "date")
    search_fields = ("lot__nom", "exploitation__nom", "fournisseur", "created_by__username")
    list_select_related = ("lot", "lot__espece", "exploitation", "created_by")
    date_hierarchy = "date"


@admin.register(Task)
class TaskAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "exploitation", "date", "created_at")
    list_filter = ("exploitation", "date")
    search_fields = ("title", "exploitation__nom")
    list_select_related = ("exploitation",)
    date_hierarchy = "date"


@admin.register(Client)
class ClientAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "nom", "telephone", "pays", "ville", "exploitation",
        "total_facture", "total_paye", "solde",
    )
    list_filter = ("exploitation", "pays", "ville")
    search_fields = ("nom", "telephone", "ville", "exploitation__nom")
    list_select_related = ("exploitation",)

    @admin.display(description="Facturé (FCFA)")
    def total_facture(self, obj):
        return obj.ventes.aggregate(total=Sum("montant_total"))["total"] or 0

    @admin.display(description="Payé (FCFA)")
    def total_paye(self, obj):
        return obj.payments.aggregate(total=Sum("montant"))["total"] or 0

    @admin.display(description="Solde (FCFA)")
    def solde(self, obj):
        return float(self.total_facture(obj)) - float(self.total_paye(obj))


@admin.register(Payment)
class PaymentAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "client", "exploitation", "date", "montant",
        "montant_lettre", "non_affecte", "created_at",
    )
    list_filter = ("exploitation", "date")
    search_fields = ("=id", "client__nom", "client__telephone", "exploitation__nom", "note")
    list_select_related = ("client", "exploitation")
    readonly_fields = ("created_at", "montant_lettre", "non_affecte")
    date_hierarchy = "date"
    inlines = (LettrageInline,)

    @admin.display(description="Lettré (FCFA)")
    def montant_lettre(self, obj):
        return obj.lettrages.aggregate(total=Sum("montant"))["total"] or 0

    @admin.display(description="Non affecté (FCFA)")
    def non_affecte(self, obj):
        return float(obj.montant) - float(self.montant_lettre(obj))


@admin.register(Lettrage)
class LettrageAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id", "vente", "payment", "client_display", "exploitation_display",
        "montant", "created_at",
    )
    list_filter = ("vente__lot__exploitation", "vente__lot__espece", "created_at")
    search_fields = (
        "=id", "vente__client__nom", "payment__client__nom",
        "vente__lot__nom", "vente__lot__exploitation__nom",
    )
    list_select_related = (
        "vente", "vente__client", "vente__lot", "vente__lot__exploitation",
        "payment", "payment__client",
    )
    autocomplete_fields = ("vente", "payment")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"

    @admin.display(description="Client")
    def client_display(self, obj):
        return obj.vente.client or obj.payment.client

    @admin.display(description="Exploitation")
    def exploitation_display(self, obj):
        return obj.vente.lot.exploitation
