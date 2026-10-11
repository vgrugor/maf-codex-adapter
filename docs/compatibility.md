# Матриця сумісності

Стан на 11 жовтня 2026 року для MAF 1.21.0 / Codex SDK 0.162.1.
Повний контракт: [contract.md](contract.md). Порядок робіт: [roadmap.md](roadmap.md).

## Як читати статуси

- **Підтримується** — код реалізовано, є відповідна перевірка.
- **Частково** — є корисна поведінка з указаними обмеженнями.
- **Не реалізовано** — потрібний bridge/mapping відсутній або опція відхиляється.
- **Не перевірено** — інтерфейс/передумови є, але відповідний сценарій не пройшов тест.

Позначення доказів: **T** — офлайн тест із fake runtime/SDK та справжніми типами
MAF; **L** — попередня реальна перевірка через ChatGPT; **C** — інспекція коду,
що підтверджує обмеження. T не означає перевірку всього сценарію на реальному Codex.

## Інтерфейс, вхід і вихід

| Можливість | Статус | Доказ / межа |
| --- | --- | --- |
| `SupportsAgentRun`, `AgentResponse` | Підтримується | T: `test_response_is_maf_compatible` |
| Текст одного user-повідомлення | Підтримується | T і L; текст передається в SDK |
| Послідовність user/assistant повідомлень | Частково | T: контракт mapping; текстовий JSON prompt, а не native multi-role input |
| System/developer як вхідні повідомлення | Не реалізовано | C/T: відхиляються; instructions задаються config |
| Мультимодальні та function content | Не реалізовано | C/T: відхиляються до старту runtime |
| Буферизований `ResponseStream` | Підтримується | T: `test_buffered_stream_final_response`, streaming workflow |
| Поступові text deltas | Не реалізовано | C: `_stream` очікує повний `_run` |
| Usage, finish reason, tool events | Не реалізовано | C: `RuntimeResponse` містить лише text/thread/turn id |
| Структурований output/schema | Не реалізовано | C/T: run options відхиляються; structured value не заповнюється |

## Стан, виконання та авторизація

| Можливість | Статус | Доказ / межа |
| --- | --- | --- |
| ChatGPT auth без API fallback | Підтримується | T: auth tests; L: чотири live turns |
| API-key режим цього адаптера | Не реалізовано | Навмисно поза поточним контрактом; задані API env відхиляються |
| Вибір model/cwd/instructions/sandbox | Частково | C/T: передавання в SDK; L лише default model та read-only |
| Окрема розмова на сесію | Підтримується | T: sessions tests; L: два незалежні маркери |
| Різні agent id у спільній сесії | Підтримується | T: `test_agents_do_not_share_thread_when_using_same_session`; L відсутній |
| JSON-відновлення сесії | Підтримується | T і попередній L для incremental; full snapshot перевірено T |
| Повна історія без дублювання | Частково | T: `test_full_history_sends_only_new_suffix`, cycle test; потрібен режим full та незмінний prefix |
| `get_session(service_session_id)` для Codex resume | Частково | T: `test_get_session_resumes_provider_id`; incremental. Full потребує snapshot і відхиляє голий id |
| Versioned binding і legacy migration | Підтримується | T: version 1, міграція старого incremental binding, відхилення невідомої версії |
| Перенесення лише JSON на інший ноутбук | Не реалізовано | Історія Codex не входить у snapshot MAF |
| Перевірка fingerprint | Частково | T: зміна instructions; ефективна default model/зовнішня config не фіксуються |
| Послідовні виклики однієї сесії | Підтримується | T/L |
| Конкурентні виклики однієї сесії | Частково | T: shared locks між instances в одному loop; різні session ids/процеси не синхронізуються |
| Одночасні різні сесії на реальному Codex | Не перевірено | Async архітектура є; live сценарій був послідовним |
| Timeout/cancellation | Частково | T: interrupt і cleanup; L cancellation відсутній; до turn handle interrupt не гарантовано |
| Збереження binding після failed turn | Підтримується | T: `test_failed_turn_preserves_binding` |
| Єдина модель всіх помилок SDK | Не реалізовано | Частина винятків SDK передається напряму |
| Автоматичні retry / exactly-once | Не реалізовано | Потребують окремої політики щодо побічних ефектів |

## Розширення агента MAF

| Можливість | Статус | Доказ / межа |
| --- | --- | --- |
| Agent-level middleware | Не реалізовано | C: `BaseAgent.run` bridge не запускає middleware pipeline |
| Context providers / memory MAF | Не реалізовано | C: власний pipeline відсутній; існує provider-owned історія Codex |
| Runtime `tools=` / function invocation kwargs | Не реалізовано | C/T: непорожні options відхиляються |
| Інструменти/MCP самого Codex | Частково | Належать runtime Codex; bridge їх не конфігурує й не мапить; tool execution L не перевірено |
| `as_tool()` — CodexAgent як інструмент | Частково | T: базовий invoke та provider-state ізоляція; private session на call. Live/manager loop/approvals не перевірено |
| Інтерактивний tool approval через MAF | Не реалізовано | Немає mapping SDK approval requests → MAF request/response |
| Трасування і телеметрія MAF | Не реалізовано | Адаптер не додає telemetry layer; зовнішня інструментація — окремий механізм |

## Оркестрація

| Сценарій MAF | Статус | Що підтверджено / чого бракує |
| --- | --- | --- |
| Один AgentExecutor у WorkflowBuilder | Підтримується | T: звичайний та buffered streaming; L: звичайний workflow |
| Послідовний граф із кількох CodexAgent | Підтримується | T: `test_sequential_graph_passes_context_between_agents`; live multi-agent ще не перевірено |
| Граф fan-out/fan-in | Підтримується | T: `test_fan_out_agents_overlap_and_fan_in_collects_both` доводить overlap і агрегацію; runtime fake |
| Цикл з умовним поверненням до агента | Підтримується | T: `test_revisited_agents_in_cycle_do_not_duplicate_full_context`; режим full |
| Іменовані Sequential/Concurrent builders | Не перевірено | Поточні тести використовують core WorkflowBuilder; окремий пакет orchestrations не встановлено |
| Group chat | Не перевірено | Потрібні контрактні перевірки message/context режимів і manager |
| Handoff, що потребує function tools | Не реалізовано | Немає tool bridge; сама наявність графа не реалізує agent-selected handoff |
| Magentic / planner orchestration | Не перевірено | Не перевірені manager interface та structured/control outputs |
| Human-in-the-loop між етапами графа | Не перевірено | Це механізм MAF; не означає підтримку внутрішніх погоджень Codex |
| Checkpoint/resume всього workflow | Не перевірено | Відновлення AgentSession перевірено; workflow checkpoint і відновлення побічних ефектів — ні |

Підтримка механізму у MAF або наявність методу в BaseAgent не є доказом підтримки
цього механізму адаптером. Майбутні contract tests мають перевіряти оркестрацію
на fake SDK; live tests підтверджують лише критичні межі, що неможливо довести офлайн.

## Джерела доказів

- [Поточна перевірка](verification.md) та локальний `.state/maf-agent-check.json`.
- `tests/test_agent.py`, `tests/test_runtime.py`, `tests/test_contract.py`, `tests/test_history.py`, `tests/test_multi_agent.py`, `tests/test_session_edges.py`.
- `src/maf_codex/agent.py`, `runtime.py`, `mapping.py`, `config.py`, `sessions.py`.
- [MAF Custom Agents](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/custom-agents).
- [MAF Workflow concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/).
