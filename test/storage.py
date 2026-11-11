"""Слой данных: схема SQLite, справочники и синтетические данные из датасета.

Модуль не зависит от веб-сервера и может использоваться отдельно
(например, для пересоздания демонстрационной базы).
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

STATUSES = [
    "Новое",
    "На рассмотрении",
    "В работе",
    "Ожидает уточнения",
    "Реализовано",
    "Отклонено",
    "Закрыто",
]
TYPES = ["Проблема", "Предложение", "Положительный отзыв"]
PRIORITIES = ["Низкий", "Средний", "Высокий"]
SLA_DAYS = {"Высокий": 3, "Средний": 7, "Низкий": 14}
FINAL_STATUSES = {"Реализовано", "Отклонено", "Закрыто"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('employee', 'manager', 'admin')),
    position      TEXT NOT NULL DEFAULT '',
    department    TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    description    TEXT NOT NULL DEFAULT '',
    responsible_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS appeals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT NOT NULL UNIQUE,
    type              TEXT NOT NULL,
    category_id       INTEGER NOT NULL REFERENCES categories(id),
    author_id         INTEGER NOT NULL REFERENCES users(id),
    anonymous         INTEGER NOT NULL DEFAULT 0,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    expected_solution TEXT NOT NULL DEFAULT '',
    priority          TEXT NOT NULL,
    status            TEXT NOT NULL,
    assignee_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    due_at            TEXT NOT NULL,
    first_response_at TEXT,
    closed_at         TEXT,
    resolution        TEXT NOT NULL DEFAULT '',
    attachment_name   TEXT NOT NULL DEFAULT '',
    attachment_path   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    appeal_id  INTEGER NOT NULL REFERENCES appeals(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES users(id),
    text       TEXT NOT NULL,
    internal   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    appeal_id  INTEGER NOT NULL REFERENCES appeals(id) ON DELETE CASCADE,
    actor_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    type       TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appeals_status ON appeals(status);
CREATE INDEX IF NOT EXISTS idx_appeals_category ON appeals(category_id);
CREATE INDEX IF NOT EXISTS idx_appeals_author ON appeals(author_id);
CREATE INDEX IF NOT EXISTS idx_comments_appeal ON comments(appeal_id);
CREATE INDEX IF NOT EXISTS idx_events_appeal ON events(appeal_id);
"""

# login, name, role, position, department
USERS = [
    ("purchase", "Анна Смирнова", "employee", "Сотрудник закупок", "Закупки"),
    ("design", "Игорь Волков", "employee", "Инженер-проектировщик", "Проектирование"),
    ("accounting", "Мария Ковалёва", "employee", "Специалист бухгалтерии", "Бухгалтерия"),
    ("sales", "Дмитрий Орлов", "employee", "Менеджер по продажам", "Продажи"),
    ("office", "Ольга Никитина", "employee", "Сотрудник офиса", "Административный отдел"),
    ("pm", "Сергей Лебедев", "employee", "Координатор проекта", "Проектный офис"),
    ("head_purchase", "Павел Громов", "manager", "Руководитель закупок", "Закупки"),
    ("head_sales", "Елена Соколова", "manager", "Руководитель направления", "Продажи"),
    ("it", "Артём Белов", "manager", "IT-специалист", "IT"),
    ("proc", "Наталья Крылова", "manager", "Ответственный за процессы", "Управление"),
    ("doc_admin", "Виктор Зайцев", "manager", "Администратор документов", "Управление"),
    ("training", "Ирина Мельникова", "manager", "Ответственный за обучение", "HR"),
    ("nomenclature", "Роман Титов", "manager", "Ответственный за номенклатуру", "Каталог"),
    ("contracts", "Светлана Фомина", "manager", "Ответственный за договоры", "Юридический отдел"),
    ("project_lead", "Андрей Ковалёв", "manager", "Руководитель проектов", "Проектный офис"),
    ("office_head", "Татьяна Егорова", "manager", "Административный руководитель", "Административный отдел"),
    ("hr", "Юлия Павлова", "manager", "HR / внутренние коммуникации", "HR"),
    ("admin", "Алексей Морозов", "admin", "Администратор системы", "IT"),
]

