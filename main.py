"""
Бот-органайзер «Седьмой квант».
Этап 2: меню с кнопками + сохранение задач в SQLite.

Запуск: python main.py
"""
import asyncio
import logging
import pytesseract
from PIL import Image
import requests
import os
from dotenv import load_dotenv
from datetime import datetime
from maxapi import Bot, Dispatcher
from maxapi.filters.command import CommandStart
from maxapi.types import CallbackButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from dates import extract_date, extract_time, pretty, find_type
from db import add_task, get_tasks, init_db, get_due_tasks, mark_as_reminded, mark_as_done, delete_old_completed_tasks, delete_task, get_user_stats, connect
import sys

logging.basicConfig(level=logging.INFO)
load_dotenv()
if sys.platform == "win32":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    pytesseract.pytesseract.tesseract_cmd = "tesseract"
bot = Bot(token=os.getenv("BOT_TOKEN"))
waiting_for_delete_input = None # Может быть "date", "num" или None
dp = Dispatcher()    # диспетчер решает, какой обработчик вызвать
waiting_for_delete = None  # Здесь будем хранить, чего мы ждем: 'num' или 'date'

def recognize_text_from_image(image_url):
    """Скачивает картинку по ссылке и распознает на ней текст"""
    temp_filename = "temp_image.jpg"
    try:
        # 1. Скачиваем картинку
        response = requests.get(image_url, timeout=10)

        if response.status_code != 200:
            print(f"Не удалось скачать картинку. Код ответа: {response.status_code}")
            return None

        # 2. Сохраняем во временный файл
        with open(temp_filename, 'wb') as f:
            f.write(response.content)

        # 3. Открываем и распознаём (русский + английский)
        img = Image.open(temp_filename)
        text = pytesseract.image_to_string(img, lang='rus+eng')

        # 4. Печатаем результат в терминал для отладки
        print(f"=== РАСПОЗНАНО С КАРТИНКИ ===\n{text}\n=== КОНЕЦ ===")

        return text.strip() if text.strip() else None

    except Exception as e:
        print(f"Ошибка при чтении картинки: {e}")
        return None

    finally:
        # Удаляем временный файл ВСЕГДА — даже если была ошибка
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except OSError:
                pass
# ─── КОДЫ КНОПОК (payload) ────────────────────────────────────────────────
BTN_ADD = "add_task"
BTN_LIST = "my_tasks"
BTN_HELP = "help"
BTN_BACK = "back"
BTN_PROFILE = "profile"
BTN_DELETE_PREFIX = "del_"


# ─── КЛАВИАТУРЫ ───────────────────────────────────────────────────────────
def main_menu():
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="👤 Профиль", payload=BTN_PROFILE))
    kb.row(CallbackButton(text="📝 Добавить задачу", payload=BTN_ADD))
    kb.row(CallbackButton(text="📋 Мои задачи", payload=BTN_LIST))
    kb.row(CallbackButton(text="❓ Помощь", payload=BTN_HELP))
    return kb.as_markup()


def back_menu():
    """Кнопка возврата в меню."""
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="⬅️ В меню", payload=BTN_BACK))
    return kb.as_markup()


# ─── ВСПОМОГАТЕЛЬНОЕ ──────────────────────────────────────────────────────
def pretty_iso(iso_text):
    """'2026-11-15' -> '15.11.2026' (для красивого списка)."""
    year, month, day = iso_text.split("-")
    return f"{day}.{month}.{year}"


# ─── ЭКРАН /start ─────────────────────────────────────────────────────────
@dp.message_created(CommandStart())
async def start(event: MessageCreated):
    await event.message.answer(
        text="Привет! Я бот-органайзер «Седьмой квант».\n"
             "Перешлите мне сообщение с датой — я сохраню задачу "
             "и не потеряй ее"
             "Выберите, что будем делать:",
        attachments=[main_menu()],
    )


