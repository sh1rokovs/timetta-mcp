# Timetta MCP — OAuth-аутентификация (design)

**Дата:** 2026-06-15
**Статус:** реализован

> **Ревизия (2026-06-15): браузерный `authorization_code` заменён на ROPG
> (`grant_type=password`).** При проверке выяснилось, что публичный клиент
> `external` в IdentityServer Timetta authorization_code с loopback redirect_uri
> **не принимает** — `/connect/authorize` сразу редиректит на `/error`. Тот же
> `external` поддерживает `grant_type=password` (+ `refresh_token`), что
> подтверждено рабочим VS Code-расширением Timetta. Поэтому `timetta-mcp login`
> теперь спрашивает email/пароль в терминале и обменивает их на токены через
> password grant. Ниже отражены решения 4–5, компонент `login`, env, тесты и
> «вне объёма» по факту реализации; разделы про PKCE/loopback оставлены
> зачёркнутыми как история решения.

## Проблема

Сейчас MCP-сервер ходит в Timetta только по статическому Token API
(`TIMETTA_API_TOKEN`, Bearer, TTL ~1 год), который вшивается в заголовок
`Authorization` один раз при создании клиента (`src/timetta_mcp/client.py:18-21`).
Нужна поддержка OAuth 2.0: пользователь логинится на сайте Timetta через браузер,
а сервер дальше сам поддерживает живой токен.

## Контекст: что поддерживает Timetta

Auth-сервер `https://auth.timetta.com` (IdentityServer). Подтверждено по
`/.well-known/openid-configuration`:

- `grant_types_supported` включает `authorization_code` и `refresh_token`.
- `code_challenge_methods_supported`: `["plain","S256"]` → PKCE доступен.
- `authorization_endpoint`: `https://auth.timetta.com/connect/authorize`
- `token_endpoint`: `https://auth.timetta.com/connect/token`
- `scopes_supported` включает `all` и `offline_access`.

`access_token` живёт ~1 час, `refresh_token` ~15 дней (ротируется при refresh).

## Решения (зафиксированы при брейнсторме; 4–5 ревизованы)

1. **Вход — только через разовую CLI-команду `timetta-mcp login`**, не внутри
   MCP-сессии (сервер headless; запрашивать креды посреди stdio-сессии нельзя).
2. **Оба режима аутентификации сосуществуют.** Статический `TIMETTA_API_TOKEN`
   остаётся (CI/автоматизация); OAuth — когда токена нет.
3. **`refresh_token` (и `access_token`) хранятся в файле** на диске.
4. **Вход через ROPG (`grant_type=password`).** ~~Пароль/ROPG не реализуем — есть
   браузерный вход.~~ Ревизовано: `external` не поддерживает authorization_code с
   loopback, поэтому `login` спрашивает email/пароль в терминале (`getpass`,
   скрытый ввод) и POST-ит `grant_type=password` на `/connect/token`. Пароль
   нигде не сохраняется — только полученные токены.
5. OAuth-клиент — публичный (`external`, без secret), грант `password` +
   `refresh_token`. ~~PKCE, loopback redirect_uri; пользователь
   регистрирует/получает клиент в Timetta.~~ Ревизовано: отдельный
   authorization_code-клиент не требуется, PKCE/loopback не используются.

## Архитектура

Строгая изоляция новой логики в отдельный модуль `auth.py`; точечные правки
в `client.py` и `server.py`.

### Выбор режима — в `get_client()` (`server.py:16`)

Приоритет сверху вниз:

1. `TIMETTA_API_TOKEN` задан → статический режим (поведение без изменений).
2. иначе → OAuth-режим: токены из файла, авто-ротация.
3. файла нет / `refresh_token` протух → `TimettaError` с понятным текстом:
   «No valid Timetta credentials — run `timetta-mcp login`».

### Компоненты (`src/timetta_mcp/auth.py`)

Каждый компонент имеет одну ответственность, тестируется изолированно.

**`TokenStore`** — персистентность файла токенов.
- `load() -> StoredTokens | None`, `save(tokens) -> None`.
- Поля файла: `access_token`, `refresh_token`, `expires_at` (epoch seconds),
  `token_endpoint`.
- Запись атомарная: temp-файл + `os.replace`.
- Права `0o600` (POSIX); на Windows — best-effort (создание в пользовательском
  каталоге профиля, явный chmod не гарантирован).
- `__repr__` не печатает значения токенов.

**`TokenProvider`** — выдача валидного access_token. Синглтон уровня процесса
(переживает отдельные вызовы инструментов, т.к. `get_client()` создаёт и
закрывает `TimettaClient` на каждый вызов).
- `async get_token() -> str`: если `access_token` валиден (с буфером ~60 c до
  `expires_at`) — вернуть его; иначе — refresh.