# key, name, description, responsible login
CATEGORIES = [
    ("interaction", "Межотдельное взаимодействие",
     "Заявки, согласования и передача задач между подразделениями", "proc"),
    ("transparency", "Информационная прозрачность",
     "Доступ к статусам, срокам и результатам без лишних запросов", "head_sales"),
    ("catalog", "Каталог и номенклатура",
     "Единые карточки позиций, обозначения и актуальность данных", "nomenclature"),
    ("training", "Обучение и наставничество",
     "Внутренние занятия, памятки и передача опыта", "training"),
    ("docs", "Документооборот и регламенты",
     "Согласование документов, хранение версий и инструкции", "doc_admin"),
    ("it", "Интеграции и IT",
     "Синхронизация систем, автоматизация и технические доработки", "it"),
    ("contracts", "Договорная работа",
     "Подготовка, проверка и согласование договоров", "contracts"),
    ("projects", "Проектная деятельность",
     "Спецификации, история изменений и планирование ресурсов", "project_lead"),
    ("corp", "Корпоративная среда",
     "Рабочее пространство, офис и внутренние коммуникации", "office_head"),
    ("comms", "Задачи и коммуникации",
     "Постановка задач, единые каналы и фиксация итогов", "head_sales"),
]

DEFAULT_PASSWORD = "demo123"

