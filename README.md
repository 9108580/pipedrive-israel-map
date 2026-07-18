# Pipedrive → Israel map

Автоматическая карта проектов по адресам из Pipedrive (все Person с заполненным полем адреса).

## Что делает

1. Забирает контакты из Pipedrive API.
2. Геокодирует через Nominatim (бесплатно).
3. Если в адресе только город/посёлок — разбрасывает точку около жилых зданий (OpenStreetMap Overpass), без наложений.
4. Пишет `map/data/projects.geojson` и публикует карту на **GitHub Pages**.
5. Каждое утро вс–чт (~09:00 Израиль) GitHub Actions добавляет только новых клиентов.

Маркеры на карте: `Project 0001`, … — без ФИО и точных адресов.

## Быстрый старт (локально)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Впишите PIPEDRIVE_API_TOKEN в .env
python -m src.sync --full --limit 5   # тест
python -m src.sync --full             # полная выгрузка (1000+ адресов — долго)
```

Откройте `map/index.html` в браузере (или любой static server из папки `map`).

## GitHub

1. Создайте репозиторий и запушьте этот проект.
2. **Settings → Secrets and variables → Actions** добавьте:
   - `PIPEDRIVE_API_TOKEN` — ваш токен
   - `PIPEDRIVE_COMPANY_DOMAIN` — `mescoil` (опционально)
3. **Settings → Pages** → Source: **GitHub Actions** (обязательно один раз, иначе будет 404).
4. После пуша папки `map/` workflow **Deploy map to GitHub Pages** опубликует сайт сразу (даже без полного sync).
5. **Actions → Sync Pipedrive Israel map → Run workflow**:
   - первый раз: mode=`full` (может идти несколько часов из‑за лимита Nominatim ~1 запрос/сек);
   - дальше: mode=`incremental` или просто дождитесь cron.
   - Даже если sync упадёт, уже собранная карта всё равно задеплоится.

Публичная ссылка после деплоя: `https://<user>.github.io/<repo>/`

## Расписание

Cron: `0 6 * * 0-4` (вс–чт 06:00 UTC ≈ 09:00 Israel летом).

## Безопасность

Файл `.env` в git не коммитится. Токен храните только в Secrets / локальном `.env`. Если токен светился в чате — перевыпустите его в Pipedrive (Preferences → API).
