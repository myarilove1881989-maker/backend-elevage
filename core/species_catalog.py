SPECIES_CATALOG = (
    "Poulet",
    "Dinde",
    "Canard",
    "Pintade",
    "Porc",
    "Bovin",
    "Mouton",
    "Chèvre",
    "Lapin",
    "Poisson",
    "Œufs",
)


def canonical_species_name(value):
    normalized = str(value or "").strip().casefold()
    return next(
        (name for name in SPECIES_CATALOG if name.casefold() == normalized),
        None,
    )


def ensure_species_catalog(exploitation):
    existing = {
        species.nom.casefold(): species.nom
        for species in exploitation.especes.all()
    }
    from .models import Espece

    Espece.objects.bulk_create(
        [
            Espece(nom=name, exploitation=exploitation)
            for name in SPECIES_CATALOG
            if name.casefold() not in existing
        ]
    )