# ─── НАЖАТИЯ НА КНОПКИ ────────────────────────────────────────────────────
@dp.message_callback()
async def on_button(event: MessageCallback):
    payload = event.callback.payload
    if payload == "ignore":
        return

    # ── Удаление по дате ──
    elif payload == "del_by_date":
        await event.message.answer(
            text="📅 Введите диапазон дат в формате:\n"
                 "`дд.мм.гггг(время)-дд.мм.гггг(время)`\n\n"
                 "Пример: `06.11.2026(00:00)-07.11.2026(23:59)`"
        )
        # Здесь мы должны запомнить, что ждем ввод.
        # Самый простой способ (для хакатона) - попросить пользователя написать это в чат.
        return

    # ── Удаление по номеру ──
    elif payload == "del_by_num":
        await event.message.answer(
            text="🔢 Введите номера задач через дефис (например, `1-3` или `2-2`):"
        )
        return

    # ── Удалить всё ──
    elif payload == "del_all":
        kb = InlineKeyboardBuilder()
        kb.row(
            CallbackButton(text="✅ Да, удалить ВСЁ", payload="confirm_del_all"),
            CallbackButton(text="❌ Отмена", payload="cancel_del")
        )
        await event.message.answer(
            text="⚠️ Вы уверены, что хотите удалить ВСЕ задачи?",
            attachments=[kb.as_markup()]
        )
        return

    elif payload == "confirm_del_all":
        user_id = event.callback.user.user_id
        conn = connect()
        conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await event.message.answer(text="🧨 Все задачи удалены!", attachments=[main_menu()])
        return

    # ── Удаление задачи ──
    # ── Удаление задачи: Шаг 1 (подтверждение) ──
    if payload.startswith(BTN_DELETE_PREFIX):
        task_id = int(payload.replace(BTN_DELETE_PREFIX, ""))

        # Создаем кнопки "Да, удалить" и "Отмена"
        kb = InlineKeyboardBuilder()
        kb.row(
            CallbackButton(text="✅ Да, удалить", payload=f"confirm_del_{task_id}"),
            CallbackButton(text="❌ Отмена", payload="cancel_del")
        )

        await event.message.answer(
            text="🤔 Вы уверены, что хотите удалить эту задачу?",
            attachments=[kb.as_markup()]
        )
        return

    # ── Шаг 2: Пользователь нажал "Да, удалить" ──
    elif payload.startswith("confirm_del_"):
        task_id = int(payload.replace("confirm_del_", ""))
        user_id = event.callback.user.user_id

        # Удаляем из базы
        delete_task(task_id, user_id)

        # "Рассыпаем" (удаляем) само сообщение с задачей, если платформа позволяет
        try:
            await event.message.delete()
        except Exception as e:
            # Если платформа не разрешает удалять сообщение, просто выводим текст
            await event.message.edit(text="🗑 Задача удалена.")

        # Отправляем подтверждение
        await event.message.answer(
            text="✅ Задача успешно удалена!",
            attachments=[back_menu()]
        )
        return

    # ── Шаг 3: Пользователь нажал "Отмена" ──
    elif payload == "cancel_del":
        # Просто меняем сообщение на "Отменено"
        try:
            await event.message.delete()
        except Exception:
            await event.message.edit(text="Отменено.")
        await event.message.answer(
            text="Главное меню. Выберите действие:",
            attachments=[main_menu()]
        )
        return
    # ── Добавить задачу: показываем инструкцию ──
    if payload == BTN_ADD:
        await event.message.answer(
            text="📝 Добавление задачи...",
            attachments=[back_menu()],
        )

    # ── Мои задачи: достаём из базы ──
    # ── Мои задачи: достаём из базы ──
    # ── Мои задачи ──
    elif payload == BTN_LIST:
        user_id = event.callback.user.user_id
        rows = get_tasks(user_id)

        if not rows:
            await event.message.answer(
                text="📋 Пока пусто.\n\n"
                     "Напишите мне сообщение с датой, например:\n"
                     "«Олимпиада по физике 15.11 в 14:30» — и я сохраню.",
                attachments=[back_menu()],
            )
            return

        # Формируем одно большое сообщение
        lines = [f"📋 Ваши задачи (всего: {len(rows)}):", ""]
        for number, (task_id, text, task_date, task_time, task_type) in enumerate(rows, start=1):
            when = pretty_iso(task_date) if task_date else "без даты"
            if task_time:
                when += f" {task_time}"
            short = text if len(text) <= 50 else text[:50] + "…"
            lines.append(f"{number}. [{task_type}] {when}\n«{short}»\n")

        full_text = "\n".join(lines)

        # Создаем клавиатуру внизу
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text="🗑 Удалить по дате", payload="del_by_date"))
        kb.row(CallbackButton(text="🔢 Удалить по номеру", payload="del_by_num"))
        kb.row(CallbackButton(text="🧨 Удалить всё", payload="del_all"))
        kb.row(CallbackButton(text="⬅️ В меню", payload=BTN_BACK))

        await event.message.answer(
            text=full_text,
            attachments=[kb.as_markup()],
        )
    # ── Профиль ──
    elif payload == BTN_PROFILE:
        user_id = event.callback.user.user_id
        active, done = get_user_stats(user_id)

        # Можно достать имя пользователя, если оно есть в event
        # (если нет — напишем просто "Твой профиль")
        name = event.callback.user.first_name if hasattr(event.callback.user, 'first_name') else "Ученик"

        await event.message.answer(
            text=f"👤 **Твой профиль**\n\n"
                 f"Имя: {name}\n"
                 f"🟢 Активные задачи: {active}\n"
                 f"✅ Завершено: {done}\n\n"
                 f"Так держать! 💪",
            attachments=[back_menu()],
        )

    # ── Помощь ──
    elif payload == BTN_HELP:
        await event.edit(
            text="❓ Помощь\n\n"
                 "1. Отправьте сообщение с датой — я сохраню задачу.\n"
                 "2. «Мои задачи» — покажу список, ближайшие сверху.\n"
                 "3. Задачи лежат в файле tasks.db и не пропадают "
                 "при перезапуске бота.\n\n"
                 "Что я понимаю:\n"
                 "• сегодня / завтра / послезавтра\n"
                 "• 15.11 или 15.11.2026\n"
                 "• 20 мая, 20 мая 2027\n"
                 "• в пятницу, через 3 дня, через неделю\n"
                 "• время: в 14:30, в 9 утра",
            attachments=[back_menu()],
        )

    # ── В меню (и любая неизвестная кнопка) ──
    else:
        await event.edit(
            text="Главное меню. Выберите действие:",
            attachments=[main_menu()],
        )


