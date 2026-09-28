from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
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

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new and not self.exploitation:
            exploitation = Exploitation.objects.create(
                nom=f"Ferme de {self.username}",
                proprietaire=self
            )
            self.exploitation = exploitation
            super().save(update_fields=["exploitation"])


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

    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="especes",
        null=True,
        blank=True
    )

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
    TYPE_PRODUCTION_CHOICES = [
        ('CHAIR', 'Élevage de chair'),
        ('OEUFS', 'Production d’œufs'),
        ('REPRODUCTION', 'Reproduction'),
        ('AUTRE', 'Autre'),
    ]

    STATUT_PRODUCTION_CHOICES = [
        ('ELEVAGE', 'Élevage'),
        ('PONTE', 'Ponte'),
        ('REFORME', 'Réforme'),
        ('TERMINE', 'Terminé'),
    ]

    exploitation = models.ForeignKey(Exploitation, on_delete=models.CASCADE, related_name='lots')
    espece = models.ForeignKey(Espece, on_delete=models.CASCADE)

    nom = models.CharField(max_length=100)

    date_debut = models.DateField()
    date_fin = models.DateField(null=True, blank=True)

    prix_vente_prevu = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    type_production = models.CharField(
        max_length=20,
        choices=TYPE_PRODUCTION_CHOICES,
        default='CHAIR',
    )
    statut_production = models.CharField(
        max_length=20,
        choices=STATUT_PRODUCTION_CHOICES,
        default='ELEVAGE',
    )
    date_naissance = models.DateField(null=True, blank=True)
    age_arrivee_semaines = models.PositiveSmallIntegerField(null=True, blank=True)
    date_debut_ponte = models.DateField(null=True, blank=True)

    date_creation = models.DateTimeField(auto_now_add=True)

    # ✅ AJOUT ICI (FIX ERREUR)
    created_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.nom} - {self.espece.nom}"

    @property
    def stock(self):
        result = self.mouvements.aggregate(
            total=Sum('quantite_signee')
        )
        return result['total'] or 0


# ===============================
# MOUVEMENT
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

    exploitation = models.ForeignKey("Exploitation", on_delete=models.CASCADE, null=True)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True)

    date = models.DateField(default=now)

    quantite = models.IntegerField()
    quantite_signee = models.IntegerField(editable=False)

    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    montant_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):

        if self.type_mouvement == 'ACHAT':
            self.quantite_signee = abs(self.quantite)
        else:
            self.quantite_signee = -abs(self.quantite)

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

    @property
    def montant_paye(self):
        return self.lettrages.aggregate(total=Sum('montant'))['total'] or 0

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

    note = models.CharField(max_length=255, blank=True, null=True)

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

    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Achat {self.lot} - {self.quantite}"


# ===============================
# TASK
# ===============================

class Task(models.Model):
    exploitation = models.ForeignKey(Exploitation, on_delete=models.CASCADE, related_name="tasks")

    title = models.CharField(max_length=255)
    date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


# ===============================
# CLIENT
# ===============================

class Client(models.Model):
    nom = models.CharField(max_length=255)
    telephone = models.CharField(max_length=20, blank=True)
    pays = models.CharField(max_length=2, blank=True, default="")
    ville = models.CharField(max_length=100, blank=True, default="")

    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        null=True,
        related_name="clients"
    )

    def __str__(self):
        return self.nom


# ===============================
# PAYMENT
# ===============================

class Payment(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="payments")
    montant = models.FloatField()
    date = models.DateField()
    note = models.TextField(blank=True, null=True)

    exploitation = models.ForeignKey("Exploitation", on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.client.nom} - {self.montant}"


# ===============================
# LETTRAGE
# ===============================

class Lettrage(models.Model):
    vente = models.ForeignKey("Vente", on_delete=models.CASCADE, related_name="lettrages")
    payment = models.ForeignKey("Payment", on_delete=models.CASCADE, related_name="lettrages")

    montant = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vente.id} ↔ {self.payment.id} ({self.montant})"


# ===============================
# PRODUCTION D'ŒUFS
# ===============================

