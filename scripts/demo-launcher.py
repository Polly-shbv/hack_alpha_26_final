"""Portable reviewer launcher. The archive contains a prebuilt frontend."""
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser


def main():
    parser = argparse.ArgumentParser(description='Запуск демо Альфа Контекст')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--use-current-python', action='store_true', help='Для разработчиков: зависимости уже установлены')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        raise SystemExit('Нужен Python 3.11–3.13 (рекомендуется 3.12). Например: python3.12 start_demo.py')
    root = Path(__file__).resolve().parent
    os.chdir(root)
    lock = root / 'requirements.lock.txt'
    if args.use_current_python:
        python = Path(sys.executable)
    else:
        venv = root / '.venv-demo'
        python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        marker = venv / '.installed-requirements'
        signature = hashlib.sha256(lock.read_bytes()).hexdigest() + f':{sys.version_info[:2]}'
        if not python.exists():
            print('Создаю окружение демо…', flush=True)
            subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        if not marker.exists() or marker.read_text() != signature:
            print('Первый запуск: устанавливаю зависимости. Нужен интернет…', flush=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(lock)], check=True)
            marker.write_text(signature)
    port = args.port
    for candidate in range(port, min(port + 20, 65536)):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', candidate))
            except OSError:
                continue
            port = candidate
            break
    else:
        raise SystemExit('Порты заняты. Закройте другое демо или укажите --port.')
    url = f'http://127.0.0.1:{port}'
    env = os.environ.copy()
    # A colleague's machine must start with its own local fixture database.
    for key in list(env):
        if key.startswith('ALFA_'):
            env.pop(key)
    env.update(ALFA_DB=str(root / 'data/local/alfa.sqlite3'), ALFA_ORIGIN=url, ALFA_AUTH_MODE='demo')
    server = subprocess.Popen([str(python), '-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', str(port)], env=env)
    try:
        opener = build_opener(ProxyHandler({}))
        for _ in range(120):
            if server.poll() is not None:
                raise SystemExit('Не удалось запустить сервер. Ошибка показана выше.')
            try:
                with opener.open(url + '/api/v1/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.25)
        else:
            raise SystemExit('Сервер не успел запуститься. Ошибка показана выше.')
        print(f'\nДемо открыто: {url}/select-company\nДля остановки нажмите Ctrl+C в этом окне.\n', flush=True)
        if not args.no_browser:
            webbrowser.open(url + '/select-company')
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
