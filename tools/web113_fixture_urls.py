from pathlib import Path
from django.conf import settings
from django.http import HttpResponse
from django.urls import path,include
import email, html, re

def mailbox(request):
    assert settings.DEBUG and request.get_host() in {'127.0.0.1:57833','localhost:57833'}
    files=sorted(Path(settings.EMAIL_FILE_PATH).glob('*'),key=lambda p:p.stat().st_mtime)
    body='Aucun e-mail synthétique reçu.'
    if files:
        message=email.message_from_bytes(files[-1].read_bytes())
        payload=message.get_payload(decode=True)
        text=payload.decode(message.get_content_charset() or 'utf-8') if payload else ''
        match=re.search(r'http://127\.0\.0\.1:57834/[^\s]+',text)
        body=html.escape(text)
        if match:body+='<p><a href="'+html.escape(match.group())+'">Ouvrir le lien de réinitialisation de test</a></p>'
    return HttpResponse('<h1>Boîte e-mail synthétique locale</h1><pre>'+body+'</pre>')

urlpatterns=[path('__qa__/mailbox/',mailbox),path('',include('config.urls'))]
