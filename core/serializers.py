from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime, time
from decimal import Decimal
from .models import (
    CategorieDepense,
    Task,
    Client,
    Achat,
    Lot,
    Mouvement,
    Espece,
    Depense,
    Vente,
    Exploitation,
    CollecteOeufs,
    VenteOeufs,
    ConsommationAliment,
    PeseeProduction,
)
from .species_catalog import ensure_species_catalog

User = get_user_model()


# ===============================
# TASK
# ===============================
class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ("id", "title", "date")


# ===============================
# CLIENT
# ===============================
class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = "__all__"


# ===============================
# REGISTER
# ===============================
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password")

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return email

    def create(self, validated_data):
        # ✅ création user
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
        )

        # L'exploitation est créée automatiquement par User.save().
        exploitation = user.exploitation

        # 🔥 AJOUT ICI
        categories = [
            "Aliment",
            "Médicament",
            "Vaccin",
            "Transport",
            "Main d'oeuvre",
            "Autre"
        ]

        from .models import CategorieDepense

        for nom in categories:
            CategorieDepense.objects.create(
            nom=nom,
            exploitation=exploitation
        )

        ensure_species_catalog(exploitation)

        return user


# ===============================
# LOT (LISTE)
# ===============================
class LotSerializer(serializers.ModelSerializer):
    stock = serializers.ReadOnlyField()
    espece_nom = serializers.CharField(source="espece.nom", read_only=True)

    class Meta:
        model = Lot
        fields = "__all__"


# ===============================
# MOUVEMENT
# ===============================
class MouvementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mouvement
        fields = [
            "id",
            "type_mouvement",
            "quantite",
            "date",
            "prix_unitaire",
            "client",  # 🔥 IMPORTANT (ajout pour client)
        ]


# ===============================
# DEPENSE
# ===============================
class DepenseSerializer(serializers.ModelSerializer):
    categorie_nom = serializers.CharField(source="categorie.nom", read_only=True)

    class Meta:
        model = Depense
        fields = [
            "id",
            "categorie",
            "categorie_nom",
            "montant",
            "date",
            "note"
        ]


# ===============================
# LOT DETAIL
# ===============================
class LotDetailSerializer(serializers.ModelSerializer):

    espece_nom = serializers.CharField(source="espece.nom", read_only=True)
    stock = serializers.ReadOnlyField()

    mouvements = MouvementSerializer(many=True, read_only=True)
    depenses = DepenseSerializer(many=True, read_only=True)

    achats = serializers.SerializerMethodField()

    class Meta:
        model = Lot
        fields = [
            "id",
            "nom",
            "espece_nom",
            "date_debut",
            "date_fin",
            "date_creation",
            "type_production",
            "statut_production",
            "date_naissance",
            "age_arrivee_semaines",
            "date_debut_ponte",
            "stock",
            "mouvements",
            "depenses",
            "achats",
        ]

    def get_achats(self, obj):
        return [
            {
                "id": a.id,
                "quantite": a.quantite,
                "date": a.date,
                "prix_unitaire": a.prix_unitaire,
                "prix_total": a.prix_total,
            }
            for a in obj.achats.all()
        ]


# ===============================
# VENTE
# ===============================
class VenteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vente
        fields = "__all__"


# ===============================
# ACHAT
# ===============================
# serializers.py

