# autoquest.py — Discord Auto Quest (open source) by @th.ragg on Discord
# discord.gg/7FCbjDMKUH

import asyncio
import base64
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput, Select, View, Button
from curl_cffi import requests

# ─── config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
NOTIFY_ROLE_ID = int(os.environ.get("NOTIFY_ROLE_ID", "0"))

# Identidade — configure via env se quiser
BRAND = os.environ.get("BRAND", "Auto Quest")
INVITE = os.environ.get("SUPPORT_INVITE", "")
BASE_DIR = os.path.join(os.getcwd(), "data")
os.makedirs(BASE_DIR, exist_ok=True)

DATA_FILE = os.path.join(BASE_DIR, "accounts.json")
NOTIFY_CFG = os.path.join(BASE_DIR, "notify.json")
SENT_IDS = os.path.join(BASE_DIR, "sent_ids.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
PANEL_META = os.path.join(BASE_DIR, "panel.json")

COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_DANGER = 0xED4245
COLOR_MUTED = 0x99AAB5

NOTIFY_INTERVAL_MIN = int(os.environ.get("NOTIFY_INTERVAL_MIN", "15"))
# Canais do tutorial (opcional). Ex: export TUTORIAL_TERMS_CHANNEL=123
TUTORIAL_TERMS_CHANNEL = int(os.environ.get("TUTORIAL_TERMS_CHANNEL", "0"))
TUTORIAL_TOKEN_CHANNEL = int(os.environ.get("TUTORIAL_TOKEN_CHANNEL", "0"))

# ─── json ─────────────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def footer_text():
    if INVITE:
        return f"{BRAND} · {INVITE}"
    return BRAND

def progress_bar(pct, width=12):
    pct = max(0, min(100, int(pct)))
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)

def orb_amount(reward):
    m = re.search(r"(\d+)\s*Orbs?", reward or "", re.I)
    return int(m.group(1)) if m else 0

def dur_fmt(secs):
    secs = int(secs or 0)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m"

# ─── accounts ─────────────────────────────────────────────────────────────────
class AccountManager:
    def __init__(self):
        self.accounts = {}
        self._load()

    def _load(self):
        raw = load_json(DATA_FILE, {})
        self.accounts = {int(k): v for k, v in raw.items()}

    def _save(self):
        save_json(DATA_FILE, {str(k): v for k, v in self.accounts.items()})

    def add(self, user_id, token, username):
        self.accounts[int(user_id)] = {
            "token": token,
            "username": username,
            "is_logged": True,
        }
        self._save()

    def remove(self, user_id):
        self.accounts.pop(int(user_id), None)
        self._save()

    def logged(self, user_id):
        a = self.accounts.get(int(user_id), {})
        return bool(a.get("is_logged") and a.get("token"))

    def token(self, user_id):
        return self.accounts.get(int(user_id), {}).get("token", "") or ""

    def username(self, user_id):
        return self.accounts.get(int(user_id), {}).get("username", "?") or "?"

    def count(self):
        return sum(1 for a in self.accounts.values() if a.get("is_logged") and a.get("token"))

am = AccountManager()

# ─── persist ──────────────────────────────────────────────────────────────────
notify_state = load_json(NOTIFY_CFG, {})
sent_quest_ids = set(load_json(SENT_IDS, []))
panel_meta = load_json(PANEL_META, {})
user_settings = {int(k): v for k, v in load_json(SETTINGS_FILE, {}).items()}

def save_notify_state():
    save_json(NOTIFY_CFG, notify_state)

def save_sent_ids():
    save_json(SENT_IDS, list(sent_quest_ids)[-200:])

def save_panel_meta():
    save_json(PANEL_META, {k: v for k, v in panel_meta.items() if not str(k).startswith("_")})

def save_settings():
    save_json(SETTINGS_FILE, {str(k): v for k, v in user_settings.items()})

def get_settings(user_id):
    uid = int(user_id)
    if uid not in user_settings:
        user_settings[uid] = {"mode": "sequential"}
    return user_settings[uid]

def set_setting(user_id, key, value):
    s = get_settings(user_id)
    s[key] = value
    save_settings()

# ─── quest api ────────────────────────────────────────────────────────────────
TASK_LABELS = {
    "WATCH_VIDEO": "Assistir vídeo",
    "WATCH_VIDEO_ON_MOBILE": "Assistir vídeo (mobile)",
    "PLAY_ON_DESKTOP": "Jogar no desktop",
    "PLAY_ON_XBOX": "Jogar no Xbox",
    "PLAY_ON_PLAYSTATION": "Jogar no PlayStation",
    "PLAY_ACTIVITY": "Jogar atividade",
    "STREAM_ON_DESKTOP": "Transmitir no desktop",
    "ACHIEVEMENT_IN_ACTIVITY": "Conquista em atividade",
}

