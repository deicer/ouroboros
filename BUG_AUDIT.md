# BUG AUDIT (бот Ouroboros)

Дата: 2026-02-25  
Формат: полноценный аудит по `find-bugs` (security/reliability first)

## Использованные skills

Локальные:
- `using-superpowers`
- `find-skills`
- `find-bugs`

Внешний поиск через Skills CLI:
- `npx skills find "python bug hunting"`
- `npx skills find "security code audit"`
- `npx skills find "python pytest testing patterns"`
- `npx skills find "telegram bot reliability backoff"`

## Методика проверки

1. Прогнаны автоматические проверки:
- `ruff check .` (много дефектов качества/структуры)
- `pytest -q` (2 падения в smoke-тестах архитектурных лимитов)

2. Проведен ручной аудит критичных модулей:
- `launcher.py`
- `ouroboros/tools/control.py`
- `ouroboros/tools/shell.py`
- `ouroboros/loop.py`
- `supervisor/trace_web.py`
- `Dockerfile`

3. Сверка с логикой рантайма и реальными симптомами (циклы, рестарты, доступ к файлам).

## Findings (по убыванию критичности)

### 1) Path traversal в чтении результатов подзадач
- Файл: `ouroboros/tools/control.py:240-257`
- Severity: **High**
- Проблема: `task_id` вставляется в путь как `f"{task_id}.json"` без нормализации/валидации.
- Риск: можно выйти из `task_results/` через `../` и читать произвольные `.json` в `drive_root`.
- Evidence: прямое конкатенирование в `result_file = results_dir / f"{task_id}.json"`.
- Fix:
  - разрешить только безопасный формат id (например `^[a-zA-Z0-9_-]{1,64}$`);
  - дополнительно проверять, что `result_file.resolve()` остается внутри `results_dir.resolve()`.

### 2) Захват owner первым написавшим пользователем
- Файл: `launcher.py:698-709`
- Severity: **High**
- Проблема: если `owner_id` пустой, бот назначает владельцем первого отправителя.
- Риск: любой пользователь, написавший первым после старта/сброса state, получает полный контроль.
- Evidence: блок `if st.get("owner_id") is None: st["owner_id"] = user_id`.
- Fix:
  - брать owner только из env/конфига (`TELEGRAM_OWNER_ID`);
  - либо делать одноразовый challenge/verification flow;
  - запретить авто-назначение "first message wins".

### 3) Выполнение неподписанного удаленного кода (`curl | bash`)
- Файлы:
  - `ouroboros/tools/shell.py:209-217`
  - `Dockerfile:30-32`
- Severity: **High**
- Проблема: OpenCode устанавливается через `curl -fsSL ... | bash`.
- Риск: supply-chain / RCE при компрометации источника/сети.
- Evidence: прямой запуск shell-скрипта из сети в рантайме и в образе.
- Fix:
  - перейти на pin-версию + checksum/signature verification;
  - либо добавлять бинарь из проверенного release-asset.

### 4) Падение на старте при недоступном `/data`
- Файл: `launcher.py:145-150`
- Severity: **High**
- Проблема: безусловный `mkdir` под `DRIVE_ROOT=/data`; при `PermissionError` процесс не стартует.
- Риск: бот не поднимается в окружениях с readonly/чужими правами на `/data`.
- Evidence: цикл `for sub ... (DRIVE_ROOT / sub).mkdir(...)` без guarded fallback.
- Fix:
  - early-check прав и понятная ошибка;
  - fallback в writable path (например `/tmp/ouroboros-data`) или обязательная валидация volume на старте.

### 5) False-positive loop guard убивает легитимный polling
- Файл: `ouroboros/loop.py:1311-1367`
- Severity: **Medium**
- Проблема: одинаковые батчи tool calls считаются зацикливанием даже для нормального polling (`get_task_result`/`wait_for_task`).
- Риск: задача останавливается преждевременно, особенно при background subtasks.
- Evidence: guard по сигнатуре args+results без semantic whitelist для polling tools.
- Fix:
  - исключить polling-инструменты из stop-guard;
  - или сбрасывать guard при изменении task state.

### 6) Проблемы trace-визуализатора: мигание и закрытие `details`
- Файл: `supervisor/trace_web.py`
- Severity: **Medium**
- Проблема A (UI flicker): ререндер триггерится на изменение `supervisorSig` (`:380-398`), а в сигнатуру входит `ts` (`:82-83`) — частые перерисовки.
- Проблема B (нестабильный key): `esc()` не экранирует `"` (`:251-255`), но `data-entry-key` строится через attribute (`:345`), ключ может ломаться на кавычках из trace.
- Риск: дерганый UI, закрытие `details`, потенциально некорректный DOM.
- Fix:
  - исключить `ts` из `supervisorSig` для ререндера;
  - хранить entry-key в безопасном виде (hash/base64) или корректно экранировать кавычки для attribute context.

### 7) Smoke-тесты падают на архитектурных ограничениях
- Файлы:
  - `tests/test_smoke.py:469`
  - `tests/test_smoke.py:530`
  - `ouroboros/loop.py` (1709 lines, `run_llm_loop` 527 lines)
- Severity: **Medium**
- Проблема: CI-like baseline не green: нарушены лимиты размера модуля/функции.
- Риск: деградация сопровождаемости, рост регрессий.
- Evidence: `pytest -q` падает на 2 тестах.
- Fix:
  - декомпозировать `loop.py` на независимые блоки (guards, tool-exec, budget, loop-state);
  - вынести `run_llm_loop` orchestration в меньшие функции.

## Что проверено и сейчас выглядит исправленным

- `run_shell` уже с timeout + cap вывода: `ouroboros/tools/shell.py:34-40`, `:111-120`.
- Усиленная path-safety (`safe_relpath`, URL decode, NUL, symlink confinement): `ouroboros/utils.py:120-149`.
- Улучшенная оценка токенов (`tiktoken` + UTF-8 fallback): `ouroboros/utils.py:192-206`.
- Auto-compaction контекста и истории уже присутствуют:
  - `ouroboros/loop.py:701+`
  - `ouroboros/memory.py:255+`.

## Рекомендованные skills для следующих итераций

Локальные (уже есть, применять по умолчанию):
- `find-bugs` — регулярный security/reliability аудит.
- `systematic-debugging` — разбор сложных runtime-сбоев.
- `python-testing-patterns` — стабилизация тестов вокруг edge-кейсов.
- `bug-hunt` — git archaeology + RCA.

Внешние (по поиску Skills CLI):
- `404kidwiz/claude-supercode-skills@security-auditor`
- `manutej/luxor-claude-marketplace@pytest-patterns`
- `openclaudia/openclaudia-skills@telegram-bot`

## Команды воспроизведения

```bash
cd /home/deicer/ouroboros
ruff check .
pytest -q
```
