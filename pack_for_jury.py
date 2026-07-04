import os
import zipfile
import datetime

def pack_project():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    zip_filename = f"NornGraph_Hackathon_Submission_{timestamp}.zip"
    zip_filepath = os.path.join(project_dir, zip_filename)

    # Папки и файлы, которые НЕ нужно класть в архив
    exclude_dirs = {'.git', 'venv', '__pycache__', '.pytest_cache', 'scratch', 'archive'}
    exclude_files = {zip_filename, 'pack_for_jury.py'}

    print(f"📦 Собираем проект в архив: {zip_filename}")
    
    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(project_dir):
            # Исключаем ненужные директории
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            
            for file in files:
                if file in exclude_files:
                    continue
                
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, project_dir)
                
                # ВАЖНО: убеждаемся, что .env попадает в архив
                if file == '.env':
                    print(f"🔑 Добавлен файл с ключами: {arcname}")
                
                zipf.write(file_path, arcname)

    print("\n✅ Успешно! Проект упакован.")
    print(f"📁 Файл: {zip_filepath}")
    print("Отправьте этот ZIP-архив жюри. Внутри уже есть файл .env со всеми ключами,")
    print("так что жюри сможет просто запустить `docker-compose up -d`.")

if __name__ == "__main__":
    pack_project()
