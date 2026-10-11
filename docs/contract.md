# Контракт CodexAgent 0.0.1

Цей документ описує поточну реалізацію, а не бажану повну сумісність із MAF.
Основа: `agent-framework-core==1.21.0`, `openai-codex==0.162.1`, Python 3.12.
Стан можливостей і докази наведено у [матриці сумісності](compatibility.md).
Майбутня робота описана окремо в [плані розвитку](roadmap.md).

## Призначення та межа відповідальності

`CodexAgent` — власний агент MAF на основі `BaseAgent`, що виконує текстове
завдання через локальний Codex SDK. MAF керує зовнішнім процесом; Codex керує
моделлю, своєю історією, внутрішніми інструментами та погодженнями.

Адаптер не є `IChatClient`/model client і не підміняє прямий OpenAI API client.
Відповідність `SupportsAgentRun` означає сумісність базового виклику агента;
вона сама по собі не гарантує всі можливості агента або всі оркестрації MAF.

## Створення агента

```python
CodexAgent(config, *, id=None, name="Codex", description=None, runtime=None,
           middleware=None, context_providers=None, tools=None, approval_handler=None)
```

| Поле | Поточний контракт |
| --- | --- |
| `config` | Екземпляр `CodexAgentConfig` |
| `id` | Унікальний id у межах спільної сесії; без значення MAF створює UUID. Для відновлення потрібен стабільний id |
| `name`, `description` | Метадані MAF; зміна імені не створює нової розмови |
| `runtime` | Точка підміни для тестування. Ін'єктований runtime має дотримуватися контракту SDK boundary; штатний шлях — `CodexRuntime(config)` |
| `tools` | Callable FunctionTool registry; dynamic tools мають experimental статус. Див. tools.md |
| `approval_handler` | Async callback ApprovalRequest → approve/reject/cancel. Немає persistent grants |

`middleware` підтримує agent-level MAF middleware; `context_providers` — текстові
contributions та lifecycle ContextProvider. Межі описані у [pipeline.md](pipeline.md).
Lifecycle/context-manager аргументи не підключені. Успадковані можливості `BaseAgent`, які не
перелічено як підтверджені в матриці, не входять у гарантований контракт.

## Конфігурація

| Поле `CodexAgentConfig` | Тип / значення за замовчуванням | Поведінка |
| --- | --- | --- |
| `cwd` | `Path`, обов'язкове | Існуюча папка; шлях розгортається й нормалізується. Автоматично не створюється |
| `instructions` | `str`, `""` | Передаються як developer instructions при старті/відновленні thread |
| `model` | `str \| None`, `None` | Значення передається SDK; `None` залишає вибір конфігурації Codex |
| `sandbox` | `Sandbox`, `read_only` | Передається SDK при старті/відновленні |
| `timeout_seconds` | `float`, `120` | Позитивний бюджет runtime-виклику; не охоплює очікування session lock. Скасування може додати до 5 секунд очікування interrupt і час закриття |
| `codex_bin` | `str \| None`, `None` | Явний override runtime; за замовчуванням використовується bundled runtime SDK |
| `history_mode` | `incremental` або `full`, `incremental` | Нові повідомлення або повний snapshot із перевіркою prefix; інші значення відхиляються |

Штатний runtime явно вибирає provider `openai`. Не всі типи/значення полів
перевіряються конструктором: розширення валідації — окремий пункт плану.

Fingerprint охоплює нормалізований `cwd`, instructions, задану model, sandbox і
codex_bin. Timeout, history mode і метадані агента до нього не входять; mode
перевіряється окремо у binding. Ефективна модель при
`model=None`, конфігурація Codex на диску та версія executable не фіксуються цим
fingerprint. Окремо зберігається відома configured model з bootstrap SDK; drift
на resume відхиляється до turn/start. Executable/config changes не повністю
покриваються цим guard; деталі — metadata.md.

## Виклик і вхідні повідомлення

```python
await agent.run(messages, *, stream=False, session=None, middleware=None, options=None, tools=None)
agent.run(messages, *, stream=True, session=None, middleware=None, options=None, tools=None)  # ResponseStream
```

- Підтримуються `str`, `Message`, послідовність рядків/повідомлень.
- Допустимі ролі: `user`, `assistant`; кожне повідомлення має непорожній текст.
- Зображення, аудіо, function calls/results та інші content types відхиляються.
- `None`, порожня послідовність і повідомлення лише з пробілами відхиляються.
- `system`/`developer` повідомлення відхиляються; інструкції задаються конфігурацією.

