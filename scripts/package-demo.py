"""Build an allowlisted reviewer ZIP; never copy a working database or .env."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
README = '''# Альфа Контекст — демо для просмотра

Распакуйте архив целиком. Нужен Python 3.11–3.13 (рекомендуется 3.12) и интернет при первом
запуске для установки зависимостей. Node.js, API-ключ и исходные CSV не нужны.

## macOS

Дважды нажмите «Запустить демо.command». Если macOS не разрешает открыть файл,
откройте Терминал в распакованной папке и выполните:

    python3 start_demo.py

Если системный Python старее или новее, установите Python 3.12 и выполните:

    python3.12 start_demo.py

## Windows

При установке Python включите «Add Python to PATH». Дважды нажмите
«Запустить демо.bat» либо выполните в терминале распакованной папки:

    py -3.12 start_demo.py

## Linux

    python3 start_demo.py

В Debian/Ubuntu может дополнительно требоваться пакет python3-venv.

Браузер откроется автоматически. Окно запуска оставьте открытым; Ctrl+C
останавливает приложение. Обычно адрес http://127.0.0.1:8765/select-company.
Если порт занят, программа выберет следующий и покажет его в окне запуска.
Повторный запуск использует уже установленное окружение.

## Что посмотреть

1. Выберите компанию и роль, откройте её и нажмите обновление анализа.
2. Откройте новость: факт, связь с компанией, условный риск и основания.
3. Посмотрите карту влияния, сравнение компаний, историю и сохранение.
4. В «Источниках» можно импортировать один PDF из data/fixtures/test-documents.
5. В «Источниках» нажмите «Собрать и проанализировать». Расписание выключено; статус готового пакета обновляется без перезагрузки страницы.
6. AI-помощник показывает «Скоро» — это запланированная заглушка.

Это тестовый режим интерпретации: стартовые новости и synthetic-bank PDF
учебные, материалы URL/RSS сохраняют внешнее происхождение. Корпус банка
включает проверенные OCR-копии девяти материалов из папки кейса; ссылки на
оригиналы доступны в приложении. Сбор Lenta/РИА/ТАСС запускается кнопкой. Без ключа интерпретация локальная. При настроенной Gemma пакет новостей для трёх компаний отправляется одной генерацией; автоматических повторов нет. ТАСС может предоставлять только RSS-анонсы. Версия 1.4 использует прямой Google API (gemma-4-26b-a4b-it). Для Gemma нужен собственный ключ в локальном .env и отдельное согласие на передачу обезличенного профиля; ключ автора не включён. Платные модели не используются. Профили трёх компаний получены
из предоставленных CSV, они не синтетические. Реестр происхождения и замены
данных — docs/test-data-replacement.md.

Локальные изменения сохраняются в data/local. Архив не содержит историю
автора, исходные CSV, персональные связи, .env или API-ключи. Не присылайте
коллегам свой data/local после добавления собственных материалов.

Сборка интерфейса уже включена. Исходники backend включены для запуска;
это пакет для просмотра, полный проект для разработки передаётся отдельно.
Запуск проверен на macOS. Windows/Linux описаны, но на этих ОС не проверены.
'''
MAC = '''#!/bin/sh
cd "$(dirname "$0")" || exit 1
TASK_PYTHON=""
for candidate in python3.12 python3.13 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    TASK_PYTHON="$candidate"
    break
  fi
done
if [ -z "$TASK_PYTHON" ]; then
  echo "Установите Python 3.12, затем повторите запуск."
  read -r reply
  exit 1
fi
"$TASK_PYTHON" start_demo.py
result=$?
if [ "$result" -ne 0 ]; then
  echo "Запуск завершился с ошибкой. Текст ошибки выше. Нажмите Enter."
  read -r reply
fi
exit "$result"
'''
WINDOWS = '''@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python start_demo.py
) else (
  py -3.12 -c "import sys" >nul 2>nul
  if errorlevel 1 (py -3 start_demo.py) else (py -3.12 start_demo.py)
)
if errorlevel 1 pause
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT / 'exports/alfa-context-demo.zip')
    args = parser.parse_args()
    source = args.source.resolve()
    files = {}
    for folder, pattern in [('backend/app', '*.py'), ('backend/migrations', '*.sql'), ('dist', '*'), ('data/fixtures', '*')]:
        for path in sorted((source / folder).rglob(pattern)):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts:
                files[path.relative_to(source).as_posix()] = path.read_bytes()
    for name in ['backend/__init__.py', 'requirements.lock.txt', 'requirements-ocr-macos.lock.txt', 'THIRD_PARTY_NOTICES.md', 'docs/test-data-replacement.md', 'docs/demo-script.md', 'docs/sharing.md', 'docs/middle-steps.md', 'docs/bank-corpus.md', 'docs/realtime-demo.md', 'docs/manual-batch.md', 'docs/google-api.md', '.env.example']:
        path = source / name
        if path.exists():
            files[name] = path.read_bytes()
    for path in sorted((source/'data/reference/bank').glob('*.json')):
        if path.name!='manifest.pending.json':files[path.relative_to(source).as_posix()]=path.read_bytes()
    if 'dist/index.html' not in files or 'data/fixtures/profiles.json' not in files:
        raise SystemExit('Не хватает сборки интерфейса или demo fixtures.')
    # ZoneInfo needs an IANA database on Windows; version is intentionally pinned.
    files['requirements.lock.txt'] += b'\ntzdata==2025.2\n'
    files['start_demo.py'] = (ROOT / 'scripts/demo-launcher.py').read_bytes()
    files['README.md'] = README.encode()
    files['Запустить демо.command'] = MAC.encode()
    files['Запустить демо.bat'] = WINDOWS.replace('\n', '\r\n').encode('utf-8')
    version=source/'docs/audit/version.json'
    if version.exists():files['build-info.json']=version.read_bytes()
    manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
    files['manifest.sha256.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            entry = zipfile.ZipInfo('alfa-context-demo/' + name)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | (0o755 if name.endswith('.command') else 0o644)) << 16
            archive.writestr(entry, raw)
    print(f'{args.output}: {len(files)} файлов, {args.output.stat().st_size:,} байт')


if __name__ == '__main__':
    main()
