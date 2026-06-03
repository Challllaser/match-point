from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urlparse
from http.cookies import SimpleCookie
from http import HTTPStatus
from email.message import Message
import hashlib
import html
import io
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", ROOT)
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "tournaments.db"))
SESSIONS = {}

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
seed_db = os.path.join(ROOT, "tournaments.db")
if DB_PATH != seed_db and not os.path.exists(DB_PATH) and os.path.exists(seed_db):
    shutil.copy2(seed_db, DB_PATH)

GAME_MAPS = {
    "CS2": ["Ancient", "Anubis", "Dust2", "Inferno", "Mirage", "Nuke", "Overpass", "Train", "Vertigo"],
    "VAL": ["Ascent", "Bind", "Breeze", "Haven", "Icebox", "Lotus", "Pearl", "Split", "Sunset"],
    "DOTA": ["Classic Draft", "Captains Mode", "Random Draft"],
    "LOL": ["Summoner's Rift", "Howling Abyss"],
    "WARZONE": ["Urzikstan", "Rebirth Island", "Fortune's Keep", "Vondel"],
    "FORTNITE": ["Battle Royale Island", "Reload", "Zero Build", "Creative Arena"],
    "PUBG": ["Erangel", "Miramar", "Sanhok", "Vikendi", "Taego", "Deston", "Rondo"],
    "APEX": ["World's Edge", "Kings Canyon", "Olympus", "Storm Point", "Broken Moon"],
}

RULE_PRESETS = {
    "CS2": [
        "CS2 5x5 MR12, overtime MR3, server veto, запрещены сторонние читы и баги.",
        "CS2 Wingman 2x2, BO1 до 13 раундов, стороны выбираются ножевым раундом.",
        "CS2 BO3: команды по очереди банят и выбирают карты, decider остается последним.",
    ],
    "VAL": [
        "Valorant 5x5 BO1, стандартные правила Riot, overtime до преимущества в 2 раунда.",
        "Valorant BO3: бан/пик карт капитанами, агенты выбираются по правилам текущего патча.",
    ],
    "DOTA": [
        "Dota 2 Captains Mode BO1, лобби создается организатором, паузы до 5 минут.",
        "Dota 2 BO3, сервер Europe, стандартный Captains Mode и fair play.",
    ],
    "LOL": [
        "League of Legends 5x5 Draft Pick, сервер EU, паузы только по техническим причинам.",
        "League of Legends BO3/BO5: стороны и пики чемпионов по регламенту организатора.",
    ],
    "WARZONE": [
        "Warzone private lobby, подсчет очков по убийствам и месту команды.",
        "Warzone BO-серия: карта выбирается из утвержденного пула перед матчем.",
    ],
    "FORTNITE": [
        "Fortnite Battle Royale, private lobby, запрещены баги карты и сторонний софт.",
        "Fortnite Zero Build: очки за место и eliminations по регламенту турнира.",
    ],
    "PUBG": [
        "PUBG squad lobby, очки за placement и kills, карты из утвержденного пула.",
        "PUBG BO-серия: администратор создает лобби, рестарт только при массовой технической ошибке.",
    ],
    "APEX": [
        "Apex Legends private match, очки за placement и kills, карты из утвержденного пула.",
        "Apex Legends BO-серия: капитаны veto выбирают карты перед матчем.",
    ],
}

DEFAULT_RULES = [
    "Участники обязаны быть вовремя. Опоздание более 10 минут считается техническим поражением.",
    "Запрещены читы, эксплойты и передача аккаунта. Спорные ситуации решает администратор.",
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(12)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def check_password(password, stored):
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    return hash_password(password, salt).split("$", 1)[1] == digest


def is_hashed_secret(value):
    if not value or "$" not in value:
        return False
    salt, digest = value.split("$", 1)
    return len(salt) == 24 and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower())


def verify_team_secret(entered, stored):
    if not stored:
        return False
    if is_hashed_secret(stored):
        return check_password(entered, stored)
    return entered == stored


def display_name(user):
    if not user:
        return ""
    return (user["nickname"] or user["login"]) if "nickname" in user.keys() else user["login"]


def profile_color(user):
    value = user["profile_color"] if user and "profile_color" in user.keys() else ""
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value or "") else "#00e5ff"


def hex_to_rgba(value, alpha):
    value = value.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "00e5ff"
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def is_staff(user):
    return bool(user and user["role"] in ("ADMIN", "OWNER"))


def is_owner(user):
    return bool(user and user["role"] == "OWNER")


def admin_user_actions(current_user, target_user):
    if current_user["id"] == target_user["id"]:
        action = "Нельзя удалить себя"
    else:
        action = f"<a class='danger' href='/admin/delete?type=user&id={target_user['id']}'>Удалить</a>"
    if is_owner(current_user) and target_user["role"] != "OWNER":
        next_role = "USER" if target_user["role"] == "ADMIN" else "ADMIN"
        label = "Снять админа" if target_user["role"] == "ADMIN" else "Выдать админа"
        action += f"<form method='post' action='/admin/role?id={target_user['id']}'><input type='hidden' name='role' value='{next_role}'><button class='btn tiny'>{label}</button></form>"
    return action


