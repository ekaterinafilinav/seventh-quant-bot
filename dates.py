"""
Поиск даты и времени в тексте сообщения.
Без нейросетей и без регулярных выражений: только datetime, calendar и правила.

Что понимает:
    сегодня / завтра / послезавтра
    через 3 дня / через неделю / через два месяца
    в пятницу / к понедельнику / до среды   (ближайший такой день)
    15.11 / 15.11.2026 / 15.11.26 / 15.11.2026г.
    15/11, 15-11, 2026-12-07
    20 мая / 20 мая 2027
    время: 14:30, 9 утра, 2 часа дня, в 9

Чего НЕ понимает (пока):
    "до конца недели", "к завтрашнему уроку", "в начале ноября"
    несколько дат в одном сообщении (берём первую)
    "каждый вторник" (повторяющиеся события)
"""

import calendar
from datetime import date, timedelta
import re
# ── СЛОВАРИ ───────────────────────────────────────────────────────────────

# Месяцы: родительный падеж ("20 мая") и именительный ("15 май")
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

# Дни недели: формы, которые пишут в чатах.
# 0 - понедельник, 6 - воскресенье (так считает Python)
WEEKDAY_FORMS = {
    "понедельник": 0, "понедельника": 0, "понедельнику": 0,
    "вторник": 1, "вторника": 1, "вторнику": 1,
    "среда": 2, "среду": 2, "среде": 2, "среды": 2, "средой": 2,
    "четверг": 3, "четверга": 3, "четвергу": 3,
    "пятница": 4, "пятницу": 4, "пятнице": 4, "пятницы": 4, "пятницей": 4,
    "суббота": 5, "субботу": 5, "субботе": 5, "субботы": 5, "субботой": 5,
    "воскресенье": 6, "воскресенья": 6, "воскресенью": 6, "воскресеньем": 6,
}

# Числа словами: "через два дня", "через пару дней"
NUMBERS = {
    "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10, "пару": 2, "пары": 2,
}

# Части суток: сколько часов прибавить. "2 часа дня" -> 2 + 12 = 14:00
PART_AFTER_HOURS = {
    "утра": 0, "утром": 0, "ночи": 0, "ночью": 0,
    "дня": 12, "днем": 12, "вечера": 12, "вечером": 12,
}
# Без слова "часа": "9 утра". Слова "дня" здесь НЕТ специально,
# иначе "через 3 дня" превратилось бы в "15:00".
PART_BARE = {
    "утра": 0, "утром": 0, "ночи": 0, "ночью": 0,
    "днем": 12, "вечера": 12, "вечером": 12,
}

# Слова-подсказки: рядом с ними короткое число "2.5" считаем датой
DATE_WORDS = {
    "до", "к", "срок", "дедлайн", "сдать", "сдай", "сдаем",
    "контрольная", "контрольную", "олимпиада", "олимпиаду",
    "конкурс", "встреча", "встречу", "экзамен", "зачет",
    "проект", "урок",
}


# ── МАЛЕНЬКИЕ ПОМОЩНИКИ ───────────────────────────────────────────────────

def only_digits(parts):
    """Все ли кусочки состоят только из цифр? ('15','11' -> да)"""
    for part in parts:
        if not part.isdigit():
            return False
    return True


