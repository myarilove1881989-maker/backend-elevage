"""Compatibility import for callers of the historical stock helper."""
from .animal_stock import current_animal_stock


def get_lot_stock(lot):
    return current_animal_stock(lot)
