import os
import random
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import pytz
import discord
import json
from zoneinfo import ZoneInfo  # Python 3.11+


# ------------- CONFIG -------------

import os
TOKEN = os.getenv("DISCORD_TOKEN", "")
if not TOKEN:
    print("❌ DISCORD_TOKEN manquant (variable d'environnement).")

CHANNEL_LOG = 1408261120925634610
CHANNEL_SAISON = 1408264960714211348
CHANNEL_METEO = 1407922710855684166

intents = discord.Intents.default()
client = discord.Client(intents=intents)

PARIS_TZ = ZoneInfo("Europe/Paris")

# ------------- CALCULE DES SAISONS -------------


# ================== SAISONS PAR CONTINENT (messages auto) ==================


# Moyenne des fuseaux par rapport à l'UTC (heures, minutes)
CONTINENT_OFFSETS = {
    "Europe":   ( +2,  0),
    "Afrique":  ( +1, 30),
    "Amérique": ( -6, -30),
    "Asie":     ( +7,  0),
    "Océanie":  ( +4, 15),
}

SEASON_EMOJI = {"Hiver":"❄️","Printemps":"🌱","Été":"☀️","Automne":"🍂"}

def season_from_day(day: int) -> str:
    # 1–8 : Hiver ; 9–15 : Printemps ; 16–23 : Été ; 24–31 : Automne
    if 1 <= day <= 8:     return "Hiver"
    if 9 <= day <= 15:    return "Printemps"
    if 16 <= day <= 23:   return "Été"
    return "Automne"

def utc_now():
    return datetime.now(timezone.utc)

def apply_offset_utc(dt_utc: datetime, h: int, m: int) -> datetime:
    return dt_utc + timedelta(hours=h, minutes=m)

def to_paris(dt: datetime) -> datetime:
    # convertit n’importe quel datetime aware en Europe/Paris
    return dt.astimezone(PARIS_TZ)

def unix(dt: datetime) -> int:
    """Retourne l'epoch (secondes) pour un datetime aware."""
    return int(dt.timestamp())

CONFIG_FILE = "season_state.json"
# structure: {"messages": {continent: message_id}, "last_season": {continent: "Été"}}
def load_state():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"messages":{}, "last_season":{}}