class CollecteOeufs(models.Model):
    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="collectes_oeufs",
    )
    lot = models.ForeignKey(
        Lot,
        on_delete=models.CASCADE,
        related_name="collectes_oeufs",
    )
    collecte_at = models.DateTimeField(default=now)
    nombre_collecte = models.PositiveIntegerField()
    nombre_casses = models.PositiveIntegerField(default=0)
    nombre_declasses = models.PositiveIntegerField(default=0)
    nombre_consommes_donnes = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collectes_oeufs_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-collecte_at", "-id")

    @property
    def nombre_commercialisable(self):
        return self.nombre_collecte - (
            self.nombre_casses
            + self.nombre_declasses
            + self.nombre_consommes_donnes
        )

    def clean(self):
        super().clean()
        if self.lot_id and self.exploitation_id:
            if self.lot.exploitation_id != self.exploitation_id:
                raise ValidationError({"lot": "Ce lot appartient à une autre exploitation."})
            if self.lot.type_production != 'OEUFS':
                raise ValidationError({"lot": "Les collectes sont réservées aux lots de ponte."})

        total_sorties_immediates = (
            self.nombre_casses
            + self.nombre_declasses
            + self.nombre_consommes_donnes
        )
        if total_sorties_immediates > self.nombre_collecte:
            raise ValidationError(
                "Le total cassé, déclassé et consommé/donné ne peut pas dépasser la collecte."
            )

    def __str__(self):
        return f"Collecte {self.lot} - {self.nombre_collecte} œufs"


class MouvementOeufs(models.Model):
    TYPE_CHOICES = [
        ('PRODUCTION', 'Production'),
        ('VENTE', 'Vente'),
        ('CONSOMMATION', 'Consommation'),
        ('DON', 'Don'),
        ('CASSE', 'Casse après stockage'),
        ('PERTE', 'Perte'),
        ('AJUSTEMENT', "Ajustement d'inventaire"),
    ]

    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="mouvements_oeufs",
    )
    lot = models.ForeignKey(
        Lot,
        on_delete=models.CASCADE,
        related_name="mouvements_oeufs",
        null=True,
        blank=True,
    )
    type_mouvement = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantite = models.IntegerField()
    quantite_signee = models.IntegerField(editable=False)
    date = models.DateTimeField(default=now)
    collecte = models.OneToOneField(
        CollecteOeufs,
        on_delete=models.CASCADE,
        related_name="mouvement_stock",
        null=True,
        blank=True,
    )
    vente_oeufs = models.OneToOneField(
        'VenteOeufs',
        on_delete=models.CASCADE,
        related_name="mouvement_stock",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_oeufs_crees",
    )
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "-id")

    def clean(self):
        super().clean()
        if self.quantite == 0:
            raise ValidationError({"quantite": "La quantité ne peut pas être nulle."})
        if self.type_mouvement != 'AJUSTEMENT' and self.quantite < 0:
            raise ValidationError({"quantite": "La quantité doit être positive."})
        if self.lot_id and self.exploitation_id:
            if self.lot.exploitation_id != self.exploitation_id:
                raise ValidationError({"lot": "Ce lot appartient à une autre exploitation."})
            if self.lot.type_production != 'OEUFS':
                raise ValidationError({"lot": "Ce lot n'est pas un lot de ponte."})

    def save(self, *args, **kwargs):
        if self.type_mouvement == 'PRODUCTION':
            self.quantite_signee = abs(self.quantite)
        elif self.type_mouvement == 'AJUSTEMENT':
            self.quantite_signee = self.quantite
        else:
            self.quantite_signee = -abs(self.quantite)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"quantite_signee"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.type_mouvement} - {self.quantite_signee} œufs"


class VenteOeufs(models.Model):
    CONDITIONNEMENT_CHOICES = [
        ('UNITE', 'Unité'),
        ('DOUZAINE', 'Douzaine'),
        ('PLATEAU', 'Plateau'),
        ('CARTON', 'Carton'),
    ]

    vente = models.OneToOneField(
        Vente,
        on_delete=models.CASCADE,
        related_name="detail_oeufs",
    )
    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="ventes_oeufs",
    )
    lot = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        related_name="ventes_oeufs",
    )
    conditionnement = models.CharField(max_length=20, choices=CONDITIONNEMENT_CHOICES)
    nombre_conditionnements = models.PositiveIntegerField()
    oeufs_par_conditionnement = models.PositiveIntegerField(default=1)
    nombre_oeufs = models.PositiveIntegerField()
    prix_unitaire_conditionnement = models.DecimalField(max_digits=10, decimal_places=2)
    montant_total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.lot_id and self.exploitation_id:
            if self.lot.exploitation_id != self.exploitation_id:
                raise ValidationError({"lot": "Ce lot appartient à une autre exploitation."})
            if self.lot.type_production != 'OEUFS':
                raise ValidationError({"lot": "Ce lot n'est pas un lot de ponte."})
        if self.vente_id and self.exploitation_id:
            if self.vente.lot.exploitation_id != self.exploitation_id:
                raise ValidationError({"vente": "Cette vente appartient à une autre exploitation."})
        quantite_attendue = self.nombre_conditionnements * self.oeufs_par_conditionnement
        if self.nombre_oeufs != quantite_attendue:
            raise ValidationError(
                {"nombre_oeufs": "Le nombre d'œufs ne correspond pas au conditionnement."}
            )

    def __str__(self):
        return f"Vente d'œufs #{self.vente_id} - {self.nombre_oeufs} œufs"