class QuestAPI:
    BASE = "https://discord.com/api/v9"
    _build = 539951

    @classmethod
    def _update_build(cls):
        try:
            import urllib.request as ur
            ua = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) discord/1.0.9236 Chrome/138.0.7204.251 "
                "Electron/37.6.0 Safari/537.36"
            )
            with ur.urlopen(ur.Request("https://discord.com/app", headers={"User-Agent": ua}), timeout=10) as r:
                html = r.read().decode()
            for h in re.findall(r"/assets/web[.]([a-f0-9]+)[.]js", html)[:5]:
                try:
                    with ur.urlopen(
                        ur.Request(f"https://discord.com/assets/web.{h}.js", headers={"User-Agent": ua}),
                        timeout=8,
                    ) as r2:
                        js = r2.read().decode()
                    m = re.search(r'buildNumber["\']?\s*[:=]\s*["\']?(\d+)', js)
                    if m:
                        cls._build = int(m.group(1))
                        return
                except Exception:
                    continue
        except Exception as e:
            print(f"⚠️ Build fallback {cls._build}: {e}")

    def __init__(self, token):
        self.token = token.strip().strip('"').strip("'")
        self.session = requests.Session(impersonate="chrome110")
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) discord/1.0.9236 Chrome/138.0.7204.251 "
            "Electron/37.6.0 Safari/537.36"
        )
        props = {
            "os": "Windows",
            "browser": "Discord Client",
            "release_channel": "stable",
            "client_version": "1.0.9236",
            "os_version": "10.0.19045",
            "os_arch": "x64",
            "app_arch": "x64",
            "system_locale": "en-US",
            "has_client_mods": False,
            "client_launch_id": str(uuid.uuid4()),
            "browser_user_agent": ua,
            "browser_version": "37.6.0",
            "os_sdk_version": "19045",
            "client_build_number": self._build,
            "native_build_number": 81687,
            "client_event_source": None,
            "launch_signature": str(uuid.uuid4()),
            "client_heartbeat_session_id": str(uuid.uuid4()),
            "client_app_state": "focused",
        }
        xsp = base64.b64encode(json.dumps(props, separators=(",", ":")).encode()).decode()
        self.h = {
            "Authorization": self.token,
            "User-Agent": ua,
            "X-Super-Properties": xsp,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "America/Sao_Paulo",
            "Accept": "*/*",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        }
        self.qh = {**self.h, "Referer": "https://discord.com/discovery/quests"}

    def close(self):
        try:
            if self.session is not None:
                self.session.close()
        except Exception:
            pass
        self.session = None

    def get_me(self):
        r = self.session.get(f"{self.BASE}/users/@me", headers=self.h, timeout=10)
        return r.json() if r.status_code == 200 else None

    def _collect_raw(self, data):
        raw = []
        for key in ("quests", "eligible_quests", "available_quests"):
            items = data.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                q = item.get("quest") if isinstance(item.get("quest"), dict) else item
                if q and q.get("id"):
                    raw.append(q)
        return raw

    def fetch_all_quests(self):
        r = self.session.get(f"{self.BASE}/quests/@me", headers=self.qh, timeout=15)
        if r.status_code != 200:
            print(f"❌ /quests/@me: {r.status_code} {r.text[:180]}")
            return []
        data = r.json() if r.text else {}
        if not isinstance(data, dict):
            return []
        counts = {k: len(data[k]) for k in data if isinstance(data.get(k), list)}
        print(f"   📡 /quests/@me lists={counts}")
        result = []
        seen = set()
        for q in self._collect_raw(data):
            parsed = self._parse(q)
            if not parsed or parsed["expired"] or parsed["progress"] >= 100:
                continue
            if parsed["id"] in seen:
                continue
            seen.add(parsed["id"])
            result.append(parsed)
            print(f"   📋 {parsed['name']} | {parsed['progress']}% | {parsed['task_type']}")
        return result

    def fetch_quests(self):
        return self.fetch_all_quests()

    def _parse(self, raw):
        try:
            qid = str(raw.get("id") or "")
            if not qid:
                return None
            config = raw.get("config") or {}
            msgs = config.get("messages") or {}
            us = raw.get("user_status") or {}
            name = msgs.get("quest_name") or config.get("title") or f"Quest {qid}"

            rc = config.get("rewards_config") or {}
            rewards = rc.get("rewards") or []
            reward = "Orbs"
            if rewards:
                r0 = rewards[0]
                rmsgs = r0.get("messages") or {}
                reward = rmsgs.get("name") or f"{r0.get('orb_quantity', '?')} Orbs"

            platforms = rc.get("platforms") or [0]
            platform = platforms[0] if platforms else 0

            image_url = None
            assets = config.get("assets") or {}
            cdn = "https://cdn.discordapp.com"
            for key in (
                "hero", "quest_bar_hero", "hero_image_asset_id",
                "cover_image_asset_id", "key_art_asset_id",
                "logotype_asset_id", "game_tile", "logotype",
            ):
                val = assets.get(key)
                if not val:
                    continue
                s = str(val).strip()
                if s.startswith("http"):
                    if any(s.lower().endswith(e) for e in (".mp4", ".webm", ".mov")):
                        image_url = s if "format=" in s else f"{s}?format=webp"
                    else:
                        image_url = s
                    break
                if re.fullmatch(r"\d{15,}", s):
                    image_url = f"{cdn}/quests/{qid}/{s}.jpg"
                    break
                if s.startswith("quests/"):
                    image_url = f"{cdn}/{s}"
                    break
                name_s = s.lstrip("/")
                if not re.search(r"\.(png|jpe?g|webp)$", name_s, re.I):
                    image_url = f"{cdn}/quests/{qid}/{name_s}.jpg"
                else:
                    image_url = f"{cdn}/quests/{qid}/{name_s}"
                break
            if not image_url:
                for val in assets.values():
                    if isinstance(val, (str, int)) and re.fullmatch(r"\d{15,}", str(val)):
                        image_url = f"{cdn}/quests/{qid}/{val}.jpg"
                        break

            tv2 = config.get("task_config_v2") or config.get("taskConfigV2") or {}
            tv1 = config.get("task_config") or config.get("taskConfig") or {}
            tmap = (tv2.get("tasks") or tv1.get("tasks") or {})
            prio = [
                "WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE",
                "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY",
                "PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION",
                "ACHIEVEMENT_IN_ACTIVITY",
            ]
            task_type = next((t for t in prio if t in tmap), next(iter(tmap), "WATCH_VIDEO") if tmap else "WATCH_VIDEO")
            raw_max = (tmap.get(task_type) or {}).get("target") or 1
            try:
                raw_max = int(raw_max)
            except Exception:
                raw_max = 1
            max_val = round(raw_max / 60) * 60 if raw_max > 30 else raw_max

            prog_map = us.get("progress") or {}
            cur_val = max(
                (v.get("value", 0) for v in prog_map.values() if isinstance(v, dict)),
                default=0,
            )
            if cur_val == 0:
                cur_val = us.get("stream_progress_seconds") or 0
            is_completed = bool(us.get("completed_at"))
            pct = 100 if is_completed else (min(100, int((cur_val / max_val) * 100)) if max_val else 0)

            exp_str = config.get("expires_at") or ""
            expired = False
            if exp_str:
                try:
                    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    expired = exp_str < agora
                except Exception:
                    pass

            def fmt_date(iso):
                if not iso:
                    return "—"
                try:
                    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                    return dt.strftime("%d/%m/%Y")
                except Exception:
                    return "—"

            app = config.get("application") or {}
            app_id = str(app.get("id") or "")
            app_name = app.get("name") or name

            return {
                "id": qid,
                "name": name,
                "reward": reward,
                "platform": platform,
                "task_type": task_type,
                "max": max_val,
                "cur": cur_val,
                "progress": pct,
                "app_id": app_id,
                "app_name": app_name,
                "expired": expired,
                "enrolled_at": us.get("enrolled_at"),
                "image_url": image_url,
                "date_start": fmt_date(config.get("starts_at") or ""),
                "date_end": fmt_date(config.get("expires_at") or ""),
            }
        except Exception as e:
            print(f"   ⚠️ _parse: {e}")
            return None

    def enroll(self, quest_id):
        body = {
            "location": 3,
            "is_targeted": False,
            "metadata_sealed": None,
            "traffic_metadata_raw": None,
            "traffic_metadata_sealed": None,
        }
        h = {**self.h, "Content-Type": "application/json", "AndroidRequest": "false"}
        r = self.session.post(f"{self.BASE}/quests/{quest_id}/enroll", headers=h, json=body, timeout=10)
        print(f"   📋 Enroll {quest_id}: {r.status_code}")
        if r.status_code in (200, 204):
            try:
                return (True, quest_id, r.json().get("enrolled_at"))
            except Exception:
                return (True, quest_id, None)
        return (False, quest_id, None)

    def get_progress(self, quest_id):
        try:
            r = self.session.get(f"{self.BASE}/quests/@me", headers=self.qh, timeout=10)
            if r.status_code != 200:
                return (0, 1, 0, None)
            for q in (r.json().get("quests") or []):
                if str(q.get("id")) != quest_id:
                    continue
                us = q.get("user_status") or {}
                cfg = q.get("config") or {}
                tv2 = cfg.get("task_config_v2") or cfg.get("task_config") or {}
                tmap = tv2.get("tasks") or {}
                prio = [
                    "WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE", "ACHIEVEMENT_IN_ACTIVITY",
                    "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY",
                    "PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION",
                ]
                task = next((t for t in prio if t in tmap), next(iter(tmap), ""))
                raw_max = (tmap.get(task) or {}).get("target") or 1
                max_v = round(raw_max / 60) * 60 if raw_max > 30 else raw_max
                prog = us.get("progress") or {}
                cur_v = max((v.get("value", 0) for v in prog.values() if isinstance(v, dict)), default=0)
                if cur_v == 0:
                    cur_v = us.get("stream_progress_seconds") or 0
                completed = us.get("completed_at")
                pct = 100 if completed else (min(100, int((cur_v / max_v) * 100)) if max_v else 0)
                return (cur_v, max_v, pct, completed)
        except Exception as e:
            print(f"   ⚠️ get_progress: {e}")
        return (0, 1, 0, None)

    def video_progress(self, quest_id, timestamp):
        h = {**self.h, "Content-Type": "application/json"}
        r = self.session.post(
            f"{self.BASE}/quests/{quest_id}/video-progress",
            headers=h,
            json={"timestamp": timestamp},
            timeout=10,
        )
        print(f"   🎬 video-progress {timestamp:.1f}s: {r.status_code}")
        try:
            return r.json()
        except Exception:
            return {}

    def heartbeat(self, quest_id, app_id=None, terminal=False, stream_key=None):
        body = (
            {"stream_key": stream_key, "terminal": terminal}
            if stream_key
            else {"application_id": app_id, "terminal": terminal}
        )
        h = {**self.h, "Content-Type": "application/json"}
        r = self.session.post(
            f"{self.BASE}/quests/{quest_id}/heartbeat",
            headers=h,
            json=body,
            timeout=10,
        )
        print(f"   💓 heartbeat terminal={terminal}: {r.status_code}")
        try:
            return r.json()
        except Exception:
            return {}

    async def do_quest(self, quest, on_progress=None):
        qid = quest["id"]
        task_type = quest["task_type"]
        max_val = quest["max"]
        app_id = quest["app_id"]
        enrolled_at = quest.get("enrolled_at")

        ok, real_id, ea = await asyncio.to_thread(self.enroll, qid)
        if not ok:
            return False
        qid = real_id
        if ea:
            enrolled_at = ea

        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            max_future, speed, interval = 10, 7, 7
            ea_ts = time.time()
            if enrolled_at:
                try:
                    ea_ts = datetime.fromisoformat(enrolled_at.replace("Z", "+00:00")).timestamp()
                except Exception:
                    pass
            cur, _, pct, completed = await asyncio.to_thread(self.get_progress, qid)
            seconds_done = cur
            done = completed is not None
            while not done and seconds_done < max_val:
                elapsed = time.time() - ea_ts
                if int(elapsed) + max_future - seconds_done >= speed:
                    ts = min(max_val, seconds_done + speed + 0.1)
                    res = await asyncio.to_thread(self.video_progress, qid, ts)
                    done = res.get("completed_at") is not None
                    seconds_done = min(max_val, seconds_done + speed)
                    pct = min(100, int((seconds_done / max_val) * 100))
                    if on_progress:
                        await on_progress(pct, seconds_done, max_val)
                if seconds_done >= max_val:
                    break
                await asyncio.sleep(interval)
            if not done:
                await asyncio.to_thread(self.video_progress, qid, float(max_val))

        elif task_type in ("PLAY_ON_DESKTOP", "PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION", "STREAM_ON_DESKTOP"):
            cur, _, pct, completed = await asyncio.to_thread(self.get_progress, qid)
            while pct < 100 and not completed:
                await asyncio.to_thread(self.heartbeat, qid, app_id, False)
                cur, max_v, pct, completed = await asyncio.to_thread(self.get_progress, qid)
                if on_progress:
                    await on_progress(pct, cur, max_v)
                if pct >= 100 or completed:
                    break
                await asyncio.sleep(20)
            await asyncio.to_thread(self.heartbeat, qid, app_id, True)

        elif task_type in ("PLAY_ACTIVITY", "ACHIEVEMENT_IN_ACTIVITY"):
            cur, _, pct, completed = await asyncio.to_thread(self.get_progress, qid)
            while pct < 100 and not completed:
                await asyncio.to_thread(self.heartbeat, qid, None, False, "call:1:1")
                cur, max_v, pct, completed = await asyncio.to_thread(self.get_progress, qid)
                if on_progress:
                    await on_progress(pct, cur, max_v)
                if pct >= 100 or completed:
                    break
                await asyncio.sleep(20)
            await asyncio.to_thread(self.heartbeat, qid, None, True, "call:1:1")
        else:
            print(f"   ⚠️ Tipo não suportado: {task_type}")
            return False

        await asyncio.sleep(3)
        cur, max_v, pct, completed = await asyncio.to_thread(self.get_progress, qid)
        print(f"   Progresso final: {pct}% ({cur}/{max_v})")
        if on_progress:
            await on_progress(pct, cur, max_v)
        return pct >= 100 or completed is not None