# ===============================
# ACHAT
# ===============================
class AchatSerializer(serializers.ModelSerializer):

    nom_lot = serializers.CharField(write_only=True)
    espece = serializers.IntegerField(write_only=True)

    fournisseur = serializers.CharField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    type_production = serializers.ChoiceField(
        choices=Lot.TYPE_PRODUCTION_CHOICES,
        write_only=True,
        required=False,
        default="CHAIR",
    )
    statut_production = serializers.ChoiceField(
        choices=Lot.STATUT_PRODUCTION_CHOICES,
        write_only=True,
        required=False,
        default="ELEVAGE",
    )
    date_naissance = serializers.DateField(write_only=True, required=False, allow_null=True)
    age_arrivee_semaines = serializers.IntegerField(
        write_only=True,
        required=False,
        allow_null=True,
        min_value=0,
    )
    date_debut_ponte = serializers.DateField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Achat
        fields = (
            "id",
            "nom_lot",
            "espece",
            "quantite",
            "prix_total",
            "prix_unitaire",
            "date",
            "fournisseur",
            "note",
            "type_production",
            "statut_production",
            "date_naissance",
            "age_arrivee_semaines",
            "date_debut_ponte",
        )

    def create(self, validated_data):
        user = self.context["request"].user

        # 🔴 Vérification user exploitation (ICI c’est correct)
        if not getattr(user, "exploitation", None):
            raise serializers.ValidationError("Utilisateur sans exploitation")

        nom_lot = validated_data.pop("nom_lot")
        espece_id = validated_data.pop("espece")

        fournisseur = validated_data.pop("fournisseur", "")
        note = validated_data.pop("note", "")
        type_production = validated_data.pop("type_production", "CHAIR")
        statut_production = validated_data.pop("statut_production", "ELEVAGE")
        date_naissance = validated_data.pop("date_naissance", None)
        age_arrivee_semaines = validated_data.pop("age_arrivee_semaines", None)
        date_debut_ponte = validated_data.pop("date_debut_ponte", None)

        try:
            espece = Espece.objects.get(id=espece_id)
        except Espece.DoesNotExist:
            raise serializers.ValidationError("Espèce invalide")

        if validated_data.get("quantite", 0) <= 0:
            raise serializers.ValidationError("Quantité invalide")

        if validated_data.get("prix_total", 0) <= 0:
            raise serializers.ValidationError("Prix total invalide")

        date_achat = validated_data.get("date")
        if not date_achat:
            raise serializers.ValidationError("Date requise")

        # ✅ Création lot
        lot = Lot.objects.create(
            nom=nom_lot,
            espece=espece,
            exploitation=user.exploitation,
            date_debut=date_achat,
            type_production=type_production,
            statut_production=statut_production,
            date_naissance=date_naissance,
            age_arrivee_semaines=age_arrivee_semaines,
            date_debut_ponte=date_debut_ponte,
        )

        # ✅ Création achat
        achat = Achat.objects.create(
            lot=lot,
            exploitation=user.exploitation,
            created_by=user,
            fournisseur=fournisseur,
            note=note,
            **validated_data
        )

        # ✅ Mouvement
        Mouvement.objects.create(
            lot=lot,
            type_mouvement="ACHAT",
            quantite=achat.quantite,
            date=achat.date,
            prix_unitaire=achat.prix_unitaire,
            exploitation=user.exploitation,
            created_by=user
        )

        return achat
# ===============================
# ANALYTICS
# ===============================
class StockDetailSerializer(serializers.Serializer):
    stock_initial = serializers.IntegerField()
    stock_restant = serializers.IntegerField()
    vendu = serializers.IntegerField()
    perdu = serializers.IntegerField()
    mortalite = serializers.IntegerField()
    vol = serializers.IntegerField()
    don = serializers.IntegerField()


class CAParLotSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nom = serializers.CharField()
    total_ca = serializers.FloatField()
    total_depense = serializers.FloatField()
    total_achat = serializers.FloatField()
    investissement = serializers.FloatField()


class DepenseDetailSerializer(serializers.Serializer):
    categorie__nom = serializers.CharField()
    total = serializers.FloatField()


class MargeParLotSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nom = serializers.CharField()
    total_ca = serializers.FloatField()
    total_depenses = serializers.FloatField()
    marge = serializers.FloatField()
    rentabilite = serializers.FloatField()


