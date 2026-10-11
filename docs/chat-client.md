# CodexChatClient і штатний MAF Agent

CodexChatClient створює справжній MAF Agent через `as_agent()`. Він доповнює
існуючий CodexAgent: кожний має власний контракт історії та інструментів.
Жоден шлях не потребує API-ключа; runtime перевіряє ChatGPT auth.

```python
from pathlib import Path
from agent_framework.orchestrations import HandoffBuilder
from maf_codex import CodexAgentConfig, CodexChatClient

client = CodexChatClient(CodexAgentConfig(cwd=Path("/path/to/target-project")))
triage = client.as_agent(
    name="Triage",
    instructions="Delegate specialist tasks with the handoff tool.",
    require_per_service_call_history_persistence=True,
)
specialist = client.as_agent(
    name="Specialist",
    instructions="Handle the specialist task and respond concisely.",
    require_per_service_call_history_persistence=True,
)
workflow = (
    HandoffBuilder(participants=[triage, specialist])
    .with_start_agent(triage)
    .build()
)
# await workflow.run("Handle this specialist task")
```

Залежність builders встановлюється через `.[orchestrations]`. Для Group Chat
передайте створений так Agent як `orchestrator_agent`; він отримує штатний
Pydantic response_format для вибору учасника та завершення розмови.

## Історія та життєвий цикл

MAF InMemoryHistoryProvider зберігає повідомлення, function calls та results.
AgentSession.to_dict()/from_dict() відновлюють цю локальну історію. Для handoff
обов'язково `require_per_service_call_history_persistence=True`, щоб зберегти
навіть хід, який MAF завершив через MiddlewareTermination.

Кожний service call запускає новий Codex thread із повним transcript. Немає
conversation_id або resume binding у сесії MAF; повернений thread id є лише
діагностичним metadata. STORES_BY_DEFAULT=False описує відсутність продовження
через сервер, а не відсутність локального службового стану самого Codex.
`history_mode` config тут не керує історією: це параметр старого CodexAgent.

Цей варіант повторно передає контекст і створює більше threads, ніж CodexAgent.
Автоматичного видалення Codex threads немає. Зате MAF може змінювати каталог
інструментів, клонувати Agent і відновлювати історію без Codex binding.

## Function calling та structured output

Коли надані FunctionTool, Codex отримує їхні схеми як instructions та output_schema
для envelope `{kind, text, name, arguments}`. Запит на інструмент перетворюється
на Content.function_call з унікальним call_id. FunctionInvocationLayer MAF
виконує callable, застосовує function middleware та повертає function_result.
Штатний handoff interceptor завершує цей цикл і маршрутизує іншому Agent.

Це адаптерний протокол вибору інструмента через structured output. Він не є
native Codex tool-call event. За один service call повертається максимум один
запит; кілька інструментів можуть виконуватися послідовними викликами моделі.
Finish reason `tool_calls`/`stop` синтезується з перевіреного результату mapping.
Невідомий інструмент, неправильний envelope або не-object arguments викликають
ProtocolError до виконання callback. Аргументи інструмента валідує сам MAF.

Pydantic response_format передається як output_schema і локально перевіряється
після завершення. Підтримано моделі з фіксованими object fields; довільні словники,
extra=allow та raw JSON schema format відхиляються. Для structured відповіді
після tools JSON міститься в text фінального envelope. Поля схеми явні/required;
defaults видалено. Структурований output не запускає callbacks сам по собі.

System/developer текст переноситься в developer instructions Codex; решта
ролей, авторів, тексту й function content передається JSON transcript. Це
текстовий mapping, а не native multi-role input SDK. Мультимодальність,
reasoning content і нетекстові function results не підтримано.

## Межі опцій і виконання

- Підтримано instructions, tools, tool_choice auto/none/required,
  allow_multiple_tool_calls (виклики все одно послідовні) та Pydantic response_format.
- Store=True та provider conversation_id відхиляються; model задається config.
  Temperature, token limits та решта невідомих chat options не ігноруються.
- Function invocation kwargs обробляє MAF; довільні client kwargs відхиляються.
- Chat/function middleware працюють через штатні MAF layers. Streaming буферизований
  для кожного service call; це не progressive deltas Codex.
- Callback виконується з дозволами Python host. Codex sandbox не обмежує цей callback.
  Native Codex operations залишаються під runtime sandbox і не стають MAF tools.
- Durable approvals, checkpoint recovery всього workflow, Magentic і міжпроцесна
  координація не заявлені як перевірені можливості цього клієнта.

Offline докази: `tests/test_chat_client.py`, `test_runtime_passes_output_schema_to_turn`.
Live перевірка: `examples/check_chat_client.py` (планово 7 коротких запитів,
обмеження 10; використовує порожню `.state/chat-client-probe` і read-only sandbox).
Підсумкові докази запуску записуються в [verification.md](verification.md).

Фінальний live сценарій пройшов (7 service calls). Попередній diagnostic echo
показав повторні запити моделі на той самий інструмент; після уточнення інструкцій
сценарій завершився одним callback. Цей результат не гарантує exactly-once.
Стабільні call IDs пов'язують call/result у межах MAF, але не дедуплікують
нові запити моделі з однаковими аргументами. Ідемпотентність side-effect tools
і recovery після невизначеного виконання потребують окремого контракту.