# ─── bot ──────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, max_messages=40)

# ─── layouts ──────────────────────────────────────────────────────────────────
class PanelLayoutView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        accounts = am.count()
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"# 🛠️ {BRAND}"),
            discord.ui.TextDisplay("Automatize suas missões do Discord e complete-as facilmente."),
            discord.ui.Separator(visible=True),
            discord.ui.TextDisplay(
                "Não consegue fazer missões para ganhar alguns Orbs e conseguir "
                "**Nitro** ou **decorações** por preguiça ou limitação?\n"
                "Acabe com esse problema com o **Auto Quest!**"
            ),
            discord.ui.Separator(visible=True),
            discord.ui.TextDisplay("✨ **Funcionalidades**"),
            discord.ui.TextDisplay(
                "Você pode:\n\n"
                "⚡ Completar suas **missões rapidamente**\n"
                "📬 Progresso direto na **DM**\n"
                "🎯 Escolher **quais missões** executar\n"
                "🎁 Rodar **uma ou várias** na fila"
            ),
            discord.ui.Separator(visible=True),
            discord.ui.TextDisplay(f"👤 Contas conectadas: **{accounts}**"),
            discord.ui.Separator(visible=True),
            discord.ui.ActionRow(
                discord.ui.Button(label="🔒 Login", style=discord.ButtonStyle.primary, custom_id="panel_login"),
                discord.ui.Button(label="🎯 Quests", style=discord.ButtonStyle.success, custom_id="panel_quests"),
                discord.ui.Button(label="📖 Tutorial", style=discord.ButtonStyle.secondary, custom_id="panel_tutorial"),
                discord.ui.Button(label="⚙️ Config", style=discord.ButtonStyle.secondary, custom_id="panel_config"),
                discord.ui.Button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="panel_logout"),
            ),
            discord.ui.Separator(visible=False),
            discord.ui.TextDisplay(f"-# {BRAND}"),
            accent_color=COLOR_PRIMARY,
        )
        self.add_item(container)

