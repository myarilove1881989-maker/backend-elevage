import hashlib
import json

FIELDS = ('nombre_collecte', 'nombre_casses', 'nombre_declasses', 'nombre_consommes_donnes', 'note')

def collection_version(collection):
    data = {key: getattr(collection, key) for key in FIELDS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()