- `async refresh()`: `POST token_endpoint`, `grant_type=refresh_token`,
  `client_id`, `refresh_token`; form-urlencoded. Ответ → обновить in-memory
  кэш и **сохранить ротированный `refresh_token`** через `TokenStore.save`.
- `asyncio.Lock` вокруг refresh — параллельные вызовы не делают N запросов.
- `force_refresh()` — для retry на 401.
- Ошибка refresh (сеть / `invalid_grant`) → `TimettaError` с понятным текстом.

**`password_login()`** — разовый ROPG-флоу (`grant_type=password`).
- `login_command()` спрашивает email (`input`) и пароль (`getpass`, скрытый
  ввод); пустые значения → ошибка с кодом выхода 1.
- POST `{auth_url}/connect/token`, form-urlencoded: `grant_type=password`,
  `client_id`, `username`, `password`, `scope=all offline_access`.
- Ответ 200 → `tokens_from_response` → запись через `TokenStore`. На любой
  не-200 / сетевой ошибке — `TimettaError` с деталью, **в стор ничего не пишется**.
- Печать «успех/ошибка»; ни пароль, ни токены в stdout не попадают.

> История: ранее планировался браузерный authorization_code + PKCE с loopback
> redirect_uri (`generate_pkce`/`build_authorize_url`/`exchange_code`/
> `_capture_redirect`). Отклонён — `external` его не принимает (см. ревизию выше).

### Изменения в `TimettaClient` (`client.py`)

- `__init__` принимает `token_provider` (объект с `async get_token()`) вместо
  строки-токена. Для статического режима — тривиальный провайдер, всегда
  возвращающий одну и ту же строку.
- `Authorization` ставится в `_send` (`client.py:86`) перед каждым запросом из
  `await token_provider.get_token()`, а не один раз в `__init__`.
- **Retry на 401 (только OAuth-режим):** при `401` один раз вызвать
  `force_refresh()` и повторить запрос; повторный `401` → `TimettaError`
  (текущий текст про Unauthorized, `client.py:110`).

### Конфиг (env)

| Переменная | Дефолт | Назначение |
|---|---|---|
| `TIMETTA_API_TOKEN` | — | статический режим (если задан, имеет приоритет) |
| `TIMETTA_BASE_URL` | `https://api.timetta.com/odata` | OData base (без изменений) |
| `TIMETTA_AUTH_URL` | `https://auth.timetta.com` | auth-сервер |
| `TIMETTA_CLIENT_ID` | `external` | публичный client_id для login (password) / refresh |
| `TIMETTA_CREDENTIALS_PATH` | платформенный дефолт | путь к файлу токенов |

Путь по умолчанию: `%APPDATA%\timetta-mcp\credentials.json` (Windows),
`~/.config/timetta-mcp/credentials.json` (POSIX).

## Безопасность

- Пароль вводится скрыто (`getpass`), используется только для одного POST и
  нигде не сохраняется; на диск пишутся лишь полученные токены.
- `__repr__`/логи не светят токены (распространить практику из `client.py:23`);
  пароль/токены в stdout не печатаются.
- Файл токенов — в пользовательском каталоге, права `0o600` (POSIX).
- ROPG несёт пароль в открытую к auth-серверу (по TLS) — приемлемо как
  единственный поддерживаемый клиентом `external` грант; CSRF-вектор
  authorization_code-флоу здесь отсутствует. ~~PKCE `S256`/`state` на callback~~
  не применимы (нет redirect-флоу).

## Тестирование

- **`TokenProvider`** (мок `token_endpoint`): refresh при истечении; ротация
  `refresh_token` сохраняется в стор; retry-путь `force_refresh`; параллельные
  `get_token` не порождают лишних запросов; `invalid_grant` → `TimettaError`.
- **`TokenStore`**: round-trip load/save; атомарность записи; токены не в repr.
- **`password_login()`** (мок `token_endpoint`): POST `grant_type=password` с
  `username`/`password`/`client_id`/`scope` и запись токенов в стор; не-200 →
  `TimettaError` с деталью и **без записи** в стор; сетевая ошибка → `TimettaError`;
  200 без `access_token` → понятная ошибка.
- **`TimettaClient`**: 401 → force_refresh → retry (успех и повторный 401);
  статический режим не сломан (заголовок ставится из тривиального провайдера).
- **Выбор режима в `get_client()`**: приоритет `TIMETTA_API_TOKEN`; отсутствие
  файла → понятная ошибка.

## Вне объёма (YAGNI)

- Хранение паролей (вводится разово, не сохраняется).
- Браузерный authorization_code/PKCE-флоу (клиент `external` не поддерживает).
- Шифрование файла токенов / интеграция с системным keychain.
- Обновление статического Token API.
- Многопользовательское/многоаккаунтное хранилище токенов.
