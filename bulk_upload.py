import os
import requests
import time
import argparse

parser = argparse.ArgumentParser(description="Загрузка документов в Neo4j")
parser.add_argument("--vision", action="store_true", help="Использовать мощный VLM парсер для PDF (долго и платно)")
args = parser.parse_args()

# Укажите порт, на котором запущен ваш сервер
API_URL = "http://localhost:8001/api/upload"
SOURCE_DIR = "base_sources"

if not os.path.exists(SOURCE_DIR):
    os.makedirs(SOURCE_DIR)
    print(f"📁 Создана папка '{SOURCE_DIR}'. Положите туда PDF или TXT файлы и запустите скрипт снова.")
    exit(0)

files = [f for f in os.listdir(SOURCE_DIR) if f.endswith('.pdf') or f.endswith('.txt') or f.endswith('.docx') or f.endswith('.md')]

if not files:
    print(f"⚠️ Папка '{SOURCE_DIR}' пуста. Добавьте документы.")
    exit(0)

print(f"🚀 Найдено {len(files)} документов для загрузки.")
print(f"👁️ Использовать Vision (VLM): {'ДА' if args.vision else 'НЕТ'}")
print(f"ВНИМАНИЕ: Убедитесь, что сервер {API_URL} запущен!")
time.sleep(2)

for filename in files:
    filepath = os.path.join(SOURCE_DIR, filename)
    print(f"📤 Отправка {filename} на сервер...")
    
    try:
        with open(filepath, 'rb') as f:
            response = requests.post(API_URL, files={'file': f}, data={'use_vision': args.vision})
        
        if response.status_code == 200:
            print(f"✅ {filename} отправлен в очередь на обработку (Job ID: {response.json().get('job_id')})")
        else:
            print(f"❌ Ошибка загрузки {filename}: HTTP {response.status_code} - {response.text}")
    except requests.exceptions.ConnectionError:
        print("❌ Ошибка соединения! Сервер не запущен на порту 8001.")
        break
        
    time.sleep(1) # Небольшая пауза, чтобы не дудосить свой же сервер

print("\n🎉 Все файлы отправлены на сервер!")
print("Их обработка идёт в фоновом режиме (посмотри логи консоли сервера).")