# ===============================
# PRODUCTION D'ŒUFS
# ===============================
class CollecteOeufsSerializer(serializers.ModelSerializer):
    nombre_commercialisable = serializers.IntegerField(read_only=True)
    lot_nom = serializers.CharField(source="lot.nom", read_only=True)

    class Meta:
        model = CollecteOeufs
        fields = (
            "id",
            "lot",
            "lot_nom",
            "collecte_at",
            "nombre_collecte",
            "nombre_casses",
            "nombre_declasses",
            "nombre_consommes_donnes",
            "nombre_commercialisable",
            "note",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate_lot(self, lot):
        request = self.context["request"]
        if lot.exploitation_id != request.user.exploitation_id:
            raise serializers.ValidationError("Ce lot appartient à une autre exploitation.")
        if lot.type_production != "OEUFS":
            raise serializers.ValidationError("Les collectes sont réservées aux lots de ponte.")
        return lot

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance

        total = attrs.get("nombre_collecte", getattr(instance, "nombre_collecte", 0))
        casses = attrs.get("nombre_casses", getattr(instance, "nombre_casses", 0))
        declasses = attrs.get("nombre_declasses", getattr(instance, "nombre_declasses", 0))
        consommes = attrs.get(
            "nombre_consommes_donnes",
            getattr(instance, "nombre_consommes_donnes", 0),
        )

        if casses + declasses + consommes > total:
            raise serializers.ValidationError(
                "Le total cassé, déclassé et consommé/donné ne peut pas dépasser la collecte."
            )
        return attrs


class VenteOeufsSerializer(serializers.ModelSerializer):
    client = serializers.IntegerField(write_only=True)
    client_id = serializers.IntegerField(source="vente.client_id", read_only=True)
    client_nom = serializers.CharField(source="vente.client.nom", read_only=True)
    date = serializers.DateField(source="vente.date", required=False)
    statut = serializers.CharField(source="vente.statut", read_only=True)
    montant_paye = serializers.FloatField(source="vente.montant_paye", read_only=True)
    reste = serializers.FloatField(source="vente.reste_a_payer", read_only=True)

    class Meta:
        model = VenteOeufs
        fields = (
            "id",
            "vente",
            "lot",
            "client",
            "client_id",
            "client_nom",
            "date",
            "conditionnement",
            "nombre_conditionnements",
            "oeufs_par_conditionnement",
            "nombre_oeufs",
            "prix_unitaire_conditionnement",
            "montant_total",
            "montant_paye",
            "reste",
            "statut",
            "created_at",
        )
        read_only_fields = (
            "vente",
            "nombre_oeufs",
            "montant_total",
            "created_at",
        )

    def validate_lot(self, lot):
        request = self.context["request"]
        if lot.exploitation_id != request.user.exploitation_id:
            raise serializers.ValidationError("Ce lot appartient à une autre exploitation.")
        if lot.type_production != "OEUFS":
            raise serializers.ValidationError("Les ventes d'œufs exigent un lot de ponte.")
        return lot

    def validate_client(self, client_id):
        request = self.context["request"]
        if not Client.objects.filter(
            id=client_id,
            exploitation=request.user.exploitation,
        ).exists():
            raise serializers.ValidationError("Client introuvable pour cette exploitation.")
        return client_id

    def validate(self, attrs):
        attrs = super().validate(attrs)
        conditionnement = attrs.get("conditionnement")
        nombre = attrs.get("nombre_conditionnements", 0)
        oeufs_par_conditionnement = attrs.get("oeufs_par_conditionnement")
        prix = attrs.get("prix_unitaire_conditionnement")

        tailles_fixes = {"UNITE": 1, "DOUZAINE": 12, "PLATEAU": 30}
        if conditionnement in tailles_fixes:
            attrs["oeufs_par_conditionnement"] = tailles_fixes[conditionnement]
        elif conditionnement == "CARTON":
            if not oeufs_par_conditionnement or oeufs_par_conditionnement <= 0:
                raise serializers.ValidationError({
                    "oeufs_par_conditionnement": "La contenance du carton est obligatoire."
                })

        if nombre <= 0:
            raise serializers.ValidationError({
                "nombre_conditionnements": "Le nombre de conditionnements doit être positif."
            })
        if prix is None or prix <= 0:
            raise serializers.ValidationError({
                "prix_unitaire_conditionnement": "Le prix doit être positif."
            })
        return attrs


class ConsommationAlimentSerializer(serializers.ModelSerializer):
    lot_nom = serializers.CharField(source="lot.nom", read_only=True)
    categorie_depense = serializers.CharField(
        source="depense.categorie.nom",
        read_only=True,
    )
    cout_calcule = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = ConsommationAliment
        fields = (
            "id",
            "lot",
            "lot_nom",
            "date",
            "distribution_at",
            "aliment",
            "quantite_kg",
            "prix_kg",
            "depense",
            "categorie_depense",
            "cout_calcule",
            "note",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate_lot(self, lot):
        request = self.context["request"]
        if lot.exploitation_id != request.user.exploitation_id:
            raise serializers.ValidationError("Ce lot appartient à une autre exploitation.")
        return lot

    def validate_aliment(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Indiquez le nom de l'aliment.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        lot = attrs.get("lot", getattr(instance, "lot", None))
        depense = attrs.get("depense", getattr(instance, "depense", None))
        quantite = attrs.get("quantite_kg", getattr(instance, "quantite_kg", None))
        prix_kg = attrs.get("prix_kg", getattr(instance, "prix_kg", None))

        # Le champ date reste disponible pour les anciens clients et les statistiques
        # avicoles. Les nouvelles saisies ont une date et une heure réelles.
        if "distribution_at" in attrs and attrs["distribution_at"] is None:
            raise serializers.ValidationError({"distribution_at": "Indiquez la date et l'heure."})
        if "distribution_at" in attrs:
            attrs["date"] = timezone.localtime(attrs["distribution_at"]).date()
        elif "date" in attrs and (instance is None or attrs["date"] != instance.date):
            attrs["distribution_at"] = timezone.make_aware(
                datetime.combine(attrs["date"], time(12, 0))
            )
        elif instance is None:
            attrs["distribution_at"] = timezone.now()
            attrs["date"] = timezone.localdate(attrs["distribution_at"])

        if quantite is None or quantite <= 0:
            raise serializers.ValidationError({"quantite_kg": "La quantité doit être positive."})
        if prix_kg is not None and prix_kg < 0:
            raise serializers.ValidationError({"prix_kg": "Le prix ne peut pas être négatif."})
        if depense:
            if depense.lot.exploitation_id != self.context["request"].user.exploitation_id:
                raise serializers.ValidationError({"depense": "Dépense introuvable."})
            if lot and depense.lot_id != lot.id:
                raise serializers.ValidationError({
                    "depense": "La dépense doit appartenir au même lot."
                })
        return attrs


class PeseeProductionSerializer(serializers.ModelSerializer):
    lot_nom = serializers.CharField(source="lot.nom", read_only=True)
    poids_moyen_kg = serializers.SerializerMethodField()
    gmq_g_par_jour = serializers.SerializerMethodField()

    class Meta:
        model = PeseeProduction
        fields = (
            "id", "lot", "lot_nom", "pesee_at", "nombre_animaux_peses",
            "poids_total_kg", "poids_moyen_kg", "gmq_g_par_jour", "note",
            "created_at",
        )
        read_only_fields = ("created_at",)
        extra_kwargs = {
            "nombre_animaux_peses": {"min_value": 1},
            "poids_total_kg": {"min_value": Decimal("0.001")},
        }

    def validate_lot(self, lot):
        request = self.context["request"]
        if lot.exploitation_id != request.user.exploitation_id:
            raise serializers.ValidationError("Ce lot appartient à une autre exploitation.")
        if lot.type_production != "CHAIR":
            raise serializers.ValidationError("Les pesées sont réservées aux lots CHAIR.")
        return lot

    def validate(self, attrs):
        attrs = super().validate(attrs)
        nombre = attrs.get("nombre_animaux_peses")
        stock_actuel = self.context.get("stock_actuel")
        if nombre is not None and stock_actuel is not None and nombre > stock_actuel:
            raise serializers.ValidationError({
                "nombre_animaux_peses": (
                    f"L'échantillon ne peut pas dépasser l'effectif actuel ({stock_actuel})."
                )
            })
        return attrs

    def get_poids_moyen_kg(self, obj):
        return obj.poids_moyen_kg.quantize(Decimal("0.001"))

    def get_gmq_g_par_jour(self, obj):
        return self.context.get("gmq_by_id", {}).get(obj.pk)
