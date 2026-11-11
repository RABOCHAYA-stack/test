#!/usr/bin/env python3
"""MVP системы обратной связи и предложений сотрудников (ГК «Модус Трейд»).

Запуск:  python app.py
Открыть: http://127.0.0.1:8000

Только стандартная библиотека Python + SQLite. Внешние сервисы не нужны.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import storage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.environ.get("FEEDBACK_DB", os.path.join(BASE_DIR, "data", "feedback.db"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

MAX_BODY = 5 * 1024 * 1024
MAX_ATTACHMENT = 2 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".txt"}
SESSION_HOURS = 12


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_iso() -> str:
    return storage.iso(now())


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def db() -> sqlite3.Connection:
    conn = storage.connect(DB_PATH)
    return conn


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def validate_responsible(conn: sqlite3.Connection, responsible_id) -> None:
    if responsible_id is None:
        return
    if not isinstance(responsible_id, int):
        raise ApiError(400, "Выберите ответственного")
    row = conn.execute("SELECT role FROM users WHERE id = ? AND active = 1", (responsible_id,)).fetchone()
    if not row:
        raise ApiError(400, "Сотрудник не найден")
    if row["role"] not in ("manager", "admin"):
        raise ApiError(400, "Ответственным может быть руководитель или администратор")


def token_hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_final(status: str) -> bool:
    return status in storage.FINAL_STATUSES


def csv_safe(value: str | None) -> str:
    """Защита от CSV-инъекций в Excel."""
    text = str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


class Handler(BaseHTTPRequestHandler):
    server_version = "FeedbackMVP/1.0"
    protocol_version = "HTTP/1.0"

    # ---------- инфраструктура ----------

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, payload, status: int = 200, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def error(self, status: int, message: str) -> None:
        self.json({"error": message}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ApiError(413, "Слишком большой запрос")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Некорректный JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "Ожидается JSON-объект")
        return data

    # ---------- HTTP ----------

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_HEAD(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def do_PATCH(self) -> None:
        self.handle_request("PATCH")

    def do_DELETE(self) -> None:
        self.handle_request("DELETE")

    def handle_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/"):
                conn = db()
                try:
                    self.api(conn, method, path, parse_qs(parsed.query))
                finally:
                    conn.close()
                return
            if method != "GET":
                self.error(405, "Метод не поддерживается")
                return
            self.serve_static(path)
        except ApiError as exc:
            self.error(exc.status, exc.message)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - последний рубеж для демо
            self.log_message("Unhandled error: %r", exc)
            self.error(500, "Внутренняя ошибка сервера")

    # ---------- статика ----------

    def serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        rel = path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep):
            self.error(403, "Доступ запрещён")
            return
        if not os.path.isfile(full):
            self.error(404, "Файл не найден")
            return
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as fh:
            body = fh.read()
        self._send(200, body, (ctype or "application/octet-stream") + ("; charset=utf-8" if (ctype or "").startswith("text/") else ""))

    # ---------- API ----------

    def api(self, conn: sqlite3.Connection, method: str, path: str, query: dict) -> None:
        user = self.current_user(conn)

        if path == "/api/login" and method == "POST":
            return self.login(conn)
        if path == "/api/logout" and method == "POST":
            return self.logout(conn)
        if path == "/api/me" and method == "GET":
            if not user:
                raise ApiError(401, "Требуется вход")
            return self.json({"user": self.public_user(user)})

        if not user:
            raise ApiError(401, "Требуется вход")

        if path == "/api/meta" and method == "GET":
            return self.meta(conn, user)
        if path == "/api/appeals" and method == "GET":
            return self.list_appeals(conn, user, query)
        if path == "/api/appeals" and method == "POST":
            return self.create_appeal(conn, user)
        if path == "/api/dashboard" and method == "GET":
            self.require_manager(user)
            return self.dashboard(conn, user)
        if path == "/api/export.csv" and method == "GET":
            self.require_manager(user)
            return self.export_csv(conn, user, query)
        if path == "/api/admin/categories" and method == "GET":
            self.require_manager(user)
            return self.list_categories(conn)
        if path == "/api/admin/categories" and method == "POST":
            self.require_admin(user)
            return self.create_category(conn, user)
        if path == "/api/users" and method == "GET":
            self.require_manager(user)
            return self.list_users(conn)

        match = re.fullmatch(r"/api/appeals/(\d+)", path)
        if match and method == "GET":
            return self.get_appeal(conn, user, int(match.group(1)))
        match = re.fullmatch(r"/api/appeals/(\d+)/comments", path)
        if match and method == "POST":
            return self.add_comment(conn, user, int(match.group(1)))
        match = re.fullmatch(r"/api/appeals/(\d+)/status", path)
        if match and method == "POST":
            return self.change_status(conn, user, int(match.group(1)))
        match = re.fullmatch(r"/api/appeals/(\d+)/assign", path)
        if match and method == "POST":
            return self.assign(conn, user, int(match.group(1)))
        match = re.fullmatch(r"/api/appeals/(\d+)/take", path)
        if match and method == "POST":
            return self.take(conn, user, int(match.group(1)))
        match = re.fullmatch(r"/api/appeals/(\d+)/attachment", path)
        if match and method == "GET":
            return self.download_attachment(conn, user, int(match.group(1)))
        match = re.fullmatch(r"/api/admin/categories/(\d+)", path)
        if match and method == "PATCH":
            self.require_admin(user)
            return self.update_category(conn, user, int(match.group(1)))

        raise ApiError(404, "Эндпоинт не найден")

    # ---------- аутентификация ----------

    def current_user(self, conn: sqlite3.Connection) -> dict | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:  # noqa: BLE001
            return None
        morsel = cookie.get("sid")
        if not morsel:
            return None
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
            (token_hash(morsel.value), now_iso()),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def public_user(user: dict) -> dict:
        return {
            "id": user["id"],
            "login": user["login"],
            "name": user["name"],
            "role": user["role"],
            "position": user["position"],
            "department": user["department"],
        }

    def login(self, conn: sqlite3.Connection) -> None:
        data = self.read_json()
        login = str(data.get("login", "")).strip().lower()
        password = str(data.get("password", ""))
        row = conn.execute("SELECT * FROM users WHERE login = ? AND active = 1", (login,)).fetchone()
        if not row or not storage.verify_password(password, row["salt"], row["password_hash"]):
            raise ApiError(401, "Неверный логин или пароль")
        token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
        expires = now() + timedelta(hours=SESSION_HOURS)
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash(token), row["id"], storage.iso(expires)),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
        conn.commit()
        cookie = f"sid={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_HOURS * 3600}"
        self.json({"user": self.public_user(dict(row))}, extra={"Set-Cookie": cookie})

    def logout(self, conn: sqlite3.Connection) -> None:
        raw = self.headers.get("Cookie")
        if raw:
            cookie = SimpleCookie()
            try:
                cookie.load(raw)
                morsel = cookie.get("sid")
                if morsel:
                    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(morsel.value),))
                    conn.commit()
            except Exception:  # noqa: BLE001
                pass
        self.json({"ok": True}, extra={"Set-Cookie": "sid=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax"})

    # ---------- права ----------

    @staticmethod
    def require_manager(user: dict) -> None:
        if user["role"] not in ("manager", "admin"):
            raise ApiError(403, "Недостаточно прав")

    @staticmethod
    def require_admin(user: dict) -> None:
        if user["role"] != "admin":
            raise ApiError(403, "Требуются права администратора")

    @staticmethod
    def can_manage(user: dict, appeal: dict) -> bool:
        return user["role"] == "admin" or (
            user["role"] == "manager" and appeal.get("assignee_id") == user["id"]
        )

    @staticmethod
    def can_view_internal(user: dict) -> bool:
        return user["role"] in ("manager", "admin")

    # ---------- сериализация ----------

    def appeal_dict(self, conn: sqlite3.Connection, row: sqlite3.Row | dict, user: dict) -> dict:
        item = dict(row)
        anonymous_hidden = bool(item["anonymous"]) and user["role"] != "admin" and user["id"] != item["author_id"]
        comments_count = conn.execute(
            "SELECT COUNT(*) AS n FROM comments WHERE appeal_id = ?" + ("" if self.can_view_internal(user) else " AND internal = 0"),
            (item["id"],),
        ).fetchone()["n"]
        due = parse_dt(item["due_at"])
        overdue = bool(due and due < now() and not is_final(item["status"]))
        return {
            "id": item["id"],
            "code": item["code"],
            "type": item["type"],
            "title": item["title"],
            "description": item["description"],
            "expected_solution": item["expected_solution"],
            "priority": item["priority"],
            "status": item["status"],
            "category": row["category_name"] if "category_name" in row.keys() else None,
            "category_id": item["category_id"],
            "author": "Анонимный сотрудник" if anonymous_hidden else (row["author_name"] if "author_name" in row.keys() else None),
            "author_department": "—" if anonymous_hidden else (row["author_department"] if "author_department" in row.keys() else None),
            "author_id": None if anonymous_hidden else item["author_id"],
            "anonymous": bool(item["anonymous"]),
            "assignee": row["assignee_name"] if "assignee_name" in row.keys() else None,
            "assignee_id": item["assignee_id"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "due_at": item["due_at"],
            "first_response_at": item["first_response_at"],
            "closed_at": item["closed_at"],
            "resolution": item["resolution"],
            "attachment_name": item["attachment_name"],
            "overdue": overdue,
            "comments_count": comments_count,
            "can_manage": self.can_manage(user, item),
            "can_take": user["role"] in ("manager", "admin")
            and item["assignee_id"] != user["id"] and not is_final(item["status"]),
            "can_comment": user["id"] == item["author_id"] or user["role"] in ("manager", "admin"),
        }

    def appeal_query(self) -> str:
        return (
            "SELECT a.*, c.name AS category_name, au.name AS author_name, au.department AS author_department, "
            "asg.name AS assignee_name "
            "FROM appeals a "
            "JOIN categories c ON c.id = a.category_id "
            "JOIN users au ON au.id = a.author_id "
            "LEFT JOIN users asg ON asg.id = a.assignee_id "
        )

    # ---------- метаданные ----------

    def meta(self, conn: sqlite3.Connection, user: dict) -> None:
        cats = []
        for row in conn.execute(
            "SELECT c.*, u.name AS responsible_name, "
            "(SELECT COUNT(*) FROM appeals a WHERE a.category_id = c.id) AS appeal_count "
            "FROM categories c LEFT JOIN users u ON u.id = c.responsible_id ORDER BY c.name"
        ):
            cats.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "responsible_id": row["responsible_id"],
                    "responsible_name": row["responsible_name"],
                    "active": bool(row["active"]),
                    "appeal_count": row["appeal_count"],
                }
            )
        departments = [r["department"] for r in conn.execute(
            "SELECT DISTINCT department FROM users WHERE department <> '' ORDER BY department"
        )]
        users = []
        if user["role"] in ("manager", "admin"):
            users = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "login": r["login"],
                    "role": r["role"],
                    "position": r["position"],
                    "department": r["department"],
                }
                for r in conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY role, name")
            ]
        self.json(
            {
                "user": self.public_user(user),
                "categories": cats,
                "departments": departments,
                "users": users,
                "statuses": storage.STATUSES,
                "types": storage.TYPES,
                "priorities": storage.PRIORITIES,
                "sla_days": storage.SLA_DAYS,
                "final_statuses": sorted(storage.FINAL_STATUSES),
            }
        )

    def list_users(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY name").fetchall()
        self.json(
            {
                "users": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "login": r["login"],
                        "role": r["role"],
                        "position": r["position"],
                        "department": r["department"],
                    }
                    for r in rows
                ]
            }
        )

    # ---------- обращения ----------

    def filters(self, user: dict, query: dict) -> tuple[str, list]:
        where = ["1 = 1"]
        params: list = []
        status = (query.get("status") or [""])[0]
        if status and status in storage.STATUSES:
            where.append("a.status = ?")
            params.append(status)
        typ = (query.get("type") or [""])[0]
        if typ and typ in storage.TYPES:
            where.append("a.type = ?")
            params.append(typ)
        priority = (query.get("priority") or [""])[0]
        if priority and priority in storage.PRIORITIES:
            where.append("a.priority = ?")
            params.append(priority)
        category = (query.get("category") or [""])[0]
        if category.isdigit():
            where.append("a.category_id = ?")
            params.append(int(category))
        department = (query.get("department") or [""])[0]
        if department:
            where.append("au.department = ?")
            params.append(department)
        search = (query.get("q") or [""])[0].strip()
        if search:
            like = f"%{search}%"
            where.append("(a.title LIKE ? OR a.description LIKE ? OR a.code LIKE ?)")
            params.extend([like, like, like])
        if (query.get("mine") or [""])[0] in ("1", "true"):
            where.append("a.author_id = ?")
            params.append(user["id"])
        if (query.get("assigned") or [""])[0] in ("1", "true"):
            where.append("a.assignee_id = ?")
            params.append(user["id"])
        if (query.get("overdue") or [""])[0] in ("1", "true"):
            placeholders = ",".join("?" for _ in storage.FINAL_STATUSES)
            where.append(f"a.due_at < ? AND a.status NOT IN ({placeholders})")
            params.append(now_iso())
            params.extend(sorted(storage.FINAL_STATUSES))
        return " AND ".join(where), params

    def list_appeals(self, conn: sqlite3.Connection, user: dict, query: dict) -> None:
        where, params = self.filters(user, query)
        rows = conn.execute(
            self.appeal_query() + f" WHERE {where} ORDER BY a.created_at DESC LIMIT 500", params
        ).fetchall()
        self.json({"appeals": [self.appeal_dict(conn, row, user) for row in rows]})

    def fetch_appeal(self, conn: sqlite3.Connection, appeal_id: int) -> sqlite3.Row | None:
        return conn.execute(self.appeal_query() + " WHERE a.id = ?", (appeal_id,)).fetchone()

    def get_appeal(self, conn: sqlite3.Connection, user: dict, appeal_id: int) -> None:
        row = self.fetch_appeal(conn, appeal_id)
        if not row:
            raise ApiError(404, "Обращение не найдено")
        item = self.appeal_dict(conn, row, user)
        events = [
            {
                "type": r["type"],
                "details": r["details"],
                "actor": r["actor_name"],
                "created_at": r["created_at"],
            }
            for r in conn.execute(
                "SELECT e.*, u.name AS actor_name FROM events e LEFT JOIN users u ON u.id = e.actor_id "
                "WHERE e.appeal_id = ? ORDER BY e.created_at",
                (appeal_id,),
            )
        ]
        comments = []
        real_author_id = row["author_id"]
        for r in conn.execute(
            "SELECT c.*, u.name AS author_name, u.role AS author_role FROM comments c "
            "JOIN users u ON u.id = c.author_id WHERE c.appeal_id = ? ORDER BY c.created_at",
            (appeal_id,),
        ):
            if r["internal"] and not self.can_view_internal(user):
                continue
            anonymous_author = (
                bool(row["anonymous"]) and r["author_id"] == real_author_id
                and user["role"] != "admin" and user["id"] != real_author_id
            )
            comments.append(
                {
                    "id": r["id"],
                    "text": r["text"],
                    "internal": bool(r["internal"]),
                    "author": "Анонимный автор" if anonymous_author else r["author_name"],
                    "author_role": r["author_role"],
                    "created_at": r["created_at"],
                }
            )
        self.json({"appeal": item, "events": events, "comments": comments})

    def route_assignee(self, conn: sqlite3.Connection, category: sqlite3.Row) -> tuple[int, str]:
        responsible_id = category["responsible_id"]
        if responsible_id:
            responsible = conn.execute(
                "SELECT id, role FROM users WHERE id = ? AND active = 1", (responsible_id,)
            ).fetchone()
            if responsible and responsible["role"] in ("manager", "admin"):
                return responsible_id, ""
            reason = (
                "ответственный по категории не является руководителем"
                if responsible else "ответственный по категории деактивирован"
            )
        else:
            reason = "ответственный по категории не назначен"
        fallback = conn.execute("SELECT id FROM users WHERE role = 'admin' AND active = 1 ORDER BY id LIMIT 1").fetchone()
        if not fallback:
            raise ApiError(500, "Нет активного администратора для маршрутизации")
        return fallback["id"], reason

    def next_code(self, conn: sqlite3.Connection) -> str:
        rows = conn.execute("SELECT code FROM appeals").fetchall()
        max_num = 0
        for row in rows:
            match = re.fullmatch(r"A-(\d+)", row["code"])
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"A-{max_num + 1:03d}"

    def create_appeal(self, conn: sqlite3.Connection, user: dict) -> None:
        data = self.read_json()
        typ = str(data.get("type", "")).strip()
        if typ not in storage.TYPES:
            raise ApiError(400, "Выберите корректный тип обращения")
        priority = str(data.get("priority", "")).strip()
        if priority not in storage.PRIORITIES:
            raise ApiError(400, "Выберите корректный приоритет")
        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip()
        expected = str(data.get("expected_solution", "")).strip()
        if not 5 <= len(title) <= 140:
            raise ApiError(400, "Заголовок должен содержать от 5 до 140 символов")
        if not 15 <= len(description) <= 5000:
            raise ApiError(400, "Описание должно содержать от 15 до 5000 символов")
        if len(expected) > 3000:
            raise ApiError(400, "Слишком длинное описание ожидаемого решения")
        category_id = data.get("category_id")
        if not isinstance(category_id, int):
            raise ApiError(400, "Выберите категорию")
        category = conn.execute("SELECT * FROM categories WHERE id = ? AND active = 1", (category_id,)).fetchone()
        if not category:
            raise ApiError(400, "Категория не найдена")

        attachment_name = ""
        attachment_path = ""
        attachment = data.get("attachment")
        if attachment:
            if not isinstance(attachment, dict):
                raise ApiError(400, "Некорректный файл")
            filename = os.path.basename(str(attachment.get("filename", "")).strip())
            raw_b64 = str(attachment.get("data", ""))
            if filename and raw_b64:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in ALLOWED_EXT:
                    raise ApiError(400, "Недопустимый тип файла. Разрешены: " + ", ".join(sorted(ALLOWED_EXT)))
                if "," in raw_b64[:100] and raw_b64.startswith("data:"):
                    raw_b64 = raw_b64.split(",", 1)[1]
                try:
                    blob = base64.b64decode(raw_b64, validate=True)
                except Exception:  # noqa: BLE001
                    raise ApiError(400, "Файл повреждён")
                if len(blob) > MAX_ATTACHMENT:
                    raise ApiError(413, "Файл больше 2 МБ")
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                stored = f"{os.urandom(8).hex()}{ext}"
                with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
                    fh.write(blob)
                attachment_name = filename[:200]
                attachment_path = stored

        created = now()
        due = created + timedelta(days=storage.SLA_DAYS[priority])
        code = self.next_code(conn)
        assignee_id, fallback_reason = self.route_assignee(conn, category)
        anonymous = 1 if data.get("anonymous") else 0

        cur = conn.execute(
            "INSERT INTO appeals (code, type, category_id, author_id, anonymous, title, description, "
            "expected_solution, priority, status, assignee_id, created_at, updated_at, due_at, "
            "attachment_name, attachment_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Новое', ?, ?, ?, ?, ?, ?)",
            (
                code, typ, category_id, user["id"], anonymous, title, description, expected,
                priority, assignee_id, storage.iso(created), storage.iso(created), storage.iso(due),
                attachment_name, attachment_path,
            ),
        )
        appeal_id = cur.lastrowid
        conn.execute(
            "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, ?, 'created', ?, ?)",
            (appeal_id, user["id"], "Обращение зарегистрировано", storage.iso(created)),
        )
        assignee = conn.execute("SELECT name FROM users WHERE id = ?", (assignee_id,)).fetchone()
        details = f"Маршрутизация по категории «{category['name']}»: {assignee['name']}"
        if fallback_reason:
            details = f"Автоназначение администратору ({fallback_reason}): {assignee['name']}"
        conn.execute(
            "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, NULL, 'assigned', ?, ?)",
            (appeal_id, details, storage.iso(created + timedelta(seconds=1))),
        )
        conn.commit()
        self.json({"id": appeal_id, "code": code, "assignee": assignee["name"]}, status=201)

    def appeal_for_action(self, conn: sqlite3.Connection, appeal_id: int) -> sqlite3.Row:
        row = self.fetch_appeal(conn, appeal_id)
        if not row:
            raise ApiError(404, "Обращение не найдено")
        return row

    def add_comment(self, conn: sqlite3.Connection, user: dict, appeal_id: int) -> None:
        row = self.appeal_for_action(conn, appeal_id)
        data = self.read_json()
        text = str(data.get("text", "")).strip()
        if not 2 <= len(text) <= 3000:
            raise ApiError(400, "Комментарий должен содержать от 2 до 3000 символов")
        if user["id"] != row["author_id"] and user["role"] not in ("manager", "admin"):
            raise ApiError(403, "Нельзя комментировать это обращение")
        internal = 1 if data.get("internal") and user["role"] in ("manager", "admin") else 0
        created = now()
        conn.execute(
            "INSERT INTO comments (appeal_id, author_id, text, internal, created_at) VALUES (?, ?, ?, ?, ?)",
            (appeal_id, user["id"], text, internal, storage.iso(created)),
        )
        if user["role"] in ("manager", "admin") and not row["first_response_at"]:
            conn.execute("UPDATE appeals SET first_response_at = ? WHERE id = ?", (storage.iso(created), appeal_id))
        conn.execute("UPDATE appeals SET updated_at = ? WHERE id = ?", (storage.iso(created), appeal_id))
        conn.commit()
        self.json({"ok": True})

    def change_status(self, conn: sqlite3.Connection, user: dict, appeal_id: int) -> None:
        row = self.appeal_for_action(conn, appeal_id)
        if not self.can_manage(user, dict(row)):
            raise ApiError(403, "Менять статус может ответственный или администратор")
        data = self.read_json()
        status = str(data.get("status", "")).strip()
        if status not in storage.STATUSES:
            raise ApiError(400, "Некорректный статус")
        if status == row["status"]:
            raise ApiError(400, "Статус уже установлен")
        resolution = str(data.get("resolution", "")).strip()
        if len(resolution) > 3000:
            raise ApiError(400, "Слишком длинное описание результата")
        if status in storage.FINAL_STATUSES and len(resolution) < 10:
            raise ApiError(400, "Для завершения обращения нужен результат рассмотрения (минимум 10 символов)")
        if status == "Ожидает уточнения":
            comment = str(data.get("comment", "")).strip()
            internal = bool(data.get("internal"))
            existing = conn.execute(
                "SELECT 1 FROM comments WHERE appeal_id = ? AND internal = 0", (appeal_id,)
            ).fetchone()
            if (not comment or internal) and not existing:
                raise ApiError(400, "Добавьте публичный комментарий с вопросом автору")
        comment = str(data.get("comment", "")).strip()

        created = now()
        closed_at = storage.iso(created) if status in storage.FINAL_STATUSES else None
        conn.execute(
            "UPDATE appeals SET status = ?, resolution = ?, updated_at = ?, closed_at = ?, "
            "first_response_at = COALESCE(first_response_at, ?) WHERE id = ?",
            (status, resolution if status in storage.FINAL_STATUSES else row["resolution"], storage.iso(created),
             closed_at, storage.iso(created), appeal_id),
        )
        details = f"Статус изменён на «{status}»"
        if resolution and status in storage.FINAL_STATUSES:
            details += f". Результат: {resolution}"
        conn.execute(
            "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, ?, 'status', ?, ?)",
            (appeal_id, user["id"], details, storage.iso(created)),
        )
        comment = str(data.get("comment", "")).strip()
        if comment:
            conn.execute(
                "INSERT INTO comments (appeal_id, author_id, text, internal, created_at) VALUES (?, ?, ?, ?, ?)",
                (appeal_id, user["id"], comment, 1 if data.get("internal") else 0, storage.iso(created)),
            )
        conn.commit()
        self.json({"ok": True})

    def assign(self, conn: sqlite3.Connection, user: dict, appeal_id: int) -> None:
        row = self.appeal_for_action(conn, appeal_id)
        if user["role"] not in ("manager", "admin"):
            raise ApiError(403, "Назначать ответственного может руководитель или администратор")
        data = self.read_json()
        assignee_id = data.get("assignee_id")
        if not isinstance(assignee_id, int):
            raise ApiError(400, "Выберите ответственного")
        target = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (assignee_id,)).fetchone()
        if not target:
            raise ApiError(400, "Сотрудник не найден")
        if target["role"] not in ("manager", "admin"):
            raise ApiError(400, "Ответственным может быть руководитель или администратор")
        if target["id"] == row["assignee_id"]:
            raise ApiError(400, "Этот сотрудник уже назначен")
        created = now()
        conn.execute("UPDATE appeals SET assignee_id = ?, updated_at = ? WHERE id = ?",
                     (assignee_id, storage.iso(created), appeal_id))
        conn.execute(
            "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, ?, 'assigned', ?, ?)",
            (appeal_id, user["id"], f"Ответственный изменён: {target['name']}", storage.iso(created)),
        )
        conn.commit()
        self.json({"ok": True})

    def take(self, conn: sqlite3.Connection, user: dict, appeal_id: int) -> None:
        row = self.appeal_for_action(conn, appeal_id)
        if user["role"] not in ("manager", "admin"):
            raise ApiError(403, "Требуются права руководителя")
        if is_final(row["status"]):
            raise ApiError(400, "Обращение уже завершено")
        if row["assignee_id"] == user["id"]:
            raise ApiError(400, "Вы уже ответственный по этому обращению")
        created = now()
        new_status = "В работе" if row["status"] == "Новое" else row["status"]
        conn.execute(
            "UPDATE appeals SET assignee_id = ?, status = ?, updated_at = ?, first_response_at = COALESCE(first_response_at, ?) "
            "WHERE id = ?",
            (user["id"], new_status, storage.iso(created), storage.iso(created), appeal_id),
        )
        conn.execute(
            "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, ?, 'assigned', ?, ?)",
            (appeal_id, user["id"], f"Обращение взято в работу: {user['name']}", storage.iso(created)),
        )
        if new_status != row["status"]:
            conn.execute(
                "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, ?, 'status', ?, ?)",
                (appeal_id, user["id"], f"Статус изменён на «{new_status}»", storage.iso(created)),
            )
        conn.commit()
        self.json({"ok": True, "status": new_status})

    # ---------- вложения ----------

    def download_attachment(self, conn: sqlite3.Connection, user: dict, appeal_id: int) -> None:
        row = conn.execute("SELECT attachment_name, attachment_path FROM appeals WHERE id = ?", (appeal_id,)).fetchone()
        if not row or not row["attachment_path"]:
            raise ApiError(404, "Вложение не найдено")
        full = os.path.realpath(os.path.join(UPLOAD_DIR, row["attachment_path"]))
        if not full.startswith(os.path.realpath(UPLOAD_DIR) + os.sep) or not os.path.isfile(full):
            raise ApiError(404, "Файл недоступен")
        ctype, _ = mimetypes.guess_type(full)
        with open(full, "rb") as fh:
            body = fh.read()
        encoded = quote(row["attachment_name"])
        self._send(
            200, body, ctype or "application/octet-stream",
            {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
        )

    # ---------- аналитика ----------

    def dashboard(self, conn: sqlite3.Connection, user: dict) -> None:
        rows = conn.execute(self.appeal_query() + " ORDER BY a.created_at DESC").fetchall()
        total = len(rows)
        final = [r for r in rows if is_final(r["status"])]
        unresolved = [r for r in rows if not is_final(r["status"])]
        overdue = [r for r in unresolved if parse_dt(r["due_at"]) and parse_dt(r["due_at"]) < now()]
        implemented = [r for r in rows if r["status"] == "Реализовано"]
        rejected = [r for r in rows if r["status"] == "Отклонено"]

        def avg(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 1) if values else None

        first_resp = [
            (parse_dt(r["first_response_at"]) - parse_dt(r["created_at"])).total_seconds() / 3600
            for r in rows
            if r["first_response_at"] and parse_dt(r["first_response_at"]) and parse_dt(r["created_at"])
        ]
        close_days = [
            (parse_dt(r["closed_at"]) - parse_dt(r["created_at"])).total_seconds() / 86400
            for r in final
            if r["closed_at"] and parse_dt(r["closed_at"]) and parse_dt(r["created_at"])
        ]

        by_status = {s: 0 for s in storage.STATUSES}
        by_type = {t: 0 for t in storage.TYPES}
        by_priority = {p: 0 for p in storage.PRIORITIES}
        by_category: dict[str, dict] = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
            by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
            entry = by_category.setdefault(r["category_name"], {"name": r["category_name"], "total": 0, "open": 0, "resolved": 0})
            entry["total"] += 1
            if is_final(r["status"]):
                entry["resolved"] += 1
            else:
                entry["open"] += 1
        categories = sorted(by_category.values(), key=lambda x: (-x["total"], x["name"]))[:8]

        weeks = []
        today = now().date()
        start = today - timedelta(days=today.weekday())
        for i in range(5, -1, -1):
            week_start = start - timedelta(weeks=i)
            week_end = week_start + timedelta(days=7)
            count = 0
            for r in rows:
                created = parse_dt(r["created_at"])
                if created and week_start <= created.date() < week_end:
                    count += 1
            weeks.append({"label": week_start.strftime("%d.%m"), "count": count})

        recurring = []
        for entry in by_category.values():
            problems = sum(1 for r in rows if r["category_name"] == entry["name"] and r["type"] == "Проблема")
            if problems >= 2:
                recurring.append({"category": entry["name"], "problems": problems, "open": entry["open"]})
        recurring.sort(key=lambda x: -x["problems"])

        resolved_share = round(len(final) / total * 100) if total else 0
        self.json(
            {
                "kpi": {
                    "total": total,
                    "open": len(unresolved),
                    "overdue": len(overdue),
                    "considered": len(final),
                    "considered_share": resolved_share,
                    "implemented": len(implemented),
                    "rejected": len(rejected),
                    "avg_first_response_hours": avg(first_resp),
                    "avg_close_days": avg(close_days),
                },
                "by_status": by_status,
                "by_type": by_type,
                "by_priority": by_priority,
                "categories": categories,
                "weeks": weeks,
                "recurring": recurring,
                "overdue": [
                    {
                        "id": r["id"],
                        "code": r["code"],
                        "title": r["title"],
                        "assignee": r["assignee_name"],
                        "due_at": r["due_at"],
                        "priority": r["priority"],
                    }
                    for r in sorted(overdue, key=lambda x: x["due_at"])[:10]
                ],
                "implemented": [
                    {
                        "id": r["id"],
                        "code": r["code"],
                        "title": r["title"],
                        "category": r["category_name"],
                        "resolution": r["resolution"],
                        "closed_at": r["closed_at"],
                    }
                    for r in implemented[:6]
                ],
            }
        )

    def export_csv(self, conn: sqlite3.Connection, user: dict, query: dict) -> None:
        where, params = self.filters(user, query)
        rows = conn.execute(self.appeal_query() + f" WHERE {where} ORDER BY a.created_at DESC", params).fetchall()
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        writer.writerow([
            "Код", "Тип", "Категория", "Заголовок", "Приоритет", "Статус",
            "Автор", "Подразделение", "Ответственный", "Создано", "Срок", "Первый ответ", "Закрыто", "Результат",
        ])
        for r in rows:
            writer.writerow([
                r["code"], r["type"], r["category_name"], csv_safe(r["title"]), r["priority"], r["status"],
                "Анонимный сотрудник" if r["anonymous"] else r["author_name"], r["author_department"],
                r["assignee_name"] or "", r["created_at"][:10], r["due_at"][:10],
                (r["first_response_at"] or "")[:10], (r["closed_at"] or "")[:10], csv_safe(r["resolution"]),
            ])
        body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        self._send(
            200, body, "text/csv; charset=utf-8",
            {"Content-Disposition": "attachment; filename*=UTF-8''feedback.csv"},
        )

    # ---------- администрирование ----------

    def list_categories(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT c.*, u.name AS responsible_name, "
            "(SELECT COUNT(*) FROM appeals a WHERE a.category_id = c.id) AS appeal_count "
            "FROM categories c LEFT JOIN users u ON u.id = c.responsible_id ORDER BY c.name"
        ).fetchall()
        self.json({"categories": [dict(r) | {"active": bool(r["active"])} for r in rows]})

    def create_category(self, conn: sqlite3.Connection, user: dict) -> None:
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        responsible_id = data.get("responsible_id")
        if not 3 <= len(name) <= 80:
            raise ApiError(400, "Название категории должно содержать от 3 до 80 символов")
        if len(description) > 300:
            raise ApiError(400, "Слишком длинное описание категории")
        validate_responsible(conn, responsible_id)
        try:
            cur = conn.execute(
                "INSERT INTO categories (name, description, responsible_id) VALUES (?, ?, ?)",
                (name, description, responsible_id),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "Категория с таким названием уже существует")
        conn.commit()
        self.json({"id": cur.lastrowid}, status=201)

    def update_category(self, conn: sqlite3.Connection, user: dict, category_id: int) -> None:
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        if not row:
            raise ApiError(404, "Категория не найдена")
        data = self.read_json()
        name = str(data.get("name", row["name"])).strip()
        description = str(data.get("description", row["description"])).strip()
        responsible_id = data.get("responsible_id", row["responsible_id"])
        active = 1 if data.get("active", bool(row["active"])) else 0
        if not 3 <= len(name) <= 80:
            raise ApiError(400, "Название категории должно содержать от 3 до 80 символов")
        validate_responsible(conn, responsible_id)
        try:
            conn.execute(
                "UPDATE categories SET name = ?, description = ?, responsible_id = ?, active = ? WHERE id = ?",
                (name, description, responsible_id, active, category_id),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "Категория с таким названием уже существует")
        conn.commit()
        self.json({"ok": True})


def main() -> None:
    storage.init_db(DB_PATH)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Feedback MVP запущен: http://{HOST}:{PORT}")
    print("Демо-аккаунты: purchase / head_purchase / admin, пароль demo123")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
