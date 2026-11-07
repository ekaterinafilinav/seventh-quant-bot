# 1. Берем официальный образ Python (версия 3.11 - стабильная)
FROM python:3.11-slim

# 2. Устанавливаем системные зависимости (Tesseract + русский язык)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# 3. Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# 4. Копируем только requirements.txt и устанавливаем библиотеки
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Копируем весь остальной код
COPY . .

# 6. Команда для запуска бота
CMD ["python", "main.py"]