def make_progress_layout(name, pct, reward, image_url, done, show_image=True):
    color = COLOR_SUCCESS if done else COLOR_PRIMARY
    emoji = "✅" if done else "🔁"
    show_pct = 100 if done else max(0, min(100, int(pct)))
    bar = progress_bar(show_pct)
    badge = "CONCLUÍDA" if done else "EM ANDAMENTO"

    class ProgressLayout(discord.ui.LayoutView):
        pass

    layout = ProgressLayout()
    components = [
        discord.ui.TextDisplay(f"# {emoji} {name}"),
        discord.ui.Separator(visible=True),
    ]
    if show_image and image_url:
        components.append(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))
        components.append(discord.ui.Separator(visible=True))
    components.append(discord.ui.TextDisplay(
        f"**Status** · `{badge}`\n"
        f"`{bar}` **{show_pct}%**\n\n"
        f"🎁 **Recompensa**\n{reward}"
    ))
    layout.add_item(discord.ui.Container(*components, accent_color=color))
    return layout

def make_quest_notify_layout(q):
    name = q["name"]
    reward = q["reward"]
    image_url = q.get("image_url")
    app_name = q.get("app_name", name)
    task_type = q.get("task_type", "WATCH_VIDEO")
    max_val = q.get("max", 0)
    quest_id = q["id"]
    task_label = TASK_LABELS.get(task_type, task_type)
    task_text = f"• {task_label}"
    if max_val > 0:
        task_text += f" ({dur_fmt(max_val)})"

    class QuestNotifyLayout(discord.ui.LayoutView):
        pass

    layout = QuestNotifyLayout()
    components = [discord.ui.TextDisplay(f"# {name}")]
    if image_url:
        components.append(discord.ui.Separator(visible=True))
        components.append(discord.ui.MediaGallery(discord.MediaGalleryItem(image_url)))
    components.append(discord.ui.Separator(visible=True))
    components.append(discord.ui.TextDisplay("**Informações da Quest**"))
    components.append(discord.ui.TextDisplay(
        f"**Jogo:** {name}\n**Aplicativo:** {app_name}"
    ))
    components.append(discord.ui.Separator(visible=True))
    components.append(discord.ui.TextDisplay("**Tarefas**"))
    components.append(discord.ui.TextDisplay(
        f"O usuário deve completar qualquer uma das tarefas abaixo:\n{task_text}"
    ))
    components.append(discord.ui.Separator(visible=True))
    components.append(discord.ui.TextDisplay("**Recompensas**"))
    components.append(discord.ui.TextDisplay(f"• {reward}"))
    components.append(discord.ui.Separator(visible=True))
    components.append(discord.ui.TextDisplay(f"ID da Quest: `{quest_id}`"))
    layout.add_item(discord.ui.Container(*components, accent_color=COLOR_PRIMARY))

    quest_url = f"https://discord.com/quests/{quest_id}"
    layout.add_item(discord.ui.ActionRow(
        discord.ui.Button(
            label="Ver Missão",
            url=quest_url,
            style=discord.ButtonStyle.link,
            emoji="🎮",
        )
    ))
    return layout

