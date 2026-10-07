from django.db import models, transaction
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.db.models import Sum
import uuid
from .audit import AuditedModel, AuditedQuerySet, ReversibleAuditedModel, ActiveBusinessManager


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

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not is_new:
            previous = type(self).objects.filter(pk=self.pk).values_list('exploitation_id', flat=True).first()
            if previous != self.exploitation_id:
                raise ValidationError('Le transfert d’exploitation nécessite un parcours dédié.')
        super().save(*args, **kwargs)

        if is_new and not self.exploitation:
            exploitation = Exploitation.objects.create(
                nom=f"Ferme de {self.username}",
                proprietaire=self
            )
            self.exploitation = exploitation
            super().save(update_fields=["exploitation"])

        if is_new and self.exploitation_id:
            role = 'OWNER' if self.exploitation.proprietaire_id == self.pk else 'OPERATEUR'
            from .audit import ActorContext, audit_scope
            with audit_scope(ActorContext(self.pk, self.exploitation_id)):
                ExploitationMembership.objects.get_or_create(
                    user=self, exploitation=self.exploitation, defaults={'role': role},
                )


# ===============================
# EXPLOITATION
# ===============================

class Exploitation(models.Model):
    nom = models.CharField(max_length=100)
    proprietaire = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_exploitations')
    date_creation = models.DateTimeField(auto_now_add=True)
    offline_policy_enabled = models.BooleanField(default=False)
    write_generation = models.PositiveIntegerField(default=1)
    business_revision = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return self.nom


# ===============================
# ESPECE
# ===============================

class Espece(AuditedModel):
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

class TenantQuerySet(AuditedQuerySet):
    def for_user(self, user):
        return self.filter(lot__exploitation=user.exploitation)


# ===============================
# LOT
# ===============================

class Lot(AuditedModel):
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
        from .terrain_models import TerrainStockAdjustment
        adjustment = TerrainStockAdjustment.objects.filter(lot=self, kind='ANIMAL').aggregate(total=Sum('signed_quantity'))['total'] or 0
        return (result['total'] or 0) + adjustment


# ===============================
# MOUVEMENT
# ===============================

class Mouvement(AuditedModel):

    objects = TenantQuerySet.as_manager()

    TYPE_CHOICES = [
        ('ACHAT', 'Achat'),
        ('VENTE', 'Vente'),
        ('MORTALITE', 'Mortalité'),
        ('DON', 'Don'),
        ('VOL', 'Vol'),
        ('NAISSANCE', 'Naissance'),
    ]

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='mouvements')
    # Null for births recorded before Phase 10.1; never infer their origin.
    lot_origine = models.ForeignKey(
        Lot, on_delete=models.PROTECT, related_name='naissances_issues',
        null=True, blank=True,
    )
    type_mouvement = models.CharField(max_length=20, choices=TYPE_CHOICES)

    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True)

    # Les anciennes ventes restent sans lien : leur rapprochement serait ambigu.
    vente = models.OneToOneField(
        'Vente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='mouvement_animal',
    )

    exploitation = models.ForeignKey("Exploitation", on_delete=models.CASCADE, null=True)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True)

    date = models.DateField(default=now)

    quantite = models.IntegerField()
    quantite_signee = models.IntegerField(editable=False)

    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    montant_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    mort_nes = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True, default='')

    def save(self, *args, **kwargs):

        if self.type_mouvement in ('ACHAT', 'NAISSANCE'):
            self.quantite_signee = abs(self.quantite)
        else:
            self.quantite_signee = -abs(self.quantite)

        if self.prix_unitaire is not None:
            self.montant_total = self.quantite * self.prix_unitaire

        super().save(*args, **kwargs)


# ===============================
# VENTE
# ===============================

class Vente(ReversibleAuditedModel):
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    objects = ActiveBusinessManager.from_queryset(TenantQuerySet)()

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
        return self.montant_total - self.montant_paye

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

class CategorieDepense(AuditedModel):
    nom = models.CharField(max_length=50)
    exploitation = models.ForeignKey(Exploitation, on_delete=models.CASCADE, related_name='categories_depense')

    def __str__(self):
        return self.nom


# ===============================
# DEPENSE
# ===============================