def ensure_column(conn, table, column, column_type):
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def seed_disciplines(conn):
    items = [
        ("Counter-Strike 2", "CS2", "Тактический шутер 5x5"),
        ("Dota 2", "DOTA", "Командная MOBA дисциплина"),
        ("Valorant", "VAL", "Тактический шутер с агентами"),
        ("League of Legends", "LOL", "MOBA 5x5 на Summoner's Rift"),
        ("Call of Duty: Warzone", "WARZONE", "Battle royale и resurgence"),
        ("Fortnite", "FORTNITE", "Battle royale, zero build и creative"),
        ("PUBG", "PUBG", "Battle royale squad discipline"),
        ("Apex Legends", "APEX", "Battle royale с легендами"),
    ]
    for name, tag, description in items:
        exists = conn.execute("SELECT id FROM disciplines WHERE tag=?", (tag,)).fetchone()
        if not exists:
            conn.execute("INSERT INTO disciplines(name, tag, description) VALUES(?,?,?)", (name, tag, description))


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT UNIQUE NOT NULL,
                full_name TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'USER',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS disciplines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                tag TEXT NOT NULL,
                description TEXT,
                map_pool TEXT,
                rule_presets TEXT
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                tag TEXT NOT NULL,
                discipline_id INTEGER,
                captain_id INTEGER NOT NULL,
                description TEXT,
                join_password TEXT,
                join_key TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (discipline_id) REFERENCES disciplines(id),
                FOREIGN KEY (captain_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS team_members (
                team_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                team_role TEXT NOT NULL DEFAULT 'Игрок',
                joined_at TEXT NOT NULL,
                PRIMARY KEY (team_id, user_id),
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                discipline_id INTEGER,
                format TEXT NOT NULL,
                max_teams INTEGER NOT NULL,
                start_date TEXT,
                description TEXT,
                rules TEXT,
                maps TEXT,
                bans TEXT,
                is_private INTEGER NOT NULL DEFAULT 0,
                private_code TEXT,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                creator_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (discipline_id) REFERENCES disciplines(id),
                FOREIGN KEY (creator_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (tournament_id, team_id),
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                media_url TEXT,
                emoji TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS global_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                media_url TEXT,
                emoji TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round INTEGER NOT NULL,
                team1_id INTEGER,
                team2_id INTEGER,
                score1 TEXT DEFAULT '-',
                score2 TEXT DEFAULT '-',
                winner_id INTEGER,
                note TEXT,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS match_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                map_scores TEXT NOT NULL,
                winner_id INTEGER,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (winner_id) REFERENCES teams(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS veto_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                match_id INTEGER,
                team_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                map_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        ensure_column(conn, "teams", "join_password", "TEXT")
        ensure_column(conn, "teams", "join_key", "TEXT")
        ensure_column(conn, "teams", "logo_url", "TEXT")
        ensure_column(conn, "disciplines", "map_pool", "TEXT")
        ensure_column(conn, "disciplines", "rule_presets", "TEXT")
        ensure_column(conn, "veto_actions", "match_id", "INTEGER")
        ensure_column(conn, "messages", "media_url", "TEXT")
        ensure_column(conn, "messages", "emoji", "TEXT")
        ensure_column(conn, "users", "nickname", "TEXT")
        ensure_column(conn, "users", "avatar_url", "TEXT")
        ensure_column(conn, "users", "banner_url", "TEXT")
        ensure_column(conn, "users", "profile_color", "TEXT")
        ensure_column(conn, "users", "about", "TEXT")
        ensure_column(conn, "users", "custom_role", "TEXT")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_user_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (profile_user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS profile_trophies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tournament_name TEXT NOT NULL,
                place INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS profile_media_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        admin = conn.execute("SELECT id FROM users WHERE login = 'admin'").fetchone()
        if not admin:
            conn.execute(
                "INSERT INTO users(login, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                ("admin", "Администратор", hash_password("admin"), "ADMIN", now()),
            )
        if conn.execute("SELECT COUNT(*) c FROM disciplines").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO disciplines(name, tag, description) VALUES(?,?,?)",
                [
                    ("Counter-Strike 2", "CS2", "Тактический шутер 5x5"),
                    ("Dota 2", "DOTA", "Командная MOBA дисциплина"),
                    ("Valorant", "VAL", "Тактический шутер с агентами"),
                ],
            )
        for login in ("challlaser", "ricka"):
            owner = conn.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
            if owner:
                conn.execute("UPDATE users SET role='OWNER' WHERE login=?", (login,))
            else:
                conn.execute(
                    "INSERT INTO users(login, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                    (login, "Владелец платформы", hash_password("12345"), "OWNER", now()),
                )
        seed_disciplines(conn)


class App(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    @property
    def path_info(self):
        return urlparse(self.path).path

    @property
    def query(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = parse_qs(raw)
        data = {k: v[0] for k, v in parsed.items()}
        for key, values in parsed.items():
            data[f"{key}[]"] = values
        return data

    def multipart_form(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self.form(), {}
        boundary_match = re.search(r"boundary=(.+)", content_type)
        if not boundary_match:
            return {}, {}
        boundary = boundary_match.group(1).strip('"').encode("utf-8")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        fields, files = {}, {}
        for part in raw.split(b"--" + boundary):
            part = part.strip(b"\r\n")
            if not part or part == b"--" or b"\r\n\r\n" not in part:
                continue
            header_blob, body = part.split(b"\r\n\r\n", 1)
            body = body.rstrip(b"\r\n")
            headers = header_blob.decode("utf-8", "ignore")
            name_match = re.search(r'name="([^"]+)"', headers)
            if not name_match:
                continue
            name = name_match.group(1)
            filename_match = re.search(r'filename="([^"]*)"', headers)
            if filename_match:
                filename = os.path.basename(filename_match.group(1))
                if filename and body:
                    files[name] = {"filename": filename, "content": body}
            else:
                fields[name] = body.decode("utf-8", "ignore")
        return fields, files

    def current_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("session")
        if not token:
            return None
        with db() as conn:
            session = conn.execute("SELECT * FROM user_sessions WHERE token = ?", (token.value,)).fetchone()
            if not session:
                return None
            conn.execute("UPDATE user_sessions SET last_seen = ? WHERE token = ?", (now(), token.value))
            return conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

    def send_html(self, body, title="Матч Поинт", status=200):
        user = self.current_user()
        page = layout(body, title, user, self.query.get("msg"))
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_fragment(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", safe_location(location))
        self.end_headers()

    def require_user(self):
        user = self.current_user()
        if not user:
            self.redirect("/login?msg=Сначала войдите в систему")
            return None
        return user

    def require_admin(self):
        user = self.require_user()
        if not user:
            return None
        if not is_staff(user):
            self.redirect("/dashboard?msg=Нужны права администратора")
            return None
        return user

    def route(self):
        path = self.path_info
        if path.startswith("/static/"):
            return self.static(path)
        routes = {
            "/": self.home,
            "/login": self.login,
            "/register": self.register,
            "/logout": self.logout,
            "/dashboard": self.dashboard,
            "/tournaments": self.tournaments,
            "/tournament/new": self.tournament_form,
            "/tournament": self.tournament_detail,
            "/tournament/edit": self.tournament_edit,
            "/tournament/register": self.tournament_register,
            "/tournament/unregister": self.tournament_unregister,
            "/tournament/message": self.tournament_message,
            "/tournament/messages": self.tournament_messages,
            "/chat/global": self.global_message,
            "/chat/global/feed": self.global_feed,
            "/message/delete": self.message_delete,
            "/match/score": self.match_score,
            "/bracket": self.bracket,
            "/veto": self.veto,
            "/teams": self.teams,
            "/team/my": self.team_my,
            "/team/new": self.team_form,
            "/team": self.team_detail,
            "/team/edit": self.team_edit,
            "/team/join": self.team_join,
            "/team/leave": self.team_leave,
            "/team/kick": self.team_kick,
            "/team/delete": self.team_delete,
            "/team/role": self.team_role,
            "/admin": self.admin,
            "/admin/disciplines": self.admin_disciplines,
            "/admin/users": self.admin_users,
            "/admin/teams": self.admin_teams,
            "/admin/tournaments": self.admin_tournaments,
            "/admin/delete": self.admin_delete,
            "/admin/status": self.admin_status,
            "/admin/role": self.admin_role,
            "/password": self.change_password,
            "/profile": self.profile,
            "/profile/edit": self.profile_edit,
            "/profile/comment": self.profile_comment,
            "/profile/comment/delete": self.profile_comment_delete,
            "/profile/role": self.profile_role,
            "/tournament/delete": self.tournament_delete,
        }
        handler = routes.get(path)
        if handler:
            return handler()
        self.send_html("<section class='panel'><h1>404</h1><p>Страница не найдена.</p></section>", "404", 404)

    def static(self, path):
        name = os.path.basename(path)
        folder = os.path.join(ROOT, "static")
        file_path = os.path.join(folder, name)
        if not os.path.exists(file_path):
            self.send_response(404)
            self.end_headers()
            return
        if name.endswith(".css"):
            content_type = "text/css"
        elif name.endswith(".gif"):
            content_type = "image/gif"
        elif name.endswith(".png"):
            content_type = "image/png"
        elif name.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
        elif name.endswith(".webp"):
            content_type = "image/webp"
        else:
            content_type = "application/javascript" if name.endswith(".js") else (mimetypes.guess_type(name)[0] or "application/octet-stream")
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(data)

    def home(self):
        discipline_filter = self.query.get("discipline_id", "")
        with db() as conn:
            params = (discipline_filter,) if discipline_filter else ()
            filter_sql = "WHERE t.discipline_id=?" if discipline_filter else ""
            tournaments = conn.execute(
                """
                SELECT t.*, d.tag discipline FROM tournaments t
                LEFT JOIN disciplines d ON d.id = t.discipline_id
                {filter_sql}
                ORDER BY t.start_date IS NULL, t.start_date, t.id DESC LIMIT 6
                """.format(filter_sql=filter_sql),
                params,
            ).fetchall()
            top_teams = rating_teams(conn, discipline_filter)
            top_players = rating_players(conn, discipline_filter)
            global_messages = conn.execute(
                "SELECT * FROM (SELECT gm.*, u.login, u.nickname, u.avatar_url FROM global_messages gm JOIN users u ON u.id=gm.user_id ORDER BY gm.id DESC LIMIT 40) ORDER BY id"
            ).fetchall()
        cards = "".join(tournament_card(t) for t in tournaments) or "<p class='muted'>Пока нет турниров. Создайте первый.</p>"
        teams_html = "".join(f"<a class='rank-row' href='/team?id={t['id']}'><b>{esc(t['name'])}</b><span>{esc(t['discipline'])} · {t['points']} очков</span></a>" for t in top_teams) or "<p class='muted'>Команд пока нет.</p>"
        players_html = "".join(f"<div class='rank-row'><b>{esc(p['login'])}</b><span>{p['points']} очков</span></div>" for p in top_players)
        user = self.current_user()
        global_chat = "".join(message_html(m, user, "global", "/") for m in global_messages) or "<p class='muted'>В общем чате пока пусто.</p>"
        chat_form = global_chat_form() if user else "<a class='btn primary' href='/login'>Войти в общий чат</a>"
        chat_panel = chat_shell("Общий чат", global_chat, chat_form, "/chat/global/feed")
        self.send_html(
            f"""
            <section class="hero">
                <div class="hero-copy">
                    <p class="eyebrow">Турнирная платформа</p>
                    <h1>Матч Поинт</h1>
                    <p>Создавайте киберспортивные турниры, собирайте команды, назначайте роли и ведите сетку в одном месте.</p>
                    <div class="actions">
                        <a class="btn primary" href="/tournaments">Найти турнир</a>
                        <a class="btn" href="/tournament/new">Создать турнир</a>
                    </div>
                </div>
                <div class="hero-board">
                    <img class="hero-logo" src="/static/match-point-logo.png" alt="Матч Поинт">
                    <strong>Готовы провести свой чемпионат?</strong>
                    <span>Регистрация команд, чат, сетка и админ-панель уже внутри.</span>
                </div>
            </section>
            <section class="panel portal-filter">
                <form method="get" action="/" class="search-line">
                    <select name="discipline_id">{'<option value="">Все дисциплины</option>' + disciplines_options(discipline_filter)}</select>
                    <button class="btn primary">Фильтр</button>
                </form>
            </section>
            <section class="grid portal-grid">
                <section class="panel"><h2>Топ команд</h2>{teams_html}</section>
                <section class="panel"><h2>Топ игроков</h2>{players_html}</section>
                {chat_panel}
            </section>
            <section class="section-head"><h2>Готовящиеся турниры</h2><a href="/tournaments">Все турниры</a></section>
            <div class="cards">{cards}</div>
            """,
            "Матч Поинт",
        )

    def login(self):
        if self.command == "POST":
            data = self.form()
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE login = ?", (data.get("login", ""),)).fetchone()
            if user and check_password(data.get("password", ""), user["password_hash"]):
                token = secrets.token_urlsafe(24)
                with db() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO user_sessions(token, user_id, created_at, last_seen) VALUES(?,?,?,?)",
                        (token, user["id"], now(), now()),
                    )
                self.send_response(303)
                self.send_header("Location", safe_location("/dashboard?msg=Успешный вход в систему"))
                self.send_header("Set-Cookie", f"session={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax")
                self.end_headers()
                return
            return self.redirect("/login?msg=Неверный логин или пароль")
        self.send_html(auth_form("Вход в систему", "/login", "Войти", include_name=False), "Вход")

    def register(self):
        if self.command == "POST":
            data = self.form()
            if data.get("password") != data.get("password2"):
                return self.redirect("/register?msg=Пароли не совпадают")
            if len(data.get("login", "")) < 3 or len(data.get("password", "")) < 4:
                return self.redirect("/register?msg=Логин от 3 символов, пароль от 4")
            try:
                with db() as conn:
                    conn.execute(
                        "INSERT INTO users(login, full_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                        (data["login"], data.get("full_name", ""), hash_password(data["password"]), "USER", now()),
                    )
            except sqlite3.IntegrityError:
                return self.redirect("/register?msg=Такой логин уже занят")
            return self.redirect("/login?msg=Аккаунт создан, войдите")
        self.send_html(auth_form("Регистрация", "/register", "Зарегистрироваться", include_name=True), "Регистрация")

    def logout(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("session")
        if token:
            SESSIONS.pop(token.value, None)
            with db() as conn:
                conn.execute("DELETE FROM user_sessions WHERE token = ?", (token.value,))
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.end_headers()

    def change_password(self):
        user = self.require_user()
        if not user:
            return
        if self.command == "POST":
            data = self.form()
            if not check_password(data.get("old_password", ""), user["password_hash"]):
                return self.redirect("/password?msg=Текущий пароль неверный")
            if len(data.get("new_password", "")) < 4 or data.get("new_password") != data.get("new_password2"):
                return self.redirect("/password?msg=Новые пароли не совпадают или слишком короткие")
            with db() as conn:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(data["new_password"]), user["id"]))
            return self.redirect("/dashboard?msg=Пароль обновлен")
        self.send_html(
            """
            <section class='auth-card'><h1>Смена пароля</h1>
            <form method='post' action='/password' class='form'>
                <label>Текущий пароль<input type='password' name='old_password' required></label>
                <label>Новый пароль<input type='password' name='new_password' required></label>
                <label>Повторите новый пароль<input type='password' name='new_password2' required></label>
                <button class='btn primary'>Сохранить пароль</button>
            </form></section>
            """,
            "Смена пароля",
        )

    def dashboard(self):
        user = self.require_user()
        if not user:
            return
        with db() as conn:
            team = conn.execute(
                """
                SELECT tm.team_role, t.*, d.tag discipline, u.login captain
                FROM team_members tm
                JOIN teams t ON t.id = tm.team_id
                LEFT JOIN disciplines d ON d.id = t.discipline_id
                JOIN users u ON u.id = t.captain_id
                WHERE tm.user_id = ?
                ORDER BY t.id DESC LIMIT 1
                """,
                (user["id"],),
            ).fetchone()
            my_tournaments = conn.execute(
                """
                SELECT t.* FROM registrations r
                JOIN team_members tm ON tm.team_id = r.team_id
                JOIN tournaments t ON t.id = r.tournament_id
                WHERE tm.user_id = ?
                ORDER BY t.id DESC
                """,
                (user["id"],),
            ).fetchall()
        team_html = team_card(team) if team else "<p class='muted'>Вы пока не состоите в команде.</p><div class='actions'><a class='btn' href='/team/new'>Создать команду</a><a class='btn' href='/teams'>Найти команду</a></div>"
        tour_html = "".join(f"<a class='list-row' href='/tournament?id={t['id']}'>{esc(t['title'])}<span>{esc(t['status'])}</span></a>" for t in my_tournaments) or "<p class='muted'>Ваша команда еще не зарегистрирована на турнир.</p><a class='btn' href='/tournaments'>Найти турнир</a>"
        admin_link = "<a class='quick' href='/admin'>Админ-панель</a>" if is_staff(user) else ""
        self.send_html(
            f"""
            <section class="welcome"><h1>Добро пожаловать, {esc(user['login'])}!</h1><p>Личный кабинет</p></section>
            <div class="grid two">
                <section class="panel"><h2>Моя команда</h2>{team_html}</section>
                <section class="panel"><h2>Мои турниры</h2>{tour_html}</section>
            </div>
            <section class="panel quicks">
                <h2>Быстрые действия</h2>
                <a class="quick" href="/tournaments">Все турниры</a>
                <a class="quick" href="/team/my">Моя команда</a>
                <a class="quick" href="/tournament/new">Создать турнир</a>
                {admin_link}
            </section>
            """,
            "Личный кабинет",
        )

    def profile(self):
        user_id = self.query.get("id")
        viewer = self.current_user()
        if not user_id and viewer:
            user_id = str(viewer["id"])
        if not user_id:
            return self.redirect("/login?msg=Сначала войдите в систему")
        with db() as conn:
            profile = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            team = conn.execute(
                """
                SELECT t.id, t.name, t.tag, tm.team_role
                FROM team_members tm JOIN teams t ON t.id=tm.team_id
                WHERE tm.user_id=? LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            comments = conn.execute(
                """
                SELECT pc.*, u.login, u.nickname, u.avatar_url
                FROM profile_comments pc JOIN users u ON u.id=pc.author_id
                WHERE pc.profile_user_id=? ORDER BY pc.id DESC LIMIT 40
                """,
                (user_id,),
            ).fetchall()
            trophies = list(conn.execute("SELECT * FROM profile_trophies WHERE user_id=? ORDER BY place, id DESC", (user_id,)).fetchall())
            trophies.extend(profile_tournament_trophies(conn, user_id))
        if not profile:
            return self.redirect("/?msg=Профиль не найден")
        is_self = viewer and viewer["id"] == profile["id"]
        can_moderate = is_self or is_staff(viewer)
        avatar = profile["avatar_url"] or "/static/matchpoint-logo-mark.png"
        banner = profile["banner_url"] or ""
        color = profile_color(profile)
        profile_style = f"--profile-color:{esc(color)};--profile-soft:{hex_to_rgba(color, .18)};--profile-glow:{hex_to_rgba(color, .38)};"
        tag = f"<a class='profile-team-tag' href='/team?id={team['id']}'>[{esc(team['tag'])}]</a>" if team else ""
        edit = f"<a class='btn primary' href='/profile/edit'>Редактировать профиль</a>" if is_self else ""
        custom = f"<span class='profile-custom-role'>{esc(profile['custom_role'])}</span>" if profile["custom_role"] else ""
        role_form = profile_role_form(profile) if is_staff(viewer) else ""
        comments_html = "".join(profile_comment_html(c, can_moderate or (viewer and viewer["id"] == c["author_id"])) for c in comments) or "<p class='muted'>Комментариев пока нет.</p>"
        comment_form = f"""
        <form method='post' action='/profile/comment?id={profile['id']}' class='form profile-comment-form'>
            <input name='body' maxlength='500' placeholder='Написать комментарий...' required>
            <button class='btn primary'>Отправить</button>
        </form>
        """ if viewer else "<a class='btn' href='/login'>Войти, чтобы комментировать</a>"
        trophy_html = "".join(trophy_card(t) for t in trophies) or "<p class='muted'>Трофеи появятся после турниров.</p>"
        banner_media = f"<img class='profile-banner-media' src='{esc(banner)}' alt='banner'>" if banner else ""
        self.send_html(
            f"""
            <section class='profile-page' style='{profile_style}'>
                <div class='profile-banner'>{banner_media}</div>
                <div class='profile-main'>
                    <aside class='profile-identity panel'>
                        <img class='profile-avatar' src='{esc(avatar)}' alt='avatar'>
                        <h1>{tag}{esc(display_name(profile))}</h1>
                        <div class='profile-badges'><span class='badge'>{esc(profile['role'])}</span>{custom}</div>
                        {edit}{role_form}
                    </aside>
                    <section class='profile-about panel'>
                        <h2>Обо мне</h2>
                        <p>{esc(profile['about']) if profile['about'] else 'Пользователь пока ничего не рассказал о себе.'}</p>
                    </section>
                    <aside class='profile-trophies panel'>
                        <h2>Трофеи</h2>
                        {trophy_html}
                    </aside>
                </div>
                <section class='panel profile-comments'><h2>Комментарии</h2>{comment_form}<div class='comments-list'>{comments_html}</div></section>
            </section>
            """,
            f"Профиль {display_name(profile)}",
        )

    def profile_edit(self):
        user = self.require_user()
        if not user:
            return
        if self.command == "POST":
            data, files = self.multipart_form()
            nickname = data.get("nickname", "").strip()[:40]
            color = data.get("profile_color", "#00e5ff").strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                color = "#00e5ff"
            about = data.get("about", "").strip()[:1200]
            selected_avatar = data.get("selected_avatar") or ""
            selected_banner = data.get("selected_banner") or ""
            avatar_url = save_profile_upload(files.get("avatar"), user["id"], "avatar") or selected_avatar or user["avatar_url"]
            banner_url = save_profile_upload(files.get("banner"), user["id"], "banner") or selected_banner or user["banner_url"]
            remember_profile_media(user["id"], "avatar", avatar_url)
            remember_profile_media(user["id"], "banner", banner_url)
            with db() as conn:
                conn.execute(
                    "UPDATE users SET nickname=?, profile_color=?, about=?, avatar_url=?, banner_url=? WHERE id=?",
                    (nickname, color, about, avatar_url, banner_url, user["id"]),
                )
            return self.redirect(f"/profile?id={user['id']}&msg=Профиль обновлен")
        self.send_html(profile_edit_form(user), "Редактирование профиля")

    def profile_comment(self):
        user = self.require_user()
        if not user:
            return
        profile_id = self.query.get("id")
        body = self.form().get("body", "").strip()[:500]
        if body:
            with db() as conn:
                conn.execute("INSERT INTO profile_comments(profile_user_id, author_id, body, created_at) VALUES(?,?,?,?)", (profile_id, user["id"], body, now()))
        self.redirect(f"/profile?id={profile_id}")

    def profile_comment_delete(self):
        user = self.require_user()
        if not user:
            return
        comment_id = self.query.get("id")
        with db() as conn:
            comment = conn.execute("SELECT * FROM profile_comments WHERE id=?", (comment_id,)).fetchone()
            if not comment:
                return self.redirect("/?msg=Комментарий не найден")
            if user["id"] not in (comment["profile_user_id"], comment["author_id"]) and not is_staff(user):
                return self.redirect(f"/profile?id={comment['profile_user_id']}&msg=Нет прав")
            conn.execute("DELETE FROM profile_comments WHERE id=?", (comment_id,))
        self.redirect(f"/profile?id={comment['profile_user_id']}")

    def profile_role(self):
        user = self.require_admin()
        if not user:
            return
        profile_id = self.query.get("id")
        custom_role = self.form().get("custom_role", "").strip()[:32]
        with db() as conn:
            conn.execute("UPDATE users SET custom_role=? WHERE id=?", (custom_role, profile_id))
        self.redirect(f"/profile?id={profile_id}&msg=Кастомная роль обновлена")

    def tournaments(self):
        with db() as conn:
            items = conn.execute(
                """
                SELECT t.*, d.tag discipline, u.login creator,
                (SELECT COUNT(*) FROM registrations r WHERE r.tournament_id = t.id) reg_count
                FROM tournaments t
                LEFT JOIN disciplines d ON d.id = t.discipline_id
                JOIN users u ON u.id = t.creator_id
                ORDER BY t.id DESC
                """
            ).fetchall()
        cards = "".join(tournament_card(t) for t in items) or "<p class='muted'>Турниров пока нет.</p>"
        self.send_html(
            f"<section class='section-head'><h1>Турниры</h1><a class='btn primary' href='/tournament/new'>Создать новый</a></section><div class='cards'>{cards}</div>",
            "Турниры",
        )

    def tournament_form(self):
        user = self.require_user()
        if not user:
            return
        if self.command == "POST":
            data = self.form()
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO tournaments(title, discipline_id, format, max_teams, start_date, description, rules, maps, bans, is_private, private_code, status, creator_id, created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        data.get("title"), data.get("discipline_id"), data.get("format"), int(data.get("max_teams", 4)),
                        data.get("start_date"), data.get("description"), data.get("rules"), collect_selected_maps(data, data.get("discipline_id")),
                        data.get("bans") or "Faceit veto: капитаны по очереди банят и выбирают карты перед стартом матча.", 1 if data.get("is_private") else 0, data.get("private_code"), "DRAFT", user["id"], now(),
                    ),
                )
            return self.redirect("/tournaments?msg=Турнир создан")
        self.send_html(tournament_form_html("/tournament/new"), "Создание турнира")

    def tournament_edit(self):
        user = self.require_user()
        if not user:
            return
        tid = self.query.get("id")
        with db() as conn:
            tournament = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
        if not tournament:
            return self.redirect("/tournaments?msg=Турнир не найден")
        if not is_staff(user) and tournament["creator_id"] != user["id"]:
            return self.redirect(f"/tournament?id={tid}&msg=Нет прав на редактирование")
        if self.command == "POST":
            data = self.form()
            with db() as conn:
                conn.execute(
                    """
                    UPDATE tournaments SET title=?, discipline_id=?, format=?, max_teams=?, start_date=?, description=?, rules=?, maps=?, bans=?, is_private=?, private_code=?, status=? WHERE id=?
                    """,
                    (
                        data.get("title"), data.get("discipline_id"), data.get("format"), int(data.get("max_teams", 4)),
                        data.get("start_date"), data.get("description"), data.get("rules"), collect_selected_maps(data, data.get("discipline_id")), data.get("bans") or "Faceit veto: капитаны по очереди банят и выбирают карты перед стартом матча.",
                        1 if data.get("is_private") else 0, data.get("private_code"), data.get("status", tournament["status"]), tid,
                    ),
                )
            return self.redirect(f"/tournament?id={tid}&msg=Турнир обновлен")
        self.send_html(tournament_form_html(f"/tournament/edit?id={tid}", tournament), "Редактирование турнира")

    def tournament_detail(self):
        tid = self.query.get("id")
        user = self.current_user()
        with db() as conn:
            t = conn.execute(
                """
                SELECT t.*, d.name discipline_name, d.tag discipline, u.login creator
                FROM tournaments t
                LEFT JOIN disciplines d ON d.id = t.discipline_id
                JOIN users u ON u.id = t.creator_id
                WHERE t.id = ?
                """,
                (tid,),
            ).fetchone()
            regs = conn.execute(
                """
                SELECT r.*, teams.name, teams.tag, d.tag discipline
                FROM registrations r
                JOIN teams ON teams.id = r.team_id
                LEFT JOIN disciplines d ON d.id = teams.discipline_id
                WHERE r.tournament_id = ?
                """,
                (tid,),
            ).fetchall()
            messages = conn.execute(
                "SELECT * FROM (SELECT m.*, u.login, u.nickname, u.avatar_url FROM messages m JOIN users u ON u.id = m.user_id WHERE tournament_id = ? ORDER BY m.id DESC LIMIT 40) ORDER BY id",
                (tid,),
            ).fetchall()
            my_teams = []
            if user:
                my_teams = conn.execute(
                    "SELECT t.*, tm.team_role FROM teams t JOIN team_members tm ON tm.team_id = t.id WHERE tm.user_id = ?",
                    (user["id"],),
                ).fetchall()
        if not t:
            return self.redirect("/tournaments?msg=Турнир не найден")
        can_edit = user and (is_staff(user) or t["creator_id"] == user["id"])
        edit = f"<a class='btn' href='/tournament/edit?id={t['id']}'>Редактировать</a>" if can_edit else ""
        delete = f"<a class='btn danger-btn' href='/tournament/delete?id={t['id']}'>Удалить турнир</a>" if can_edit else ""
        register_form = registration_form(t, my_teams, regs) if user else "<a class='btn primary' href='/login'>Войти для регистрации</a>"
        unregister_form = tournament_unregister_controls(t, my_teams, regs) if user else ""
        participants = "".join(f"<a class='team-tile' href='/team?id={r['team_id']}'><b>{esc(r['name'])}</b><span>{esc(r['discipline'])} · {esc(r['tag'])}</span></a>" for r in regs) or "<p class='muted'>Команды пока не зарегистрированы.</p>"
        chat = "".join(message_html(m, user, "tournament", f"/tournament?id={t['id']}") for m in messages) or "<p class='muted'>В чате пока тихо.</p>"
        chat_form = tournament_chat_form(t["id"]) if user else ""
        chat_panel = chat_shell("Чат турнира", chat, chat_form, f"/tournament/messages?id={t['id']}")
        winner_banner = finished_winner_banner(t["id"]) if t["status"] == "FINISHED" else ""
        self.send_html(
            f"""
            {winner_banner}
            <section class="tournament-title">
                <div><h1>{esc(t['title'])}</h1><p>{esc(t['discipline'])} · {esc(t['format'])} · {esc(t['start_date'] or 'Дата не указана')} · <span class='badge'>{esc(t['status'])}</span></p></div>
                <a class="btn" href="/tournaments">Все турниры</a>
            </section>
            <div class="grid aside">
                <main>
                    <section class="panel"><h2>Информация о турнире</h2>
                        <div class="info-grid"><div><span>Максимум команд</span><b>{t['max_teams']}</b></div><div><span>Создал</span><b>{esc(t['creator'])}</b></div></div>
                        <h3>Описание</h3><p>{esc(t['description']) or 'Описание не заполнено.'}</p>
                        <h3>Карты</h3><p class="chips">{chips(t['maps'])}</p>
                        <h3>Баны / пики</h3><p>{esc(t['bans']) or 'Команды договариваются перед игрой.'}</p>
                        <h3>Правила</h3><p>{esc(t['rules']) or 'Стандартные правила дисциплины.'}</p>
                    </section>
                    <section class="panel"><h2>Участники <span>{len(regs)}/{t['max_teams']}</span></h2><div class="team-list">{participants}</div></section>
                    {chat_panel}
                </main>
                <aside class="panel sticky"><h2>Ваша команда</h2>{register_form}{unregister_form}<a class="btn" href="/bracket?id={t['id']}">Сетка и бан-пики</a>{edit}{delete}</aside>
            </div>
            """,
            esc(t["title"]),
        )

    def tournament_register(self):
        user = self.require_user()
        if not user:
            return
        tid = self.query.get("id")
        data = self.form()
        team_id = data.get("team_id")
        code = data.get("private_code", "")
        with db() as conn:
            t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
            member = conn.execute("SELECT * FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, user["id"])).fetchone()
            count = conn.execute("SELECT COUNT(*) c FROM registrations WHERE tournament_id = ?", (tid,)).fetchone()["c"]
            if not t or not member:
                return self.redirect(f"/tournament?id={tid}&msg=Выберите свою команду")
            if t["status"] == "FINISHED":
                return self.redirect(f"/tournament?id={tid}&msg=Турнир завершен, регистрация закрыта")
            if member["team_role"] not in ("Капитан", "Тренер", "Менеджер"):
                return self.redirect(f"/tournament?id={tid}&msg=Регистрировать команду может только владелец, тренер или менеджер")
            if t["is_private"] and code != (t["private_code"] or ""):
                return self.redirect(f"/tournament?id={tid}&msg=Неверный код приватного турнира")
            if count >= t["max_teams"]:
                return self.redirect(f"/tournament?id={tid}&msg=Лимит команд уже заполнен")
            try:
                conn.execute("INSERT INTO registrations(tournament_id, team_id, created_at) VALUES(?,?,?)", (tid, team_id, now()))
            except sqlite3.IntegrityError:
                return self.redirect(f"/tournament?id={tid}&msg=Команда уже зарегистрирована")
        return self.redirect(f"/tournament?id={tid}&msg=Команда зарегистрирована")

    def tournament_unregister(self):
        user = self.require_user()
        if not user:
            return
        tid = self.query.get("id")
        team_id = self.form().get("team_id")
        with db() as conn:
            t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
            member = conn.execute("SELECT * FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, user["id"])).fetchone()
            if not t or not member:
                return self.redirect(f"/tournament?id={tid}&msg=Команда не найдена")
            if member["team_role"] not in ("Капитан", "Тренер", "Менеджер") and not is_staff(user):
                return self.redirect(f"/tournament?id={tid}&msg=Снять команду может владелец, тренер или менеджер")
            conn.execute("DELETE FROM registrations WHERE tournament_id=? AND team_id=?", (tid, team_id))
        self.redirect(f"/tournament?id={tid}&msg=Команда снята с турнира")

    def tournament_message(self):
        user = self.require_user()
        if not user:
            return
        tid = self.query.get("id")
        form = self.form()
        body = form.get("body", "").strip()
        media_url = form.get("media_url", "").strip()
        emoji = form.get("emoji", "").strip()
        if body or media_url or emoji:
            with db() as conn:
                conn.execute("INSERT INTO messages(tournament_id, user_id, body, media_url, emoji, created_at) VALUES(?,?,?,?,?,?)", (tid, user["id"], body, media_url, emoji, now()))
        if self.headers.get("X-Requested-With") == "fetch":
            self.send_response(204)
            self.end_headers()
            return
        self.redirect(f"/tournament?id={tid}")

    def tournament_messages(self):
        tid = self.query.get("id")
        user = self.current_user()
        with db() as conn:
            messages = conn.execute(
                "SELECT * FROM (SELECT m.*, u.login, u.nickname, u.avatar_url FROM messages m JOIN users u ON u.id=m.user_id WHERE m.tournament_id=? ORDER BY m.id DESC LIMIT 40) ORDER BY id",
                (tid,),
            ).fetchall()
        html_body = "".join(message_html(m, user, "tournament", f"/tournament?id={tid}") for m in messages) or "<p class='muted'>В чате пока тихо.</p>"
        self.send_fragment(html_body)

    def global_message(self):
        user = self.require_user()
        if not user:
            return
        data = self.form()
        body = data.get("body", "").strip()
        media_url = data.get("media_url", "").strip()
        emoji = data.get("emoji", "").strip()
        if body or media_url or emoji:
            with db() as conn:
                conn.execute("INSERT INTO global_messages(user_id, body, media_url, emoji, created_at) VALUES(?,?,?,?,?)", (user["id"], body, media_url, emoji, now()))
        if self.headers.get("X-Requested-With") == "fetch":
            self.send_response(204)
            self.end_headers()
            return
        self.redirect("/")

    def global_feed(self):
        user = self.current_user()
        with db() as conn:
            messages = conn.execute(
                "SELECT * FROM (SELECT gm.*, u.login, u.nickname, u.avatar_url FROM global_messages gm JOIN users u ON u.id=gm.user_id ORDER BY gm.id DESC LIMIT 40) ORDER BY id"
            ).fetchall()
        html_body = "".join(message_html(m, user, "global", "/") for m in messages) or "<p class='muted'>В общем чате пока пусто.</p>"
        self.send_fragment(html_body)

    def message_delete(self):
        user = self.require_user()
        if not user:
            return
        scope = self.query.get("scope", "tournament")
        message_id = self.query.get("id")
        back = self.query.get("back", "/")
        table_name = "global_messages" if scope == "global" else "messages"
        with db() as conn:
            msg = conn.execute(f"SELECT * FROM {table_name} WHERE id=?", (message_id,)).fetchone()
            if not msg:
                return self.redirect(f"{back}?msg=Сообщение не найдено")
            if msg["user_id"] != user["id"] and not is_staff(user):
                return self.redirect(f"{back}?msg=Недостаточно прав")
            conn.execute(f"DELETE FROM {table_name} WHERE id=?", (message_id,))
        self.redirect(back)

    def match_score(self):
        user = self.require_user()
        if not user:
            return
        match_id = self.query.get("match_id")
        data = self.form()
        with db() as conn:
            match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
            if not match:
                return self.redirect("/tournaments?msg=Матч не найден")
            is_captain = conn.execute(
                "SELECT id FROM teams WHERE id IN (?,?) AND captain_id=?",
                (match["team1_id"], match["team2_id"], user["id"]),
            ).fetchone()
            if not is_staff(user) and not is_captain:
                return self.redirect(f"/bracket?id={match['tournament_id']}&msg=Нет прав на счет матча")
            scores = []
            for i in range(1, 8):
                score = data.get(f"map{i}", "").strip()
                if score:
                    scores.append(f"Карта {i}: {score}")
            map_scores = "; ".join(scores) or data.get("map_scores", "").strip()
            winner_id = data.get("winner_id") or None
            status = "CONFIRMED" if is_staff(user) else "PENDING"
            conn.execute(
                "INSERT INTO match_scores(match_id, user_id, map_scores, winner_id, status, created_at) VALUES(?,?,?,?,?,?)",
                (match_id, user["id"], map_scores, winner_id, status, now()),
            )
            if status == "CONFIRMED":
                conn.execute("UPDATE matches SET winner_id=?, note=? WHERE id=?", (winner_id, map_scores, match_id))
                rebuild_bracket_rounds(conn, match["tournament_id"])
        self.redirect(f"/bracket?id={match['tournament_id']}&msg=Счет сохранен")

    def veto(self):
        tid = self.query.get("id")
        match_id = self.query.get("match_id")
        user = self.current_user()
        if self.command == "POST":
            user = self.require_user()
            if not user:
                return
            data = self.form()
            with db() as conn:
                t = conn.execute("SELECT * FROM tournaments WHERE id=?", (tid,)).fetchone()
                ensure_matches(conn, tid)
                match = conn.execute("SELECT * FROM matches WHERE id=? AND tournament_id=?", (match_id, tid)).fetchone()
                if not t or not match:
                    return self.redirect(f"/bracket?id={tid}&msg=Матч для бан-пика не найден")
                actions = conn.execute("SELECT * FROM veto_actions WHERE match_id=? ORDER BY id", (match_id,)).fetchall()
                progress = veto_progress(t, match, actions)
                team = conn.execute(
                    """
                    SELECT teams.* FROM teams
                    JOIN registrations r ON r.team_id=teams.id
                    WHERE r.tournament_id=? AND teams.captain_id=? AND teams.id=?
                    """,
                    (tid, user["id"], progress.get("team_id")),
                ).fetchone()
                map_name = data.get("map_name")
                if progress.get("complete"):
                    return self.redirect(f"/veto?id={tid}&match_id={match_id}&msg=Бан-пик уже завершен")
                if not team:
                    return self.redirect(f"/veto?id={tid}&match_id={match_id}&msg=Сейчас ход капитана другой команды")
                if data.get("action") != progress.get("action"):
                    return self.redirect(f"/veto?id={tid}&match_id={match_id}&msg=Сейчас нужно выполнить {progress.get('action')}")
                if map_name not in progress["remaining"]:
                    return self.redirect(f"/veto?id={tid}&match_id={match_id}&msg=Эта карта уже использована или не входит в пул")
                conn.execute(
                    "INSERT INTO veto_actions(tournament_id, match_id, team_id, user_id, action, map_name, created_at) VALUES(?,?,?,?,?,?,?)",
                    (tid, match_id, team["id"], user["id"], progress["action"], map_name, now()),
                )
            return self.redirect(f"/veto?id={tid}&match_id={match_id}&msg=Действие сохранено")
        with db() as conn:
            ensure_matches(conn, tid)
            t = conn.execute(
                """
                SELECT t.*, d.tag discipline FROM tournaments t
                LEFT JOIN disciplines d ON d.id=t.discipline_id
                WHERE t.id=?
                """,
                (tid,),
            ).fetchone()
            if not match_id:
                first_match = conn.execute("SELECT id FROM matches WHERE tournament_id=? ORDER BY id LIMIT 1", (tid,)).fetchone()
                if first_match:
                    return self.redirect(f"/veto?id={tid}&match_id={first_match['id']}")
            match = conn.execute(
                """
                SELECT m.*, a.name team1, b.name team2
                FROM matches m
                LEFT JOIN teams a ON a.id=m.team1_id
                LEFT JOIN teams b ON b.id=m.team2_id
                WHERE m.id=? AND m.tournament_id=?
                """,
                (match_id, tid),
            ).fetchone()
            actions = conn.execute(
                """
                SELECT v.*, teams.name team_name, u.login
                FROM veto_actions v
                JOIN teams ON teams.id=v.team_id
                JOIN users u ON u.id=v.user_id
                WHERE v.match_id=?
                ORDER BY v.id
                """,
                (match_id,),
            ).fetchall()
            members1 = team_members_for_veto(conn, match["team1_id"]) if match else []
            members2 = team_members_for_veto(conn, match["team2_id"]) if match else []
        if not t:
            return self.redirect("/tournaments?msg=Турнир не найден")
        if not match:
            return self.redirect(f"/bracket?id={tid}&msg=Матч для бан-пика не найден")
        progress = veto_progress(t, match, actions)
        current_team_name = match["team1"] if progress.get("team_id") == match["team1_id"] else match["team2"]
        current_captain = None
        if progress.get("team_id"):
            with db() as conn:
                current_captain = conn.execute("SELECT captain_id FROM teams WHERE id=?", (progress.get("team_id"),)).fetchone()
        can_vote = bool(user and not progress.get("complete") and current_captain and current_captain["captain_id"] == user["id"])
        used_by_map = {a["map_name"]: a for a in actions}
        map_cards = "".join(veto_map_card(tid, match_id, map_name, used_by_map, progress, can_vote) for map_name in split_csv(t["maps"]))
        status_text = progress["message"] if progress.get("complete") else f"Ход: {current_team_name} · {progress.get('action')}"
        self.send_html(
            f"""
            <section class='veto-hero'><h1>VETO SYSTEM</h1><p>{esc(match['team1'] or 'TBD')} vs {esc(match['team2'] or 'TBD')} · {esc(t['format'])}</p><a class='btn' href='/bracket?id={tid}'>Назад к сетке</a></section>
            <section class="veto-stage">
                {veto_team_panel(match['team1'] or 'TBD', members1, progress.get('team_id') == match['team1_id'])}
                <main class="veto-center">
                    <div class="veto-status">{esc(status_text)}</div>
                    <div class="veto-map-grid">{map_cards}</div>
                </main>
                {veto_team_panel(match['team2'] or 'TBD', members2, progress.get('team_id') == match['team2_id'])}
            </section>
            """,
            "Баны / пики",
        )

    def tournament_delete(self):
        user = self.require_user()
        if not user:
            return
        tid = self.query.get("id")
        with db() as conn:
            t = conn.execute("SELECT * FROM tournaments WHERE id=?", (tid,)).fetchone()
            if not t:
                return self.redirect("/tournaments?msg=Турнир не найден")
            if not is_staff(user) and t["creator_id"] != user["id"]:
                return self.redirect(f"/tournament?id={tid}&msg=Нет прав на удаление турнира")
            conn.execute("DELETE FROM tournaments WHERE id=?", (tid,))
        self.redirect("/tournaments?msg=Турнир удален")

    def bracket(self):
        tid = self.query.get("id")
        user = self.current_user()
        with db() as conn:
            t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
            ensure_matches(conn, tid)
            matches = conn.execute(
                """
                SELECT m.*, a.name team1, b.name team2, w.name winner_name
                FROM matches m
                LEFT JOIN teams a ON a.id = m.team1_id
                LEFT JOIN teams b ON b.id = m.team2_id
                LEFT JOIN teams w ON w.id = m.winner_id
                WHERE m.tournament_id = ? ORDER BY m.round, m.id
                """,
                (tid,),
            ).fetchall()
            latest_scores = {
                row["match_id"]: row
                for row in conn.execute(
                    "SELECT * FROM match_scores WHERE match_id IN (SELECT id FROM matches WHERE tournament_id=?) ORDER BY id",
                    (tid,),
                ).fetchall()
            }
        if not t:
            return self.redirect("/tournaments?msg=Турнир не найден")
        bracket_html = render_bracket(t, matches, latest_scores, user)
        self.send_html(f"<section class='section-head'><h1>Турнирная сетка: {esc(t['title'])}</h1><a class='btn' href='/tournament?id={tid}'>Назад к турниру</a></section>{bracket_html}", "Сетка")

    def teams(self):
        user = self.current_user()
        q = self.query.get("q", "").strip()
        with db() as conn:
            where = "WHERE t.name LIKE ? OR t.tag LIKE ? OR d.name LIKE ?"
            params = (f"%{q}%", f"%{q}%", f"%{q}%")
            sql = """
                SELECT t.*, d.tag discipline, d.name discipline_name, u.login captain,
                (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id=t.id) members
                FROM teams t
                LEFT JOIN disciplines d ON d.id=t.discipline_id
                JOIN users u ON u.id=t.captain_id
            """
            items = conn.execute(sql + (f" {where}" if q else "") + " ORDER BY t.id DESC", params if q else ()).fetchall()
            my_team = None
            if user:
                my_team = conn.execute("SELECT team_id FROM team_members WHERE user_id=? LIMIT 1", (user["id"],)).fetchone()
        cards = "".join(team_search_card(t, user, my_team) for t in items) or "<p class='muted'>Команды не найдены.</p>"
        self.send_html(
            f"""
            <section class="section-head"><h1>Команды</h1><a class="btn primary" href="/team/new">Создать команду</a></section>
            <section class="panel">
                <form class="search-line" method="get" action="/teams">
                    <input name="q" value="{esc(q)}" placeholder="Найти команду по названию, тегу или игре">
                    <button class="btn">Найти</button>
                </form>
            </section>
            <div class="cards">{cards}</div>
            """,
            "Команды",
        )

    def team_my(self):
        user = self.require_user()
        if not user:
            return
        with db() as conn:
            team = conn.execute("SELECT team_id FROM team_members WHERE user_id=? LIMIT 1", (user["id"],)).fetchone()
        if team:
            return self.redirect(f"/team?id={team['team_id']}")
        self.send_html(
            "<section class='panel narrow'><h1>Моя команда</h1><p class='muted'>Вы пока не состоите в команде.</p><div class='actions'><a class='btn primary' href='/team/new'>Создать команду</a><a class='btn' href='/teams'>Найти команду</a></div></section>",
            "Моя команда",
        )

    def team_form(self):
        user = self.require_user()
        if not user:
            return
        with db() as conn:
            existing = conn.execute("SELECT team_id FROM team_members WHERE user_id=? LIMIT 1", (user["id"],)).fetchone()
        if existing:
            return self.redirect(f"/team?id={existing['team_id']}&msg=Вы уже состоите в команде")
        if self.command == "POST":
            data, files = self.multipart_form()
            join_key = data.get("join_key")
            if not join_key or join_key == "Сгенерируется автоматически":
                join_key = secrets.token_hex(4)
            join_password = data.get("join_password", "").strip()
            join_password_hash = hash_password(join_password) if join_password else ""
            logo_url = save_public_upload(files.get("logo"), f"team_logo_{user['id']}")
            with db() as conn:
                cur = conn.execute(
                    "INSERT INTO teams(name, tag, discipline_id, captain_id, description, join_password, join_key, logo_url, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (data.get("name"), data.get("tag"), data.get("discipline_id"), user["id"], data.get("description"), join_password_hash, join_key, logo_url, now()),
                )
                conn.execute("INSERT INTO team_members(team_id, user_id, team_role, joined_at) VALUES(?,?,?,?)", (cur.lastrowid, user["id"], "Капитан", now()))
            return self.redirect("/dashboard?msg=Команда создана")
        self.send_html(team_form_html("/team/new"), "Создание команды")

    def team_detail(self):
        team_id = self.query.get("id")
        user = self.current_user()
        with db() as conn:
            team = conn.execute(
                """
                SELECT t.*, d.tag discipline, d.name discipline_name, u.login captain
                FROM teams t LEFT JOIN disciplines d ON d.id = t.discipline_id
                JOIN users u ON u.id = t.captain_id WHERE t.id = ?
                """,
                (team_id,),
            ).fetchone()
            members = conn.execute(
                "SELECT tm.*, u.login, u.full_name FROM team_members tm JOIN users u ON u.id = tm.user_id WHERE tm.team_id = ?",
                (team_id,),
            ).fetchall()
        if not team:
            return self.redirect("/dashboard?msg=Команда не найдена")
        is_member = user and any(m["user_id"] == user["id"] for m in members)
        is_captain = user and (user["id"] == team["captain_id"] or is_staff(user))
        member_html = "".join(member_row(team, m, is_captain) for m in members)
        actions = join_team_form(team_id) if user and not is_member else ""
        if user and is_member and not is_captain:
            actions += f"<a class='btn danger-btn' href='/team/leave?id={team_id}'>Выйти из команды</a>"
        if is_captain:
            actions += f"<a class='btn' href='/team/edit?id={team_id}'>Редактировать команду</a>"
            actions += f"<a class='btn danger-btn' href='/team/delete?id={team_id}'>Расформировать команду</a>"
            actions += f"<p class='note'>Ключ вступления: <b>{esc(team['join_key'] or 'не задан')}</b></p>"
        logo = f"<img class='team-logo-large' src='{esc(team['logo_url'])}' alt='logo'>" if team["logo_url"] else ""
        self.send_html(
            f"""
            <div class="grid aside">
                <section class="panel team-hero">{logo}<h1>{esc(team['name'])} <span class='badge'>{esc(team['tag'])}</span></h1>
                    <p><b>Дисциплина:</b> {esc(team['discipline_name'])}</p><p><b>Владелец:</b> {esc(team['captain'])}</p>
                    <p><b>Описание:</b> {esc(team['description']) or 'Описание не заполнено.'}</p>
                </section>
                <aside class="panel actions-stack"><a class="btn" href="/tournaments">Найти турнир</a>{actions}</aside>
            </div>
            <section class="panel"><h2>Состав команды</h2><div class="members">{member_html}</div></section>
            """,
            esc(team["name"]),
        )

    def team_join(self):
        user = self.require_user()
        if not user:
            return
        team_id = self.query.get("id")
        data = self.form()
        with db() as conn:
            existing = conn.execute("SELECT team_id FROM team_members WHERE user_id=? LIMIT 1", (user["id"],)).fetchone()
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            if existing:
                return self.redirect(f"/team?id={existing['team_id']}&msg=Сначала выйдите из текущей команды")
            if not team:
                return self.redirect("/teams?msg=Команда не найдена")
            entered = data.get("join_secret", "")
            password_ok = verify_team_secret(entered, team["join_password"])
            key_ok = bool(team["join_key"] and entered == team["join_key"])
            if (team["join_password"] or team["join_key"]) and not (password_ok or key_ok):
                return self.redirect(f"/team?id={team_id}&msg=Неверный пароль или ключ команды")
            if password_ok and team["join_password"] and not is_hashed_secret(team["join_password"]):
                conn.execute("UPDATE teams SET join_password=? WHERE id=?", (hash_password(entered), team_id))
            try:
                conn.execute("INSERT INTO team_members(team_id, user_id, team_role, joined_at) VALUES(?,?,?,?)", (team_id, user["id"], "Игрок", now()))
            except sqlite3.IntegrityError:
                pass
        self.redirect(f"/team?id={team_id}&msg=Вы вступили в команду")

    def team_leave(self):
        user = self.require_user()
        if not user:
            return
        team_id = self.query.get("id")
        with db() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            if not team:
                return self.redirect("/teams?msg=Команда не найдена")
            if team["captain_id"] == user["id"]:
                return self.redirect(f"/team?id={team_id}&msg=Владелец расформировывает команду через кнопку удаления")
            conn.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user["id"]))
        self.redirect("/teams?msg=Вы вышли из команды")

    def team_kick(self):
        user = self.require_user()
        if not user:
            return
        team_id = self.query.get("id")
        target_id = self.query.get("user_id")
        with db() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            if not team:
                return self.redirect("/teams?msg=Команда не найдена")
            if not is_staff(user) and team["captain_id"] != user["id"]:
                return self.redirect(f"/team?id={team_id}&msg=Недостаточно прав")
            if str(team["captain_id"]) == str(target_id):
                return self.redirect(f"/team?id={team_id}&msg=Владельца нельзя кикнуть")
            conn.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, target_id))
        self.redirect(f"/team?id={team_id}&msg=Участник удален из команды")

    def team_delete(self):
        user = self.require_user()
        if not user:
            return
        team_id = self.query.get("id")
        with db() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team:
            return self.redirect("/teams?msg=Команда не найдена")
        if not is_staff(user) and team["captain_id"] != user["id"]:
            return self.redirect(f"/team?id={team_id}&msg=Нет прав на удаление команды")
        if self.command != "POST":
            return self.send_html(
                f"""
                <section class='panel narrow danger-zone'><h1>Расформировать команду</h1>
                <p class='muted'>Это действие удалит команду и все связанные регистрации. Подтвердите своим паролем.</p>
                <form method='post' action='/team/delete?id={team_id}' class='form'>
                    <label>Ваш пароль<input type='password' name='password' required autocomplete='current-password'></label>
                    <div class='form-actions'><a class='btn' href='/team?id={team_id}'>Отмена</a><button class='btn danger-btn'>Расформировать</button></div>
                </form></section>
                """,
                "Подтверждение удаления",
            )
        if not check_password(self.form().get("password", ""), user["password_hash"]):
            return self.redirect(f"/team/delete?id={team_id}&msg=Неверный пароль")
        with db() as conn:
            conn.execute("DELETE FROM teams WHERE id=?", (team_id,))
        self.redirect("/teams?msg=Команда удалена")

    def team_role(self):
        user = self.require_user()
        if not user:
            return
        data = self.form()
        team_id = data.get("team_id")
        with db() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
            if team and (team["captain_id"] == user["id"] or is_staff(user)):
                new_role = data.get("team_role")
                target_id = data.get("user_id")
                if new_role in ("Капитан", "Тренер", "Менеджер"):
                    exists = conn.execute(
                        "SELECT user_id FROM team_members WHERE team_id=? AND team_role=? AND user_id<>?",
                        (team_id, new_role, target_id),
                    ).fetchone()
                    if exists:
                        return self.redirect(f"/team?id={team_id}&msg=В команде уже есть {new_role}")
                conn.execute("UPDATE team_members SET team_role = ? WHERE team_id = ? AND user_id = ?", (new_role, team_id, target_id))
                if new_role == "Капитан":
                    conn.execute("UPDATE teams SET captain_id=? WHERE id=?", (target_id, team_id))
        self.redirect(f"/team?id={team_id}&msg=Роль обновлена")

    def team_edit(self):
        user = self.require_user()
        if not user:
            return
        team_id = self.query.get("id")
        with db() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        if not team:
            return self.redirect("/dashboard?msg=Команда не найдена")
        if not is_staff(user) and team["captain_id"] != user["id"]:
            return self.redirect(f"/team?id={team_id}&msg=Нет прав")
        if self.command == "POST":
            data, files = self.multipart_form()
            join_key = data.get("join_key")
            if not join_key or join_key == "Сгенерируется автоматически":
                join_key = secrets.token_hex(4)
            join_password = data.get("join_password", "").strip()
            join_password_value = hash_password(join_password) if join_password else team["join_password"]
            logo_url = save_public_upload(files.get("logo"), f"team_logo_{team_id}") or team["logo_url"]
            with db() as conn:
                conn.execute(
                    "UPDATE teams SET name=?, tag=?, discipline_id=?, description=?, join_password=?, join_key=?, logo_url=? WHERE id=?",
                    (data.get("name"), data.get("tag"), data.get("discipline_id"), data.get("description"), join_password_value, join_key, logo_url, team_id),
                )
            return self.redirect(f"/team?id={team_id}&msg=Команда обновлена")
        self.send_html(team_form_html(f"/team/edit?id={team_id}", team), "Редактирование команды")

    def admin(self):
        if not self.require_admin():
            return
        self.send_html(
            """
            <section class="welcome"><h1>Админ-панель</h1><p>Управление платформой Матч Поинт</p></section>
            <section class="panel quicks">
                <a class="quick" href="/admin/disciplines">Игровые дисциплины</a>
                <a class="quick" href="/admin/users">Пользователи</a>
                <a class="quick" href="/admin/teams">Команды</a>
                <a class="quick" href="/admin/tournaments">Турниры</a>
            </section>
            """,
            "Админ-панель",
        )

    def admin_disciplines(self):
        if not self.require_admin():
            return
        if self.command == "POST":
            data = self.form()
            with db() as conn:
                conn.execute(
                    "INSERT INTO disciplines(name, tag, description, map_pool, rule_presets) VALUES(?,?,?,?,?)",
                    (data.get("name"), data.get("tag"), data.get("description"), data.get("map_pool"), data.get("rule_presets")),
                )
            return self.redirect("/admin/disciplines?msg=Дисциплина добавлена")
        with db() as conn:
            items = conn.execute("SELECT * FROM disciplines ORDER BY id DESC").fetchall()
        rows = "".join(f"<tr><td>{d['id']}</td><td>{esc(d['name'])}</td><td><span class='badge'>{esc(d['tag'])}</span></td><td>{esc(d['description'])}</td><td><a class='danger' href='/admin/delete?type=discipline&id={d['id']}'>Удалить</a></td></tr>" for d in items)
        self.send_html(f"<section class='section-head'><h1>Игровые дисциплины</h1><a class='btn' href='/admin'>Назад</a></section><section class='panel'><h2>Добавить новую дисциплину</h2><form method='post' class='form grid-form'><label>Название<input name='name' required></label><label>Короткий тег<input name='tag' required></label><label class='wide'>Описание<textarea name='description'></textarea></label><label class='wide'>Пул карт / режимов<textarea name='map_pool' placeholder='Dust2, Mirage, Inferno'></textarea></label><label class='wide'>Правила<textarea name='rule_presets' placeholder='Одно правило на строку'></textarea></label><button class='btn primary'>Добавить</button></form></section>{table(['ID','Название','Тег','Описание','Действия'], rows)}", "Дисциплины")

    def admin_users(self):
        user = self.require_admin()
        if not user:
            return
        with db() as conn:
            items = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        rows = ""
        for u in items:
            action = "Нельзя удалить себя"
            if u["id"] != user["id"]:
                action = f"<a class='danger' href='/admin/delete?type=user&id={u['id']}'>Удалить</a>"
            action = admin_user_actions(user, u)
            rows += f"<tr><td>{u['id']}</td><td><a href='/profile?id={u['id']}'>{esc(u['login'])}</a></td><td>{esc(u['full_name']) or '-'}</td><td><span class='badge'>{esc(u['role'])}</span></td><td>{esc(u['created_at'])}</td><td>{action}</td></tr>"
        self.send_html(f"<section class='section-head'><h1>Управление пользователями</h1><a class='btn' href='/admin'>Назад</a></section>{table(['ID','Логин','ФИО','Роль','Дата регистрации','Действия'], rows)}<p class='note'>Удаление пользователя также очищает связанные членства в командах.</p>", "Пользователи")

    def admin_teams(self):
        if not self.require_admin():
            return
        with db() as conn:
            items = conn.execute(
                """
                SELECT t.*, d.tag discipline, u.login captain,
                (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id=t.id) members
                FROM teams t LEFT JOIN disciplines d ON d.id=t.discipline_id JOIN users u ON u.id=t.captain_id
                ORDER BY t.id DESC
                """
            ).fetchall()
        rows = "".join(f"<tr><td>{t['id']}</td><td><a href='/team?id={t['id']}'>{esc(t['name'])}</a></td><td>{esc(t['tag'])}</td><td>{esc(t['discipline'])}</td><td>{esc(t['captain'])}</td><td>{t['members']}</td><td>{esc(t['created_at'])}</td><td><a class='danger' href='/admin/delete?type=team&id={t['id']}'>Удалить</a></td></tr>" for t in items)
        self.send_html(f"<section class='section-head'><h1>Управление командами</h1><a class='btn' href='/admin'>Назад</a></section>{table(['ID','Название','Тег','Дисциплина','Владелец','Участники','Дата','Действия'], rows)}", "Команды")

    def admin_tournaments(self):
        if not self.require_admin():
            return
        with db() as conn:
            items = conn.execute(
                "SELECT t.*, d.tag discipline FROM tournaments t LEFT JOIN disciplines d ON d.id=t.discipline_id ORDER BY t.id DESC"
            ).fetchall()
        rows = "".join(
            f"<tr><td>{t['id']}</td><td><a href='/tournament?id={t['id']}'>{esc(t['title'])}</a></td><td>{esc(t['discipline'])}</td><td>{esc(t['format'])}</td><td>{t['max_teams']}</td><td><span class='badge'>{esc(t['status'])}</span></td><td>{esc(t['created_at'])}</td><td><form method='post' action='/admin/status?id={t['id']}'><select name='status'><option>DRAFT</option><option>ONGOING</option><option>FINISHED</option></select><button class='btn tiny'>OK</button></form><a class='danger' href='/admin/delete?type=tournament&id={t['id']}'>Удалить</a></td></tr>"
            for t in items
        )
        self.send_html(f"<section class='section-head'><h1>Управление турнирами</h1><a class='btn primary' href='/tournament/new'>Создать новый</a></section>{table(['ID','Название','Дисциплина','Формат','Макс. команд','Статус','Дата','Действия'], rows)}", "Турниры")

    def admin_status(self):
        if not self.require_admin():
            return
        with db() as conn:
            conn.execute("UPDATE tournaments SET status=? WHERE id=?", (self.form().get("status"), self.query.get("id")))
        self.redirect("/admin/tournaments?msg=Статус обновлен")

    def admin_role(self):
        user = self.require_admin()
        if not user:
            return
        if not is_owner(user):
            return self.redirect("/admin/users?msg=Только владелец может менять админ-права")
        target_id = self.query.get("id")
        role = self.form().get("role")
        if role not in ("ADMIN", "USER"):
            return self.redirect("/admin/users?msg=Некорректная роль")
        with db() as conn:
            target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
            if not target or target["role"] == "OWNER":
                return self.redirect("/admin/users?msg=Нельзя менять этого пользователя")
            conn.execute("UPDATE users SET role=? WHERE id=?", (role, target_id))
        self.redirect("/admin/users?msg=Права обновлены")

    def admin_delete(self):
        user = self.require_admin()
        if not user:
            return
        kind, item_id = self.query.get("type"), self.query.get("id")
        tables = {"discipline": "disciplines", "team": "teams", "user": "users", "tournament": "tournaments"}
        labels = {"discipline": "дисциплину", "team": "команду", "user": "пользователя", "tournament": "турнир"}
        backs = {"discipline": "/admin/disciplines", "team": "/admin/teams", "user": "/admin/users", "tournament": "/admin/tournaments"}
        back = backs.get(kind, "/admin")
        if kind not in tables:
            return self.redirect("/admin?msg=Неизвестный тип удаления")
        if kind == "user" and str(user["id"]) == str(item_id):
            return self.redirect("/admin/users?msg=Нельзя удалить себя")
        if self.command != "POST":
            return self.send_html(
                f"""
                <section class='panel narrow danger-zone'><h1>Подтверждение удаления</h1>
                <p class='muted'>Вы собираетесь удалить {labels[kind]}. Введите пароль администратора, чтобы действие не сработало случайно.</p>
                <form method='post' action='/admin/delete?type={esc(kind)}&id={esc(item_id)}' class='form'>
                    <label>Ваш пароль<input type='password' name='password' required autocomplete='current-password'></label>
                    <div class='form-actions'><a class='btn' href='{back}'>Отмена</a><button class='btn danger-btn'>Удалить</button></div>
                </form></section>
                """,
                "Подтверждение удаления",
            )
        if not check_password(self.form().get("password", ""), user["password_hash"]):
            return self.redirect(f"{back}?msg=Неверный пароль")
        with db() as conn:
            conn.execute(f"DELETE FROM {tables[kind]} WHERE id=?", (item_id,))
        self.redirect(f"{back}?msg=Удалено")



def layout(body, title, user, msg=None):
    auth = f"<a href='/profile?id={user['id']}'>{esc(display_name(user))}</a><a href='/password'>Пароль</a><a href='/logout'>Выйти</a>" if user else "<a href='/login'>Войти</a><a href='/register'>Регистрация</a>"
    admin = "<a href='/admin'>Админ</a>" if is_staff(user) else ""
    flash = f"<div class='flash'>{esc(msg)}<button data-close>×</button></div>" if msg else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{esc(title)}</title>
    <link rel="stylesheet" href="/static/style.css?v=media-chat-fix-4">
</head>
<body>
    <header class="topbar">
        <a class="brand" href="/"><img class="brand-logo" src="/static/matchpoint-logo-mark.png" alt="Матч Поинт"><strong>Матч Поинт</strong></a>
        <nav><a href="/tournaments">Турниры</a><a href="/teams">Команды</a><a href="/team/my">Моя команда</a>{admin}</nav>
        <div class="auth">{auth}</div>
    </header>
    <main class="container">{flash}{body}</main>
    <script src="/static/app.js?v=media-chat-fix-4"></script>
</body>
</html>"""


def safe_location(location):
    return quote(location, safe="/:?&=%#[]@!$'()*+,;")


def save_public_upload(file_info, stem):
    if not file_info:
        return ""
    ext = os.path.splitext(file_info["filename"])[1].lower()
    if ext not in (".gif", ".png", ".jpg", ".jpeg", ".webp"):
        return ""
    if len(file_info["content"]) > 4 * 1024 * 1024:
        return ""
    name = f"{stem}_{secrets.token_hex(4)}{ext}"
    for folder in (os.path.join(ROOT, "static"), os.path.join(ROOT, "public_html", "static")):
        if os.path.isdir(folder):
            path = os.path.join(folder, name)
            with open(path, "wb") as f:
                f.write(file_info["content"])
    return f"/static/{name}"


def save_profile_upload(file_info, user_id, kind):
    return save_public_upload(file_info, f"profile_{user_id}_{kind}")


def remember_profile_media(user_id, kind, url):
    if not url:
        return
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM profile_media_history WHERE user_id=? AND kind=? AND url=?",
            (user_id, kind, url),
        ).fetchone()
        if existing:
            conn.execute("UPDATE profile_media_history SET created_at=? WHERE id=?", (now(), existing["id"]))
        else:
            conn.execute(
                "INSERT INTO profile_media_history(user_id, kind, url, created_at) VALUES(?,?,?,?)",
                (user_id, kind, url, now()),
            )
        old = conn.execute(
            "SELECT id FROM profile_media_history WHERE user_id=? AND kind=? ORDER BY id DESC LIMIT -1 OFFSET 5",
            (user_id, kind),
        ).fetchall()
        for row in old:
            conn.execute("DELETE FROM profile_media_history WHERE id=?", (row["id"],))


def profile_media_history(user_id, kind, selected):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM profile_media_history WHERE user_id=? AND kind=? ORDER BY id DESC LIMIT 5",
            (user_id, kind),
        ).fetchall()
    if not rows:
        return ""
    cards = []
    for row in rows:
        checked = "checked" if row["url"] == selected else ""
        cards.append(
            f"<label class='media-choice'><input type='radio' name='selected_{kind}' value='{esc(row['url'])}' {checked}>"
            f"<img src='{esc(row['url'])}' alt='{esc(kind)}'></label>"
        )
    return f"<div class='media-history'>{''.join(cards)}</div>"


def profile_tournament_trophies(conn, user_id):
    teams = [row["team_id"] for row in conn.execute("SELECT team_id FROM team_members WHERE user_id=?", (user_id,)).fetchall()]
    if not teams:
        return []
    trophies = []
    for team_id in teams:
        tournaments = conn.execute(
            """
            SELECT DISTINCT t.id, t.title, t.created_at
            FROM tournaments t
            JOIN matches m ON m.tournament_id=t.id
            WHERE t.status='FINISHED' AND (m.team1_id=? OR m.team2_id=?)
            """,
            (team_id, team_id),
        ).fetchall()
        for tournament in tournaments:
            matches = conn.execute(
                "SELECT * FROM matches WHERE tournament_id=? ORDER BY round DESC, id",
                (tournament["id"],),
            ).fetchall()
            if not matches:
                continue
            max_round = max(m["round"] for m in matches)
            final = next((m for m in matches if m["round"] == max_round), None)
            place = None
            if final and final["winner_id"] == team_id:
                place = 1
            elif final and team_id in (final["team1_id"], final["team2_id"]):
                place = 2
            else:
                semi_losers = []
                for m in sorted([x for x in matches if x["round"] == max_round - 1], key=lambda item: item["id"]):
                    loser = None
                    if m["winner_id"] == m["team1_id"]:
                        loser = m["team2_id"]
                    elif m["winner_id"] == m["team2_id"]:
                        loser = m["team1_id"]
                    if loser:
                        semi_losers.append(loser)
                if team_id in semi_losers:
                    place = 3 + semi_losers.index(team_id)
            if place:
                trophies.append({"tournament_name": tournament["title"], "place": place, "created_at": tournament["created_at"]})
    unique = {}
    for trophy in trophies:
        key = trophy["tournament_name"]
        if key not in unique or trophy["place"] < unique[key]["place"]:
            unique[key] = trophy
    return sorted(unique.values(), key=lambda item: (item["place"], item["tournament_name"]))


def trophy_card(trophy):
    place = int(trophy["place"])
    styles = {
        1: ("diamond", "💎"),
        2: ("gold", "🏆"),
        3: ("silver", "🥈"),
        4: ("bronze", "🥉"),
    }
    tone, icon = styles.get(place, ("bronze", "🏅"))
    return f"<div class='trophy-card {tone}'><div><b>{esc(trophy['tournament_name'])}</b><span>{place} место</span></div><strong>{icon}</strong></div>"


def profile_comment_html(comment, can_delete):
    avatar = comment["avatar_url"] or "/static/matchpoint-logo-mark.png"
    name = esc(comment["nickname"] or comment["login"])
    delete = f"<a class='message-delete' href='/profile/comment/delete?id={comment['id']}'>Удалить</a>" if can_delete else ""
    return f"<article class='profile-comment'><img src='{esc(avatar)}' alt=''><div><b>{name}</b><time>{esc(comment['created_at'])}</time><p>{esc(comment['body'])}</p></div>{delete}</article>"


def profile_role_form(profile):
    return f"""
    <form method='post' action='/profile/role?id={profile['id']}' class='form custom-role-form'>
        <label>Кастомная роль<input name='custom_role' value='{esc(profile['custom_role'] or '')}' maxlength='32' placeholder='Например: caster'></label>
        <button class='btn tiny'>Сохранить роль</button>
    </form>
    """


def profile_edit_form(user):
    color = profile_color(user)
    avatar_history = profile_media_history(user["id"], "avatar", user["avatar_url"])
    banner_history = profile_media_history(user["id"], "banner", user["banner_url"])
    return f"""
    <section class='panel profile-edit'>
        <h1>Редактирование профиля</h1>
        <form method='post' action='/profile/edit' enctype='multipart/form-data' class='form grid-form'>
            <label>Ник<input name='nickname' value='{esc(user['nickname'] or user['login'])}' maxlength='40'></label>
            <label>Цвет профиля<input type='color' name='profile_color' value='{esc(color)}'></label>
            <label class='wide'>Аватарка GIF/PNG/JPG/WEBP<input type='file' name='avatar' accept='image/gif,image/png,image/jpeg,image/webp'></label>
            <div class='wide'><h3>Последние аватарки</h3>{avatar_history or '<p class="muted">Пока пусто.</p>'}</div>
            <label class='wide'>Баннер GIF/PNG/JPG/WEBP<input type='file' name='banner' accept='image/gif,image/png,image/jpeg,image/webp'></label>
            <div class='wide'><h3>Последние баннеры</h3>{banner_history or '<p class="muted">Пока пусто.</p>'}</div>
            <label class='wide'>Обо мне<textarea name='about' maxlength='1200'>{esc(user['about'] or '')}</textarea></label>
            <div class='form-actions wide'><a class='btn' href='/profile?id={user['id']}'>Отмена</a><button class='btn primary'>Сохранить профиль</button></div>
        </form>
    </section>
    """


def auth_form(title, action, button, include_name):
    name = "<label>ФИО <span class='hint'>не обязательно</span><input name='full_name' placeholder='Иванов Иван Иванович'></label>" if include_name else ""
    repeat = "<label>Повторите пароль <input type='password' name='password2' required></label>" if include_name else ""
    switch = "<a href='/login'>Войти в систему</a>" if include_name else "<a href='/register'>Зарегистрироваться</a>"
    return f"<section class='auth-card'><h1>{title}</h1><form method='post' action='{action}' class='form'><label>Логин<input name='login' required placeholder='Введите логин'></label>{name}<label>Пароль<input type='password' name='password' required placeholder='Введите пароль'></label>{repeat}<button class='btn primary'>{button}</button></form><p>Уже есть аккаунт?</p>{switch}</section>"


def disciplines_options(selected=None):
    with db() as conn:
        items = conn.execute("SELECT * FROM disciplines ORDER BY name").fetchall()
    return "".join(f"<option value='{d['id']}' data-game='{esc(d['tag'].upper())}' {'selected' if str(selected)==str(d['id']) else ''}>{esc(d['name'])}</option>" for d in items)


def collect_selected_maps(data, discipline_id=None):
    selected = data.get("maps[]", [])
    if not selected:
        selected = [value for key, value in data.items() if key.startswith("map_") and not key.endswith("[]")]
    allowed = set(game_maps_for_discipline(discipline_id))
    if allowed:
        selected = [item for item in selected if item in allowed]
    return ", ".join(selected)


def game_tag_for_discipline(discipline_id):
    if not discipline_id:
        return "CS2"
    with db() as conn:
        row = conn.execute("SELECT tag FROM disciplines WHERE id=?", (discipline_id,)).fetchone()
    return (row["tag"] if row else "CS2").upper()


def game_maps_for_discipline(discipline_id):
    if discipline_id:
        with db() as conn:
            row = conn.execute("SELECT tag, map_pool FROM disciplines WHERE id=?", (discipline_id,)).fetchone()
        if row and row["map_pool"]:
            return split_csv(row["map_pool"])
        if row:
            return GAME_MAPS.get(row["tag"].upper(), [])
    return GAME_MAPS.get(game_tag_for_discipline(discipline_id), [])


def split_csv(text):
    return [p.strip() for p in (text or "").replace("\n", ",").split(",") if p.strip()]


def best_of_value(format_text):
    text = (format_text or "").upper()
    for value in (7, 5, 3, 2, 1):
        if f"BO{value}" in text or f"BEST OF {value}" in text:
            return value
    return 1


def ensure_matches(conn, tournament_id):
    existing = conn.execute("SELECT COUNT(*) c FROM matches WHERE tournament_id=?", (tournament_id,)).fetchone()["c"]
    if existing:
        return
    regs = conn.execute("SELECT team_id FROM registrations WHERE tournament_id = ? ORDER BY id", (tournament_id,)).fetchall()
    for i in range(0, len(regs), 2):
        conn.execute(
            "INSERT INTO matches(tournament_id, round, team1_id, team2_id) VALUES(?,?,?,?)",
            (tournament_id, 1, regs[i]["team_id"], regs[i + 1]["team_id"] if i + 1 < len(regs) else None),
        )


def rebuild_bracket_rounds(conn, tournament_id):
    round_no = 1
    while True:
        matches = conn.execute(
            "SELECT * FROM matches WHERE tournament_id=? AND round=? ORDER BY id",
            (tournament_id, round_no),
        ).fetchall()
        if not matches:
            break
        winners = [m["winner_id"] for m in matches if m["winner_id"]]
        if len(winners) < 2:
            break
        next_round = round_no + 1
        existing = conn.execute(
            "SELECT COUNT(*) c FROM matches WHERE tournament_id=? AND round=?",
            (tournament_id, next_round),
        ).fetchone()["c"]
        if existing:
            round_no += 1
            continue
        for i in range(0, len(winners), 2):
            conn.execute(
                "INSERT INTO matches(tournament_id, round, team1_id, team2_id) VALUES(?,?,?,?)",
                (tournament_id, next_round, winners[i], winners[i + 1] if i + 1 < len(winners) else None),
            )
        round_no += 1


def rating_teams(conn, discipline_id="", limit=5):
    teams = conn.execute(
        """
        SELECT teams.id, teams.name, d.tag discipline
        FROM teams LEFT JOIN disciplines d ON d.id=teams.discipline_id
        {where}
        """.format(where="WHERE teams.discipline_id=?" if discipline_id else ""),
        (discipline_id,) if discipline_id else (),
    ).fetchall()
    points = {team["id"]: 0 for team in teams}
    for match in conn.execute("SELECT * FROM matches WHERE team1_id IS NOT NULL AND team2_id IS NOT NULL").fetchall():
        if match["winner_id"]:
            loser = match["team2_id"] if match["winner_id"] == match["team1_id"] else match["team1_id"]
            points[match["winner_id"]] = points.get(match["winner_id"], 0) + 3
            points[loser] = points.get(loser, 0) + 1
        elif match["score1"] not in ("-", None) or match["score2"] not in ("-", None):
            points[match["team1_id"]] = points.get(match["team1_id"], 0) + 2
            points[match["team2_id"]] = points.get(match["team2_id"], 0) + 2
    rows = [dict(team) | {"points": points.get(team["id"], 0)} for team in teams]
    rows = sorted(rows, key=lambda r: (-r["points"], r["name"]))
    return rows[:limit] if limit else rows


def rating_players(conn, discipline_id=""):
    teams = rating_teams(conn, discipline_id, limit=None)
    team_points = {team["id"]: team["points"] for team in teams}
    rows = []
    for row in conn.execute(
        """
        SELECT u.login, tm.team_id FROM users u
        JOIN team_members tm ON tm.user_id=u.id
        ORDER BY u.login
        """
    ).fetchall():
        rows.append({"login": row["login"], "points": team_points.get(row["team_id"], 0)})
    return sorted(rows, key=lambda r: (-r["points"], r["login"]))[:8]


def veto_progress(tournament, match, actions):
    maps = split_csv(tournament["maps"])
    used = {action["map_name"] for action in actions}
    remaining = [map_name for map_name in maps if map_name not in used]
    picked = [action["map_name"] for action in actions if action["action"] == "PICK"]
    target = min(best_of_value(tournament["format"]), len(maps)) or 1
    teams = [match["team1_id"], match["team2_id"]]
    teams = [team_id for team_id in teams if team_id]
    if len(teams) < 2:
        return {
            "complete": True,
            "message": "Для бан-пика нужны две команды в матче.",
            "remaining": remaining,
            "picked": picked,
            "used": used,
        }
    if len(remaining) <= max(1, target - len(picked)):
        return {
            "complete": True,
            "message": "Veto завершен. Итоговые карты: " + ", ".join(picked + remaining),
            "remaining": remaining,
            "picked": picked,
            "used": used,
        }
    if target == 1:
        step = len(actions)
        return {"complete": False, "action": "BAN", "team_id": teams[step % 2], "remaining": remaining, "picked": picked, "used": used}
    pre_bans = max(0, len(maps) - target - min(2, target))
    step = len(actions)
    if step < pre_bans:
        first_team_bans = (pre_bans + 1) // 2
        team_id = teams[0] if step < first_team_bans else teams[1]
        return {"complete": False, "action": "BAN", "team_id": team_id, "remaining": remaining, "picked": picked, "used": used}
    pick_step = step - pre_bans
    picks_needed = max(0, target - 1)
    if len(picked) < picks_needed:
        return {"complete": False, "action": "PICK", "team_id": teams[pick_step % 2], "remaining": remaining, "picked": picked, "used": used}
    ban_step = step - pre_bans - picks_needed
    return {"complete": False, "action": "BAN", "team_id": teams[ban_step % 2], "remaining": remaining, "picked": picked, "used": used}


def map_pool_controls(selected_text="", selected_discipline=None):
    selected = set(split_csv(selected_text))
    groups = []
    with db() as conn:
        disciplines = conn.execute("SELECT * FROM disciplines ORDER BY name").fetchall()
    for discipline in disciplines:
        tag = discipline["tag"].upper()
        maps = split_csv(discipline["map_pool"]) if discipline["map_pool"] else GAME_MAPS.get(tag, [])
        if not maps:
            continue
        checks = "".join(
            f"<label class='check option-check'><input type='checkbox' name='maps' value='{esc(map_name)}' {'checked' if map_name in selected else ''}> {esc(map_name)}</label>"
            for map_name in maps
        )
        active = "active" if tag == game_tag_for_discipline(selected_discipline) else ""
        groups.append(f"<fieldset class='option-group map-group {active}' data-game='{esc(tag)}'><legend>{esc(tag)}</legend>{checks}</fieldset>")
    return "<div class='choice-grid'>" + "".join(groups) + "</div>"


def rules_options(selected="", selected_discipline=None):
    active_tag = game_tag_for_discipline(selected_discipline)
    options = []
    with db() as conn:
        disciplines = conn.execute("SELECT * FROM disciplines ORDER BY name").fetchall()
    for discipline in disciplines:
        tag = discipline["tag"].upper()
        rules = split_csv(discipline["rule_presets"]) if discipline["rule_presets"] else RULE_PRESETS.get(tag, [])
        if not rules:
            continue
        inner = "".join(f"<option value='{esc(rule)}' {'selected' if selected == rule else ''}>{esc(rule[:96])}</option>" for rule in rules)
        active = "data-active='1'" if tag == active_tag else ""
        options.append(f"<optgroup label='{esc(tag)}' data-game='{esc(tag)}' {active}>{inner}</optgroup>")
    fallback = "".join(f"<option value='{esc(rule)}' {'selected' if selected == rule else ''}>{esc(rule)}</option>" for rule in DEFAULT_RULES)
    return "".join(options) + f"<optgroup label='Общие' data-game='DEFAULT'>{fallback}</optgroup>"


def tournament_form_html(action, t=None):
    t = dict(t) if t else {}
    checked = "checked" if t.get("is_private") else ""
    status = f"<label>Статус<select name='status'><option {'selected' if t.get('status')=='DRAFT' else ''}>DRAFT</option><option {'selected' if t.get('status')=='ONGOING' else ''}>ONGOING</option><option {'selected' if t.get('status')=='FINISHED' else ''}>FINISHED</option></select></label>" if t else ""
    return f"""
    <section class="panel narrow"><h1>Создание нового турнира</h1>
    <form method="post" action="{action}" class="form grid-form">
        <label class="wide">Название турнира<input name="title" required value="{esc(t.get('title',''))}" placeholder="Например: Матч Поинт Cup CS2 #3"></label>
        <label>Игровая дисциплина<select name="discipline_id" data-discipline-select>{disciplines_options(t.get('discipline_id'))}</select></label>
        <label>Формат<select name="format"><option>BO1</option><option>BO2</option><option>BO3</option><option>BO5</option><option>BO7</option><option>Олимпийская система</option><option>Круговая система</option><option>Группы + плей-офф</option></select></label>
        <label>Максимум команд<input type="number" min="2" name="max_teams" value="{esc(t.get('max_teams',4))}"></label>
        <label>Дата начала<input type="date" name="start_date" value="{esc(t.get('start_date',''))}"></label>
        {status}
        <label class="wide">Описание турнира<textarea name="description">{esc(t.get('description',''))}</textarea></label>
        <label class="wide check"><input type="checkbox" name="is_private" {checked}> Приватный турнир</label>
        <label class="wide">Пароль приватного турнира<input name="private_code" value="{esc(t.get('private_code',''))}"></label>
        <div class="wide"><h3>Пул карт</h3><p class="note">Выберите игру выше, и здесь останется только пул карт этой дисциплины.</p>{map_pool_controls(t.get('maps',''), t.get('discipline_id'))}</div>
        <label class="wide">Правила<select name="rules" data-rules-select>{rules_options(t.get('rules',''), t.get('discipline_id'))}</select></label>
        <label class="wide">Описание системы банов / пиков<textarea name="bans">{esc(t.get('bans','Faceit veto: капитаны по очереди банят и выбирают карты перед стартом матча.'))}</textarea></label>
        <div class="form-actions wide"><a class="btn" href="/tournaments">Отмена</a><button class="btn primary">Сохранить турнир</button></div>
    </form></section>"""


def team_form_html(action, team=None):
    team = dict(team) if team else {}
    key_value = team.get("join_key", "Сгенерируется автоматически") if team else "Сгенерируется автоматически"
    key_readonly = "" if team else "readonly"
    password_placeholder = "Оставьте пустым, чтобы не менять" if team else "Можно оставить пустым"
    return f"""
    <section class="panel narrow"><h1>{'Редактирование команды' if team else 'Создание команды'}</h1>
    <form method="post" action="{action}" enctype="multipart/form-data" class="form grid-form">
        <label>Название команды<input name="name" required value="{esc(team.get('name',''))}"></label>
        <label>Тег<input name="tag" required value="{esc(team.get('tag',''))}"></label>
        <label class="wide">Дисциплина<select name="discipline_id">{disciplines_options(team.get('discipline_id'))}</select></label>
        <label class="wide">Логотип команды GIF/PNG/JPG/WEBP<input type="file" name="logo" accept="image/gif,image/png,image/jpeg,image/webp"></label>
        <label class="wide">Описание<textarea name="description">{esc(team.get('description',''))}</textarea></label>
        <label>Пароль для вступления<input name="join_password" value="" placeholder="{password_placeholder}"></label>
        <label>Ключ вступления<input name="join_key" value="{esc(key_value)}" {key_readonly}></label>
        <div class="form-actions wide"><a class="btn" href="/dashboard">Отмена</a><button class="btn primary">Сохранить</button></div>
    </form></section>"""

def tournament_card(t):
    count = t["reg_count"] if "reg_count" in t.keys() else "0"
    return f"<article class='card'><span class='badge'>{esc(t['discipline'])}</span><h3>{esc(t['title'])}</h3><p>{esc(t['format'])}</p><p>{count}/{t['max_teams']} команд · {esc(t['status'])}</p><a class='btn' href='/tournament?id={t['id']}'>Подробнее</a></article>"


def team_card(team):
    return f"<div class='team-summary'><h3>{esc(team['name'])} <span class='badge'>{esc(team['tag'])}</span></h3><p><b>Дисциплина:</b> {esc(team['discipline'])}</p><p><b>Владелец:</b> {esc(team['captain'])}</p><p><b>Ваша роль:</b> {esc(team['team_role'])}</p><a class='btn' href='/team?id={team['id']}'>Перейти к команде</a></div>"


def message_html(m):
    media = ""
    url = m["media_url"] if "media_url" in m.keys() else ""
    if url:
        if any(url.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
            media = f"<img class='chat-media' src='{esc(url)}' alt='media'>"
        else:
            media = f"<a class='chat-media-link' href='{esc(url)}' target='_blank'>Медиа</a>"
    emoji = f"<span class='chat-emoji'>{esc(m['emoji'])}</span>" if "emoji" in m.keys() and m["emoji"] else ""
    return f"<div class='message'><b>{esc(m['login'])}</b><time>{esc(m['created_at'])}</time><p>{emoji} {esc(m['body'])}</p>{media}</div>"


def tournament_chat_form(tid):
    return f"""
    <form class="chat-form rich-chat" method="post" action="/tournament/message?id={tid}">
        <select name="emoji"><option value="">☺</option><option>🔥</option><option>💀</option><option>🏆</option><option>😎</option><option>❤️</option></select>
        <input name="body" placeholder="Сообщение...">
        <input name="media_url" placeholder="Ссылка на фото/GIF">
        <button class="icon-btn">➤</button>
    </form>
    """


def global_chat_form():
    return """
    <form class="chat-form rich-chat" method="post" action="/chat/global">
        <select name="emoji"><option value="">☺</option><option>🔥</option><option>💀</option><option>🏆</option><option>😎</option><option>❤️</option></select>
        <input name="body" placeholder="Общий чат...">
        <input name="media_url" placeholder="Ссылка на фото/GIF">
        <button class="icon-btn">➤</button>
    </form>
    """


def chat_shell(title, messages, form, feed_url):
    return f"""
    <section class="panel chat-shell" data-chat-feed="{esc(feed_url)}">
        <h2>{title}</h2>
        <div class="chat-messages" data-chat-messages>{messages}</div>
        {form}
    </section>
    """


def extract_media_urls(text):
    urls = re.findall(r"https?://[^\s<>'\"]+", text or "")
    return [url.rstrip(").,!?") for url in urls]


def is_media_url(url):
    clean = url.split("?", 1)[0].lower()
    return any(clean.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"])


def message_html(m, user=None, scope="tournament", back="/"):
    body = m["body"] or ""
    urls = []
    if "media_url" in m.keys() and m["media_url"]:
        urls.append(m["media_url"])
    urls.extend(extract_media_urls(body))
    media = ""
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        if is_media_url(url):
            media += f"<img class='chat-media' src='{esc(url)}' alt='media' loading='lazy'>"
        else:
            media += f"<a class='chat-media-link' href='{esc(url)}' target='_blank'>{esc(url)}</a>"
    emoji = f"<span class='chat-emoji'>{esc(m['emoji'])}</span>" if "emoji" in m.keys() and m["emoji"] else ""
    can_delete = user and (is_staff(user) or m["user_id"] == user["id"])
    back_url = quote(back, safe="/:")
    delete = f"<a class='message-delete' href='/message/delete?scope={esc(scope)}&id={m['id']}&back={esc(back_url)}'>Удалить</a>" if can_delete else ""
    author = esc(m["nickname"] or m["login"]) if "nickname" in m.keys() else esc(m["login"])
    avatar = m["avatar_url"] if "avatar_url" in m.keys() and m["avatar_url"] else "/static/matchpoint-logo-mark.png"
    return f"<div class='message' data-message-id='{m['id']}'><a class='chat-avatar' href='/profile?id={m['user_id']}'><img src='{esc(avatar)}' alt=''></a><b><a href='/profile?id={m['user_id']}'>{author}</a></b><time>{esc(m['created_at'])}</time>{delete}<p>{emoji} {esc(body)}</p>{media}</div>"


EMOJI_POOL = ["🔥", "💀", "🏆", "😎", "❤️", "😂", "👍", "😡", "👀", "⚡", "🎯", "🥶", "🤝", "🚀", "💪", "🤯", "🎮", "✅"]


def emoji_buttons():
    buttons = "".join(f"<button type='button' class='emoji-btn' data-emoji='{esc(item)}'>{esc(item)}</button>" for item in EMOJI_POOL)
    return f"<div class='emoji-bar' data-emoji-panel hidden>{buttons}</div>"


def tournament_chat_form(tid):
    return f"""
    <form class="chat-form rich-chat" method="post" action="/tournament/message?id={tid}" data-chat-form>
        <input type="hidden" name="emoji" data-emoji-input>
        {emoji_buttons()}
        <div class="chat-compose"><button type="button" class="icon-btn emoji-toggle" data-emoji-toggle>☺</button><input name="body" placeholder="Сообщение, ссылка на фото или GIF..."><button class="icon-btn">➤</button></div>
    </form>
    """


def global_chat_form():
    return f"""
    <form class="chat-form rich-chat" method="post" action="/chat/global" data-chat-form>
        <input type="hidden" name="emoji" data-emoji-input>
        {emoji_buttons()}
        <div class="chat-compose"><button type="button" class="icon-btn emoji-toggle" data-emoji-toggle>☺</button><input name="body" placeholder="Общий чат, ссылка на фото или GIF..."><button class="icon-btn">➤</button></div>
    </form>
    """


def render_bracket(t, matches, latest_scores, user):
    rounds = {}
    for match in matches:
        rounds.setdefault(match["round"], []).append(match)
    columns = []
    for round_no in sorted(rounds):
        title = "Финал" if len(rounds[round_no]) == 1 and round_no > 1 else f"Раунд {round_no}"
        columns.append(
            f"<section class='bracket-round'><h2>{title}</h2>{''.join(match_card(t, m, latest_scores.get(m['id']), user) for m in rounds[round_no])}</section>"
        )
    final_match = None
    for match in matches:
        if match["winner_id"]:
            final_match = match
    final_name = final_match["winner_name"] if final_match and final_match["round"] == max(rounds or {1: []}) else "Победитель турнира"
    return f"""
    <div class="olympic-bracket">
        {''.join(columns)}
        <section class="bracket-final gold-bless"><h2>ЧЕМПИОН</h2><div class="final-cup">{esc(final_name)}</div></section>
    </div>
    """


def match_card(t, m, score, user):
    winner_class1 = "winner-bless" if m["winner_id"] == m["team1_id"] else ""
    winner_class2 = "winner-bless" if m["winner_id"] == m["team2_id"] else ""
    score_text = f"<p class='match-note'>{esc(score['map_scores'])} · {esc(score['status'])}</p>" if score else ""
    form = match_score_form(m, user)
    return f"""
    <article class="match bracket-match">
        <div class="match-team {winner_class1}"><b>{esc(m['team1'] or 'Свободный слот')}</b><span>{esc(m['score1'])}</span></div>
        <div class="match-team {winner_class2}"><b>{esc(m['team2'] or 'BYE')}</b><span>{esc(m['score2'])}</span></div>
        <small>Раунд {m['round']}</small>
        {score_text}
        <a class="btn tiny" href="/veto?id={t['id']}&match_id={m['id']}">Veto</a>
        {form}
    </article>
    """


def match_score_form(m, user):
    if not user:
        return ""
    options = ""
    if m["team1_id"]:
        options += f"<option value='{m['team1_id']}'>{esc(m['team1'])}</option>"
    if m["team2_id"]:
        options += f"<option value='{m['team2_id']}'>{esc(m['team2'])}</option>"
    return f"""
    <details class="score-details"><summary>Счет</summary>
        <form method="post" action="/match/score?match_id={m['id']}" class="score-form">
            <input name="map1" placeholder="1 карта 11:13">
            <input name="map2" placeholder="2 карта 13:7">
            <input name="map3" placeholder="3 карта 4:13">
            <select name="winner_id"><option value="">Победитель</option>{options}</select>
            <button class="btn tiny">Сохранить</button>
        </form>
    </details>
    """


def team_members_for_veto(conn, team_id):
    if not team_id:
        return []
    return conn.execute(
        "SELECT tm.team_role, u.login FROM team_members tm JOIN users u ON u.id=tm.user_id WHERE tm.team_id=? ORDER BY tm.team_role='Капитан' DESC, u.login",
        (team_id,),
    ).fetchall()


def veto_team_panel(name, members, active):
    rows = "".join(f"<div class='veto-player {'captain' if m['team_role']=='Капитан' else ''}'><span>{esc(m['login'])}</span><small>{esc(m['team_role'])}</small></div>" for m in members)
    return f"<aside class='veto-team {'active' if active else ''}'><h2>{esc(name)}</h2>{rows}</aside>"


def veto_map_card(tid, match_id, map_name, used_by_map, progress, can_vote):
    action = used_by_map.get(map_name)
    cls = ""
    label = ""
    if action:
        cls = "map-ban" if action["action"] == "BAN" else "map-pick"
        label = action["action"]
    elif progress.get("complete") and map_name in progress.get("remaining", []):
        cls = "map-decider gold-bless"
        label = "DECIDER"
    buttons = ""
    if can_vote and map_name in progress.get("remaining", []):
        buttons = f"<form method='post' action='/veto?id={tid}&match_id={match_id}'><input type='hidden' name='action' value='{esc(progress['action'])}'><input type='hidden' name='map_name' value='{esc(map_name)}'><button class='btn tiny primary'>{esc(progress['action'])}</button></form>"
    return f"<article class='veto-map {cls}'><div class='map-thumb'>{esc(map_name[:2].upper())}</div><b>{esc(map_name)}</b><span>{label}</span>{buttons}</article>"


def team_search_card(team, user, my_team):
    locked = "Нужен пароль/ключ" if team["join_password"] or team["join_key"] else "Открытый вход"
    action = f"<a class='btn' href='/team?id={team['id']}'>Открыть</a>"
    if user and not my_team:
        action += join_team_form(team["id"])
    return f"<article class='card'><span class='badge'>{esc(team['discipline'])}</span><h3>{esc(team['name'])}</h3><p>{esc(team['tag'])} · владелец {esc(team['captain'])}</p><p>{team['members']} участников · {locked}</p>{action}</article>"


def join_team_form(team_id):
    return f"""
    <form method="post" action="/team/join?id={team_id}" class="form join-form">
        <input name="join_secret" placeholder="Пароль или ключ команды">
        <button class="btn primary">Вступить в команду</button>
    </form>
    """


def finished_winner_banner(tournament_id):
    with db() as conn:
        winner = conn.execute(
            """
            SELECT teams.name FROM matches
            JOIN teams ON teams.id = matches.winner_id
            WHERE matches.tournament_id=? AND matches.winner_id IS NOT NULL
            ORDER BY matches.round DESC, matches.id DESC LIMIT 1
            """,
            (tournament_id,),
        ).fetchone()
    if not winner:
        return "<section class='winner-banner sky-bless'>Турнир завершен. Победитель еще не указан.</section>"
    return f"<section class='winner-banner gold-bless'>Поздравляем, победитель турнира команда {esc(winner['name'])}</section>"


def registration_form(t, my_teams, regs):
    if t["status"] == "FINISHED":
        return "<p class='muted'>Турнир завершен, регистрация закрыта.</p>"
    registered_ids = {r["team_id"] for r in regs}
    allowed_roles = {"Капитан", "Тренер", "Менеджер"}
    options = "".join(f"<option value='{team['id']}'>{esc(team['name'])}</option>" for team in my_teams if team["id"] not in registered_ids and team["team_role"] in allowed_roles)
    if not options:
        return "<p class='muted'>Нет доступной команды: регистрацию может отправить владелец, тренер или менеджер.</p><a class='btn' href='/team/new'>Создать команду</a>"
    code = "<input name='private_code' placeholder='Код турнира'>" if t["is_private"] else ""
    return f"<form method='post' action='/tournament/register?id={t['id']}' class='form'><select name='team_id'>{options}</select>{code}<button class='btn primary'>Зарегистрировать команду</button></form>"


def tournament_unregister_controls(t, my_teams, regs):
    registered_ids = {r["team_id"] for r in regs}
    allowed_roles = {"Капитан", "Тренер", "Менеджер"}
    buttons = []
    for team in my_teams:
        if team["id"] in registered_ids and team["team_role"] in allowed_roles:
            buttons.append(
                f"<form method='post' action='/tournament/unregister?id={t['id']}' class='form mini-form'>"
                f"<input type='hidden' name='team_id' value='{team['id']}'>"
                f"<button class='btn danger-btn'>Снять {esc(team['name'])} с турнира</button></form>"
            )
    return "".join(buttons)


def member_row(team, member, can_edit):
    role = esc(member["team_role"])
    form = f"<div class='member-role'><span>Роль</span><b>{role}</b></div>"
    kick = ""
    if can_edit:
        options = "".join(f"<option {'selected' if member['team_role']==value else ''}>{value}</option>" for value in ["Игрок", "Капитан", "Тренер", "Менеджер", "Запасной"])
        form = f"<form method='post' action='/team/role' class='role-form'><input type='hidden' name='team_id' value='{team['id']}'><input type='hidden' name='user_id' value='{member['user_id']}'><label>Роль<select name='team_role'>{options}</select></label><button class='btn tiny'>Назначить</button></form>"
        if member["user_id"] != team["captain_id"]:
            kick = f"<a class='btn tiny danger-btn' href='/team/kick?id={team['id']}&user_id={member['user_id']}'>Кикнуть</a>"
    return f"<div class='member'><div class='member-nick'><span>Ник</span><b><a href='/profile?id={member['user_id']}'>{esc(member['login'])}</a></b></div>{form}{kick}</div>"


def chips(text):
    parts = [p.strip() for p in (text or "").replace("\n", ",").split(",") if p.strip()]
    return "".join(f"<span>{esc(p)}</span>" for p in parts) or "<span>Не указаны</span>"


def table(headers, rows):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    return f"<section class='panel table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></section>"


def _wsgi_headers(environ):
    headers = Message()
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    if environ.get("CONTENT_LENGTH"):
        headers["Content-Length"] = environ["CONTENT_LENGTH"]
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            headers[name] = value
    return headers


def application(environ, start_response):
    init_db()
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    query = environ.get("QUERY_STRING", "")
    if query:
        path = f"{path}?{query}"

    length = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(length) if length else b""

    handler = App.__new__(App)
    handler.command = method
    handler.path = path
    handler.headers = _wsgi_headers(environ)
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler._wsgi_status = 200
    handler._wsgi_headers = []

    def send_response(code, message=None):
        handler._wsgi_status = code

    def send_header(key, value):
        handler._wsgi_headers.append((key, str(value)))

    def end_headers():
        return None

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = end_headers
    handler.route()

    reason = HTTPStatus(handler._wsgi_status).phrase
    start_response(f"{handler._wsgi_status} {reason}", handler._wsgi_headers)
    return [handler.wfile.getvalue()]


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Матч Поинт запущен: http://{host}:{port}")
    HTTPServer((host, port), App).serve_forever()