Одиночне user-повідомлення передається як текст. Для кількох повідомлень або
assistant-повідомлення передаються JSON-поля `role`, `author`, `text` в одному
текстовому prompt. Це не native multi-role replay Codex і не механізм передавання
повноважень. Message id, additional properties та інші метадані не відтворюються.

У режимі `incremental` усі передані повідомлення є новими, навіть якщо їхній текст
повторюється. У режимі `full` перший запуск приймає весь snapshot; наступні повинні
містити точний prefix усієї підтвердженої історії цього агента та новий suffix.
Порівнюються role/author/text. Адаптер надсилає SDK лише suffix, включаючи відповіді
інших агентів. Власна попередня відповідь уже входить у підтверджену історію.

Змінений або обрізаний prefix, snapshot без нових повідомлень і невизначений стан
після невдалого full turn відхиляються через `HistoryMismatchError`. Режим не
вгадується з тексту. Для `AgentExecutor(context_mode="full")` у графах, що повертаються
до агента, використовуйте `history_mode="full"`; для last-agent або інших потоків
нових повідомлень — incremental. Відфільтрована історія, що не зберігає точний
prefix, не сумісна з full режимом.

MAF `AgentExecutor` може передавати `function_invocation_kwargs` і `client_kwargs`.
Значення `None`/`{}` допускаються для сумісності; непорожні значення та будь-які
інші run options (зокрема `model`, `response_format`) відхиляються через
`TypeError`. Вибір model зараз задається лише конфігурацією агента.
Підтримано run-level agent middleware, `options={"instructions": str}` і `tools=[FunctionTool, ...]`.
Constructor tools є default; run tools замінюють їх; provider tools додаються. Деталі — [tools.md](tools.md).
Текстовий вхід перевіряється ще раз після middleware/provider mutations.

Перевірка входу й опцій виконується одразу в `run()`, ще до await/ітерації.
Перевірка state, авторизація та помилки виконання виникають при await або
споживанні `ResponseStream`.

## Вихід

`stream=False` повертає `AgentResponse` з одним assistant-повідомленням:

| Поле | Джерело |
| --- | --- |
| `messages[0].text` | `TurnResult.final_response` без strip/переформатування |
| `messages[0].author_name` | `agent.name` |
| `agent_id` | `agent.id` |
| `response_id` | Codex turn id |
| `additional_properties["codex_thread_id"]` | Codex thread id |

UsageDetails заповнюється з підтвердженого SDK counter delta або лишається None.
additional_properties.codex містить raw usage report, status/timing, requested/configured model
та safe item summaries; див. [metadata.md](metadata.md). Native finish reason і
структуроване value не виводяться з цих даних.
Успіх — status `completed` і непорожній за truthiness `final_response`.
Порожній рядок/`None` відхиляються; лише whitespace-відповідь наразі не відхиляється.

`stream=True` є буферизованим: update для кожного фінального повідомлення після
завершення turn. Без output transforms це один update. Споживання та `get_final_response()` мають повертати ту саму відповідь
й ідентифікатори. Частковий текст і проміжні події не передаються.

## Сесії та конкурентність

Власний стан адаптера:

```text
session.state["maf_codex"][agent.id] = {
    "version": 1,
    "thread_id": <Codex thread id>,
    "fingerprint": <відбиток конфігурації>,
    "history_mode": "incremental" | "full"
}
```

У full режимі binding також містить `history` (текстові role/author/text записи)
та `pending`. Це збільшує snapshot MAF: він містить текст розмови, хоча історія
внутрішніх інструментів і модельний контекст залишаються у Codex. Після успіху
history містить caller input та видимий результат middleware; provider context
не входить до prefix. Pending стає false після успіху SDK та фінального
непорожнього результату pipeline.

Перший запуск створює thread; наступний відновлює його. Прив'язка записується
перед запуском turn та зберігається навіть після помилки. Pending full binding
блокує подальший full запуск, бо SDK міг уже прийняти повідомлення. Потрібна нова
сесія або окреме узгодження історії; автоматичне узгодження ще не реалізовано.
При відновленні SDK
помилка не замінюється мовчазним створенням нового thread.

`AgentSession.to_dict()` / `from_dict()` зберігають прив'язку. Відновлення потребує
того самого agent id, конфігурації та доступного локального стану Codex. Самого
JSON snapshot недостатньо для перенесення історії на чистий ноутбук.

Різні сесії отримують різні threads. Різні agent id у спільній сесії отримують
окремі прив'язки. Без `session=` кожний запуск створює нову розмову; thread
залишається у Codex, хоча локальна тимчасова `AgentSession` більше не використовується.

