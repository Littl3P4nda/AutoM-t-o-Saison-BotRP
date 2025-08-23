# bot.py
# ──────────────────────────────────────────────────────────────────────────────
# Bot Discord – Saisons & Météo par continent (5 + 5 embeds)
# – Affiche compte à rebours et “il y a … min” (FR)
# – Météo mise à jour chaque jour à minuit local, saisons aux seuils 1/9/16/24
# – Rafraîchit les timers FR toutes les 5 minutes (sans recalcul inutile)
# – Anti rate-limit (hash + pauses)
# ──────────────────────────────────────────────────────────────────────────────

import os, sys, json, asyncio, hashlib, random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional
import discord

# ───────────────── CONFIG ─────────────────
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not TOKEN:
    print("❌ DISCORD_TOKEN manquant (Railway > Variables).")
    sys.exit(1)

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except:
        return default

CHANNEL_SAISON = _env_int("CHANNEL_SAISON", 1408264960714211348)  # ← renseigne l’ID si pas via env
CHANNEL_METEO  = _env_int("CHANNEL_METEO",  1407922710855684166)
CHANNEL_LOG    = _env_int("CHANNEL_LOG",    1408261120925634610)

# Offsets “moyens” par rapport à l’UTC (h, m) pour la logique locale
CONTINENT_OFFSETS = {
    "Afrique":  (+1, 30),
    "Amérique": (-6, -30),
    "Asie":     (+7,  0),
    "Europe":   (+2,  0),
    "Océanie":  (+4, 15),
}

# Emojis saisons
SEASON_EMOJI = {"Hiver":"❄️","Printemps":"🌱","Été":"☀️","Automne":"🍂"}

# Paris pour l’affichage des timers (unifié côté joueurs)
PARIS_TZ = ZoneInfo("Europe/Paris")

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def to_paris(dt: datetime) -> datetime:
    return dt.astimezone(PARIS_TZ)

def apply_offset_utc(dt_utc: datetime, h: int, m: int) -> datetime:
    return dt_utc + timedelta(hours=h, minutes=m)

def unix(dt: datetime) -> int:
    return int(dt.timestamp())

def mins_between(a: datetime, b: datetime) -> int:
    return max(1, int(round(abs((b - a).total_seconds()) / 60.0)))

def fmt_rel_fr(now_dt: datetime, target_dt: datetime, future=True) -> str:
    m = mins_between(now_dt, target_dt)
    return f"dans {m} min" if future else f"il y a {m} min"

def season_from_day(day: int) -> str:
    # 1–8 Hiver ; 9–15 Printemps ; 16–23 Été ; 24–31 Automne
    if 1 <= day <= 8:   return "Hiver"
    if 9 <= day <= 15:  return "Printemps"
    if 16 <= day <= 23: return "Été"
    return "Automne"

def next_season(season: str) -> str:
    order = ["Hiver","Printemps","Été","Automne"]
    return order[(order.index(season)+1)%4]

def next_season_boundary_local(local_dt: datetime) -> datetime:
    """Renvoie 00:00 local du prochain jour-seuil (9, 16, 24, 1)."""
    base = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    d = local_dt.day
    if d <= 8:   return base.replace(day=9)
    if d <= 15:  return base.replace(day=16)
    if d <= 23:  return base.replace(day=24)
    # 1er du mois suivant
    year  = base.year + (1 if base.month == 12 else 0)
    month = 1 if base.month == 12 else base.month + 1
    return base.replace(year=year, month=month, day=1)

# ──────────────── Discord client ────────────────
intents = discord.Intents.default()
client  = discord.Client(intents=intents)

# ──────────────────────── SAISONS ────────────────────────

SEASON_STATE_FILE = "season_state.json"  # {messages:{continent:id}, last_sig:{continent:hash}}

def season_state_load():
    try:
        with open(SEASON_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"messages":{}, "last_sig":{}}

