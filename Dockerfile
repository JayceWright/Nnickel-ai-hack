FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаем папки для данных, если их нет
RUN mkdir -p data/docs

# Открываем порт
EXPOSE 8001

# Команда для запуска сервера
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