# Демонстрационные обращения. Данные синтетические (датасет хакатона).
# comments: (author_login_or_'manager', text, age_days, internal)
APPEALS = [
    {
        "code": "A-001", "type": "Проблема", "category": "interaction", "author": "purchase",
        "priority": "Средний", "status": "Новое", "age": 14,
        "title": "В заявках не хватает назначения и параметров изделия",
        "desc": "В заявках на приобретение оборудования иногда не хватает назначения изделия и требуемых параметров. "
                "Из-за этого приходится возвращаться к автору за уточнениями.",
        "expected": "Добавить обязательные поля в шаблон заявки, чтобы сократить число уточнений.",
        "comments": [],
    },
    {
        "code": "A-002", "type": "Предложение", "category": "transparency", "author": "purchase",
        "priority": "Средний", "status": "Реализовано", "age": 21,
        "title": "Показывать общую стадию проекта поставщикам",
        "desc": "При общении с поставщиками полезно видеть общую стадию проекта без доступа к коммерческим деталям. "
                "Можно ли показывать краткий статус и ориентировочные контрольные даты?",
        "expected": "Определить перечень доступных статусов и источник их обновления.",
        "resolution": "Согласован краткий набор статусов проекта. Обновление выполняет координатор проекта раз в неделю.",
        "comments": [
            ("manager", "Статусы согласованы, добавляем их в карточку проекта.", 18, False),
            ("author", "Спасибо, так гораздо понятнее.", 16, False),
        ],
    },
    {
        "code": "A-003", "type": "Проблема", "category": "catalog", "author": "design",
        "priority": "Высокий", "status": "В работе", "age": 26,
        "title": "Разные обозначения одного изделия в рабочих материалах",
        "desc": "В рабочих материалах иногда встречаются разные обозначения одного изделия. "
                "Это увеличивает время проверки спецификации.",
        "expected": "Нужен понятный способ находить актуальную карточку позиции.",
        "comments": [
            ("manager", "Собираю примеры расхождений, проверяю правила сопоставления.", 20, False),
            ("manager", "Промежуточный итог: нашли 14 повторяющихся пар обозначений, готовим правило проверки.", 9, True),
        ],
    },
    {
        "code": "A-004", "type": "Предложение", "category": "training", "author": "purchase",
        "priority": "Низкий", "status": "Реализовано", "age": 60,
        "title": "Короткие занятия по новым товарным линейкам",
        "desc": "Было бы полезно проводить короткие занятия по новым товарным линейкам с разбором типовых запросов. "
                "Предлагаю составить календарь таких встреч.",
        "expected": "Составить календарь занятий и назначить ответственных за темы.",
        "resolution": "Запланирован пробный обучающий семинар по новой линейке. Календарь ведёт ответственный за обучение.",
        "comments": [
            ("manager", "Пилот назначен на следующий месяц, формат — 40 минут онлайн.", 52, False),
        ],
    },
    {
        "code": "A-005", "type": "Проблема", "category": "docs", "author": "accounting",
        "priority": "Средний", "status": "Ожидает уточнения", "age": 9,
        "title": "Непонятно, на каком этапе находится пакет документов",
        "desc": "При обработке документов между подразделениями возникают дополнительные вопросы о том, "
                "на каком этапе находится пакет. Хотелось бы видеть ответственного и текущий статус.",
        "expected": "Уточнить виды документов и ключевые этапы согласования.",
        "comments": [
            ("manager", "Уточните, пожалуйста, какие виды документов критичнее всего и кто сейчас ведёт согласование.", 7, False),
        ],
    },
    {
        "code": "A-006", "type": "Предложение", "category": "docs", "author": "accounting",
        "priority": "Средний", "status": "Реализовано", "age": 18,
        "title": "Пошаговая памятка для операций с несколькими подразделениями",
        "desc": "Предлагаю собрать короткую пошаговую памятку для операций, в которых последовательно участвуют "
                "несколько подразделений, и определить порядок актуализации памятки.",
        "expected": "Назначить владельца процесса и согласовать черновик инструкции.",
        "resolution": "Памятка согласована и опубликована во внутренней базе знаний. Владелец процесса обновляет её ежеквартально.",
        "comments": [
            ("manager", "Черновик готов, отправлен на согласование участникам процесса.", 12, False),
        ],
    },
    {
        "code": "A-007", "type": "Проблема", "category": "it", "author": "accounting",
        "priority": "Высокий", "status": "Новое", "age": 4,
        "title": "Повторный ввод одних и тех же сведений в разных системах",
        "desc": "Иногда повторный ввод одних и тех же сведений в разных рабочих системах занимает много времени. "
                "Нужна оценка, какие операции можно синхронизировать безопасно.",
        "expected": "Собрать примеры операций и оценить техническую реализуемость.",
        "comments": [],
    },
    {
        "code": "A-008", "type": "Предложение", "category": "training", "author": "accounting",
        "priority": "Низкий", "status": "Реализовано", "age": 45,
        "title": "Памятка по типовым ошибкам при передаче задач",
        "desc": "При передаче сложной задачи новому сотруднику полезен короткий разбор типовых ошибок и контактов, "
                "к которым можно обратиться за помощью. Предлагаю сделать такую памятку.",
        "expected": "Подготовить памятку и включить её в вводный набор новичка.",
        "resolution": "Создана учебная инструкция с разбором типовых ошибок и списком контактов.",
        "comments": [
            ("manager", "Инструкция добавлена в вводный набор новичка.", 38, False),
        ],
    },
    {
        "code": "A-009", "type": "Проблема", "category": "contracts", "author": "sales",
        "priority": "Высокий", "status": "В работе", "age": 12,
        "title": "Непонятно, кто готовит и проверяет версию приложения к договору",
        "desc": "Не всегда понятно, кто готовит очередную версию приложения к договору и кто должен проверить её "
                "перед отправкой. Это приводит к повторным согласованиям.",
        "expected": "Разграничить этапы подготовки, проверки и утверждения.",
        "comments": [
            ("manager", "Готовлю матрицу ответственности по этапам, согласую с юридическим отделом.", 6, False),
        ],
    },
    {
        "code": "A-010", "type": "Предложение", "category": "comms", "author": "sales",
        "priority": "Средний", "status": "На рассмотрении", "age": 12,
        "title": "Не закрывать задачу до подтверждения результата заказчиком",
        "desc": "Предлагаю не закрывать рабочую задачу до подтверждения заказчиком результата внутри компании, "
                "чтобы связанные вопросы оставались в общей истории обсуждения.",
        "expected": "Проверить, подходит ли правило для разных типов задач.",
        "comments": [
            ("manager", "Проверяю правило на задачах двух направлений, первые результаты через неделю.", 5, False),
        ],
    },
    {
        "code": "A-011", "type": "Проблема", "category": "transparency", "author": "sales",
        "priority": "Высокий", "status": "Новое", "age": 3,
        "title": "Сложно прогнозировать срок ответа смежных подразделений",
        "desc": "При срочной подготовке клиентского предложения сложно прогнозировать сроки ответа от смежных "
                "подразделений. Хотелось бы видеть ожидаемый срок и уведомление при задержке.",
        "expected": "Определить допустимые сроки реакции для разных категорий.",
        "comments": [],
    },
    {
        "code": "A-012", "type": "Предложение", "category": "comms", "author": "sales",
        "priority": "Низкий", "status": "Отклонено", "age": 32,
        "title": "Единое место для итоговых решений по задаче",
        "desc": "Рабочие обсуждения распределены по нескольким каналам, из-за чего теряется контекст. "
                "Предлагаю выбрать единое место для итоговых решений по задаче.",
        "expected": "Сравнить варианты и предложить правила фиксации итогов.",
        "resolution": "Отклонено: в рамках MVP не выбран единый инструмент. Вопрос вернём при обновлении регламента коммуникаций.",
        "comments": [
            ("manager", "Вопрос требует согласования на уровне нескольких подразделений, выносим за рамки текущего этапа.", 20, False),
        ],
    },
    {
        "code": "A-013", "type": "Проблема", "category": "projects", "author": "pm",
        "priority": "Средний", "status": "Ожидает уточнения", "age": 16,
        "title": "Не видно, какие строки спецификации изменились",
        "desc": "Когда изменяется состав оборудования в проекте, не всегда ясно, какие строки спецификации были "
                "пересмотрены. Нужна более заметная история изменений.",
        "expected": "Уточнить формат исходных файлов и способы сравнения версий.",
        "comments": [
            ("manager", "Пришлите, пожалуйста, пример двух версий файла — от этого зависит способ сравнения.", 13, False),
        ],
    },
    {
        "code": "A-014", "type": "Предложение", "category": "projects", "author": "pm",
        "priority": "Средний", "status": "Реализовано", "age": 22,
        "title": "Показывать загруженность проектной группы",
        "desc": "Предлагаю показывать загруженность проектной группы на ближайшие недели в укрупнённом виде. "
                "Так будет проще согласовывать сроки запуска новых задач.",
        "expected": "Согласовать показатели загрузки и права просмотра.",
        "resolution": "Добавлен еженедельный обзор загрузки в укрупнённом виде. Доступен руководителям проектов.",
        "comments": [
            ("manager", "Первый обзор загрузки подготовлен, сверяем показатели с планом.", 10, False),
        ],
    },
    {
        "code": "A-015", "type": "Проблема", "category": "comms", "author": "pm",
        "priority": "Средний", "status": "В работе", "age": 11,
        "title": "Поручения без ожидаемого результата и срока",
        "desc": "Некоторые поручения поступают без ожидаемого результата и даты завершения. "
                "Предлагаю договориться о минимальном наборе данных при постановке задачи.",
        "expected": "Подготовить шаблон с целью, сроком, приоритетом и ответственным.",
        "comments": [
            ("manager", "Готовлю единый шаблон постановки задачи, согласую с руководителями направлений.", 4, False),
        ],
    },
    {
        "code": "A-016", "type": "Предложение", "category": "training", "author": "pm",
        "priority": "Низкий", "status": "Новое", "age": 6,
        "title": "Практические демонстрации оборудования для новичков",
        "desc": "Для новых сотрудников полезны практические демонстрации оборудования, а не только презентации. "
                "Предлагаю пилотное занятие с учебными образцами.",
        "expected": "Проверить наличие образцов и составить план пилота.",
        "comments": [],
    },
    {
        "code": "A-017", "type": "Предложение", "category": "corp", "author": "office",
        "priority": "Низкий", "status": "Отклонено", "age": 40,
        "title": "Небольшая зона для коротких перерывов рядом с рабочими помещениями",
        "desc": "Предлагаю организовать отдельную небольшую зону для коротких перерывов рядом с рабочими помещениями.",
        "expected": "Оценить возможность выделения места и бюджет.",
        "resolution": "Сейчас нет подходящего помещения. Вернёмся к вопросу при следующей перепланировке офиса.",
        "comments": [
            ("manager", "Проверили доступные площади — свободных зон нет. Вопрос в плане на перепланировку.", 30, False),
        ],
    },
    {
        "code": "A-018", "type": "Положительный отзыв", "category": "corp", "author": "office",
        "priority": "Низкий", "status": "Закрыто", "age": 30,
        "title": "Польза внутренних учебных встреч",
        "desc": "Хочу отметить пользу внутренних учебных встреч: после них стало легче обращаться к коллегам "
                "за консультацией. Было бы хорошо сохранить этот формат.",
        "expected": "Сохранить формат внутренних встреч.",
        "resolution": "Отзыв передан организаторам обучения, формат сохранён в плане на следующий квартал.",
        "comments": [
            ("manager", "Спасибо за отзыв! Передали организаторам обучения.", 24, False),
        ],
    },
    {
        "code": "A-019", "type": "Проблема", "category": "docs", "author": "pm",
        "priority": "Средний", "status": "В работе", "age": 8,
        "title": "Трудно определить актуальную редакцию инструкции",
        "desc": "В общих папках трудно быстро определить актуальную редакцию инструкции. "
                "Можно ли закрепить единые правила именования и архивирования старых версий?",
        "expected": "Согласовать порядок хранения и владельцев папок.",
        "comments": [
            ("manager", "Готовлю правила именования и список владельцев папок.", 3, False),
        ],
    },
    {
        "code": "A-020", "type": "Предложение", "category": "transparency", "author": "purchase",
        "priority": "Низкий", "status": "Новое", "age": 2,
        "title": "Публиковать обзор завершённых внутренних улучшений",
        "desc": "Предлагаю публиковать краткий обзор завершённых внутренних улучшений, чтобы сотрудники видели "
                "результат направленных предложений.",
        "expected": "Рассмотреть формат квартальной подборки без конфиденциальных сведений.",
        "comments": [],
    },
    {
        "code": "A-021", "type": "Проблема", "category": "interaction", "author": "purchase",
        "priority": "Средний", "status": "Новое", "age": 5, "anonymous": True,
        "title": "Долгое согласование заявок между отделами",
        "desc": "Согласование заявок между отделами занимает несколько дней, при этом не видно, у кого сейчас "
                "находится заявка. Хотелось бы получать уведомление о смене ответственного.",
        "expected": "Добавить уведомления и отображение текущего ответственного.",
        "comments": [],
    },
]


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    return secrets.compare_digest(hash_password(password, salt), password_hash)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str, reset: bool = False) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if reset and os.path.exists(db_path):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(db_path + suffix)
            except FileNotFoundError:
                pass
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        seeded = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if seeded == 0:
            seed(conn)
    finally:
        conn.close()