def add_months(start, months):
    """Прибавить месяцы, не выходя за границы месяца.
    31 октября + 1 месяц = 30 ноября (31 ноября не существует)."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(start.day, last_day)
    return date(year, month, day)


def fix_year(found, today):
    """Год не указан, а дата сильно в прошлом? Значит, следующий год."""
    if found < today and (today - found).days > 30:
        try:
            return date(found.year + 1, found.month, found.day)
        except ValueError:
            return found
    return found


def normalize_words(text):
    """Приводим текст к удобному виду и режем на слова."""
    text = text.lower().replace("ё", "е")

    # / и - МЕЖДУ цифрами заменяем на точку: 15/11 -> 15.11
    chars = []
    for i, ch in enumerate(text):
        if ch in "/-":
            left = i > 0 and text[i - 1].isdigit()
            right = i + 1 < len(text) and text[i + 1].isdigit()
            chars.append("." if left and right else " ")
        else:
            chars.append(ch)
    text = "".join(chars)

    words = []
    for word in text.split():
        word = word.strip(".,!?;:()\"'«»")
        # "15.11.2026г" -> "15.11.2026"
        if word.endswith("г") and len(word) > 1 and word[-2].isdigit():
            word = word[:-1]
        words.append(word)
    return words


def has_date_word(words):
    """Есть ли слово-подсказка ('до', 'сдать', 'контрольная'...)."""
    for word in words:
        if word in DATE_WORDS:
            return True
    return False


# ── ПРАВИЛА ПОИСКА ДАТЫ ───────────────────────────────────────────────────

def relative_date(words):
    """'через 3 дня', 'через неделю', 'через два месяца'."""
    if "через" not in words:
        return None
    start = words.index("через")
    number = None
    unit = None
    for word in words[start + 1:start + 4]:
        if word.isdigit():
            number = int(word)
        elif word in NUMBERS:
            number = NUMBERS[word]
        elif word.startswith("недел"):
            unit = "weeks"
        elif word.startswith("месяц"):
            unit = "months"
        elif word.startswith("дн") or word.startswith("ден"):
            unit = "days"

    if unit is None:
        return None
    if number is None:
        number = 1

    today = date.today()
    if unit == "days":
        return today + timedelta(days=number)
    if unit == "weeks":
        return today + timedelta(weeks=number)
    return add_months(today, number)


def numeric_date(words):
    """Даты цифрами: 15.11, 15.11.2026, 15.11.26, 2026.12.07."""
    today = date.today()
    for word in words:
        if "." not in word:
            continue
        parts = word.split(".")
        if not only_digits(parts):
            continue

        year_given = False
        if len(parts) == 3 and len(parts[0]) == 4:
            # международный формат: 2026.12.07
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            year_given = True
        elif len(parts) in (2, 3):
            day, month = int(parts[0]), int(parts[1])
            year = today.year
            if len(parts) == 3:
                year = int(parts[2])
                if year < 100:          # 26 -> 2026
                    year += 2000
                year_given = True
            # Защита от дробей: "2.5 литра" - не дата
            if (len(parts[0]) == 1 and len(parts[1]) == 1
                    and not has_date_word(words)):
                continue
        else:
            continue

        try:
            found = date(year, month, day)
        except ValueError:
            continue        # 32.13 - такой даты не бывает
        if not year_given:
            found = fix_year(found, today)
        return found
    return None


def month_word_date(words):
    """Даты со словом-месяцем: '20 мая', '20 мая 2027'."""
    today = date.today()
    for i, word in enumerate(words):
        if word not in MONTHS:
            continue
        if i == 0 or not words[i - 1].isdigit():
            continue
        day = int(words[i - 1])
        month = MONTHS[word]
        year = today.year
        year_given = False
        if i + 1 < len(words) and words[i + 1].isdigit() and len(words[i + 1]) == 4:
            year = int(words[i + 1])
            year_given = True
        try:
            found = date(year, month, day)
        except ValueError:
            continue
        if not year_given:
            found = fix_year(found, today)
        return found
    return None


def weekday_date(words):
    """'в пятницу' - ближайший будущий такой день недели."""
    for word in words:
        if word in WEEKDAY_FORMS:
            today = date.today()
            shift = (WEEKDAY_FORMS[word] - today.weekday()) % 7
            return today + timedelta(days=shift)
    return None


def extract_date(text: str):
    """Главная функция поиска даты. Возвращает date или None."""
    words = normalize_words(text)

    # 1. Явные слова-сдвиги
    if "послезавтра" in words:
        return date.today() + timedelta(days=2)
    if "завтра" in words:
        return date.today() + timedelta(days=1)
    if "сегодня" in words:
        return date.today()

    # 2. Остальные правила: кто первый нашёл, тот и прав
    for finder in (relative_date, numeric_date, month_word_date, weekday_date):
        found = finder(words)
        if found is not None:
            return found

    return None


# ── ВРЕМЯ ─────────────────────────────────────────────────────────────────

def extract_time(text):
    """Ищет время. Возвращает строку '14:30' или None."""
    text = text.lower().replace("ё", "е")
    # здесь НЕ убираем ":" - он нужен
    words = [w.strip(".,!?;()\"'«»") for w in text.split()]

    # 1. Готовое время: 14:30
    for word in words:
        if ":" in word:
            parts = word.split(":")
            if len(parts) == 2 and only_digits(parts):
                hour = int(parts[0])
                minute = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return f"{hour:02d}:{minute:02d}"

    # 2. "9 утра", "2 часа дня"
    for i, word in enumerate(words):
        if not word.isdigit():
            continue
        hour = int(word)
        if hour > 23:
            continue
        nxt = words[i + 1] if i + 1 < len(words) else ""
        after = words[i + 2] if i + 2 < len(words) else ""
        if nxt.startswith("час"):
            if hour < 12 and after in PART_AFTER_HOURS:
                hour = hour + PART_AFTER_HOURS[after]
            return f"{hour:02d}:00"
        if nxt in PART_BARE:
            if hour < 12:
                hour = hour + PART_BARE[nxt]
            return f"{hour:02d}:00"

    # 3. Просто "в 9" или "к 9"
    for i, word in enumerate(words):
        if word.isdigit() and i > 0 and words[i - 1] in ("в", "к"):
            hour = int(word)
            if 0 <= hour <= 23:
                return f"{hour:02d}:00"

    return None


# ── КРАСИВЫЙ ВЫВОД ────────────────────────────────────────────────────────

WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def pretty(found) -> str:
    """15.11.2026 (воскресенье) или 'дата не найдена'."""
    if found is None:
        return "дата не найдена"
    return f"{found.strftime('%d.%m.%Y')} ({WEEKDAYS[found.weekday()]})"

# ── ПРОВЕРКА (работает только при прямом запуске файла) ───────────────────
if __name__ == "__main__":
    examples = [ ... ]  # этот блок оставь как есть
    ...

# ← вот тут блок if __name__ заканчивается, дальше идёт find_type на обычном уровне

def find_type(text):
    text_lower = text.lower()

    keywords = {
        "домашнее задание": ["дз", "домашк", "домашнее задание", "задано", "упражнение"],
        "олимпиада":        ["олимпиад", "конкурс", "соревнован"],
        "встреча":          ["встреча", "созвон", "собрание", "вебинар"],
        "дедлайн":          ["дедлайн", "сдать до", "срок сдачи", "не забыть до"],
    }

    for task_type, words in keywords.items():
        for word in words:
            if word in text_lower:
                return task_type

    return "неизвестно"