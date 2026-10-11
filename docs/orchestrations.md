# Готові оркестрації MAF

Перевірено офлайн зі справжніми builders із
`agent-framework-orchestrations==1.3.1`, MAF core 1.21.0 та fake Codex runtime.
Це доводить маршрутизацію, контракт повідомлень і сесій, але не поведінку моделі
чи одночасну роботу справжніх Codex процесів.

## Встановлення та запуск

Пакет є optional dependency; dev extra та requirements.lock також містять його:

```bash
python -m pip install -e '.[orchestrations]'
python examples/orchestrations.py group --cwd /path/to/target-project
```

Приклад запускає реальні запити й витрачає ліміти підписки: sequential/concurrent
мають два виклики, group — три. Використовуються ChatGPT auth та read-only sandbox.
Під час цього етапу приклад не запускався на реальному Codex.

## Режими історії

| Builder | Конфігурація CodexAgent | Перевірено |
| --- | --- | --- |
| SequentialBuilder | `history_mode="full"` за стандартного chaining | Контекст task → draft → review, автор, окремі threads, buffered stream |
| ConcurrentBuilder | `full` або incremental для одного початкового запуску | Одночасний вхід двох runtime через barrier та агрегація обох відповідей |
| GroupChatBuilder із selection_func | `history_mode="incremental"` | Writer → Reviewer → Writer, новий suffix, той самий thread повторного учасника, buffered stream |

Group Chat використовує AgentExecutor у custom context mode. На першому ході
учасник отримує накопичений контекст; на наступному — лише повідомлення після
його останнього ходу. `full` у стандартній конфігурації цього builder викликає
HistoryMismatchError при поверненні до учасника. Адаптер не змінює режим автоматично:
це могло б приховати помилку caller-owned history.

Для Sequential `chain_only_agent_responses=True` змінює контракт контексту;
цей варіант, повторне використання workflow між запусками та checkpoint recovery
не входять до перевірених сценаріїв.

## Handoff і manager як агент

HandoffBuilder відхиляє CodexAgent до запуску runtime. У версії 1.3.1 учасники
мають бути екземплярами MAF `Agent`: builder клонує агента, додає handoff tools
та function middleware. CodexAgent є власним BaseAgent з SupportsAgentRun,
і цих передумов не реалізує. Підтверджено тестом відхилення та інспекцією коду;
наявності tools bridge недостатньо.

Group Chat підтримує CodexAgent як учасника та Python selection_func як
координатора. Штатний `orchestrator_agent=CodexAgent(...)` не підтримано: MAF
очікує `Agent` або його фабрику. Його внутрішній manager також використовує
system messages і response_format, які поточний контракт адаптера відхиляє.
Це не означає підтримку довільного LLM manager чи Magentic.

Для штатних handoff та agent manager додано окремий [CodexChatClient](chat-client.md):
він створює справжній MAF Agent, використовує локальну MAF історію та function loop.
Ці два сценарії пройшли offline і live; обмеження старого CodexAgent залишаються
чинними. Обидва варіанти існують без форку MAF.

Докази: `tests/test_orchestrations.py`; код установленого пакета `_handoff.py`,
`_group_chat.py` та core `_agent_executor.py`.
Офіційний опис:
[пакет оркестрацій Microsoft](https://github.com/microsoft/agent-framework/blob/main/python/packages/orchestrations/README.md).