def seed(conn: sqlite3.Connection) -> None:
    now = utc_now()
    user_ids: dict[str, int] = {}
    for login, name, role, position, department in USERS:
        salt = secrets.token_hex(16)
        cur = conn.execute(
            "INSERT INTO users (login, name, role, position, department, password_hash, salt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (login, name, role, position, department, hash_password(DEFAULT_PASSWORD, salt), salt),
        )
        user_ids[login] = cur.lastrowid

    category_ids: dict[str, int] = {}
    for key, name, description, responsible in CATEGORIES:
        cur = conn.execute(
            "INSERT INTO categories (name, description, responsible_id) VALUES (?, ?, ?)",
            (name, description, user_ids[responsible]),
        )
        category_ids[key] = cur.lastrowid

    for item in APPEALS:
        created = now - timedelta(days=item["age"], hours=3)
        assignee = user_ids[CATEGORIES_BY_KEY[item["category"]][3]]
        due = created + timedelta(days=SLA_DAYS[item["priority"]])
        status = item["status"]
        resolution = item.get("resolution", "")
        final = status in FINAL_STATUSES
        closed_at = None
        first_response = None
        updated = created

        cur = conn.execute(
            "INSERT INTO appeals (code, type, category_id, author_id, anonymous, title, description, "
            "expected_solution, priority, status, assignee_id, created_at, updated_at, due_at, "
            "first_response_at, closed_at, resolution) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item["code"], item["type"], category_ids[item["category"]], user_ids[item["author"]],
                1 if item.get("anonymous") else 0, item["title"], item["desc"], item["expected"],
                item["priority"], status, assignee, iso(created), iso(created), iso(due),
                None, None, resolution,
            ),
        )
        appeal_id = cur.lastrowid

        events = [("created", user_ids[item["author"]], "Обращение зарегистрировано", created)]
        events.append(("assigned", None, f"Маршрутизация по категории: {USERS_BY_LOGIN[CATEGORIES_BY_KEY[item['category']][3]][1]}", created + timedelta(minutes=5)))

        if status != "Новое":
            status_time = created + timedelta(days=1, hours=2)
            if status_time > now:
                status_time = now
            events.append(("status", assignee, f"Статус изменён на «{status}»", status_time))
            first_response = status_time
            updated = max(updated, status_time)
            if final:
                closed_at = status_time
                if resolution:
                    events.append(("resolution", assignee, f"Зафиксирован результат: {resolution}", status_time))

        for author_key, text, age_days, internal in item["comments"]:
            author_id = assignee if author_key == "manager" else user_ids[item["author"]]
            c_time = now - timedelta(days=age_days, hours=5)
            if c_time < created:
                c_time = created + timedelta(hours=1)
            conn.execute(
                "INSERT INTO comments (appeal_id, author_id, text, internal, created_at) VALUES (?, ?, ?, ?, ?)",
                (appeal_id, author_id, text, 1 if internal else 0, iso(c_time)),
            )
            if author_key == "manager" and first_response is None:
                first_response = c_time
            updated = max(updated, c_time)

        for etype, actor, details, when in events:
            conn.execute(
                "INSERT INTO events (appeal_id, actor_id, type, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (appeal_id, actor, etype, details, iso(when)),
            )

        conn.execute(
            "UPDATE appeals SET updated_at = ?, first_response_at = ?, closed_at = ? WHERE id = ?",
            (iso(updated), iso(first_response) if first_response else None,
             iso(closed_at) if closed_at else None, appeal_id),
        )

    conn.commit()


USERS_BY_LOGIN = {u[0]: u for u in USERS}
CATEGORIES_BY_KEY = {c[0]: c for c in CATEGORIES}


if __name__ == "__main__":
    import sys

    base = os.path.dirname(os.path.abspath(__file__))
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(base, "data", "feedback.db")
    init_db(path, reset=True)
    uploads = os.path.join(base, "uploads")
    if os.path.isdir(uploads):
        for name in os.listdir(uploads):
            target = os.path.join(uploads, name)
            if os.path.isfile(target):
                os.remove(target)
    print(f"База создана: {path}")
