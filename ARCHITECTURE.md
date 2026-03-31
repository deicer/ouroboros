# Архитектура Ouroboros

Этот документ описывает фактическую архитектуру проекта и поддерживается агентом.
Цель: чтобы после рестарта или смены контекста было понятно, как система устроена, где хранятся данные, как проходит цикл задач и как выполняются самомодификации.

## 1. Назначение системы

Ouroboros — автономный агент в Docker, который:
- принимает сообщения из Telegram;
- выполняет задачи через LLM + инструменты;
- ведёт память, бюджет и логи;
- умеет менять собственный код (через OpenCode/инструменты git);
- способен к фоновому самоулучшению (evolution/review).

## 2. Верхнеуровневая схема

```text
Telegram
   |
   v
launcher.py (supervisor loop)
   |
   +-- supervisor/telegram.py      -> polling/send/retry/backoff
   +-- supervisor/workers.py       -> worker processes + direct chat agent
   +-- supervisor/queue.py         -> PENDING/RUNNING, приоритеты, timeouts
   +-- supervisor/events.py        -> обработка событий от воркеров
   +-- supervisor/state.py         -> state.json, бюджет, блокировки
   |
   +-- ouroboros/agent.py          -> orchestration задачи в воркере
        +-- ouroboros/context.py   -> сбор контекста/компактация
        +-- ouroboros/loop.py      -> LLM/tool loop
        +-- ouroboros/llm.py       -> LLM клиент + usage/cost
        +-- ouroboros/memory.py    -> память, chat history, summary/archive
        +-- ouroboros/tools/*      -> инструменты (git/shell/search/...)
```

## 3. Основные рантайм-компоненты

### 3.1 `launcher.py` (оркестратор процесса)
- Инициализирует окружение, state, очереди, воркеры, веб-трейс.
- Ведёт главный supervisor-цикл (poll Telegram -> dispatch events -> assign tasks).
- Делает bootstrap/восстановление после рестартов.

### 3.2 `supervisor/*` (управление системой)
- `workers.py`: lifecycle воркеров, direct-chat обработка, авто-резюм.
- `queue.py`: приоритеты задач, дедуп, circuit-breaker эволюции, автопостановка evolution.
- `events.py`: единая обработка worker events (`task_done`, `llm_usage`, `restart_request`, ...).
- `state.py`: единый источник состояния (`/data/state/state.json`) + бюджет OpenRouter.
- `telegram.py`: Telegram API с retry/backoff для 429/5xx.
- `trace_web.py`: live thinking trace веб-страница.

### 3.3 `ouroboros/*` (ядро агента)
- `agent.py`: подготовка task context, запуск loop, возврат событий.
- `loop.py`: основной цикл LLM + tools, usage accounting, fallback, context compaction.
- `context.py`: формирование сообщений для LLM, инъекции памяти/правил.
- `memory.py`: scratchpad/identity/goals/history + авто-суммаризация старого чата.
- `memory_backends.py`: backend-абстракция для knowledge tools (Mem0 + file fallback).
- `llm.py`: модели, провайдер, учёт токенов/стоимости.
- `tools/`: инструментальный слой (repo/shell/opencode/search/control/...).

## 4. Потоки данных и хранилища

### 4.1 Репозиторий (`/app`)
- Исходный код, тесты, документация.
- Самомодификация происходит именно здесь.

### 4.2 Drive (`/data`)
- `state/state.json`: owner, budget, runtime flags, counters.
- `logs/*.jsonl`: события, tool calls, supervisor, thinking trace, evolution log.
- `memory/*`: scratchpad, identity, goals, summaries.
- `memory/mem0_history.db`: локальная history-база Mem0.
- `task_results/*`: результаты задач для parent/child workflow.

### 4.3 Логи (наблюдаемость)
- `logs/events.jsonl`: системные события, включая usage/ошибки/архивирование.
- `logs/tools.jsonl`: вызовы инструментов + аргументы + preview результата.
- `logs/supervisor.jsonl`: технические события supervisor.
- `logs/thinking_trace.jsonl`: поток мыслей и промежуточных шагов.

## 5. Жизненный цикл пользовательской задачи

1. Telegram update попадает в `launcher.py`.
2. Сообщение маршрутизируется либо в direct chat, либо в очередь.
3. Воркер вызывает `agent.handle_task()`.
4. `agent` формирует контекст и запускает `run_llm_loop`.
5. `loop` вызывает инструменты по tool-calls, логирует usage и прогресс.
6. Воркер шлёт события в supervisor (`task_done`, `task_metrics`, ...).
7. `supervisor/events.py` обновляет state/логи/метрики и отправляет ответ владельцу.

## 6. Самомодификация (self-edit pipeline)

Ключевые инструменты:
- `opencode_edit`: агентные правки кода.
- `run_shell`: системные команды в repo (с таймаутами и лимитом вывода).
- `repo_commit_push`: `git add/commit/push` + pre-push проверки.

Ограничения/защита:
- блокировки git (`/data/locks/git.lock`);
- path safety (`safe_resolve_under_root`);
- бюджетные гейты и fallback на free-модели;
- restart safety (`safe_restart`, autopreserve).

## 7. Память и идентичность

Долгоживущие артефакты:
- `memory/identity.md`: личность.
- `memory/scratchpad.md`: краткосрочный рабочий контекст.
- `memory/goals.json`: цели и статус.
- `memory/chat_history_summary.md` + `logs/chat.archive.jsonl`: деградация старой переписки.
- knowledge layer: `knowledge_*` инструменты по умолчанию используют Mem0 (Qdrant + Gemini),
  при недоступности автоматически деградируют на file backend (`memory/knowledge/*.md`).

Принцип: после рестарта агент должен восстановить контекст из памяти и логов, а не начинать с нуля.

## 8. Бюджет и модели

- Источник правды по бюджету: OpenRouter snapshot в `state.json`.
- Реализовано обновление устаревшего budget snapshot перед критичными решениями.
- Поддержан auto-switch с paid на free модели при низком остатке.
- Категории затрат: `task`, `evolution`, `consciousness`, `review`, ...

## 9. Точки расширения

- Новые инструменты: добавить модуль в `ouroboros/tools/` с `get_tools()`.
- Новые event handlers: добавить в `supervisor/events.py` + `EVENT_HANDLERS`.
- Новые политики: через `.env` и обработчики в `state/queue/loop`.

## 10. Инварианты архитектуры

1. Один supervisor-процесс управляет воркерами и state.
2. Все долговечные данные пишутся в `/data`, код — в `/app`.
3. Самомодификация должна быть наблюдаемой (tool/events logs + git history).
4. Бюджетные решения не принимаются по устаревшему значению лимита.
5. Любая автономная кодовая эволюция должна оставлять след в документации.

## 11. Автообновление архитектурной документации

Механизм:
- `supervisor/events.py::_handle_task_done` проверяет автономные задачи (`evolution`, `review`, `consciousness`).
- Если в `logs/tools.jsonl` найден успешный `repo_commit_push` для `task_id`, в этот файл автоматически добавляется запись в журнал архитектурных изменений (append-only, с дедупликацией по `task_id`).

Это закрывает разрыв между реальным изменением кода и архитектурной документацией.

## 12. Журнал архитектурных изменений (авто, append-only)

Ниже агент автоматически дописывает записи после автономных code-change задач.
Формат маркера: `<!-- architecture-task:<task_id> -->`.
