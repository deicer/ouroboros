# Ouroboros: описание проекта и его устройства (RU)

Версия кода: `7.1.0` (см. `VERSION`)  
Последнее обновление этого документа: `2026-02-24`

## Коротко: что это за проект

**Ouroboros** это self-developing AI-агент (саморазвивающийся агент), который:

- общается с владельцем через **Telegram** (бот),
- выполняет задачи и может **планировать/раскладывать** их на подзадачи,
- хранит состояние и “память” на диске (Docker volume `/data/`),
- вызывает LLM через **OpenRouter** (API совместимо с OpenAI SDK),
- умеет **изменять собственный репозиторий**: редактирует файлы через OpenCode CLI, затем делает `git commit` и `git push` в **ваш fork** репозитория,
- управляется “конституцией” проекта: `BIBLE.md` (философские ограничения/принципы).

Проект рассчитан на запуск на VPS в Docker. Предполагается, что вы **форкаете** репозиторий на GitHub, и агент пушит изменения в вашу копию (это “тело” агента).

## Глоссарий (термины в коде)

- **Repo**: исходники внутри контейнера (`/app/`), это git-репозиторий.
- **Drive**: постоянное хранилище (`/data/`), Docker volume. Здесь: состояние, логи, memory-файлы, очереди, результаты подзадач.
- **Owner**: владелец бота (первый пользователь, написавший боту; остальные игнорируются).
- **Supervisor** (`supervisor/`): “операционная система” агента: Telegram, очередь, воркеры, рестарты, состояние, git-sync.
- **Worker**: отдельный процесс, выполняющий фоновые задачи из очереди.
- **Direct chat**: обработка входящих сообщений владельца “вживую” (не через воркер), одним агентом в процессе supervisor.
- **Tools** (`ouroboros/tools/`): плагины-инструменты, которыми LLM управляет (чтение файлов, запуск shell, web search, браузер, git commit, и т.д.).

## Быстрый запуск (как обычно запускают на VPS)

Описано в `README.md`, но кратко:

1. Форкнуть репозиторий на GitHub.
2. На сервере: `cp .env.example .env` и заполнить ключи.
3. Запуск: `docker compose up -d --build`
4. Написать своему Telegram-боту любое сообщение (вы станете owner).

## Архитектура: 3 слоя

В `CLAUDE.md` уже есть краткое описание; ниже то же самое, но подробнее.

```mermaid
flowchart TB
  TG[Telegram Bot API] --> L[launcher.py]
  L --> S[Supervisor: supervisor/*]
  S -->|direct chat| DC[Direct Chat Agent (ouroboros/agent.py)]
  S -->|queue| Q[Queue: PENDING/RUNNING]
  Q --> W[Worker processes]
  W --> AC[Agent Core per worker]
  AC --> OR[OpenRouter LLM API]
  AC --> T[Tools registry]
  T --> Repo[/app (git repo)/]
  T --> Drive[/data (volume)/]
  S --> Drive
  W --> Drive
```

### Слой 1: Supervisor (`supervisor/`)

Отвечает за:

- polling Telegram (`getUpdates`) и доставку сообщений,
- управление очередью задач и воркерами,
- перезапуск/rollback,
- запись/чтение состояния на `/data/`,
- git-операции: reset на нужную ветку/тег, rescue-снимки, синхронизация зависимостей,
- обработку событий от воркеров (budget usage, отправка сообщений, done/metrics, и т.д.).

### Слой 2: Agent Core (`ouroboros/`)

Логика “как агент думает”:

- сбор контекста для LLM (`context.py`): SYSTEM prompt + BIBLE + память + runtime state + свежие логи,
- главный tool-loop (`loop.py`): вызов LLM → tool calls → выполнение tool’ов → следующий круг,
- LLM клиент (`llm.py`) через OpenRouter,
- память (`memory.py`) на `/data/memory/*`,
- background consciousness (`consciousness.py`) как отдельный “фоновый” цикл.

### Слой 3: Tools (`ouroboros/tools/`)

Плагины-инструменты. Реестр (`ouroboros/tools/registry.py`) сам находит модули и собирает `get_tools()`.

Важная оптимизация: в LLM-контекст по умолчанию подгружается **не весь** список инструментов, а “core tools” + meta-инструменты для обнаружения остальных (`list_available_tools`, `enable_tools`). Это экономит токены.

## Потоки выполнения (runtime flow)

### 1) Сообщение от owner: direct chat

