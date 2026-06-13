# timetta-mcp — Design Spec

**Date:** 2026-06-13
**Status:** Approved
**Author:** sh1rokovs (with Claude)

## Goal

Python MCP-сервер, дающий MCP-клиентам (Claude Desktop и др.) универсальный доступ
к Timetta main OData API. Запускается через `uvx`, аутентификация — статический Token API.

## Scope

**In scope:**
- Универсальный OData-шлюз к `https://api.timetta.com/odata`
- Аутентификация: статический Token API из env-переменной
- Discovery сущностей и полей через `$metadata`
- Запуск через `uvx` (stdio-транспорт)

**Out of scope (YAGNI для v1):**
- Reporting API (`reporting.timetta.com`)
- OAuth 2.0 / refresh-token flow
- Запись/мутации (create/update/delete TimeEntries)
- Кэширование метаданных между запусками

## Architecture

Три слоя, клиент изолирован от MCP-обвязки:

```
MCP client (Claude Desktop и т.п.)
        │  stdio (JSON-RPC)
        ▼
FastMCP server  ──►  @mcp.tool инструменты (валидация аргументов, формат ответа)
        │
        ▼
TimettaClient (httpx.AsyncClient)  ──►  Bearer Token API  ──►  api.timetta.com/odata
```

- **Инструменты** (`server.py`): объявление через `@mcp.tool()`, валидация аргументов,
  форматирование ответа для модели.
- **Клиент** (`client.py`): HTTP через `httpx.AsyncClient`, Bearer-аутентификация,
  обработка HTTP-ошибок. Не зависит от MCP — тестируется отдельно.
- **Метаданные** (`metadata.py`): парсинг `$metadata` (XML) → компактный список
  сущностей и полей.

SDK: официальный `mcp` (FastMCP, встроенный). HTTP: `httpx` (async).

## Tools

| Инструмент | Назначение |
|---|---|
| `list_entities()` | Список доступных OData-сущностей (парсинг `$metadata`) |
| `get_entity_schema(entity)` | Поля и типы одной сущности из `$metadata` |
| `query_odata(entity, filter?, select?, expand?, orderby?, top?, skip?)` | Универсальный GET к любой сущности с OData-параметрами |

`query_odata` — ядро. `list_entities`/`get_entity_schema` — discovery, чтобы модель
не угадывала имена сущностей и полей (Timetta API рекомендует загружать `$metadata`
первым делом).

## Data Flow & Formatting

- Аргументы инструмента → клиент собирает query-параметры (`$filter`, `$select`,
  `$expand`, `$orderby`, `$top`, `$skip`) → `GET` → `resp.json()["value"]`.
- Ответ возвращается как компактный JSON-текст.
- `$top` по умолчанию = 50, кэпится сверху = 200, чтобы не раздувать контекст модели;
  `$skip` для пагинации.
- `$metadata` (XML) парсится в компактный список сущностей/полей; сырой XML наружу
  не отдаётся.

## Configuration & Launch

- `TIMETTA_API_TOKEN` (env, **обязательно**) — статический Token API. Никогда не
  попадает в код, логи или текст ошибок.
- `TIMETTA_BASE_URL` (env, опционально) — default `https://api.timetta.com/odata`.
- `pyproject.toml` с console-script: `timetta-mcp = "timetta_mcp.server:main"`.
- Запуск:
  - локально: `uvx --from . timetta-mcp`
  - из репо: `uvx --from git+https://github.com/sh1rokovs/timetta-mcp timetta-mcp`
  - после публикации в PyPI: `uvx timetta-mcp`
- Транспорт: stdio.

## Error Handling

- **401** → понятное сообщение о неверном/просроченном `TIMETTA_API_TOKEN`.
- **403/404** → «нет доступа или сущность/запись не найдена» + имя сущности.
- **500** → Timetta отдаёт бизнес-ошибку JSON `{code, message}`; `message`
  пробрасывается модели как текст ошибки (не как системный сбой).
- **Сетевые/timeout** (httpx) → ловятся, возвращается краткое сообщение.
- Инструменты возвращают ошибки текстом для модели, а не роняют сервер.
- Токен никогда не попадает в текст ошибки или лог.

## Testing

- **Юнит-тесты клиента** (`pytest` + `respx`/`httpx.MockTransport`): мок ответов
  Timetta — успех, 401, 500-бизнес-ошибка, парсинг `$metadata`, сборка
  query-параметров. Без реальной сети.
- **Тесты инструментов**: вызов `query_odata`/`get_entity_schema` с замоканным
  клиентом — форматирование ответа, дефолт/кэп `$top`.
- TDD: тест → реализация.
- Интеграционных тестов против живого API в CI нет (нужен реальный токен);
  опционально — ручной smoke-скрипт.

## File Structure

```
timetta-mcp/
├── pyproject.toml          # метаданные, deps (mcp, httpx), entry point
├── README.md               # настройка, env, подключение к клиенту
├── .gitignore
├── src/
│   └── timetta_mcp/
│       ├── __init__.py
│       ├── server.py       # FastMCP, @mcp.tool, main()
│       ├── client.py       # TimettaClient (httpx, auth, ошибки)
│       └── metadata.py     # парсинг $metadata → сущности/поля
└── tests/
    ├── test_client.py
    ├── test_metadata.py
    └── test_tools.py
```

Зависимости: `mcp`, `httpx`; dev: `pytest`, `respx`.