class Depense(ReversibleAuditedModel):
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    objects = ActiveBusinessManager.from_queryset(TenantQuerySet)()

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

class Achat(ReversibleAuditedModel):
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

class Task(AuditedModel):
    exploitation = models.ForeignKey(Exploitation, on_delete=models.CASCADE, related_name="tasks")

    title = models.CharField(max_length=255)
    date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='created_tasks')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='assigned_tasks')
    description = models.TextField(blank=True, default='')
    priority = models.CharField(max_length=10, default='NORMAL',
                                choices=[('LOW', 'Low'), ('NORMAL', 'Normal'), ('HIGH', 'High')])
    status = models.CharField(max_length=20, default='TODO', choices=[
        ('TODO', 'To do'), ('IN_PROGRESS', 'In progress'), ('DONE', 'Done'), ('CANCELLED', 'Cancelled')])
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='completed_tasks')
    completed_at = models.DateTimeField(null=True, blank=True)
    report = models.TextField(blank=True, default='')
    version = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.title


# ===============================
# CLIENT
# ===============================

class Client(ReversibleAuditedModel):
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
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

class Payment(AuditedModel):
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="payments")
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    note = models.TextField(blank=True, null=True)

    exploitation = models.ForeignKey("Exploitation", on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.client.nom} - {self.montant}"


# ===============================
# LETTRAGE
# ===============================

class Lettrage(ReversibleAuditedModel):
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    vente = models.ForeignKey("Vente", on_delete=models.CASCADE, related_name="lettrages")
    payment = models.ForeignKey("Payment", on_delete=models.CASCADE, related_name="lettrages")

    montant = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.vente.id} ↔ {self.payment.id} ({self.montant})"


# ===============================
# PRODUCTION D'ŒUFS
# ===============================

class CollecteOeufs(ReversibleAuditedModel):
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

    class Meta(ReversibleAuditedModel.Meta):
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


class MouvementOeufs(AuditedModel):
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


class AffectationMouvementOeufs(AuditedModel):
    """Origine déclarée d'une sortie d'œufs, sans modifier le mouvement comptable."""

    mouvement = models.ForeignKey(
        MouvementOeufs,
        on_delete=models.CASCADE,
        related_name="affectations",
    )
    collecte = models.ForeignKey(
        CollecteOeufs,
        on_delete=models.PROTECT,
        related_name="affectations_sortie",
    )
    quantite = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantite__gt=0),
                name="affectation_oeufs_quantite_positive",
            ),
            models.UniqueConstraint(
                fields=["mouvement", "collecte"],
                name="affectation_oeufs_origine_unique",
            ),
        ]

    def clean(self):
        super().clean()
        if self.mouvement_id and self.collecte_id:
            if (self.mouvement.exploitation_id != self.collecte.exploitation_id
                    or self.mouvement.lot_id != self.collecte.lot_id):
                raise ValidationError("La sortie et la collecte doivent appartenir au même lot et à la même exploitation.")
            if self.mouvement.quantite_signee >= 0:
                raise ValidationError("Seule une sortie du stock peut être affectée à une collecte.")


class VenteOeufs(ReversibleAuditedModel):
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    CONDITIONNEMENT_CHOICES = [
        ('UNITE', 'Unité'),
        ('DOUZAINE', 'Douzaine'),
        ('PLATEAU', 'Plateau'),
        ('CARTON', 'Carton'),
        ('COMPOSE', 'Alvéoles et œufs supplémentaires'),
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


class ConsommationAliment(ReversibleAuditedModel):
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

    class Meta(ReversibleAuditedModel.Meta):
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


class PeseeProduction(ReversibleAuditedModel):
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

    class Meta(ReversibleAuditedModel.Meta):
        ordering = ("pesee_at", "id")
        constraints = [
            *ReversibleAuditedModel._meta.constraints,
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


from .foundation_models import (  # noqa: E402 - string relations resolve after model loading
    ExploitationMembership, DeviceRegistration, DeviceChallenge,
    OfflineAuthorization, AuditEvent,
)
from .terrain_models import DeviceTransportChallenge, TerrainSubmission, TerrainOutcome, TerrainEntityMapping, EncaissementTerrain, TerrainDecision, TerrainStockAdjustment  # noqa: E402
