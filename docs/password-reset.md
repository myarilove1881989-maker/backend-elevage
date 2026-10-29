# Mot de passe oublié — préparation sans déploiement

Branche `feature/password-reset-complete`, basée sur le candidat offline 2K
`b815c45`. Les routes et le bouton existaient déjà sur ce candidat ; leur
présence dans l’application distribuée n’est pas démontrée.

## Fonctionnement et protections

Trois routes publiques POST : `/api/password-reset/request/` (`email`),
`verify/` (`email`, `code`) et `confirm/` (`email`, `reset_token`, `new_password`).
La confirmation du mot de passe est effectuée dans Flutter ; Django applique
également ses validateurs de robustesse.

Code aléatoire à six chiffres, haché en base, dix minutes de validité, cinq
essais. L’échange consomme le code et délivre un jeton signé valable au plus
dix minutes, soumis aussi à l’expiration originale. La confirmation consomme
le jeton sous verrou et invalide les autres codes du compte. Un compte inactif
ne peut pas réinitialiser son mot de passe.

Réponse et statut identiques pour compte inconnu, inactif, limité ou en attente
d’envoi. Le calcul coûteux du hash est effectué aussi pour les adresses inconnues
et les demandes en cooldown. Aucun appel SMTP dans cette requête publique.
La file stocke le code chiffré avec une clé dérivée de SECRET_KEY ; aucun code,
jeton, destinataire ou message d’erreur du fournisseur n’est journalisé.
Une rotation de SECRET_KEY invalide les jetons et les e-mails encore en attente.

Limites partagées en base entre processus : 30 appels par IP/action/heure,
3 demandes par adresse/heure, 10 vérifications et 10 confirmations par
adresse/heure ; un envoi par minute par compte. Compteurs indexés par HMAC,
sans adresse/IP en clair. REMOTE_ADDR est utilisé ; X-Forwarded-For est ignoré.
Vérifier en préproduction si le proxy expose une IP commune aux utilisateurs :
la limite IP serait alors partagée. Ne pas accepter des en-têtes arbitraires.

Les refresh tokens sont blacklistés. Les access tokens déjà émis peuvent
rester actifs jusqu’à leur expiration actuelle (60 minutes). Les autorisations
hors ligne conservent leur protocole d’expiration/révocation existant. Aucune
déclaration locale n’est effacée, aucune tablette réactivée.

## Configuration Render à appliquer seulement après validation

Conserver les autres variables du service. Renseigner les secrets dans Render
Environment ; choisir **Save only** pour ne pas déployer. Aucun fournisseur,
abonnement, service ou paramètre distant n’a été créé/modifié.

| Variable | Valeur attendue |
|---|---|
| EMAIL_BACKEND | django.core.mail.backends.smtp.EmailBackend |
| EMAIL_HOST | hôte SMTP du fournisseur autorisé |
| EMAIL_PORT | 587 pour STARTTLS |
| EMAIL_USE_TLS | true pour STARTTLS |
| EMAIL_USE_SSL | false pour STARTTLS ; jamais TLS et SSL ensemble |
| EMAIL_HOST_USER | identifiant SMTP dédié |
| EMAIL_HOST_PASSWORD | secret SMTP à conserver uniquement dans Render |
| DEFAULT_FROM_EMAIL | adresse du domaine expéditeur vérifié |
| DEBUG | false |
| SECRET_KEY | conserver la clé existante |

Timeout SMTP fixé à dix secondes. Configurer SPF, DKIM et DMARC pour le domaine
expéditeur. Ne jamais utiliser le backend console en production (codes dans
les logs). Tester une boîte de recette seulement après autorisation d’envoi.

Les instances Render Free bloquent les ports SMTP 25/465/587 ; le port 25 est
bloqué pour tous les services. Le type d’instance et le fournisseur autorisé
restent à confirmer avant activation. Ne pas changer de plan sans accord.

Après livraison approuvée : appliquer la migration 0032 sur la base sauvegardée,
exécuter chaque minute `python manage.py send_password_reset_emails` dans le
même environnement backend, avec accès à la même base et SECRET_KEY. Ce
traitement peut être programmé sur l’infrastructure existante ; aucun nouveau
service payant n’a été créé. Exécuter chaque jour
`python manage.py cleanup_password_resets` pour supprimer les données expirées.
Une panne d’envoi invalide le code, écrit un message fixe et retire le travail
de la file : l’utilisateur peut redemander un code après le délai de limitation.
Un arrêt après acceptation SMTP mais avant validation en base peut renvoyer le
même code ; cela ne crée aucun nouveau jeton ni double réinitialisation.

Les tests locaux SQLite ne prouvent pas les verrous de concurrence PostgreSQL.
Exécuter la suite sur PostgreSQL avant fusion et faire la recette mail avant
activation. Aucun déploiement autorisé dans cette tâche.

Documentation consultée le 25/10/2026 :
- https://render.com/docs/configure-environment-variables
- https://render.com/docs/free
