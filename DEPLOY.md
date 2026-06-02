# Быстрый деплой Матч Поинт

## Render

1. Загрузить проект в GitHub.
2. Открыть Render: https://render.com/
3. New -> Blueprint.
4. Выбрать GitHub-репозиторий с проектом.
5. Render прочитает `render.yaml` и создаст web service.
6. После запуска сайт будет доступен по ссылке Render.

## Важное про базу

Проект использует SQLite. Для сохранения регистраций, команд, турниров и чата после перезапуска нужен persistent disk.

В `render.yaml` уже указан диск:

```yaml
disk:
  name: match-point-data
  mountPath: /data
  sizeGB: 1
```

На бесплатном тарифе Render сайт можно запустить без диска, но данные после перезапуска могут потеряться.

## Переменные окружения

```text
DATA_DIR=/data
```

Приложение само использует `PORT`, который выдаст Render.

## PythonAnywhere Free

1. Создать бесплатный аккаунт на https://www.pythonanywhere.com/
2. Открыть вкладку `Consoles` -> `Bash`.
3. Выполнить:

```bash
git clone https://github.com/Challllaser/match-point.git
```

4. Открыть вкладку `Web`.
5. Нажать `Add a new web app`.
6. Выбрать домен `your_username.pythonanywhere.com`.
7. Выбрать `Manual configuration`.
8. Выбрать версию Python 3.
9. В секции `Code` открыть `WSGI configuration file`.
10. Заменить содержимое файла на:

```python
import os
import sys

PROJECT_DIR = "/home/YOUR_USERNAME/match-point"

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ.setdefault("DATA_DIR", PROJECT_DIR)

from app import application
```

Вместо `YOUR_USERNAME` указать логин PythonAnywhere.

11. В секции `Static files` добавить:

```text
URL: /static/
Directory: /home/YOUR_USERNAME/match-point/static
```

12. Нажать `Reload`.

После этого сайт будет доступен по адресу:

```text
https://YOUR_USERNAME.pythonanywhere.com/
```