1. `launcher.py` опрашивает Telegram `getUpdates`.
2. Команда вида `/status`, `/budget`, `/restart`, `/rollback` и т.п. обрабатывается supervisor’ом сразу (см. `_handle_supervisor_command`).
3. Обычный текст попадает в `supervisor.workers.handle_chat_direct(...)`.
4. Direct chat агент (`ouroboros/agent.py`) строит контекст (`build_llm_messages`) и запускает `run_llm_loop`.
5. Tool-loop вызывает LLM (OpenRouter), тот возвращает либо финальный текст, либо `tool_calls`.
6. Tools выполняются через `ToolRegistry.execute(...)`, результаты добавляются в сообщения и цикл повторяется.
7. Supervisor получает events (например `send_message`) и отправляет ответ в Telegram.

Отдельно: supervisor делает **batching** быстрых сообщений (несколько сообщений, пришедших подряд, объединяются в одно), чтобы не раздувать “стоимость” и не обрабатывать одно и то же многократно.

### 2) Очередь задач: воркеры

1. Любая часть системы может создать task (например evolution/review/подзадача) через события/очередь.
2. `supervisor.queue` хранит `PENDING` и `RUNNING`, сортирует по приоритету.
3. `supervisor.workers.assign_tasks()` раздаёт задачи свободным воркерам.
4. Воркеры это процессы `multiprocessing`; каждый создаёт свой `OuroborosAgent` и выполняет `handle_task`.
5. Воркеры публикуют события в общую очередь событий, supervisor их читает и обрабатывает (`supervisor.events.dispatch_event`).

### 3) Сообщение владельца “во время фоновой задачи”

Если воркер выполняет долгую задачу, а owner пишет что-то важное именно для неё, используется механизм “почтового ящика задачи”:

- tool `forward_to_worker` пишет сообщения в `/data/memory/owner_mailbox/<task_id>.jsonl`,
- воркер на каждом раунде tool-loop читает эти сообщения и инжектит их в диалог (`owner_inject.py` + `_drain_incoming_messages` в `loop.py`).

Это сделано, чтобы избежать “двойной обработки” одного и того же сообщения разными consumer’ами.

## Структура репозитория (что где лежит)

- `launcher.py`: главный entrypoint в контейнере. Загружает `.env`, инициирует supervisor-модули, запускает main loop.
- `supervisor/`: слой управления процессами и инфраструктурой.
  - `state.py`: состояние + бюджет + file locks + snapshots очереди.
  - `telegram.py`: TelegramClient + отправка сообщений/фото + форматирование.
  - `queue.py`: PENDING/RUNNING, приоритет, таймауты, retry, evolution/review scheduling, snapshot.
  - `workers.py`: spawn/respawn/killer воркеров, direct chat, health checks.
  - `events.py`: диспетчер событий от воркеров (usage, done, restart, schedule_task, и т.д.).
  - `git_ops.py`: checkout/reset, rescue snapshots, deps sync, safe_restart с fallback на stable tag.
- `ouroboros/`: ядро агента.
  - `agent.py`: оркестратор обработки одной задачи.
  - `loop.py`: основной LLM tool-loop.
  - `context.py`: сбор контекста для LLM + каппинг/компакция истории.
  - `llm.py`: OpenRouter API-клиент (через OpenAI SDK) + pricing.
  - `memory.py`: scratchpad/identity/user context, чтение логов и их сжатие для контекста.
  - `consciousness.py`: фоновый “поток сознания” между задачами.
  - `owner_inject.py`: per-task mailbox для `forward_to_worker`.
  - `review.py`, `arch_review.py`: сбор/оценка кода, метрики и архитектурные проверки.
  - `tools/`: инструменты (плагины), см. ниже.
- `prompts/`:
  - `SYSTEM.md`: главный системный промпт агента.
  - `CONSCIOUSNESS.md`: промпт для фонового режима.
- `docs/`: статическая страница (GitHub Pages): лендинг и артефакты “эволюции”.
- `tests/`: smoke-тесты и e2e harness.
- `Dockerfile`, `docker-compose.yml`, `.env.example`: окружение и настройки запуска.

## Supervisor: детали модулей

### `supervisor/state.py` (состояние и бюджет)

- Хранит `state.json` в `/data/state/state.json` и резервную копию `state.last_good.json`.
- Запись атомарная (`atomic_write_text`) + file lock (`/data/locks/state.lock`).
- Следит за бюджетом:
  - локальные счётчики `spent_usd`, `spent_tokens_*`,
  - периодически (каждые 10 LLM вызовов) обновляет ground truth через OpenRouter endpoint `/auth/key` и сохраняет `openrouter_limit_remaining`.
