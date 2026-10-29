"""Disposable synthetic browser fixture. Binds loopback; never accepts production DSNs."""
import email, html, json, os, re, socket, sys, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'.local/web113-browser'
LOCAL.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
os.environ.update(SECRET_KEY='synthetic-web113-browser-only-20261029',DEBUG='true',APP_ENV='test',RENDER='false',DATABASE_URL='',DJANGO_SETTINGS_MODULE='config.settings',EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend',PASSWORD_RESET_WEB_URL='http://127.0.0.1:57834',SECURE_SSL_REDIRECT='false',ALLOWED_HOSTS='127.0.0.1,localhost,testserver',OFFLINE_SIGNING_PRIVATE_KEY='')
from django.conf import settings
settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':LOCAL/'synthetic-v2.sqlite3'},'health':{'ENGINE':'django.db.backends.sqlite3','NAME':LOCAL/'synthetic-v2.sqlite3'}}
settings.ROOT_URLCONF='web113_fixture_urls'
settings.EMAIL_FILE_PATH=str(LOCAL/'mailbox')
settings.DEFAULT_FROM_EMAIL='ngo.mbi.sas@gmail.com'
settings.PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']
settings.CORS_ALLOW_ALL_ORIGINS=False
settings.CORS_ALLOWED_ORIGINS=['http://127.0.0.1:57834']
settings.OFFLINE_FINE_PERMISSIONS_ENABLED=False
import django
django.setup()
from django.core.management import call_command
call_command('migrate',interactive=False,verbosity=0)
from core.models import User,MembershipPermission,Task,Lot,Client,Payment,Vente
from django.utils import timezone
if not User.objects.filter(username='qa_owner').exists():
    from rest_framework.test import APIClient
    owner=User.objects.create_user(username='qa_owner',email='owner@example.test',password='QualificationOwner!113')
    farm=owner.exploitation
    operator=User.objects.create_user(username='qa_operator',email='operator@example.test',password='QualificationOperator!113',exploitation=farm)
    member=operator.memberships.get(exploitation=farm)
    for right in ['purchases.create','clients.create','animal_sales.create','expenses.create','cash.create','agenda.progress']:
        MembershipPermission.objects.get_or_create(membership=member,code=right,defaults={'created_by':owner})
    from core.models import Espece,CategorieDepense
    species=Espece.objects.create(exploitation=farm,nom='Poulets qualification')
    category=CategorieDepense.objects.create(exploitation=farm,nom='Qualification')
    api=APIClient();api.force_authenticate(operator)
    today=timezone.localdate().isoformat()
    customer=api.post('/api/clients/create/',{'nom':'Client démonstration','telephone':'0000000000'},format='json');assert customer.status_code==201,customer.data
    purchase=api.post('/api/achats/create/',{'nom_lot':'Lot démonstration 113','espece':species.pk,'quantite':20,'prix_unitaire':'10.00','prix_total':'200.00','date':today},format='json');assert purchase.status_code==201,purchase.data
    lot=Lot.objects.get(exploitation=farm)
    sale=api.post('/api/mouvements/create/',{'lot':lot.pk,'client':customer.data['id'],'type_mouvement':'VENTE','quantite':2,'prix_unitaire':'15.00','date':today},format='json');assert sale.status_code==201,sale.data
    expense=api.post('/api/depenses/create/',{'lot':lot.pk,'categorie':category.pk,'montant':'12.34','date':today},format='json');assert expense.status_code==201,expense.data
    client=Client.objects.get(pk=customer.data['id'])
    Payment.objects.create(exploitation=farm,client=client,montant='10.00',date=today,created_by=operator)
    Task.objects.create(exploitation=farm,title='Contrôle qualification 113',date=today,created_by=owner,assigned_to=operator)
    other=User.objects.create_user(username='qa_other',email='other@example.test',password='QualificationOther!113')
    foreign_species=Espece.objects.create(exploitation=other.exploitation,nom='Autre exploitation')
    Lot.objects.create(exploitation=other.exploitation,espece=foreign_species,nom='Lot privé autre ferme',date_debut=timezone.localdate())
    # Same policy as the delivered tablet; no device evidence is bypassed by Web.
    farm.offline_policy_enabled=True;farm.save()
    (LOCAL/'expected.json').write_text(json.dumps({'stock':18,'revenue':'30.00','expense':'12.34','payments':'10.00','owner':owner.pk,'farm':farm.pk,'lot':lot.pk,'foreign_lot':Lot.objects.get(exploitation=other.exploitation).pk}))

from django.db import close_old_connections
def deliver_test_mail():
    from core.reset_mail import deliver_pending
    while True:
        try:
            close_old_connections();deliver_pending()
        except Exception:
            pass
        time.sleep(0.25)
threading.Thread(target=deliver_test_mail,daemon=True).start()
original=socket.socket.connect
def loopback_only(sock,address):
    if not isinstance(address,tuple) or address[0] not in {'127.0.0.1','::1'}:
        raise RuntimeError('External connections forbidden in synthetic browser fixture')
    return original(sock,address)
socket.socket.connect=loopback_only
print('SYNTHETIC_BROWSER_FIXTURE_READY',flush=True)
call_command('runserver','127.0.0.1:57833',use_reloader=False,verbosity=0)
