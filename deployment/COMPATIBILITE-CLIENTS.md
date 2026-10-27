# Compatibilité et parcours de schéma — revue source du 22 octobre 2026

Aucun ancien APK installé n’a été extrait ou exécuté. Les versions réellement en circulation, leurs signatures et leurs URLs restent à inventorier. Aucune compatibilité générale n’est affirmée à partir de la seule source candidate.

| Domaine | Preuve source / risque | Porte requise |
|---|---|---|
| URL mobile | mobile/lib/config.dart fixe production release à https://backend-elevage.onrender.com/api ; API_URL divergent refusé en release | Vérifier URL incorporée dans APK final ; aucune bascule sur secondaire |
| Build Android | production flavor, applicationId com.elevage.app ; pubspec 1.0.0+103 | Inventorier plus grand versionCode installé avant choix du suivant ; pas de downgrade ni changement d’identité |
| Signature | build.gradle.kts exige quatre variables de signature et keystore externe, sans fallback debug | Comparer certificat final au certificat attendu des installations ; aucun keystore ouvert aujourd’hui |
| Laboratoire | flavor LAB refuse les domaines onrender.com ; production release impose le principal | Les essais backend Render peuvent utiliser un client de test synthétique. Tester un APK réel contre un laboratoire distant nécessite une solution déjà permise (hôte dédié autorisé) ou une mission séparée ; ne pas contourner la garde |
| CORS/CSRF | Nouveau profil production restreint aux origines explicites ; cookies inter-origines désactivés | Android natif inchangé par CORS ; inventorier clients web et formulaires admin, autoriser seulement origines nécessaires |
| Montants 0023 | float devient Decimal ; JSON décimal peut être transmis comme chaîne selon sérialiseur | Vérifier parsing des anciens clients et égalité des centimes, bornes/NaN/refus ; pas de conversion silencieuse |
| Membership 0017/0032/0033 | Bootstrap depuis proprietaire_id puis contraintes OWNER unique/actif | Propriétaire et opérateur séparés ; anciens clients écrivant sous OWNER peuvent recevoir refus ; ne pas rendre les droits permissifs pour les satisfaire |
| Auth 0034 + blacklist | credential_version/password_change_required ajoutés ; 12 migrations JWT | Rejouer login, refresh, expiration et révocation avec anciens JWT synthétiques ; ne pas présumer une reconnexion transparente |
| Suppressions et annulations | Historique protégé et annulations traçables, nouveaux triggers | Anciens écrans DELETE peuvent recevoir HISTORY_IMMUTABLE_USE_SUPPORTED_CANCELLATION ; rendre cette limite explicite avant ouverture |
| Offline | Transport SERVER_RECEIVED distinct de métier CONFIRMED ; UUID/sequence/signature et auteurs conservés | Rejeu, timeout/réponse perdue et reprise sans double effet ; reçu serveur ≠ application métier |
| Révocations 0037 | BUSINESS et SECURITY distinctes ; nouvelles versions/contraintes | Vérifier les anciennes déclarations retardées sur fixtures, aucune réactivation d’appareil révoqué |
| V2 | OFFLINE_FINE_PERMISSIONS_ENABLED=False, FINE_REVIEW_NOT_ACTIVATED et V2_RECONCILIATION_READ_ONLY conservés | Aucun contournement pour rendre la recette verte |
| Données tablette | Outbox/PIN/Android Keystore ne sont pas validés par les tests backend | Appareil propre distinct, crash/reprise/clé/stockage/backup à tester après autorisation ; pas d’ancien LAB Jean |
| Retour ancien backend | Colonnes/types/contraintes et auth différents après 0037 ; start historique avec effets | Répéter ancien build de repli sûr contre copie migrée avant autoriser rollback |

Commande de build future, NON exécutée, après revue signature/version/outils et autorisation F, depuis les sources mobile :

```sh
flutter build apk --release --flavor production --dart-define=APP_ENV=production --dart-define=FLUTTER_APP_FLAVOR=production --dart-define=API_URL=https://backend-elevage.onrender.com/api
```

Le paquet est une sélection source de production : ni outils Flutter/SDK, ni caches, ni wrapper binaire Gradle, ni clés ne sont embarqués. Les outils et dépendances existants doivent être vérifiés avant build ; aucun téléchargement, génération de wrapper ou installation n’est implicitement autorisé. Ne pas installer l’APK pour conclure cette étape.