- Даёт `status_text(...)` для `/status` (сводка воркеров/очереди/денег/токенов).

### `supervisor/telegram.py` (Telegram)

- `TelegramClient.get_updates(...)` реализует polling (без webhook).
- `send_with_budget(...)` (ниже по файлу) отправляет сообщения + периодические budget отчёты.
- Есть утилиты:
  - безопасное деление длинного текста на чанки,
  - преобразование Markdown в Telegram-safe HTML.
- Умеет скачивать изображения из Telegram и отдавать как base64 (для vision/скриншотов).

### `supervisor/queue.py` (очередь)

- Два основных контейнера: `PENDING` (список) и `RUNNING` (словарь).
- Приоритет: обычные `task`/`review` выше `evolution`.
- Таймауты:
  - “soft timeout”: уведомить owner, что задача долго идёт,
  - “hard timeout”: прибить воркер, перезапустить, при необходимости requeue (с лимитом попыток).
- Snapshot очереди пишется в `/data/state/queue_snapshot.json`, чтобы после рестарта можно было восстановить ожидание.
- Планировщик `evolution` и `review` задач живёт здесь же.

### `supervisor/workers.py` (воркеры + direct chat)

- Воркеры: `multiprocessing`, на Linux по умолчанию используется `fork` (из-за side effects в `launcher.py` при `spawn`).
- Direct chat агент живёт в supervisor-процессе: `_get_chat_agent()` создаёт `OuroborosAgent` один раз и переиспользует.
- Есть health monitoring: если воркер умер, его задача возвращается в очередь, воркер respawn.

### `supervisor/git_ops.py` (git и безопасный рестарт)

- `checkout_and_reset(...)` умеет:
  - перед reset оценить “несинхронизированность” (грязные файлы/непушенные коммиты),
  - сделать rescue snapshot (diff + status + untracked) в `/data/archive/rescue/...`,
  - затем `git reset --hard origin/<branch>`.
- `safe_restart(...)`:
  1) reset на dev ветку,  
  2) `pip install -r requirements.txt`,  
  3) import test,  
  4) если import сломан: fallback на **последний stable tag** (`stable-*`) (или на legacy stable branch).

### `supervisor/events.py` (диспетчер событий)

Воркеры не “делают побочные эффекты” напрямую. Они создают events, а supervisor применяет их:

- `send_message` → отправка в Telegram,
- `llm_usage` → обновление бюджета и логирование,
- `restart_request` → безопасный restart,
- `schedule_task`/`cancel_task` → работа с очередью,
- `promote_to_stable` → создание stable tag,
- и т.д.

## Agent Core: ключевые части

### `ouroboros/agent.py` (оркестратор задачи)

Отвечает за:

- подготовку `ToolContext` (пути, chat_id, task_id, очередь событий, browser state),
- сбор LLM сообщений (`build_llm_messages`),
- запуск `run_llm_loop`,
- сбор метрик/событий конца задачи (`task_done`, `task_metrics`, `send_message`),
- best-effort cleanup (например закрыть браузер Playwright).

На старте агент делает “startup verification” (best-effort):

- проверка uncommitted изменений и попытка auto-rescue коммита,
- синхронизация версий `VERSION`/`pyproject.toml`/git tags,
- оценка бюджета.

### `ouroboros/context.py` (контекст для LLM)

Собирает контекст из трёх “блоков” (под идею prompt caching):

1. **Статический**: `prompts/SYSTEM.md` + `BIBLE.md` (+ иногда `README.md`).
2. **Полустабильный**: `scratchpad.md`, `identity.md`, `USER_CONTEXT.md`, индекс knowledge base.
3. **Динамический**: state.json, runtime section (git sha, ветка, тип задачи, бюджет), свежие логи (чат/прогресс/tools/events/supervisor), health invariants.

Также включает механизмы:

- token cap: выкидывание/обрезка самых “дорогих” секций,
- compaction tool history: старые tool results сжимаются до коротких summary.

### `ouroboros/loop.py` (LLM tool-loop)

Это главный цикл вида:

1. `LLM.chat(messages, tools=core_tools)`  
2. если `tool_calls` есть: выполнить инструменты (частично параллельно для read-only whitelist)  
3. добавить tool results в `messages`  
4. повторить

Ключевые детали:

- **Selective tool schemas**: по умолчанию в LLM передаются только core tools + `list_available_tools`/`enable_tools`.
- Таймауты на каждый tool call; для Playwright “stateful” инструменты выполняются в sticky thread из-за greenlet thread-affinity.
- Fallback chain: если модель возвращает пустой ответ, пробует другие модели из `OUROBOROS_MODEL_FALLBACK_LIST`.
- Self-check подсказки каждые 50 раундов: LLM сам решает, менять ли стратегию/сжимать контекст/декомпозировать.

