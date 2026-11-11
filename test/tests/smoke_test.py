#!/usr/bin/env python3
"""Дымовой тест API. Запускать при работающем сервере: py tests/smoke_test.py"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
results: list[tuple[bool, str]] = []


def check(ok: bool, title: str, extra: str = "") -> None:
    results.append((ok, title))
    print(("PASS " if ok else "FAIL ") + title + ((" | " + extra) if extra and not ok else ""))


def opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(op, method: str, path: str, body=None, raw: bool = False):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        resp = op.open(req, timeout=15)
        payload = resp.read()
        status = resp.status
        ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        status = exc.code
        ctype = exc.headers.get("Content-Type", "")
    if raw:
        return status, payload
    parsed = json.loads(payload.decode("utf-8")) if "json" in ctype else payload
    return status, parsed


def login(login_name: str, password: str = "demo123"):
    op = opener()
    status, data = request(op, "POST", "/api/login", {"login": login_name, "password": password})
    check(status == 200, f"login {login_name}", str(data))
    return op


def find_appeal(appeals: list, code: str) -> dict | None:
    return next((a for a in appeals if a["code"] == code), None)


def main() -> int:
    anon = opener()
    status, _ = request(anon, "GET", "/api/appeals")
    check(status == 401, "anonymous access is denied")

    bad = opener()
    status, _ = request(bad, "POST", "/api/login", {"login": "purchase", "password": "wrong"})
    check(status == 401, "wrong password is rejected")

    employee = login("purchase")
    status, me = request(employee, "GET", "/api/me")
    check(status == 200 and me["user"]["role"] == "employee", "employee profile")

    status, meta = request(employee, "GET", "/api/meta")
    check(status == 200 and len(meta["categories"]) == 10, "meta has 10 categories")
    check(len(meta["statuses"]) == 7 and len(meta["types"]) == 3, "statuses and types")
    check(meta["users"] == [], "employee does not receive staff list")

    status, data = request(employee, "GET", "/api/appeals")
    check(status == 200 and len(data["appeals"]) == 21, "seeded appeals are visible", str(len(data["appeals"])))
    anon_appeal = find_appeal(data["appeals"], "A-021")
    check(anon_appeal is not None and anon_appeal["author"] == "Анна Смирнова", "author sees own anonymous appeal")

    status, _ = request(employee, "GET", "/api/dashboard")
    check(status == 403, "employee cannot open analytics")

    manager = login("head_purchase")
    status, data = request(manager, "GET", "/api/appeals")
    anon_appeal = find_appeal(data["appeals"], "A-021")
    check(anon_appeal is not None and anon_appeal["author"] == "Анонимный сотрудник", "anonymous author hidden from manager")

    status, detail = request(manager, "GET", f"/api/appeals/{anon_appeal['id']}")
    check(status == 200 and detail["appeal"]["anonymous"] is True, "anonymous appeal detail")

    status, dash = request(manager, "GET", "/api/dashboard")
    check(status == 200 and dash["kpi"]["total"] == 21, "dashboard totals")
    check(dash["kpi"]["overdue"] > 0, "overdue appeals are detected")
    check(len(dash["recurring"]) > 0, "recurring problems are detected")

    status, csv_bytes = request(manager, "GET", "/api/export.csv", raw=True)
    check(status == 200 and csv_bytes.startswith(b"\xef\xbb\xbf"), "csv export with BOM")

    status, created = request(
        employee,
        "POST",
        "/api/appeals",
        {
            "type": "Проблема",
            "category_id": next(c["id"] for c in meta["categories"] if c["name"] == "Межотдельное взаимодействие"),
            "title": "Тестовое обращение из дымового теста",
            "description": "Проверяем регистрацию, маршрутизацию и смену статусов в демонстрационной системе.",
            "expected_solution": "Убедиться, что обращение попало ответственному.",
            "priority": "Средний",
            "anonymous": False,
        },
    )
    check(status == 201 and created.get("code") == "A-022", "appeal is created with next code", str(created))
    appeal_id = created["id"]
    check(created.get("assignee") == "Наталья Крылова", "routing assigned category owner", str(created))

    status, _ = request(
        employee,
        "POST",
        f"/api/appeals/{appeal_id}/status",
        {"status": "В работе", "resolution": ""},
    )
    check(status == 403, "employee cannot change status")

    status, detail = request(manager, "GET", f"/api/appeals/{appeal_id}")
    check(status == 200 and detail["appeal"]["can_take"] is True, "manager can take appeal")

    status, _ = request(manager, "POST", f"/api/appeals/{appeal_id}/take")
    check(status == 200, "manager takes appeal")

    status, detail = request(manager, "GET", f"/api/appeals/{appeal_id}")
    check(detail["appeal"]["assignee"] == "Павел Громов", "manager became assignee")
    check(detail["appeal"]["can_manage"] is True, "assignee can manage appeal")

    status, _ = request(
        manager,
        "POST",
        f"/api/appeals/{appeal_id}/status",
        {"status": "Ожидает уточнения", "comment": "Уточните, пожалуйста, подразделение."},
    )
    check(status == 200, "status changes to clarification")

    status, detail = request(manager, "GET", f"/api/appeals/{appeal_id}")
    check(detail["appeal"]["first_response_at"] is not None, "first response time recorded")

    status, _ = request(manager, "POST", f"/api/appeals/{appeal_id}/comments", {"text": "Внутренняя заметка", "internal": True})
    check(status == 200, "internal comment is added")

    status, employee_detail = request(employee, "GET", f"/api/appeals/{appeal_id}")
    internal = [c for c in employee_detail["comments"] if c["internal"]]
    check(len(internal) == 0, "internal comment hidden from employee")

    status, _ = request(
        manager,
        "POST",
        f"/api/appeals/{appeal_id}/status",
        {"status": "Реализовано", "resolution": "Проверено: обращение обработано в рамках теста."},
    )
    check(status == 200, "appeal is completed")

    status, employee_detail = request(employee, "GET", f"/api/appeals/{appeal_id}")
    check(employee_detail["appeal"]["status"] == "Реализовано", "employee sees result")
    check(employee_detail["appeal"]["closed_at"] is not None, "closed date is set")

    status, _ = request(employee, "POST", f"/api/appeals/{appeal_id}/comments", {"text": "Спасибо, проверено."})
    check(status == 200, "author can comment")

    # Вложение
    payload = base64.b64encode(b"demo attachment").decode("ascii")
    status, created2 = request(
        employee,
        "POST",
        "/api/appeals",
        {
            "type": "Предложение",
            "category_id": meta["categories"][1]["id"],
            "title": "Обращение с вложением из теста",
            "description": "Проверяем загрузку и скачивание файла-заглушки в демонстрационной системе.",
            "priority": "Низкий",
            "attachment": {"filename": "readme.txt", "data": payload},
        },
    )
    check(status == 201, "appeal with attachment is created")
    status, blob = request(employee, "GET", f"/api/appeals/{created2['id']}/attachment", raw=True)
    check(status == 200 and blob == b"demo attachment", "attachment is downloaded")

    status, _ = request(
        employee,
        "POST",
        "/api/appeals",
        {
            "type": "Предложение",
            "category_id": meta["categories"][1]["id"],
            "title": "Запрещённый тип файла",
            "description": "Проверяем, что исполняемые файлы не принимаются системой.",
            "priority": "Низкий",
            "attachment": {"filename": "virus.exe", "data": payload},
        },
    )
    check(status == 400, "executable attachment is rejected")

    admin = login("admin")
    status, cats = request(admin, "GET", "/api/admin/categories")
    check(status == 200 and len(cats["categories"]) == 10, "admin sees categories")
    status, new_cat = request(
        admin,
        "POST",
        "/api/admin/categories",
        {"name": "Тестовая категория", "description": "Для проверки", "responsible_id": cats["categories"][0]["responsible_id"]},
    )
    check(status == 201, "admin creates category")
    status, _ = request(
        admin,
        "PATCH",
        f"/api/admin/categories/{new_cat['id']}",
        {"responsible_id": None, "active": False},
    )
    check(status == 200, "category can be disabled without responsible")

    status, _ = request(admin, "POST", "/api/logout")
    check(status == 200, "logout")

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