# ─── login / select / execute ─────────────────────────────────────────────────
class LoginModal(Modal, title="Login Discord"):
    token_input = TextInput(
        label="Token da sua conta Discord",
        placeholder="Cole seu token aqui",
        style=discord.TextStyle.long,
    )

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        token = self.token_input.value.strip()
        api = QuestAPI(token)
        try:
            me = await asyncio.to_thread(api.get_me)
        finally:
            api.close()
        if not me:
            await interaction.followup.send("❌ Token inválido.", ephemeral=True)
            return
        username = me.get("global_name") or me.get("username") or "?"
        am.add(interaction.user.id, token, username)
        await interaction.followup.send(
            f"✅ Conta **@{username}** conectada.\nContas no bot: **{am.count()}**\nToque em **Quests**.",
            ephemeral=True,
        )
        await refresh_panel_message()

class QuestSelect(Select):
    def __init__(self, options, quests, user_id):
        self._quests = quests
        self._user_id = user_id
        super().__init__(
            placeholder="✨ Quests disponíveis...",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            custom_id="quest_select",
        )

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        selected = [self._quests[int(v)] for v in self.values]
        names = "\n".join(f"• {q['name']}" for q in selected)
        view = ExecuteView(selected, self._user_id)
        await interaction.followup.send(
            f"✅ **{len(selected)}** selecionada(s)\n{names}\n\nClique em **▶ Executar**.",
            view=view,
            ephemeral=True,
        )