class ConsommationAliment(models.Model):
    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="consommations_aliment",
    )
    lot = models.ForeignKey(
        Lot,
        on_delete=models.CASCADE,
        related_name="consommations_aliment",
    )
    date = models.DateField(default=now)
    distribution_at = models.DateTimeField(default=now, null=True, blank=True)
    aliment = models.CharField(max_length=120, default="Aliment")
    quantite_kg = models.DecimalField(max_digits=10, decimal_places=3)
    prix_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    depense = models.OneToOneField(
        Depense,
        on_delete=models.SET_NULL,
        related_name="consommation_aliment",
        null=True,
        blank=True,
    )
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consommations_aliment_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-distribution_at", "-id")

    @property
    def cout_calcule(self):
        if self.depense_id:
            return self.depense.montant
        if self.prix_kg is None:
            return None
        return self.quantite_kg * self.prix_kg

    def clean(self):
        super().clean()
        if self.quantite_kg is not None and self.quantite_kg <= 0:
            raise ValidationError({"quantite_kg": "La quantité doit être supérieure à zéro."})
        if not self.aliment or not self.aliment.strip():
            raise ValidationError({"aliment": "Indiquez le nom de l'aliment."})
        if self.lot_id and self.exploitation_id:
            if self.lot.exploitation_id != self.exploitation_id:
                raise ValidationError({"lot": "Ce lot appartient à une autre exploitation."})
        if self.depense_id:
            if self.depense.lot_id != self.lot_id:
                raise ValidationError({"depense": "La dépense doit appartenir au même lot."})

    def __str__(self):
        return f"{self.lot} - {self.quantite_kg} kg le {self.date}"


class PeseeProduction(models.Model):
    """Pondération d'un échantillon d'animaux d'un lot de production."""

    exploitation = models.ForeignKey(
        Exploitation,
        on_delete=models.CASCADE,
        related_name="pesees_production",
    )
    lot = models.ForeignKey(
        Lot,
        on_delete=models.CASCADE,
        related_name="pesees_production",
    )
    pesee_at = models.DateTimeField(default=now)
    nombre_animaux_peses = models.PositiveIntegerField()
    poids_total_kg = models.DecimalField(max_digits=12, decimal_places=3)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pesees_production_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("pesee_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(nombre_animaux_peses__gt=0),
                name="pesee_animaux_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(poids_total_kg__gt=0),
                name="pesee_poids_gt_zero",
            ),
        ]
        indexes = [
            models.Index(
                fields=("lot", "pesee_at"),
                name="core_pesee_lot_id_at_idx",
            )
        ]

    @property
    def poids_moyen_kg(self):
        if not self.nombre_animaux_peses:
            return None
        return self.poids_total_kg / self.nombre_animaux_peses

    def clean(self):
        super().clean()
        if self.nombre_animaux_peses is not None and self.nombre_animaux_peses <= 0:
            raise ValidationError({"nombre_animaux_peses": "Le nombre d'animaux doit être supérieur à zéro."})
        if self.poids_total_kg is not None and self.poids_total_kg <= 0:
            raise ValidationError({"poids_total_kg": "Le poids total doit être supérieur à zéro."})
        if self.lot_id and self.exploitation_id:
            if self.lot.exploitation_id != self.exploitation_id:
                raise ValidationError({"lot": "Ce lot appartient à une autre exploitation."})
            if self.lot.type_production != "CHAIR":
                raise ValidationError({"lot": "Les pesées de croissance sont réservées aux lots CHAIR."})

    def __str__(self):
        return f"Pesée de {self.lot} - {self.pesee_at:%Y-%m-%d %H:%M}"


# ===============================
# PASSWORD RESET
# ===============================

class PasswordResetCode(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_codes",
    )
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > now()