# ─── ОБЫЧНЫЕ СООБЩЕНИЯ (сохраняем задачи) ─────────────────────────────────
# ВАЖНО: этот обработчик обязан быть ПОСЛЕДНИМ,
# иначе он перехватит /start и кнопки.

@dp.message_created()
async def fallback(event: MessageCreated):
    global waiting_for_delete_input
    if waiting_for_delete_input:
        user_id = event.message.sender.user_id
        if waiting_for_delete_input == "date":
            # Тут типа парсим "06.11.2026(00:00)-07.11.2026(23:59)"
            await event.message.answer("Удаляю по дате... (код в разработке)")
        elif waiting_for_delete_input == "num":
            # Тут парсим "1-3"
            await event.message.answer("Удаляю по номеру... (код в разработке)")

        waiting_for_delete_input = None  # Сбрасываем флаг
        return
    body = event.message.body
    text = body.text if body and body.text else ""

    # --- НОВОЕ (Этап 4): Если пользователь прислал картинку ---
    if not text and body.attachments:
        attachment = body.attachments[0]
        if attachment.type == "image":
            await event.message.answer("👀 Вижу картинку! Сейчас прочитаю текст...")
            image_url = attachment.payload.url

            # Запускаем функцию из Этапа 4
            recognized_text = recognize_text_from_image(image_url)

            if recognized_text:
                text = recognized_text  # Заменяем пустой текст на распознанный с картинки
            else:
                await event.message.answer("😔 Не удалось распознать текст на картинке.")
                return

    # Если текста нет вообще (ни своего, ни с картинки)
    if not text:
        await event.message.answer(
            text="Я пока понимаю только текст и картинки 😔\n"
                 "Пришлите сообщение с датой, например: "
                 "«контрольная 15.11 в 14:30».",
            attachments=[main_menu()],
        )
        return

    sender = event.message.sender
    user_id = sender.user_id if sender else 0

    # Ищем дату, время и ТИП (Этап 3)
    found = extract_date(text)
    clock = extract_time(text)
    task_type = find_type(text)  # <--- Вот он, поиск типа!

    # Ищем дату, время и ТИП (Этап 3)
    found = extract_date(text)
    clock = extract_time(text)
    task_type = find_type(text)  # <--- Вот он, поиск типа!

    # Ищем дату, время и ТИП (Этап 3)
    found = extract_date(text)
    clock = extract_time(text)
    task_type = find_type(text)

    # 1. Защита: если есть ВРЕМЯ, но нет ДАТЫ
    if found is None and clock is not None:
        await event.message.answer(
            text=f"⏰ Я вижу время ({clock}), но не вижу ДАТЫ! 😔\n"
                 f"Напишите, КОГДА это нужно (например, «сегодня», «15.11» или «завтра»).",
            attachments=[main_menu()],
        )
        return

    # 2. Защита: если есть ДАТА, но нет ВРЕМЕНИ
    if found is not None and clock is None:
        await event.message.answer(
            text=f"📅 Я вижу дату ({pretty(found)}), но не вижу ВРЕМЕНИ! 😔\n"
                 f"Напишите, ВО СКОЛЬКО это нужно (например, «в 14:30» или «в 9 утра»).",
            attachments=[main_menu()],
        )
        return

    # 3. Защита: если нет ни даты, ни времени
    if found is None and clock is None:
        await event.message.answer(
            text="Я не вижу ни даты, ни времени 😔\n"
                 "Напишите, КОГДА и ВО СКОЛЬКО это нужно, например:\n"
                 "«Контрольная 15.11 в 14:30».",
            attachments=[main_menu()],
        )
        return

    # Сохраняем
    task_date = str(found) if found else None

    # --- ВАЖНО: Добавили task_type ---
    add_task(user_id=user_id, text=text, task_date=task_date, task_time=clock, task_type=task_type)

    # Красивый ответ
    if found and clock:
        when = f"{pretty(found)} в {clock}"
    elif found:
        when = pretty(found)
    else:
        when = f"без даты, время {clock}"

    short = text if len(text) <= 200 else text[:200] + "..."

    # Отвечаем пользователю и показываем ТИП
    await event.message.answer(
        text=f"✅ Сохранил: {when}\n"
             f"📂 Тип: {task_type}\n"
             f"«{short}»\n\n"
             f"Открыть список — кнопка «Мои задачи».",
        attachments=[main_menu()]
    )

