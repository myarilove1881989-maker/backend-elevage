from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import (
    Task,
    Client,
    Achat,
    Lot,
    Mouvement,
    Espece,
    Depense,
    Vente
)

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

    class Meta:
        model = User
        fields = ("username", "password")

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
        )
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
class AchatSerializer(serializers.ModelSerializer):

    nom_lot = serializers.CharField(write_only=True)
    espece = serializers.IntegerField(write_only=True)

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
        )

    def create(self, validated_data):
        user = self.context["request"].user

        nom_lot = validated_data.pop("nom_lot")
        espece_id = validated_data.pop("espece")

        espece = Espece.objects.get(id=espece_id)

        lot = Lot.objects.create(
            nom=nom_lot,
            espece=espece,
            exploitation=user.exploitation,
            date_debut=validated_data["date"]
        )

        achat = Achat.objects.create(
            lot=lot,
            exploitation=user.exploitation,
            created_by=user,
            quantite=validated_data.get("quantite", 0),
            prix_total=validated_data.get("prix_total", 0),
            prix_unitaire=validated_data.get("prix_unitaire", 0),
            date=validated_data.get("date"),
            fournisseur=validated_data.get("fournisseur"),
            note=validated_data.get("note"),
        )

        Mouvement.objects.create(
            lot=lot,
            type_mouvement='ACHAT',
            quantite=achat.quantite,
            date=achat.date
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