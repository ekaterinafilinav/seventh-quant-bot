"""
Работа с базой данных SQLite.

База - это обычный файл tasks.db рядом с ботом.
При перезапуске бота файл остаётся на диске, поэтому задачи не пропадают.
"""
import re
import sqlite3
from datetime import datetime
def find_date(text):
    # Ищем сначала полный формат: ДД.ММ.ГГГГ
    # \d{2} — две цифры, \d{4} — четыре цифры (год)
    match = re.search(r'\d{2}\.\d{2}\.\d{4}', text)
    if match:
        return match.group()  # вернуть найденный кусок текста

    # Если полной даты нет — ищем короткую: ДД.ММ
    match = re.search(r'\d{2}\.\d{2}', text)
    if match:
        return match.group()

    # Если ничего не нашли — вернём None (по-русски "ничего")
    return None
def find_type(text):
    # Приводим текст к нижнему регистру, чтобы "ДЗ" и "дз" считались одинаково
    text_lower = text.lower()

    # Словари: {тип: [список слов-подсказок]}
    # Если хотя бы одно слово из списка встретится в тексте — это и есть тип
    keywords = {
        "домашнее задание": ["дз", "домашк", "домашнее задание", "задано", "упражнение"],
        "олимпиада":        ["олимпиад", "конкурс", "соревнован"],
        "встреча":          ["встреча", "созвон", "собрание", "вебинар"],
        "дедлайн":          ["дедлайн", "сдать до", "срок сдачи", "не забыть до"],
    }

    # Проходимся по каждому типу
    for task_type, words in keywords.items():
        for word in words:
            if word in text_lower:
                return task_type

    # Если ничего не нашли — вернём "неизвестно"
    return "неизвестно"

from datetime import date

DB_NAME = "tasks.db"


def connect():
    """Открывает базу. Если файла ещё нет - SQLite создаст его сам."""
    return sqlite3.connect(DB_NAME)


def init_db():
    """Создаёт таблицу tasks, если её ещё нет. Вызываем при старте бота."""
    con = connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            task_date TEXT,
            task_time TEXT,
            task_type TEXT,
            done INTEGER DEFAULT 0,
            created_at TEXT,
            reminded INTEGER DEFAULT 0,
            last_remind TEXT
        )
    """)
    con.commit()
    con.close()


def add_task(user_id, text, task_date, task_time, task_type="Другое"):
    conn = connect()
    cursor = conn.cursor()
    # Добавили task_type в запрос
    cursor.execute(
        "INSERT INTO tasks (user_id, text, task_date, task_time, task_type) VALUES (?, ?, ?, ?, ?)",
        (user_id, text, task_date, task_time, task_type)
    )
    conn.commit()
    conn.close()


def get_tasks(user_id):
    con = connect()
    rows = con.execute(
        "SELECT id, text, task_date, task_time, task_type FROM tasks "
        "WHERE user_id = ? AND done = 0 "
        "ORDER BY task_date IS NULL, task_date, task_time",
        (user_id,)
    ).fetchall()
    con.close()
    return rows


def get_due_tasks():
    """Ищет задачи, время которых уже наступило, и по которым мы еще не напоминали"""
    conn = connect()  # Используем вашу функцию connect() из db.py
    cursor = conn.cursor()

    # Получаем текущее время.
    # Важно: дата в базе у вас хранится в формате '2027-12-11' (потому что вы делаете str(found))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Ищем задачи, где дата+время меньше или равны текущему времени,
    # и по которым еще не было напоминания (reminded = 0)
    cursor.execute("""
        SELECT id, user_id, text, task_date, task_time 
        FROM tasks 
        WHERE task_date IS NOT NULL 
          AND task_time IS NOT NULL 
          AND (reminded IS NULL OR reminded = 0)
          AND done = 0
          AND (task_date || ' ' || task_time) <= ?
    """, (now_str,))

    tasks = cursor.fetchall()
    conn.close()
    return tasks


def mark_as_reminded(task_id):
    """Ставит галочку (1), что напоминание по этой задаче уже отправлено"""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

def mark_as_done(task_id):
    """Помечает задачу выполненной, чтобы она пропала из списка 'Мои задачи'"""
    conn = connect()
    conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def delete_old_completed_tasks():
    """Жёсткая очистка: сносит всё, что просрочено, пустое или битое"""
    conn = connect()
    cursor = conn.cursor()

    # 1. Сразу сносим всё, где нет даты или времени (защита от None)
    cursor.execute("DELETE FROM tasks WHERE task_date IS NULL OR task_time IS NULL OR task_date = '' OR task_time = ''")

    # 2. Получаем текущее время (без секунд)
    now = datetime.now().replace(second=0, microsecond=0)

    # 3. Достаем ВСЕ оставшиеся задачи
    cursor.execute("SELECT id, task_date, task_time FROM tasks")
    rows = cursor.fetchall()

    for task_id, t_date, t_time in rows:
        try:
            # Парсим дату и время
            task_dt = datetime.strptime(f"{t_date} {t_time}", "%Y-%m-%d %H:%M").replace(second=0, microsecond=0)

            # Если время задачи наступило или прошло — удаляем (даже если done=0)
            if task_dt <= now:
                cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                print(f"🗑 Удалена старая задача №{task_id} (время {t_date} {t_time} уже прошло)")

        except ValueError:
            # Если формат кривой — тоже удаляем, чтобы не засорять базу
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            print(f"🗑 Удалена битая задача №{task_id}")

    conn.commit()
    conn.close()

def get_all_active_tasks():
    """Берет ВСЕ невыполненные задачи, чтобы будильник сам решил, когда напоминать"""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, text, task_date, task_time, last_remind FROM tasks WHERE done = 0 AND task_date IS NOT NULL AND task_time IS NOT NULL")
    tasks = cursor.fetchall()
    conn.close()
    return tasks

def update_last_remind(task_id, remind_text):
    """Запоминает, что мы уже напомнили за этот интервал"""
    conn = connect()
    conn.execute("UPDATE tasks SET last_remind = ? WHERE id = ?", (remind_text, task_id))
    conn.commit()
    conn.close()


def get_user_stats(user_id):
    """Считает, сколько задач выполнено и сколько еще в строю"""
    conn = connect()
    cursor = conn.cursor()

    # Считаем активные задачи (done = 0)
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND done = 0", (user_id,))
    active = cursor.fetchone()[0]

    # Считаем выполненные задачи (done = 1)
    # ВНИМАНИЕ: у нас есть автоудаление выполненных задач.
    # Если они удаляются, статистика выполненных будет сбрасываться.
    # Но пока пусть считает то, что есть.
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND done = 1", (user_id,))
    done = cursor.fetchone()[0]

    conn.close()
    return active, done

def delete_task(task_id, user_id):
    """Удаляет задачу по ID, но только если она принадлежит этому пользователю"""
    conn = connect()
    # Удаляем только если ID совпадает и это задача этого пользователя (защита)
    conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
    conn.commit()
    conn.close()