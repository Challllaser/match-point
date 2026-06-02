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

## Beget

Проект подготовлен под Python на Beget через Passenger/WSGI.

В корне проекта есть:

```text
.htaccess
passenger_wsgi.py
tmp/restart.txt
```

### Быстрый деплой через SSH

1. В панели Beget создать сайт/домен.
2. Включить SSH-доступ.
3. Зайти по SSH на сервер Beget.
4. Перейти в папку сайта.
5. Клонировать проект:

```bash
git clone https://github.com/Challllaser/match-point.git .
```

Если папка не пустая, можно клонировать во временную папку и перенести файлы в корень сайта.

6. Проверить, что в корне сайта лежат:

```text
app.py
passenger_wsgi.py
.htaccess
static/
tournaments.db
```

7. Перезапустить Passenger:

```bash
mkdir -p tmp
touch tmp/restart.txt
```

После этого сайт должен открыться по домену Beget.

### Обновление после изменений

```bash
git pull
touch tmp/restart.txt
```