def season_state_save(st):
    try:
        with open(SEASON_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ save season:", e)

season_state = season_state_load()

def season_embed(continent: str, now_utc: datetime) -> discord.Embed:
    """Construit l’embed Saison pour un continent, avec timers FR (Paris)."""
    h_off, m_off = CONTINENT_OFFSETS[continent]
    local_dt     = apply_offset_utc(now_utc, h_off, m_off)
    season       = season_from_day(local_dt.day)

    title = f"{continent} — Saison actuelle"
    desc  = f"{SEASON_EMOJI[season]} **{season}**\n"
    desc += f"_Date locale de référence :_ **{local_dt.strftime('%d %b %Y')}**"

    # Prochaine saison = prochain seuil local → converti pour l’affichage Paris
    next_local   = next_season_boundary_local(local_dt)
    next_utc     = (next_local - timedelta(hours=h_off, minutes=m_off)).replace(tzinfo=timezone.utc)
    now_paris    = to_paris(now_utc)
    next_paris   = to_paris(next_utc)

    # Lignes FR (rafraîchies à chaque tick)
    # “Dernière actualisation” = l’instant du tick (on affiche “il y a … min” depuis now_paris)
    derniere  = fmt_rel_fr(now_paris, now_paris, future=False)
    prochaine = fmt_rel_fr(now_paris, next_paris, future=True)

    desc += (
        f"\n\n**Horaires (Europe/Paris)**\n"
        f"• Prochaine Saison : {fmt_rel_fr(now_paris, next_paris, future=True)}\n"
        f"• Dernière Actualisation : {derniere}\n"
        f"• Prochaine Actualisation : {fmt_rel_fr(now_paris, now_paris + timedelta(minutes=5), future=True)}"
    )

    emb = discord.Embed(title=title, description=desc, color=discord.Color.orange())
    emb.timestamp = now_paris
    emb.set_footer(text="Heure affichée : Europe/Paris")
    return emb, season, local_dt

def season_signature(cont: str, season: str, local_dt: datetime) -> str:
    payload = f"{cont}|{season}|{local_dt.strftime('%Y-%m-%d')}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

async def _get_text_channel(chan_id: int, label: str):
    if not chan_id:
        print(f"[DIAG] {label}: ID manquant (0).")
        return None
    ch = client.get_channel(chan_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(chan_id)
        except discord.Forbidden:
            print(f"[DIAG] {label}: Forbidden (pas la permission de voir le salon {chan_id}).")
            return None
        except discord.NotFound:
            print(f"[DIAG] {label}: NotFound (ID {chan_id} introuvable).")
            return None
        except Exception as e:
            print(f"[DIAG] {label}: fetch_channel({chan_id}) a échoué: {e}")
            return None
    # Vérifie type texte
    if not isinstance(ch, discord.TextChannel):
        print(f"[DIAG] {label}: type non supporté ({type(ch)}). Donne un salon TEXTE.")
        return None
    print(f"[DIAG] {label}: OK → {ch} (guild={getattr(ch.guild,'name','?')})")
    return ch

async def seasons_ensure_messages():
    ch = await _get_text_channel(CHANNEL_SAISON, "SAISON")
    if ch is None:
        return

    now = utc_now()
      for cont in CONTINENT_OFFSETS.keys():
        try:
            emb, season, local_dt = season_embed(cont, now)
            sig = season_signature(cont, season, local_dt)

            msg_id = season_state["messages"].get(cont)
            last   = season_state["last_sig"].get(cont)
        if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    # on édite à chaque tick (pour rafraîchir les timers FR)
                    await msg.edit(embed=emb)
                    if last != sig:
                        print(f"[SAISON] {cont}: contenu changé → signature maj.")
                        season_state["last_sig"][cont] = sig
                    else:
                        print(f"[SAISON] {cont}: timers rafraîchis (pas de changement de saison).")
                except discord.NotFound:
                    print(f"[SAISON] {cont}: ancien message introuvable → recréation.")
                    new = await ch.send(embed=emb)
                    season_state["messages"][cont] = new.id
                    season_state["last_sig"][cont]  = sig
                except discord.Forbidden:
                    print(f"[SAISON] {cont}: Forbidden (pas la permission d’éditer/écrire dans #{ch}).")
                    return
         else:
                new = await ch.send(embed=emb)
                season_state["messages"][cont] = new.id
                season_state["last_sig"][cont]  = sig
                print(f"[SAISON] {cont}: message créé (id={new.id}).")

     season_state_save(season_state)
            await asyncio.sleep(1)  # anti-rafale
        except Exception as e:
            print(f"[SAISON] {cont}: erreur → {e}")

async def seasons_tick():
    """Toutes les 5 min : rafraîchit les 5 embeds (timers) et met à jour si la saison change."""
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await seasons_ensure_messages()
        except Exception as e:
            print("⚠️ seasons_tick:", e)
        await asyncio.sleep(300)  # 5 min

# ──────────────────────── METEO ────────────────────────

# Biomes par continent (noms d’affichage)
BIOMES = {
    "Afrique":  ["🌾 Zones Savanes", "🌵 Zones Deserts", "🦜 Zones Tropicales", "🌱 Zones Marécageuses", "🏙️ Zones Urbaines"],
    "Amérique": ["🌳 Zones Forestières", "🌾 Zones Clairière", "🌵 Zones Deserts", "⛰️ Zones Montagneuses", "❄️ Zones Enneigées", "🦜 Zones Tropicales", "🌱 Zones Mangroves", "🏙️ Zones Urbaines"],
    "Asie":     ["🦜 Zones Tropicales", "🌾 Zones Prairies", "⛰️ Zones Montagneuses", "❄️ Zones Enneigées", "🌳 Zones Forestières", "🏙️ Zones Urbaines"],
    "Europe":   ["🌳 Zones Forestières", "⛰️ Zones Montagneuses", "❄️ Zones Enneigées", "🌾 Zones Prairies", "🏙️ Zones Urbaines"],
    "Océanie":  ["🌴 Zones Insulaires", "🌾 Zones Savanes", "🦜 Zones Tropicales", "🌵 Zones Deserts", "⛰️ Zones Montagneuses", "🏙️ Zones Urbaines"],
}

def short_key(display: str) -> str:
    # "🌳 Zones Forestières" -> "Forestières"
    return display.split(" ", 1)[1].replace("Zones ","").strip()

# Références N-1 (°C moyennes) – à ajuster si tu veux
N1 = {
    # Afrique
    ("Afrique","Savanes","Hiver"):24, ("Afrique","Savanes","Printemps"):26, ("Afrique","Savanes","Été"):27, ("Afrique","Savanes","Automne"):25,
    ("Afrique","Deserts","Hiver"):20, ("Afrique","Deserts","Printemps"):30, ("Afrique","Deserts","Été"):38, ("Afrique","Deserts","Automne"):28,
    ("Afrique","Tropicales","Hiver"):27, ("Afrique","Tropicales","Printemps"):28, ("Afrique","Tropicales","Été"):28, ("Afrique","Tropicales","Automne"):27,
    ("Afrique","Marécageuses","Hiver"):25, ("Afrique","Marécageuses","Printemps"):26, ("Afrique","Marécageuses","Été"):26, ("Afrique","Marécageuses","Automne"):25,
    ("Afrique","Urbaines","Hiver"):26, ("Afrique","Urbaines","Printemps"):28, ("Afrique","Urbaines","Été"):29, ("Afrique","Urbaines","Automne"):27,
    # Amérique
    ("Amérique","Forestières","Hiver"):0, ("Amérique","Forestières","Printemps"):10, ("Amérique","Forestières","Été"):20, ("Amérique","Forestières","Automne"):9,
    ("Amérique","Clairière","Hiver"):-2, ("Amérique","Clairière","Printemps"):12, ("Amérique","Clairière","Été"):24, ("Amérique","Clairière","Automne"):10,
    ("Amérique","Deserts","Hiver"):10, ("Amérique","Deserts","Printemps"):25, ("Amérique","Deserts","Été"):35, ("Amérique","Deserts","Automne"):20,
    ("Amérique","Montagneuses","Hiver"):-5, ("Amérique","Montagneuses","Printemps"):5, ("Amérique","Montagneuses","Été"):12, ("Amérique","Montagneuses","Automne"):3,
    ("Amérique","Enneigées","Hiver"):-15, ("Amérique","Enneigées","Printemps"):-2, ("Amérique","Enneigées","Été"):8, ("Amérique","Enneigées","Automne"):-5,
    ("Amérique","Tropicales","Hiver"):25, ("Amérique","Tropicales","Printemps"):27, ("Amérique","Tropicales","Été"):28, ("Amérique","Tropicales","Automne"):26,
    ("Amérique","Mangroves","Hiver"):26, ("Amérique","Mangroves","Printemps"):27, ("Amérique","Mangroves","Été"):27, ("Amérique","Mangroves","Automne"):26,
    ("Amérique","Urbaines","Hiver"):1, ("Amérique","Urbaines","Printemps"):12, ("Amérique","Urbaines","Été"):22, ("Amérique","Urbaines","Automne"):11,
    # Asie
    ("Asie","Tropicales","Hiver"):26, ("Asie","Tropicales","Printemps"):28, ("Asie","Tropicales","Été"):29, ("Asie","Tropicales","Automne"):27,
    ("Asie","Prairies","Hiver"):5, ("Asie","Prairies","Printemps"):15, ("Asie","Prairies","Été"):24, ("Asie","Prairies","Automne"):14,
    ("Asie","Montagneuses","Hiver"):-2, ("Asie","Montagneuses","Printemps"):6, ("Asie","Montagneuses","Été"):12, ("Asie","Montagneuses","Automne"):4,
    ("Asie","Enneigées","Hiver"):-10, ("Asie","Enneigées","Printemps"):0, ("Asie","Enneigées","Été"):8, ("Asie","Enneigées","Automne"):-2,
    ("Asie","Forestières","Hiver"):2, ("Asie","Forestières","Printemps"):12, ("Asie","Forestières","Été"):20, ("Asie","Forestières","Automne"):10,
    ("Asie","Urbaines","Hiver"):3, ("Asie","Urbaines","Printemps"):14, ("Asie","Urbaines","Été"):23, ("Asie","Urbaines","Automne"):12,
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

EMOJI_DESC = {
    "☀️":"Ciel dégagé, chaleur marquée",
    "🌤️":"Soleil dominant, quelques nuages",
    "⛅":"Partiellement nuageux",
    "🌥️":"Nuages épais majoritaires",
    "🌦️":"Éclaircies et averses",
    "🌧️":"Averses fréquentes",
    "🌨️":"Neige",
    "🌩️":"Orage sec",
    "⛈️":"Orage avec averse",
    "🌪️":"Vents très violents, tornades possibles",
    "🌫️":"Brouillard épais",
    "💨":"Vent fort",
}

def pick_emoji(continent: str, biome_short: str, season: str, temp_c: int) -> str:
    b = biome_short.lower()
    if temp_c <= 0 or ("enneig" in b and season in ("Hiver","Automne")) or ("montagne" in b and season == "Hiver"):
        return "🌨️"
    if "désert" in b or "desert" in b:
        return "☀️" if season in ("Printemps","Été") else "💨"
    if "tropic" in b or "mangrove" in b:
        return "⛈️" if season in ("Printemps","Été") else "🌧️"
    if "maréc" in b or "marec" in b:
        return "🌦️"
    if "urbain" in b:
        return "🌥️" if season in ("Hiver","Automne") else "⛅"
    if "montagne" in b:
        return "🌥️" if season != "Hiver" else "🌨️"
    if any(x in b for x in ("forêt","forest","prairie","clairière","insulaire","savane")):
        if season == "Été":       return "☀️" if temp_c >= 24 else "⛅"
        if season == "Printemps": return "🌤️"
        if season == "Automne":   return "🌥️"
        return "🌫️" if temp_c <= 3 else "⛅"
    return "⛅"

def blend_factor(day: int) -> float:
    # glissement doux autour des bornes
    if day in (8, 15, 23):  return 0.2
    if day in (9, 16, 24):  return 0.8
    return 0.0

WEATHER_STATE_FILE = "meteo_daily_state.json"  # {messages:{continent:id}, last_sig:{}, last_date:{}}

def weather_state_load():
    try:
        with open(WEATHER_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"messages":{}, "last_sig":{}, "last_date":{}}

def weather_state_save(st):
    try:
        with open(WEATHER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ save meteo:", e)

weather_state = weather_state_load()

def continent_local_now(cont: str, now_utc: datetime) -> datetime:
    h, m = CONTINENT_OFFSETS[cont]
    return apply_offset_utc(now_utc, h, m)

def meteo_embed(continent: str, now_utc: datetime):
    """Construit l’embed météo + signature; temporise en Paris."""
    local = continent_local_now(continent, now_utc)
    season = season_from_day(local.day)
    alpha  = blend_factor(local.day)
    season_next = next_season(season)

    # Titre + description (timers FR en bas)
    icon = {"Afrique":"🦁","Amérique":"🐿️","Asie":"🐼","Europe":"🐺","Océanie":"🐹"}[continent]
    title = f"{icon} {continent} — Météo régionale"
    desc  = ""

    fields_for_sig = []
    emb = discord.Embed(title=title, description=desc, color=discord.Color.blue())

    for biome_disp in BIOMES[continent]:
        short = short_key(biome_disp)
        base_cur  = N1.get((continent, short, season))
        base_next = N1.get((continent, short, season_next))
        if base_cur is None or base_next is None:
            continue
        t = (1 - alpha) * base_cur + alpha * base_next
        t += random.randint(-2, 2)  # variabilité jour
        t = int(round(t))

        emoji = pick_emoji(continent, short, season, t)
        legend = EMOJI_DESC.get(emoji, "")
        value = f"🌡️ **{t} °C**\nMétéo : {emoji}\n*({legend})*"

        emb.add_field(name=biome_disp, value=value, inline=True)
        fields_for_sig.append((short, t, emoji))

    # Timers / Horaires FR (Paris)
    now_paris = to_paris(now_utc)
    # Prochaine météo = prochain minuit local → converti pour affichage Paris
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    next_local_midnight = local_midnight + timedelta(days=1)
    h_off, m_off = CONTINENT_OFFSETS[continent]
    next_midnight_utc = (next_local_midnight - timedelta(hours=h_off, minutes=m_off)).replace(tzinfo=timezone.utc)
    next_midnight_paris = to_paris(next_midnight_utc)

    emb.description = (emb.description or "") + (
        f"\n\n**Horaires (Europe/Paris)**\n"
        f"• Prochaine Météo : {fmt_rel_fr(now_paris, next_midnight_paris, future=True)}\n"
        f"• Dernière Actualisation : {fmt_rel_fr(now_paris, now_paris, future=False)}\n"
        f"• Prochaine Actualisation : {fmt_rel_fr(now_paris, now_paris + timedelta(minutes=5), future=True)}"
    )
    emb.timestamp = now_paris
    emb.set_footer(text=f"Heure affichée : Europe/Paris • Saison : {season}")

    # signature pour éviter edits inutiles (valeurs du jour)
    flat = "|".join(f"{n}:{t}:{e}" for (n,t,e) in fields_for_sig)
    sig  = hashlib.sha256(f"{continent}|{local.strftime('%Y-%m-%d')}|{flat}".encode("utf-8")).hexdigest()
    return emb, sig, local

async def weather_ensure_messages():
    ch = await _get_text_channel(CHANNEL_METEO, "METEO")
    if ch is None:
        return

     now = utc_now()
    for cont in BIOMES.keys():
        try:
            emb, sig, local = meteo_embed(cont, now)

            msg_id = weather_state["messages"].get(cont)
            last   = weather_state["last_sig"].get(cont)

        # (re)création / édition (on édite même si sig identique pour rafraîchir les timers)
        if msg_id:
                try:
                    msg = await ch.fetch_message(msg_id)
                    # on édite à chaque tick (pour rafraîchir les timers FR)
                    await msg.edit(embed=emb)
                    if last != sig:
                        print(f"[METEO] {cont}: nouvelles valeurs journalières (signature changée).")
                        weather_state["last_sig"][cont] = sig
                        weather_state["last_date"][cont] = local.strftime("%Y%m%d")
                    else:
                        print(f"[METEO] {cont}: timers rafraîchis (même journée).")
                except discord.NotFound:
                    print(f"[METEO] {cont}: ancien message introuvable → recréation.")
                    new = await ch.send(embed=emb)
                    weather_state["messages"][cont] = new.id
                    weather_state["last_sig"][cont]  = sig
                    weather_state["last_date"][cont] = local.strftime("%Y%m%d")
                except discord.Forbidden:
                    print(f"[METEO] {cont}: Forbidden (pas la permission d’éditer/écrire dans #{ch}).")
                    return
        else:
                new = await ch.send(embed=emb)
                weather_state["messages"][cont] = new.id
                weather_state["last_sig"][cont]  = sig
                weather_state["last_date"][cont] = local.strftime("%Y%m%d")
                print(f"[METEO] {cont}: message créé (id={new.id}).")
        weather_state["last_sig"][cont]  = sig
        weather_state["last_date"][cont] = local.strftime("%Y%m%d")

    weather_state_save(weather_state)
            await asyncio.sleep(1)  # anti-rafale
        except Exception as e:
            print(f"[METEO] {cont}: erreur → {e}")

async def weather_tick():
    """Toutes les 5 min : rafraîchit timers. À minuit local: nouvelles valeurs journalières."""
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            now = utc_now()
            # 1) si nouveau jour local → régénère entièrement les 5 embeds (valeurs/émojis)
            need_full = []
            for cont in BIOMES.keys():
                local = continent_local_now(cont, now)
                code  = local.strftime("%Y%m%d")
                if weather_state["last_date"].get(cont) != code:
                    need_full.append(cont)
            if need_full:
                await weather_ensure_messages()
            else:
                # 2) sinon, rafraîchir uniquement les timers (mais on réédite l’embed complet pour simplicité)
                await weather_ensure_messages()
        except Exception as e:
            print("⚠️ weather_tick:", e)
        await asyncio.sleep(300)  # 5 min

# ──────────────────────── on_ready & lancement ────────────────────────

@client.event
async def on_ready():
    print(f"✅ Connecté comme {client.user} (ID: {client.user.id})")
    print(f"[DIAG] CHANNEL_SAISON={CHANNEL_SAISON}, CHANNEL_METEO={CHANNEL_METEO}, CHANNEL_LOG={CHANNEL_LOG}")


    # --- DIAGNOSTIC DES CANAUX ---
    print(f"[DIAG] CHANNEL_SAISON={CHANNEL_SAISON}, CHANNEL_METEO={CHANNEL_METEO}, CHANNEL_LOG={CHANNEL_LOG}")

    async def _chk(chan_id: int, label: str):
        if not chan_id:
            print(f"[DIAG] {label}: ID manquant (0).")
            return None
        ch = client.get_channel(chan_id)
        if ch is None:
            try:
                ch = await client.fetch_channel(chan_id)
            except Exception as e:
                print(f"[DIAG] {label}: fetch_channel({chan_id}) a échoué: {e}")
                return None
        print(f"[DIAG] {label}: OK → {ch} (type={type(ch)})")
        try:
            await ch.send(f"🔎 Ping {label} depuis bot {client.user} (test diag).")
            print(f"[DIAG] {label}: message test envoyé.")
        except Exception as e:
            print(f"[DIAG] {label}: échec envoi message test: {e}")
        return ch

    await _chk(CHANNEL_LOG,   "LOG")
    await _chk(CHANNEL_SAISON,"SAISON")
    await _chk(CHANNEL_METEO, "METEO")

    # message de log
    if CHANNEL_LOG:
        try:
            logch = client.get_channel(CHANNEL_LOG) or await client.fetch_channel(CHANNEL_LOG)
            await logch.send("✅ Bot opérationnel (saisons + météo).")
        except Exception as e:
            print(f"⚠️ log: {e}")

    # init (crée/rafraîchit tout)
    try:
        await seasons_ensure_messages()
    except Exception as e:
        print(f"⚠️ init saisons: {e}")

    try:
        await weather_ensure_messages()
    except Exception as e:
        print(f"⚠️ init météo: {e}")

    # boucles périodiques (5 min)
    client.loop.create_task(seasons_tick())
    client.loop.create_task(weather_tick())

# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        client.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Token Discord invalide. Régénère-le et mets-le dans DISCORD_TOKEN.")
        sys.exit(1)
