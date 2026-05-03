from django.contrib import admin
from django.db.models import Sum
from .models import Exploitation, Espece, Lot, Mouvement, Depense, Achat
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    pass


# ===============================
# EXPLOITATION
# ===============================

@admin.register(Exploitation)
class ExploitationAdmin(admin.ModelAdmin):
    list_display = ('nom', 'proprietaire', 'date_creation')


# ===============================
# ESPECE
# ===============================

@admin.register(Espece)
class EspeceAdmin(admin.ModelAdmin):
    list_display = ('nom',)


# ===============================
# LOT
# ===============================

@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):

    list_display = (
        'nom',
        'espece',
        'exploitation',
        'stock_display',
        'total_achats_display',
        'total_ventes_display',
    )

    # ===== DISPLAY METHODS =====

    def stock_display(self, obj):
        return obj.stock

    def total_achats_display(self, obj):
        total = obj.mouvements.filter(
            type_mouvement='ACHAT'
        ).aggregate(total=Sum('quantite'))['total'] or 0
        return total

    def total_ventes_display(self, obj):
        total = obj.mouvements.filter(
            type_mouvement='VENTE'
        ).aggregate(total=Sum('quantite'))['total'] or 0
        return total


# ===============================
# MOUVEMENT
# ===============================

@admin.register(Mouvement)
class MouvementAdmin(admin.ModelAdmin):
    list_display = (
        'lot',
        'type_mouvement',
        'date',
        'quantite',
        'quantite_signee',
        'prix_unitaire',
        'montant_total'
    )


# ===============================
# DEPENSE
# ===============================

@admin.register(Depense)
class DepenseAdmin(admin.ModelAdmin):
    list_display = ('lot', 'categorie', 'date', 'montant')


# ===============================
# ACHAT
# ===============================

@admin.register(Achat)
class AchatAdmin(admin.ModelAdmin):
    list_display = (
        'lot',
        'quantite',
        'prix_total',
        'prix_unitaire',
        'date',
        'created_by'
    )