# ─── ЗАПУСК ───────────────────────────────────────────────────────────────
def print_new_task(task_date, task_time):
    """Превращает 2026-11-06 в 06.11.2026 и печатает красивую строку"""
    if task_date and "-" in task_date:
        year, month, day = task_date.split("-")
        pretty_date = f"{day}.{month}.{year}"
    else:
        pretty_date = task_date or "без даты"

    print(f"Задача выполнена! на: {pretty_date} в {task_time}")


async def reminder_loop():
    """Фоновые напоминания + жесткая очистка базы каждую секунду"""
    try:
        while True:
            # 1. Сначала отправляем напоминания (пока задача еще не удалена)
            try:
                due_tasks = get_due_tasks()
                for task_id, user_id, text, task_date, task_time in due_tasks:
                    try:
                        await bot.send_message(
                            user_id=user_id,
                            text=f"🔔 НАПОМИНАНИЕ!\n\n"
                                 f"Задача: «{text}»\n"
                                 f"Когда: {task_date} в {task_time}"
                        )
                        mark_as_reminded(task_id)
                        mark_as_done(task_id)
                        print(f"✅ Напоминание отправлено: {text}")
                    except Exception as e:
                        print(f"Не смог отправить напоминание {user_id}: {e}")
            except Exception as e:
                print(f"Ошибка в напоминаниях: {e}")

            # 2. Затем ЖЕСТКО чистим всё, что уже прошло или битое
            try:
                delete_old_completed_tasks()
            except Exception as e:
                print(f"❌ ОШИБКА ОЧИСТКИ: {e}")

            await asyncio.sleep(1)

    except asyncio.CancelledError:
        # Это ловит момент, когда мы нажали Ctrl+C
        print("Выключение...")
        await asyncio.sleep(1)

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Бот выключен")