class ExecuteView(View):
    def __init__(self, quests, user_id):
        super().__init__(timeout=300)
        self._quests = quests
        self._user_id = user_id

    @discord.ui.button(label="▶ Executar", style=discord.ButtonStyle.success, custom_id="exec_run")
    async def run_btn(self, interaction, button):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("❌ Não é sua sessão.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        api = QuestAPI(am.token(self._user_id))
        status_msg = await interaction.followup.send("⏳ Iniciando...", ephemeral=True)

        dm_channel = None
        try:
            dm_user = await bot.fetch_user(self._user_id)
            dm_channel = await dm_user.create_dm()
        except Exception as e:
            print(f"⚠️ DM: {e}")

        mode = get_settings(self._user_id).get("mode", "sequential")
        total_n = len(self._quests)

        async def run_one(quest):
            name = quest["name"]
            reward = quest["reward"]
            image_url = quest.get("image_url")
            print(f"\n▶ {name} [{quest['task_type']}]")
            dm_msg = None
            if dm_channel:
                try:
                    dm_msg = await dm_channel.send(
                        view=make_progress_layout(name, 0, reward, image_url, False)
                    )
                except Exception as e:
                    print(f"⚠️ DM inicial: {e}")

            last_pct = [-1]

            async def on_prog(pct, cur, max_v, _name=name, _img=image_url, _rew=reward):
                if abs(pct - last_pct[0]) < 5 and pct < 100:
                    return
                last_pct[0] = pct
                try:
                    await status_msg.edit(content=f"▶ **{_name}** · `{pct}%`")
                except Exception:
                    pass
                if dm_msg:
                    try:
                        await dm_msg.edit(
                            view=make_progress_layout(_name, pct, _rew, _img, pct >= 100)
                        )
                    except Exception:
                        pass

            done = False
            last_err = None
            for attempt in range(3):
                try:
                    if attempt > 0:
                        print(f"   ↻ retry {attempt}/2: {name}")
                        if dm_channel:
                            try:
                                await dm_channel.send(f"⚠️ **{name}** — tentativa {attempt}/2…")
                            except Exception:
                                pass
                        await asyncio.sleep(3)
                    done = await api.do_quest(quest, on_prog)
                    if done:
                        break
                    last_err = "progresso incompleto"
                except Exception as e:
                    last_err = str(e)
                    print(f"   ❌ {e}")

            if done:
                if dm_msg:
                    try:
                        await dm_msg.edit(
                            view=make_progress_layout(name, 100, reward, image_url, True)
                        )
                    except Exception:
                        pass
                if dm_channel:
                    try:
                        await dm_channel.send(
                            f"<@{self._user_id}>\n"
                            f"✅ **{name}** concluída com sucesso! "
                            f"Recompensa pronta para resgate."
                        )
                    except Exception:
                        pass
            elif dm_channel:
                try:
                    await dm_channel.send(
                        f"⚠️ **{name}** — não concluída"
                        + (f" (`{last_err}`)" if last_err else "")
                    )
                except Exception:
                    pass
            return done

        if mode == "parallel" and total_n > 1:
            sem = asyncio.Semaphore(2)

            async def guarded(q):
                async with sem:
                    return await run_one(q)

            await asyncio.gather(*[guarded(q) for q in self._quests])
        else:
            for quest in self._quests:
                await run_one(quest)

        api.close()
        try:
            await status_msg.edit(
                content=f"🏁 {len(self._quests)} quest(s) processadas. Veja a DM."
            )
        except Exception:
            pass

# ─── panel refresh ────────────────────────────────────────────────────────────
async def refresh_panel_message():
    ch_id = panel_meta.get("channel_id")
    msg_id = panel_meta.get("message_id")
    if not ch_id or not msg_id:
        return
    try:
        channel = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
        msg = await channel.fetch_message(msg_id)
        await msg.edit(view=PanelLayoutView())
    except discord.NotFound:
        panel_meta.clear()
        save_panel_meta()
    except Exception as e:
        print(f"⚠️ refresh panel: {e}")

# ─── notify loop ──────────────────────────────────────────────────────────────
@tasks.loop(minutes=NOTIFY_INTERVAL_MIN)
async def quest_notify_loop():
    if not notify_state.get("token") or not notify_state.get("channel_id"):
        return
    channel = bot.get_channel(notify_state["channel_id"])
    if not channel:
        try:
            channel = await bot.fetch_channel(notify_state["channel_id"])
        except Exception:
            return

    api = QuestAPI(notify_state["token"])
    try:
        quests = await asyncio.wait_for(asyncio.to_thread(api.fetch_all_quests), timeout=60)
    except Exception as e:
        print(f"⚠️ notify_loop: {e}")
        api.close()
        return
    api.close()

    role_mention = None
    if NOTIFY_ROLE_ID:
        guild = getattr(channel, "guild", None)
        role = guild.get_role(NOTIFY_ROLE_ID) if guild else None
        if role is not None:
            role_mention = role.mention
            print(f"📢 Ping cargo: @{role.name} ({role.id})")
        else:
            role_mention = f"<@&{NOTIFY_ROLE_ID}>"
            print(f"⚠️ Cargo {NOTIFY_ROLE_ID} não encontrado no servidor.")

    novas = [q for q in quests if q["id"] not in sent_quest_ids]
    for q in novas:
        try:
            if role_mention:
                await channel.send(
                    content=role_mention,
                    allowed_mentions=discord.AllowedMentions(
                        roles=[discord.Object(id=NOTIFY_ROLE_ID)]
                    ),
                )
            await channel.send(view=make_quest_notify_layout(q))
            sent_quest_ids.add(q["id"])
            print(f"📢 Quest: {q['name']}")
        except Exception as e:
            print(f"⚠️ notify send: {e}")
    if novas:
        save_sent_ids()

# ─── setup notify ─────────────────────────────────────────────────────────────
class SetupNotifyModal(Modal, title="Token — Quest Notify"):
    token_input = TextInput(
        label="Token da conta",
        placeholder="Token para checar quests novas",
        style=discord.TextStyle.long,
    )

    def __init__(self, channel):
        super().__init__()
        self._channel = channel

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        token = self.token_input.value.strip()
        api = QuestAPI(token)
        try:
            me = await asyncio.to_thread(api.get_me)
        finally:
            api.close()
        if not me:
            await interaction.followup.send("❌ Token inválido.", ephemeral=True)
            return
        username = me.get("global_name") or me.get("username") or "?"
        notify_state["token"] = token
        notify_state["channel_id"] = self._channel.id
        save_notify_state()
        if quest_notify_loop.is_running():
            quest_notify_loop.restart()
        else:
            quest_notify_loop.start()
        await interaction.followup.send(
            f"✅ Notify: **@{username}** · canal {self._channel.mention} · a cada {NOTIFY_INTERVAL_MIN} min",
            ephemeral=True,
        )

class SetupNotifyView(View):
    def __init__(self, channel):
        super().__init__(timeout=300)
        self._channel = channel

    @discord.ui.button(label="🔑 Inserir Token", style=discord.ButtonStyle.primary, custom_id="setup_qn_btn")
    async def setup_btn(self, interaction, button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Só o dono.", ephemeral=True)
            return
        await interaction.response.send_modal(SetupNotifyModal(self._channel))

# ─── commands ─────────────────────────────────────────────────────────────────
@bot.command(name="painel")
async def cmd_painel(ctx, tipo: str = None):
    if ctx.author.id != OWNER_ID:
        await ctx.send("❌ Só o dono.", delete_after=5)
        return
    if tipo != "orbs":
        await ctx.send("Use: `!painel orbs`", delete_after=5)
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass
    msg = await ctx.send(view=PanelLayoutView())
    panel_meta["channel_id"] = msg.channel.id
    panel_meta["message_id"] = msg.id
    save_panel_meta()

@bot.command(name="setup")
async def cmd_setup(ctx, sub: str = None):
    if ctx.author.id != OWNER_ID:
        await ctx.send("❌ Só o dono.", delete_after=5)
        return
    if sub != "qn":
        await ctx.send("Use: `!setup qn`", delete_after=5)
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass
    try:
        await ctx.author.send(view=SetupNotifyView(ctx.channel))
        await ctx.send("📩 Setup na DM.", delete_after=8)
    except Exception:
        await ctx.send("Abra a DM com o bot e tente de novo.", delete_after=10)

@bot.group(name="quest", invoke_without_command=True)
async def cmd_quest(ctx):
    if ctx.author.id != OWNER_ID:
        return
    await ctx.send("Use: `!quest notify`", delete_after=5)

@cmd_quest.command(name="notify")
async def cmd_quest_notify(ctx):
    if ctx.author.id != OWNER_ID:
        return
    if not notify_state.get("token"):
        await ctx.send("Configure com `!setup qn`.", delete_after=10)
        return
    notify_state["channel_id"] = ctx.channel.id
    save_notify_state()
    if quest_notify_loop.is_running():
        quest_notify_loop.restart()
    else:
        quest_notify_loop.start()
    await ctx.send(f"✅ Notify ativo em {ctx.channel.mention} (a cada {NOTIFY_INTERVAL_MIN} min).")

# ─── interactions ─────────────────────────────────────────────────────────────
@bot.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component:
        return
    cid = (interaction.data or {}).get("custom_id", "")
    uid = interaction.user.id

    if cid == "panel_login":
        await interaction.response.send_modal(LoginModal())
        return

    if cid == "panel_tutorial":
        lines = [
            "📖 **Tutorial**",
            "",
            "1. Leia os termos / avisos do servidor (se houver).",
            "2. Pegue o token da conta com segurança (nunca compartilhe).",
            "3. **Login** → **Quests** → selecione → **Executar**.",
            "4. O progresso chega na **DM**.",
        ]
        if TUTORIAL_TERMS_CHANNEL:
            lines.insert(2, f"⚠️ Termos: <#{TUTORIAL_TERMS_CHANNEL}>")
        if TUTORIAL_TOKEN_CHANNEL:
            lines.insert(3 if TUTORIAL_TERMS_CHANNEL else 2, f"🪙 Token: <#{TUTORIAL_TOKEN_CHANNEL}>")
        await interaction.response.send_message(
            content="\n".join(lines),
            ephemeral=True,
        )
        return

    if cid == "panel_logout":
        if not am.logged(uid):
            await interaction.response.send_message("❌ Conta não conectada.", ephemeral=True)
            return
        name = am.username(uid)
        am.remove(uid)
        await interaction.response.send_message(
            f"✅ **@{name}** desconectada. Contas: **{am.count()}**",
            ephemeral=True,
        )
        await refresh_panel_message()
        return

    if cid == "panel_config":
        if not am.logged(uid):
            await interaction.response.send_message("❌ Faça login primeiro.", ephemeral=True)
            return
        mode = get_settings(uid).get("mode", "sequential")
        mode_lbl = "1 por vez" if mode == "sequential" else "Múltiplas (até 2)"
        view = View(timeout=120)
        view.add_item(discord.ui.Button(label="1 por vez", style=discord.ButtonStyle.success, custom_id="cfg_mode_seq"))
        view.add_item(discord.ui.Button(label="Múltiplas", style=discord.ButtonStyle.primary, custom_id="cfg_mode_par"))
        await interaction.response.send_message(
            f"⚙️ **Config**\n**Execução:** `{mode_lbl}`",
            view=view,
            ephemeral=True,
        )
        return

    if cid == "cfg_mode_seq":
        set_setting(uid, "mode", "sequential")
        await interaction.response.edit_message(content="⚙️ Execução: **1 por vez**", view=None)
        return

    if cid == "cfg_mode_par":
        set_setting(uid, "mode", "parallel")
        await interaction.response.edit_message(content="⚙️ Execução: **Múltiplas (até 2)**", view=None)
        return

    if cid == "panel_quests":
        if not am.logged(uid):
            await interaction.response.send_message("❌ Faça login primeiro.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        api = QuestAPI(am.token(uid))
        status = await interaction.followup.send("🔄 Buscando missões...", ephemeral=True)
        try:
            quests = await asyncio.wait_for(asyncio.to_thread(api.fetch_quests), timeout=60)
        except Exception as e:
            await status.edit(content=f"❌ Erro: {e}")
            api.close()
            return
        api.close()

        disponiveis = [q for q in quests if not q["expired"] and q["progress"] < 100]
        if not disponiveis:
            await status.edit(content="🎉 Nenhuma quest disponível no momento.")
            return

        disponiveis.sort(key=lambda q: orb_amount(q.get("reward", "")), reverse=True)
        opts = []
        seen = set()
        for i, q in enumerate(disponiveis):
            key = q["name"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            opts.append(discord.SelectOption(
                label=q["name"][:100],
                value=str(i),
                description=f"🎁 {q['reward']} · ⏳ {dur_fmt(q['max'])}"[:100],
                emoji="✨",
            ))
            if len(opts) >= 25:
                break

        if not opts:
            await status.edit(content="Nenhuma quest ativa encontrada.")
            return

        sel_view = View(timeout=300)
        sel_view.add_item(QuestSelect(opts, disponiveis, uid))
        await status.edit(
            content=f"✨ **{len(opts)}** quest(s) — selecione e confirme:",
            view=sel_view,
        )
        return

@bot.event
async def on_ready():
    print(f"✅ {bot.user} · contas={am.count()}")
    await asyncio.to_thread(QuestAPI._update_build)
    if notify_state.get("token") and notify_state.get("channel_id"):
        if not quest_notify_loop.is_running():
            quest_notify_loop.start()
            print("📡 Notify loop on")

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Defina BOT_TOKEN")
        raise SystemExit(1)
    bot.run(BOT_TOKEN)