### `ouroboros/llm.py` (LLM клиент)

- Единственная точка общения с OpenRouter: `LLMClient.chat(...)`.
- Использует `openai` SDK, но с `base_url=https://openrouter.ai/api/v1`.
- Поддерживает:
  - tool calls,
  - prompt caching (для Anthropic через `cache_control`),
  - оценку cost (из usage или через OpenRouter generation endpoint).

### `ouroboros/memory.py` (память)

Хранит и читает из `/data/`:

- `/data/memory/scratchpad.md`
- `/data/memory/identity.md`
- `/data/memory/USER_CONTEXT.md`
- `/data/logs/*.jsonl` (chat/progress/tools/events/supervisor)

И умеет “сжато” пересказывать хвост логов, чтобы не заливать LLM гигабайтами истории.

### `ouroboros/consciousness.py` (фоновые мысли)

Фоновый поток, который:

- просыпается с интервалом (по умолчанию 300 сек),
- думает по `prompts/CONSCIOUSNESS.md` лёгкой моделью,
- может:
  - написать owner’у,
  - добавить knowledge,
  - запланировать задачу,
  - обновить scratchpad/identity/user context,
  - триггерить архитектурный review,
- имеет собственный “лимит” по бюджету (`OUROBOROS_BG_BUDGET_PCT`) и пишет расход в общий state.

## Tools: как расширять и что уже есть

### Реестр инструментов

`ouroboros/tools/registry.py` делает auto-discovery: любой модуль `ouroboros/tools/*.py`, который экспортирует `get_tools()`, автоматически подключается.

Каждый tool это `ToolEntry`:

- `name`: имя функции (то, что увидит LLM),
- `schema`: JSON schema для tool calls,
- `handler(ctx, **args) -> str`,
- `timeout_sec`, `is_code_tool`.

### Core tools (доступны LLM по умолчанию)

По умолчанию tool-loop передаёт модели **не весь** реестр инструментов, а только core-набор (см. `CORE_TOOL_NAMES` в `ouroboros/tools/registry.py`):

- repo/drive: `repo_read`, `repo_list`, `drive_read`, `drive_list`, `drive_write`
- shell/код: `run_shell`, `opencode_edit`
- git: `git_status`, `git_diff`, `repo_commit_push`
- подзадачи: `schedule_task`, `wait_for_task`, `get_task_result`
- память: `chat_history`, `update_scratchpad`, `update_identity`, `update_user_context`, `knowledge_read`, `knowledge_write`
- web/browser: `web_search`, `browse_page`, `browser_action`, `analyze_screenshot`
- control: `send_owner_message`, `switch_model`, `request_restart`, `promote_to_stable`

Дополнительно в core-набор всегда добавляются meta-инструменты:

- `list_available_tools` (показать, какие ещё инструменты есть в реестре),
- `enable_tools` (подключить нужные инструменты к текущей задаче).

### Дополнительные tools (включаются по требованию)

Всё остальное остаётся “дополнительным” и по умолчанию не попадает в tool schema текущей задачи. Примеры (не исчерпывающе):

- `send_photo` (доставка скриншота/картинки в Telegram),
- `vlm_query` (vision для произвольной картинки),
- `knowledge_list` (листинг knowledge topics),
- `codebase_digest`, `codebase_health` (дайджест и метрики сложности),
- `compact_context` (явная команда на компакцию истории tool calls),
- `forward_to_worker` (сообщение конкретной фоновой задаче),
- `multi_model_review` (консенсус-ревью несколькими моделями),
- GitHub issues tools (`list_github_issues`, `comment_on_issue`, ...),
- tools управления режимами (`toggle_evolution`, `toggle_consciousness`, `request_review`, `cancel_task`, ...).

LLM может увидеть их через `list_available_tools` и подключить через `enable_tools`.

### Самоизменение кода (важно)

Self-improvement в этой архитектуре выглядит так:

1. LLM решает, что нужно поменять код/промпт.
2. Вызывает `opencode_edit(prompt=...)`:
   - внутри запускается OpenCode CLI в неинтерактивном режиме,
   - CLI меняет файлы в репозитории, **но не коммитит**.