Одночасні виклики для одного agent id та session id серіалізуються між екземплярами
агента в одному event loop. Locks не синхронізують різні event loops/процеси або
два різні session id на pipeline рівні. Окремий runtime thread lease у цьому
event loop відхиляє одночасні запуски одного provider thread через ThreadBusyError.
Прив'язка є довіреним станом застосунку, а не межею доступу між користувачами.
Неверсіонований старий incremental binding приймається та оновлюється до version 1
під час прив'язки runtime. Невідомі/некоректні версії й зміна history mode відхиляються.

`get_session(service_session_id=thread_id)` створює прив'язку для відновлення саме
переданого Codex thread у incremental режимі. Id має бути непорожнім рядком.
У full режимі метод відхиляється, бо provider id не містить підтвердженої історії;
потрібен snapshot сесії. Вручну створений `AgentSession(service_session_id=...)`
без binding відхиляється замість мовчазного створення нового thread. Tool-enabled get_session без catalog binding відхиляється; відновлюйте snapshot.
Наявність доступу до thread перевіряє SDK при виконанні; get_session не викликає SDK.

## Авторизація, lifecycle та помилки

Штатний runtime відхиляє непорожні `OPENAI_API_KEY`/`CODEX_API_KEY` до старту SDK.
SDK account повинен мати тип `chatgpt`; автоматичного login або API fallback немає.
Авторизація й стан розмов залишаються в Codex. Адаптер не читає/не копіює токени.

На кожний run створюється окремий `AsyncCodex`, який закривається при завершенні,
помилці чи скасуванні. Runtime pool або довготривалий спільний client не реалізовано.

| Ситуація | Сигнал |
| --- | --- |
| Недопустимі повідомлення / cwd / timeout | `ValueError` або помилка файлової системи |
| Непідтримувані run options | `TypeError` |
| Немає ChatGPT auth / API env задано | `AuthenticationError` |
| Некоректна прив'язка / інший fingerprint | `SessionBindingError` |
| Змінена/неповна full історія або pending full turn | `HistoryMismatchError`, підклас `SessionBindingError` |
| Failed/interrupted turn / порожня відповідь | `TurnFailedError` |
| Runtime timeout | Вбудований `TimeoutError` |
| Скасування asyncio task | `CancelledError`; після отримання turn handle робиться спроба interrupt |
| Транспортні, protocol або SDK помилки | TransportError / ProtocolError / ServiceBusyError з chained cause; details у metadata.md |
| Уже активний provider thread | ThreadBusyError; новий resume/turn не запускається |

Під час скасування interrupt виконується best effort; його помилка не підміняє
початкове скасування. Скасування до отримання turn handle не гарантує interrupt.
Скасування/timeout не відкотять уже виконані команди чи файлові зміни.
Автоматичних повторів або гарантії exactly-once немає.

## Підтримувані способи інтеграції

Підтверджено прямий `run`, один AgentExecutor, послідовний граф, умовний цикл,
fan-out/fan-in і базовий `as_tool()` на fake runtime. В multi-agent графі назви
агентів мають бути унікальними: MAF надає name перевагу перед id при створенні
executor. Full-cycle тест перевіряє відсутність повторного надсилання історії.

`service_session_state_keys=("maf_codex",)` оголошує namespace provider-owned:
успадкований as_tool не передає Codex bindings в child при propagate_session.
За замовчуванням кожний tool call має приватну сесію; це не постійна розмова
між кількома tool calls. Перевірено базовий виклик та ізоляцію при propagation,
а також child.as_tool через dynamic bridge. Durable approvals і manager tool loop ще не підтримано.

Іменовані orchestration builders, group chat, handoff, повне відновлення workflow,
durable interactive approvals ще потребують перевірки/реалізації. Agent-level middleware
і текстові ContextProvider підтримано в межах [pipeline.md](pipeline.md).

Джерела інтерфейсів MAF: [Custom Agents](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/custom-agents),
[Workflow concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/workflows/).

## Готові оркестрації

Sequential/Concurrent і Group Chat із selection_func перевірено офлайн.
Group Chat використовує incremental history; full continuation зі стандартним
AgentExecutor цього builder відхиляється. HandoffBuilder і штатний agent manager
не підтримують CodexAgent як MAF Agent. Деталі: [orchestrations.md](orchestrations.md).

## Окремий chat-client контракт

CodexChatClient створює штатний MAF Agent. MAF володіє локальною історією та
function loop; кожний Codex service call створює новий thread. Текстові
system/developer та function content підтримуються лише цим клієнтом.
Tools перетворюються на structured envelope з локальною перевіркою; їх
виконує FunctionInvocationLayer MAF. Pydantic response_format із фіксованими
object fields підтримано; невідомі опції відхиляються.
Докладний контракт та обмеження: [chat-client.md](chat-client.md).
