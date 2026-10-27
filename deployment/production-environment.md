# Réglages à préparer puis approuver, jamais appliqués par ce fichier

Cible unique : backend-elevage, srv-d7sdmucm0tmc73cvc6u0. Le secondaire reste suspendu et intact.

- DJANGO_SETTINGS_MODULE=config.settings_production ; APP_ENV=production ; DEBUG=False.
- ALLOWED_HOSTS=backend-elevage.onrender.com ; ajouter uniquement les domaines vérifiés réellement utilisés. Une ancienne valeur * bloque désormais le démarrage.
- CORS_ALLOWED_ORIGINS : liste HTTPS explicite, vide par défaut. Android natif ne dépend pas de CORS ; inventorier les clients web avant activation. CORS avec cookies désactivé dans cette livraison : tout besoin de cookies entre origines nécessite une revue distincte.
- CSRF_TRUSTED_ORIGINS : vide par défaut ; ajouter uniquement les origines HTTPS nécessaires aux formulaires de confiance. Même origine n'exige pas d'exception.
- HTTPS et cookies sécurisés imposés par settings_production, indépendamment des anciens réglages False. Proxy X-Forwarded-Proto existant conservé ; vérifier la chaîne proxy et l'absence de boucle. L'exemption HTTPS ne concerne que /api/health/.
- OFFLINE_FINE_PERMISSIONS_ENABLED=false exigé ; aucun changement des gardes V2.
- SECRET_KEY, DATABASE_URL, clé privée offline et identifiants SMTP : injection sécurisée existante à vérifier, aucune valeur dans le paquet. Aucune rotation.
- PostgreSQL : TLS requis par le helper ; préférer verify-full avec CA/nom vérifiés après essai isolé. Le helper existant ne prouve pas lui-même l'authentification du serveur.
- EMAIL_BACKEND : choisir et vérifier le backend SMTP approprié ; le défaut console historique peut journaliser des codes de réinitialisation. Ne pas lancer de test d'email réel sans accord.
- Version Python Render à épingler après qualification Linux des distributions épinglées ; ne pas présumer l'équivalence de Python Windows 3.14.2.

render-settings.json décrit les réglages proposés, pas un Blueprint à appliquer. Aucun pré-déploiement migrateur sur le service gratuit. Le build collectstatic écrit des fichiers statiques dans l'environnement de build, jamais des données métier ; l'initialisation Django et ses éventuels effets doivent être vérifiés en laboratoire.