3. LLM проверяет изменения (`git_status`, `git_diff`, при желании `run_shell`/`pytest`).
4. LLM вызывает `repo_commit_push(commit_message=...)`:
   - делает `git add`, `git commit`,
   - запускает pre-push тесты (по умолчанию `pytest tests/`),
   - делает `git push origin <dev-ветка>`.

## Хранилище `/data/` (что сохраняется между рестартами)

Типичное дерево:

- `/data/state/state.json`: основное состояние (owner, budget, текущая ветка/sha, флаги режимов).
- `/data/state/queue_snapshot.json`: снимок очереди (PENDING/RUNNING) для восстановления.
- `/data/locks/state.lock`: file-lock для state.
- `/data/locks/git.lock`: lock для git операций (commit/push/claude edit).
- `/data/logs/chat.jsonl`: переписка (важные сообщения).
- `/data/logs/progress.jsonl`: “прогресс-сообщения” (самокомментарии агента).
- `/data/logs/tools.jsonl`: лог вызовов tools.
- `/data/logs/events.jsonl`: usage, ошибки, метрики, сердцебиения.
- `/data/logs/supervisor.jsonl`: события supervisor (рестарты, спавн воркеров, таймауты, и т.п.).
- `/data/memory/scratchpad.md`: рабочая память.
- `/data/memory/identity.md`: “кто я”.
- `/data/memory/USER_CONTEXT.md`: информация о владельце (ограничение по размеру задаётся BIBLE).
- `/data/memory/knowledge/*.md`: topic-based knowledge base + `_index.md`.
- `/data/memory/owner_mailbox/<task_id>.jsonl`: сообщения владельца для конкретной фоновой задачи.
- `/data/task_results/<task_id>.json`: результаты подзадач для `get_task_result`.
- `/data/archive/rescue/...`: rescue snapshots перед reset’ами, если были грязные изменения.

## Конфигурация (.env)

См. `.env.example`. Важно:

Обязательные:

- `OPENROUTER_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `GITHUB_TOKEN` (для git push в ваш fork)
- `GITHUB_USER`
- `GITHUB_REPO`
- `OPENCODE_API_KEY` (опционально, если требуется вашей OpenCode-конфигурации)
- `ANTHROPIC_API_KEY` (опционально, если используется соответствующий provider в OpenCode)

Опциональные:

- `OPENAI_API_KEY` (включает tool `web_search` через OpenAI Responses API)

Тюнинг:

- `OUROBOROS_MODEL`, `OUROBOROS_MODEL_CODE`, `OUROBOROS_MODEL_LIGHT`
- `OUROBOROS_MODEL_FALLBACK_LIST`
- `OUROBOROS_MAX_WORKERS`, `OUROBOROS_MAX_ROUNDS`
- `OUROBOROS_SOFT_TIMEOUT_SEC`, `OUROBOROS_HARD_TIMEOUT_SEC`
- `OUROBOROS_BG_BUDGET_PCT`

## Telegram команды (supervisor)

Команды обрабатываются в `launcher.py`:

- `/status`: статус воркеров/очереди/бюджета.
- `/budget`: обновить ground truth из OpenRouter и показать breakdown.
- `/restart`: soft restart контейнера (exit с кодом 1, Docker перезапускает).
- `/panic`: аварийная остановка (exit 0, Docker policy on-failure не перезапустит).
- `/review`: поставить review-задачу в очередь.
- `/evolve [on|off]`: включить/выключить режим эволюции (фоновые self-improvement циклы).
- `/bg [start|stop|status]`: управление background consciousness.
- `/break`: послать “стоп-сигнал” текущей direct chat задаче (инжект сообщения).
- `/rollback`: откат на последний stable tag/ветку через `safe_restart`.
- `/no-approve`: переключить режим “без подтверждений” (self-improvements без ask/approve).

## Тестирование и разработка

См. `Makefile` и `CLAUDE.md`:

- `make test`: быстрые smoke tests (без e2e).
- `make test-e2e`: e2e в Docker (с реальным LLM, нужны ключи).
- `make health`: метрики сложности.

Ruff настроен в `pyproject.toml`.

## Где дальше смотреть (точки входа)

Если вы хотите понять систему “сверху вниз”, хороший порядок:

1. `README.md` (идея, команды, конфиг)
2. `BIBLE.md` (принципы)
3. `launcher.py` (main loop, команды, маршрутизация)
4. `supervisor/*` (state/queue/workers/events/git_ops)
5. `ouroboros/agent.py` → `ouroboros/context.py` → `ouroboros/loop.py`
6. `ouroboros/tools/registry.py` + конкретные tools
7. `tests/test_smoke.py` (ожидаемый список инструментов и базовые invariants)
