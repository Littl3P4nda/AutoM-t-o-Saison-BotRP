# Discord Scheduled Announcer (Monthly cycles + Weekly weather)

## Setup rapide
1. Python 3.10+ recommandé.
2. Installez les dépendances :
   ```bash
   pip install discord.py pytz
   ```
3. Créez un bot sur https://discord.com/developers/applications, ajoutez un **Bot Token** et invitez-le avec les permissions d'écriture dans votre salon d'annonces.
4. Exportez les variables d'environnement :
   - `DISCORD_TOKEN` : le token de votre bot
   - `CHANNEL_ID` : l'ID du salon où poster
   - (optionnel) `TIMEZONE` (par défaut `Europe/Paris`)
   - (optionnel) `WEEKLY_WEATHER_DOW` (0=Mon ... 6=Sun, par défaut 0)
   - (optionnel) `POST_HOUR` et `POST_MINUTE` (par défaut 10:00)

## Lancer
```bash
python bot.py
```

## Ce que fait le bot
- **Mensuel (4 messages différents récurrents)** : envoie un embed le **1, 8, 15, 22** à l'heure définie.
- **Hebdomadaire (météo)** : chaque **lundi** à l'heure définie, envoie un bulletin couvrant **les 7 jours**. 
- Le **pool météo** et les **textes de cycles** sont configurables dans `MONTHLY_MESSAGES` et `WEATHER_POOL`.

## Personnalisation
- Modifiez `MONTHLY_MESSAGES` (index 0..3) pour vos 4 messages cycliques.
- Modifiez `WEATHER_POOL` par cycle (0..3) pour vos phrases météo RP.
- Changez `ANCHOR_DAYS` si vous souhaitez d'autres jalons que 1/8/15/22.

## Important
- L'état anti-duplication (dernière exécution) est en mémoire ; si vous relancez le bot pile à l'heure, il peut reposter. 
  Pour de la persistance, sauvez `last_monthly_post`/`last_weekly_post` dans un fichier JSON.
