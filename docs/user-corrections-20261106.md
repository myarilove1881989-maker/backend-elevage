# Correctifs utilisateurs Web — 6 novembre 2026

## Comportement
La programmation sanitaire accepte un utilisateur autorisé existant ou un contact vétérinaire/opérateur de la même exploitation. Un contact ne crée ni compte, ni permission. Création et programmation sont idempotentes ; les dates explicites restent atomiques et ne consomment aucun vaccin.
L’historique propriétaire réunit les déclarations terrain et les événements Web audités, conserve leurs auteurs et applique les filtres et la pagination au même périmètre d’exploitation. Le prix d’origine d’un lot est fourni uniquement lorsqu’il est déterminable sans arrondi : achat unique cohérent ou naissance ; les origines mixtes/corrigées restent indéterminées. Aucun calcul financier existant n’est modifié.

## Validation locale
- Suite PostgreSQL ciblée : 88 tests réussis (bilan, achats physiques, multidates, historique et offline).
- Suite complémentaire finale : 35 tests réussis, comprenant des tests communs à la série précédente et la répétition de migration.
- Migration synthétique 0049 → 0050 : refus sans sauvegarde déclarée ; conservation vérifiée de toutes les colonnes historiques ; redémarrage 0050 accepté.
- Refus d’un contact réellement rattaché à une autre exploitation ; report de vaccination conservant son responsable ; création de contact sans création de compte.

## Publication conditionnelle
Production observée : backend 1f1cd3f, schéma 0049. Ce candidat inclut aussi la prise en charge multidate bbd7119.
La migration 0050 est additive : référentiel de contacts, clé nullable sur HealthTask, unicité exploitation/UUID. Avant publication : export logique récent, empreinte SHA256 hors Git, restauration de vérification isolée. Aucune sauvegarde de production n’a encore été vérifiée pendant cette mission.
Ne définir ELEVAGE_ALLOW_HEALTH_UPGRADE=true et ELEVAGE_HEALTH_BACKUP_SHA256 qu’après cette vérification. La garde vérifie le contenu exact de la migration, le registre, le plan unique, les verrous, les délais et les données historiques. Retirer les paramètres d’autorisation après succès. Auto-déploiement désactivé.

## Retour arrière
Ne pas redéployer aveuglément 1f1cd3f sur le schéma 0050 : sa garde de démarrage le refuserait. En cas de retrait du Web, conserver ce backend compatible ou préparer une version antérieure avec une garde explicitement compatible 0050. Ne supprimer aucune table, ne pas inverser la migration et ne restaurer aucune donnée de production sans décision séparée. Le retour Web utilise le commit e735f051af607347bb0edbd1e95ab95730eccd5f.
Android 118, les formats signés hors ligne et les secrets restent inchangés. Aucun achat de production n’a été effectué.