def save_state(state: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ save_state:", e)

def _next_season_boundary_local(local_dt: datetime) -> datetime:
    """Renvoie le début (00:00 local approx) du prochain jour-seuil : 9, 16, 24, 1."""
    base_midnight = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    d = local_dt.day
    if d <= 8:
        return base_midnight.replace(day=9)
    elif d <= 15:
        return base_midnight.replace(day=16)
    elif d <= 23:
        return base_midnight.replace(day=24)
    else:
        year = base_midnight.year + (1 if base_midnight.month == 12 else 0)
        month = 1 if base_midnight.month == 12 else base_midnight.month + 1
        return base_midnight.replace(year=year, month=month, day=1)

state = load_state()

def build_continent_embed(continent: str, local_dt: datetime, season: str) -> discord.Embed:
    titre = f"{continent} — Saison actuelle"
    desc  = f"{SEASON_EMOJI[season]} **{season}**\n"
    desc += f"_Date locale de référence :_ **{local_dt.strftime('%d %b %Y')}**"
    emb = discord.Embed(title=titre, description=desc, color=discord.Color.orange())

    # --- Footer en heure de Paris ---
    # 1) “Dernière vérif” = maintenant (Paris)
    now_paris = to_paris(utc_now())

    # 2) “Prochaine transition” = prochain seuil local (minuit local approx) converti en Paris
    #    On dispose de local_dt = utc_now() + offset ; donc:
    #    a) calcule le prochain minuit local approx
    next_local_midnight = _next_season_boundary_local(local_dt)
    #    b) reviens en UTC (en retirant l’offset du continent)
    h, m = CONTINENT_OFFSETS[continent]
    next_boundary_utc = (next_local_midnight - timedelta(hours=h, minutes=m)).replace(tzinfo=timezone.utc)
    #    c) convertis pour affichage Paris
    next_boundary_paris = to_paris(next_boundary_utc)

    emb.set_footer(
        text=f"Dernière vérif : <t:{unix(now_paris)}:f> • Prochaine transition : <t:{unix(next_boundary_paris)}:R>"
    )
    return emb

async def ensure_continent_messages():
    """Crée (ou récupère) 1 message par continent dans CHANNEL_SAISON et mémorise leur ID."""
    saison_channel = client.get_channel(CHANNEL_SAISON) or await client.fetch_channel(CHANNEL_SAISON)
    for cont, (h, m) in CONTINENT_OFFSETS.items():
        msg_id = state["messages"].get(cont)
        now_utc = datetime.utcnow()
        local_dt = apply_offset_utc(now_utc, h, m)
        season = season_from_day(local_dt.day)
        emb = build_continent_embed(cont, local_dt, season)

        if msg_id:
            # vérifier que le message existe encore
            try:
                msg = await saison_channel.fetch_message(msg_id)
                # on met déjà à jour pour être sûr que le contenu est correct
                await msg.edit(embed=emb)
                state["last_season"][cont] = season
                continue
            except Exception:
                # message supprimé → on recrée
                pass

        # créer un nouveau message et mémoriser l'ID
        new_msg = await saison_channel.send(embed=emb)
        state["messages"][cont] = new_msg.id
        state["last_season"][cont] = season

    save_state(state)
    print("✅ Messages saison par continent prêts.")

async def update_continent_messages_if_needed():
    """Toutes les quelques minutes : recalcule la saison locale de chaque continent.
       Si changement → édite le message correspondant uniquement pour ce continent."""
    saison_channel = client.get_channel(CHANNEL_SAISON) or await client.fetch_channel(CHANNEL_SAISON)
    for cont, (h, m) in CONTINENT_OFFSETS.items():
        msg_id = state["messages"].get(cont)
        if not msg_id:
            continue
        try:
            msg = await saison_channel.fetch_message(msg_id)
        except Exception as e:
            print(f"⚠️ Impossible de fetch le message {cont}: {e}")
            continue

        now_utc = datetime.utcnow()
        local_dt = apply_offset_utc(now_utc, h, m)
        season = season_from_day(local_dt.day)
        last = state["last_season"].get(cont)

        if season != last:
            emb = build_continent_embed(cont, local_dt, season)
            try:
                await msg.edit(embed=emb)
                state["last_season"][cont] = season
                save_state(state)
                print(f"🔄 Saison mise à jour pour {cont} → {season}")
            except Exception as e:
                print(f"❌ Edition échouée pour {cont}: {e}")

async def season_scheduler_loop():
    """Boucle légère qui vérifie périodiquement (toutes les 10 minutes)."""
    await client.wait_until_ready()
    # s'assure que les messages existent au démarrage
    await ensure_continent_messages()

    while not client.is_closed():
        try:
            await update_continent_messages_if_needed()
        except Exception as e:
            print("⚠️ season_scheduler_loop:", e)
        # on vérifie toutes les 10 minutes (suffisant pour détecter un changement de jour)
        await asyncio.sleep(600)

# ====================== METEO QUOTIDIENNE PAR CONTINENT / BIOMES ======================
# Ce module s'auto-enregistre : AUCUNE modif ailleurs. Il ajoute son listener on_ready.
# Il crée 5 messages (1/continent) dans CHANNEL_METEO et les met à jour chaque jour
# à minuit local du continent (offset moyen). Températures = moyennes N-1 + lissage
# aux bornes de saison (crossfade) + petite variabilité quotidienne.

import json as _wxjson
import random as _wxrand
from datetime import datetime as _dt, timedelta as _td

# ---- sécurités : récupérer objets existants ou définir fallback ----
try:
    _client = client
except NameError:
    import discord as _discord
    _client = _discord.Client(intents=_discord.Intents.default())

try:
    _CH_METEO = CHANNEL_METEO
except NameError:
    _CH_METEO = None  # doit être défini dans ton code

try:
    _CH_LOG = CHANNEL_LOG
except NameError:
    _CH_LOG = None

# Offsets moyens continents (UTC) — on réutilise si déjà présents
try:
    _CONT_OFFSETS = CONTINENT_OFFSETS
except NameError:
    _CONT_OFFSETS = {
        "Europe":   ( +2,  0),
        "Afrique":  ( +1, 30),
        "Amérique": ( -6, -30),
        "Asie":     ( +7,  0),
        "Océanie":  ( +4, 15),
    }

# Découpage saison — on réutilise si déjà présent
try:
    _season_from_day = season_from_day
except NameError:
    def _season_from_day(day: int) -> str:
        if 1 <= day <= 8:   return "Hiver"
        if 9 <= day <= 15:  return "Printemps"
        if 16 <= day <= 23: return "Été"
        return "Automne"

# ---- biomes par continent (selon ta liste) ----
_BIOMES = {
    "Afrique": ["🌾 Zones Savanes", "🌵 Zones Deserts", "🦜 Zones Tropicales", "🌱 Zones Marécageuses", "🏙️ Zones Urbaines"],
    "Asie": ["🦜 Zones Tropicales", "🌾 Zones Prairies", "⛰️ Zones Montagneuses", "❄️ Zones Enneigées", "🌳 Zones Forestières", "🏙️ Zones Urbaines"],
    "Amérique": ["🌳 Zones Forestières", "🌾 Zones Clairière", "🌵 Zones Deserts", "⛰️ Zones Montagneuses", "❄️ Zones Enneigées", "🦜 Zones Tropicales", "🌱 Zones Mangroves", "🏙️ Zones Urbaines"],
    "Europe": ["🌳 Zones Forestières", "⛰️ Zones Montagneuses", "❄️ Zones Enneigées", "🌾 Zones Prairies", "🏙️ Zones Urbaines"],
    "Océanie": ["🌴 Zones Insulaires", "🌾 Zones Savanes", "🦜 Zones Tropicales", "🌵 Zones Deserts", "⛰️ Zones Montagneuses", "🏙️ Zones Urbaines"],
}

# ---- températures moyennes N-1 par (continent, "nom biome abrégé", saison) ----
# On utilise une clé "abrégée" pour matcher facilement : ex. "Savanes", "Deserts", "Tropicales", etc.
# Tu peux ajuster ces valeurs plus tard si besoin.
_N1 = {
    # Afrique
    ("Afrique","Savanes","Hiver"):24, ("Afrique","Savanes","Printemps"):26, ("Afrique","Savanes","Été"):27, ("Afrique","Savanes","Automne"):25,
    ("Afrique","Deserts","Hiver"):20, ("Afrique","Deserts","Printemps"):30, ("Afrique","Deserts","Été"):38, ("Afrique","Deserts","Automne"):28,
    ("Afrique","Tropicales","Hiver"):27, ("Afrique","Tropicales","Printemps"):28, ("Afrique","Tropicales","Été"):28, ("Afrique","Tropicales","Automne"):27,
    ("Afrique","Marécageuses","Hiver"):25, ("Afrique","Marécageuses","Printemps"):26, ("Afrique","Marécageuses","Été"):26, ("Afrique","Marécageuses","Automne"):25,
    ("Afrique","Urbaines","Hiver"):26, ("Afrique","Urbaines","Printemps"):28, ("Afrique","Urbaines","Été"):29, ("Afrique","Urbaines","Automne"):27,

    # Asie
    ("Asie","Tropicales","Hiver"):26, ("Asie","Tropicales","Printemps"):28, ("Asie","Tropicales","Été"):29, ("Asie","Tropicales","Automne"):27,
    ("Asie","Prairies","Hiver"):5,  ("Asie","Prairies","Printemps"):15, ("Asie","Prairies","Été"):24, ("Asie","Prairies","Automne"):14,
    ("Asie","Montagneuses","Hiver"):-2, ("Asie","Montagneuses","Printemps"):6, ("Asie","Montagneuses","Été"):12, ("Asie","Montagneuses","Automne"):4,
    ("Asie","Enneigées","Hiver"):-10, ("Asie","Enneigées","Printemps"):0, ("Asie","Enneigées","Été"):8, ("Asie","Enneigées","Automne"):-2,
    ("Asie","Forestières","Hiver"):2, ("Asie","Forestières","Printemps"):12, ("Asie","Forestières","Été"):20, ("Asie","Forestières","Automne"):10,
    ("Asie","Urbaines","Hiver"):3, ("Asie","Urbaines","Printemps"):14, ("Asie","Urbaines","Été"):23, ("Asie","Urbaines","Automne"):12,

    # Amérique
    ("Amérique","Forestières","Hiver"):0, ("Amérique","Forestières","Printemps"):10, ("Amérique","Forestières","Été"):20, ("Amérique","Forestières","Automne"):9,
    ("Amérique","Clairière","Hiver"):-2, ("Amérique","Clairière","Printemps"):12, ("Amérique","Clairière","Été"):24, ("Amérique","Clairière","Automne"):10,
    ("Amérique","Deserts","Hiver"):10, ("Amérique","Deserts","Printemps"):25, ("Amérique","Deserts","Été"):35, ("Amérique","Deserts","Automne"):20,
    ("Amérique","Montagneuses","Hiver"):-5, ("Amérique","Montagneuses","Printemps"):5, ("Amérique","Montagneuses","Été"):12, ("Amérique","Montagneuses","Automne"):3,
    ("Amérique","Enneigées","Hiver"):-15, ("Amérique","Enneigées","Printemps"):-2, ("Amérique","Enneigées","Été"):8, ("Amérique","Enneigées","Automne"):-5,
    ("Amérique","Tropicales","Hiver"):25, ("Amérique","Tropicales","Printemps"):27, ("Amérique","Tropicales","Été"):28, ("Amérique","Tropicales","Automne"):26,
    ("Amérique","Mangroves","Hiver"):26, ("Amérique","Mangroves","Printemps"):27, ("Amérique","Mangroves","Été"):27, ("Amérique","Mangroves","Automne"):26,
    ("Amérique","Urbaines","Hiver"):1, ("Amérique","Urbaines","Printemps"):12, ("Amérique","Urbaines","Été"):22, ("Amérique","Urbaines","Automne"):11,

    # Europe
    ("Europe","Forestières","Hiver"):2, ("Europe","Forestières","Printemps"):13, ("Europe","Forestières","Été"):19, ("Europe","Forestières","Automne"):9,
    ("Europe","Montagneuses","Hiver"):-4, ("Europe","Montagneuses","Printemps"):5, ("Europe","Montagneuses","Été"):12, ("Europe","Montagneuses","Automne"):3,
    ("Europe","Enneigées","Hiver"):-10, ("Europe","Enneigées","Printemps"):1, ("Europe","Enneigées","Été"):10, ("Europe","Enneigées","Automne"):0,
    ("Europe","Prairies","Hiver"):1, ("Europe","Prairies","Printemps"):12, ("Europe","Prairies","Été"):22, ("Europe","Prairies","Automne"):10,
    ("Europe","Urbaines","Hiver"):3, ("Europe","Urbaines","Printemps"):14, ("Europe","Urbaines","Été"):23, ("Europe","Urbaines","Automne"):11,

    # Océanie
    ("Océanie","Insulaires","Hiver"):18, ("Océanie","Insulaires","Printemps"):22, ("Océanie","Insulaires","Été"):26, ("Océanie","Insulaires","Automne"):22,
    ("Océanie","Savanes","Hiver"):22, ("Océanie","Savanes","Printemps"):26, ("Océanie","Savanes","Été"):30, ("Océanie","Savanes","Automne"):24,
    ("Océanie","Tropicales","Hiver"):26, ("Océanie","Tropicales","Printemps"):27, ("Océanie","Tropicales","Été"):28, ("Océanie","Tropicales","Automne"):27,
    ("Océanie","Deserts","Hiver"):18, ("Océanie","Deserts","Printemps"):28, ("Océanie","Deserts","Été"):36, ("Océanie","Deserts","Automne"):24,
    ("Océanie","Montagneuses","Hiver"):5, ("Océanie","Montagneuses","Printemps"):10, ("Océanie","Montagneuses","Été"):16, ("Océanie","Montagneuses","Automne"):8,
    ("Océanie","Urbaines","Hiver"):19, ("Océanie","Urbaines","Printemps"):23, ("Océanie","Urbaines","Été"):27, ("Océanie","Urbaines","Automne"):23,
}

# map emoji -> description courte
_EMOJI_DESC = {
    "☀️":"Ciel dégagé, chaleur marquée",
    "🌤️":"Soleil dominant, quelques nuages",
    "⛅":"Partiellement nuageux",
    "🌥️":"Nuages épais majoritaires",
    "🌦️":"Éclaircies et averses",
    "🌧️":"Averses fréquentes",
    "🌨️":"Neige",
    "🌩️":"Orage sec",
    "⛈️":"Orage et pluie",
    "🌪️":"Vents violents, tornades possibles",
    "🌫️":"Brouillard épais",
    "💨":"Vent fort",
}

# choix de l'emoji selon biome/saison/temp (1 seul)
def _pick_emoji(continent: str, biome_name: str, season: str, temp_c: float) -> str:
    b = biome_name.lower()
    # froid franc
    if temp_c <= 0 or ("enneig" in b and season in ("Hiver","Automne")) or ("montagne" in b and season == "Hiver"):
        return "🌨️"
    # désert / intérieur sec
    if "désert" in b or "desert" in b:
        return "☀️" if season in ("Printemps","Été") else "💨"
    # tropical / mangrove / mousson
    if "tropic" in b or "mangrove" in b:
        return "⛈️" if season in ("Printemps","Été") else "🌧️"
    # marécage
    if "maréc" in b or "marec" in b:
        return "🌦️"
    # urbain
    if "urbain" in b or "urbaines" in b:
        return "🌥️" if season in ("Hiver","Automne") else "⛅"
    # montagne
    if "montagne" in b:
        return "🌥️" if season != "Hiver" else "🌨️"
    # forêts / prairies / clairières / insulaires / savanes
    if "forêt" in b or "foresti" in b or "prairie" in b or "clairière" in b or "insulaire" in b or "savane" in b:
        if season == "Été":
            return "☀️" if temp_c >= 24 else "⛅"
        if season == "Printemps":
            return "🌤️"
        if season == "Automne":
            return "🌥️"
        return "🌫️" if temp_c <= 3 else "⛅"
    # défaut
    return "⛅"

# lissage autour des bornes (8↔9, 15↔16, 23↔24, 31↔1)
def _next_season(season: str) -> str:
    order = ["Hiver","Printemps","Été","Automne"]
    return order[(order.index(season)+1) % 4]

def _blend_factor(day: int) -> float:
    # Renvoie alpha (0..1) vers saison suivante selon jour de mois
    if day in (8, 31):
        return 0.2
    if day in (9, 1):
        return 0.8
    if day == 15:
        return 0.2
    if day == 16:
        return 0.8
    if day == 23:
        return 0.2
    if day == 24:
        return 0.8
    return 0.0

def _abbr(biome_display: str) -> str:
    # "🌳 Zones Forestières" -> "Forestières"
    return biome_display.split(" ", 1)[1].replace("Zones ","").strip()

def _local_dt_for(continent: str) -> _dt:
    h, m = _CONT_OFFSETS[continent]
    return _dt.utcnow() + _td(hours=h, minutes=m)

def _build_embed(continent: str):
    import discord as _discord
    local = _local_dt_for(continent)  # aware
    day = local.day
    season = _season_from_day(day)
    alpha = _blend_factor(day)
    season_next = _next_season(season)

    title = f"{'🦁' if continent=='Afrique' else '🐼' if continent=='Asie' else '🐿️' if continent=='Amérique' else '🐺' if continent=='Europe' else '🐹'} {continent} — Météo régionale"
    emb = _discord.Embed(title=title, color=_discord.Color.blue())

    # ----- Footer en heure de Paris -----
    # Dernière vérif = maintenant Paris
    now_paris = to_paris(utc_now())

    # Prochaine météo = prochain minuit "local" du continent → converti en Paris
    # local_midnight = 00:00 du jour local approx
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_local_midnight = local_midnight + timedelta(days=1)
    # repasse en UTC en retirant l’offset continent
    h, m = _CONT_OFFSETS[continent]
    next_midnight_utc = (next_local_midnight - timedelta(hours=h, minutes=m)).replace(tzinfo=timezone.utc)
    # affiche en Paris
    next_midnight_paris = to_paris(next_midnight_utc)

    emb.set_footer(
        text=f"Dernière vérif : <t:{unix(now_paris)}:f> • Prochaine météo : <t:{unix(next_midnight_paris)}:R> • Saison : {season}"
    )

    fields = []
    # ... (le reste de ta construction des champs ne change pas)
    for biome_disp in _BIOMES[continent]:
        short = _abbr(biome_disp)
        base_cur = _N1.get((continent, short, season))
        base_next = _N1.get((continent, short, season_next))
        if base_cur is None or base_next is None:
            continue

        t = (1 - alpha) * base_cur + alpha * base_next
        t += _wxrand.randint(-2, 2)
        t_rounded = int(round(t))

        emoji = _pick_emoji(continent, short, season, t_rounded)
        desc = _EMOJI_DESC.get(emoji, "")
        fields.append((short, t_rounded, emoji))

        value = f"🌡️ **{t_rounded} °C**\nMétéo : {emoji}\n*({desc})*"
        emb.add_field(name=biome_disp, value=value, inline=True)

    return emb, local, fields

# ---- persistance des messages et du dernier jour local ----
_WX_STATE_FILE = "meteo_daily_state.json"
def _load_state():
    try:
        with open(_WX_STATE_FILE, "r", encoding="utf-8") as f:
            return _wxjson.load(f)
    except Exception:
        return {"messages":{}, "last_date":{}}  # last_date[continent] = "YYYYMMDD"

def _save_state(st):
    try:
        with open(_WX_STATE_FILE, "w", encoding="utf-8") as f:
            _wxjson.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ save meteo state:", e)

_wx_state = _load_state()

async def _ensure_meteo_messages():
    # crée/rafraîchit 1 message par continent dans CHANNEL_METEO
    ch = _client.get_channel(_CH_METEO) or await _client.fetch_channel(_CH_METEO)
    for cont in _BIOMES.keys():
        emb = _build_embed(cont)
        msg_id = _wx_state["messages"].get(cont)
        if msg_id:
            try:
                msg = await ch.fetch_message(msg_id)
                await msg.edit(embed=emb)
            except Exception:
                new_msg = await ch.send(embed=emb)
                _wx_state["messages"][cont] = new_msg.id
        else:
            new_msg = await ch.send(embed=emb)
            _wx_state["messages"][cont] = new_msg.id

        local = _local_dt_for(cont)
        _wx_state["last_date"][cont] = local.strftime("%Y%m%d")

    _save_state(_wx_state)
    print("✅ Météo: messages par continent prêts/rafraîchis.")

async def _update_meteo_if_needed():
    ch = _client.get_channel(_CH_METEO) or await _client.fetch_channel(_CH_METEO)
    for cont in _BIOMES.keys():
        local = _local_dt_for(cont)
        code = local.strftime("%Y%m%d")
        if _wx_state["last_date"].get(cont) != code:
            # nouveau jour local → regénérer l'embed et éditer
            emb = _build_embed(cont)
            msg_id = _wx_state["messages"].get(cont)
            if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    await msg.edit(embed=emb)
                except Exception:
                    new_msg = await ch.send(embed=emb)
                    _wx_state["messages"][cont] = new_msg.id
            else:
                new_msg = await ch.send(embed=emb)
                _wx_state["messages"][cont] = new_msg.id
            _wx_state["last_date"][cont] = code
            _save_state(_wx_state)
            print(f"🔄 Météo mise à jour ({cont}) pour la date locale {code}.")

async def _meteo_daily_scheduler():
    await _client.wait_until_ready()
    # sécurité : channel obligatoire
    if not _CH_METEO:
        print("❌ CHANNEL_METEO n'est pas défini.")
        return
    try:
        await _ensure_meteo_messages()
    except Exception as e:
        print(f"⚠️ init météo: {e}")
    while not _client.is_closed():
        try:
            await _update_meteo_if_needed()
        except Exception as e:
            print("⚠️ boucle météo:", e)
        # on vérifie toutes les 10 minutes; ça suffit pour capter minuit local
        await asyncio.sleep(600)

# ==================== FIN MODULE METEO ====================

@client.event
async def on_ready():
    print(f"✅ Connecté comme {client.user} (ID: {client.user.id})")

    # --- LOG DÉMARRAGE ---
    log_ch = client.get_channel(CHANNEL_LOG)
    if log_ch is None:
        try:
            log_ch = await client.fetch_channel(CHANNEL_LOG)
        except Exception as e:
            print(f"⚠️ Log: impossible de récupérer CHANNEL_LOG ({CHANNEL_LOG}) : {e}")
            log_ch = None
    if log_ch:
        try:
            await log_ch.send("✅ Bot opérationnel (connexion réussie).")
        except Exception as e:
            print(f"⚠️ Log: envoi impossible : {e}")

    # --- INIT SAISONS (crée/rafraîchit 1 msg par continent) ---
    try:
        await ensure_continent_messages()   # vient de ton module saisons
    except NameError:
        print("ℹ️ ensure_continent_messages() non défini (module saisons absent).")
    except Exception as e:
        print(f"⚠️ init saisons: {e}")

    # --- LANCER LA BOUCLE SAISONS ---
    try:
        client.loop.create_task(season_scheduler_loop())
    except NameError:
        print("ℹ️ season_scheduler_loop() non défini (module saisons absent).")
    except Exception as e:
        print(f"⚠️ start scheduler saisons: {e}")

    # --- INIT METEO (crée/rafraîchit 1 msg MÉTÉO par continent) ---
    try:
        await _ensure_meteo_messages()      # vient du module météo quotidien
    except NameError:
        print("ℹ️ _ensure_meteo_messages() non défini (module météo absent).")
    except Exception as e:
        print(f"⚠️ init météo: {e}")

    # --- LANCER LA BOUCLE METEO QUOTIDIENNE ---
    try:
        client.loop.create_task(_meteo_daily_scheduler())
    except NameError:
        print("ℹ️ _meteo_daily_scheduler() non défini (module météo absent).")
    except Exception as e:
        print(f"⚠️ start scheduler météo: {e}")


# DÉMARRAGE DU BOT  ⬇️  (indispensable)
if __name__ == "__main__":
    client.run(TOKEN)
