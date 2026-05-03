from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.timezone import now
from django.db.models import Sum


# ===============================
# USER (multi-tenant ready)
# ===============================

class User(AbstractUser):
    exploitation = models.ForeignKey(
        'Exploitation',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='users'
    )

    def __str__(self):
        return self.username


# ===============================
# EXPLOITATION
# ===============================

class Exploitation(models.Model):
    nom = models.CharField(max_length=100)
    proprietaire = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_exploitations')
    date_creation = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nom


# ===============================
# ESPECE
# ===============================

class Espece(models.Model):
    nom = models.CharField(max_length=100)

    def __str__(self):
        return self.nom


# ===============================
# QUERYSET (multi-tenant)
# ===============================

class TenantQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(lot__exploitation=user.exploitation)


# ===============================
# LOT
# ===============================

class Lot(models.Model):
    exploitation = models.ForeignKey(Exploitation, on_delete=models.CASCADE, related_name='lots')
    espece = models.ForeignKey(Espece, on_delete=models.CASCADE)

    nom = models.CharField(max_length=100)

    date_debut = models.DateField()
    date_fin = models.DateField(null=True, blank=True)

    prix_vente_prevu = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    date_creation = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nom} - {self.espece.nom}"

    # 🔥 STOCK ACTUEL (déjà parfait chez toi)
    @property
    def stock(self):
        from django.db.models import Sum

        result = self.mouvements.aggregate(
            total=Sum('quantite_signee')
        )
        return result['total'] or 0


# ===============================
# MOUVEMENT (stock uniquement)
# ===============================

class Mouvement(models.Model):

    objects = TenantQuerySet.as_manager()

    TYPE_CHOICES = [
        ('ACHAT', 'Achat'),
        ('VENTE', 'Vente'),
        ('MORTALITE', 'Mortalité'),
        ('DON', 'Don'),
        ('VOL', 'Vol'),
    ]

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='mouvements')
    type_mouvement = models.CharField(max_length=20, choices=TYPE_CHOICES)

    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True)


    date = models.DateField(default=now)

    quantite = models.IntegerField()
    quantite_signee = models.IntegerField(editable=False)

    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    montant_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):

        # 🔁 quantité signée
        if self.type_mouvement == 'ACHAT':
            self.quantite_signee = abs(self.quantite)
        else:
            self.quantite_signee = -abs(self.quantite)

        # 💰 montant
        if self.prix_unitaire is not None:
            self.montant_total = self.quantite * self.prix_unitaire

        super().save(*args, **kwargs)


# ===============================
# VENTE
# ===============================

class Vente(models.Model):

    objects = TenantQuerySet.as_manager()

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='ventes')
    client = models.ForeignKey("Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="ventes")

    date = models.DateField(default=now)

    quantite = models.IntegerField()
    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2)

    montant_total = models.DecimalField(max_digits=12, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        self.montant_total = self.quantite * self.prix_unitaire
        super().save(*args, **kwargs)

    # ===============================
    # 💰 SUIVI PAIEMENT (LETTRAGE)
    # ===============================

    @property
    def montant_paye(self):
        return self.lettrages.aggregate(
            total=Sum('montant')
        )['total'] or 0

    @property
    def reste_a_payer(self):
        return float(self.montant_total) - float(self.montant_paye)

    @property
    def statut(self):
        if self.reste_a_payer <= 0:
            return "PAYE"
        elif self.montant_paye > 0:
            return "PARTIEL"
        else:
            return "IMPAYE"


# ===============================
# CATEGORIE DEPENSE
# ===============================

class CategorieDepense(models.Model):
    nom = models.CharField(max_length=50)
    exploitation = models.ForeignKey(Exploitation, on_delete=models.CASCADE, related_name='categories_depense')

    def __str__(self):
        return self.nom


# ===============================
# DEPENSE
# ===============================

class Depense(models.Model):

    objects = TenantQuerySet.as_manager()

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='depenses')

    categorie = models.ForeignKey(CategorieDepense, on_delete=models.SET_NULL, null=True)

    date = models.DateField(default=now)
    montant = models.DecimalField(max_digits=12, decimal_places=2)

    note = models.CharField(max_length=255, blank=True, null=True)  # ✅ AJOUT IMPORTANT

    def __str__(self):
        return f"{self.categorie} - {self.montant}"


# ===============================
# ACHAT
# ===============================

class Achat(models.Model):
    exploitation = models.ForeignKey("Exploitation", on_delete=models.CASCADE)
    lot = models.ForeignKey("Lot", on_delete=models.CASCADE, related_name="achats")

    quantite = models.PositiveIntegerField()
    prix_total = models.DecimalField(max_digits=10, decimal_places=2)
    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2)

    fournisseur = models.CharField(max_length=255, blank=True, null=True)
    date = models.DateField()
    note = models.TextField(blank=True, null=True)

    created_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Achat {self.lot} - {self.quantite}"
class Task(models.Model):
    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="tasks"
    )

    title = models.CharField(max_length=255)
    date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class Client(models.Model):
    nom = models.CharField(max_length=255)
    telephone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.nom
    
class Payment(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="payments")
    montant = models.FloatField()
    date = models.DateField()
    note = models.TextField(blank=True, null=True)

    exploitation = models.ForeignKey("Exploitation", on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.client.nom} - {self.montant}"

class Lettrage(models.Model):
    vente = models.ForeignKey("Vente", on_delete=models.CASCADE, related_name="lettrages")
    payment = models.ForeignKey("Payment", on_delete=models.CASCADE, related_name="lettrages")

    montant = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vente.id} ↔ {self.payment.id} ({self.montant})"