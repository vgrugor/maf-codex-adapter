# Перевірка мінімального агента

Дата: 11 жовтня 2026 року. Середовище: macOS ARM64, Python 3.12.14.
Версії: MAF core 1.21.0, orchestrations 1.3.1, Codex SDK та bundled runtime 0.162.1.

## Автоматичні перевірки

212 unit/contract тестів пройшли. Тести не запускають Codex і не витрачають ліміти.
Вони перевіряють:

- стандартні типи MAF та виконання через WorkflowBuilder у звичайному й streaming режимі;
- незалежність сесій і агентів, відновлення JSON-сесії та серіалізацію одночасних запитів;
- відмову при зміні конфігурації, збереження прив'язки після помилки;
- відмову від API-авторизації та unsupported входів/опцій;
- відхилення failed/interrupted/порожніх відповідей;
- переривання активного запиту та закриття SDK при скасуванні й timeout.
- mapping ролей/авторів і збереження тексту без переформатування;
- metadata final response в обох режимах, зокрема finalization без попередньої ітерації;
- допустимі порожні MAF invocation options і відхилення непорожніх;
- відхилення некоректного binding state до виклику runtime.
- incremental/full режими, delta без повторного prefix, відновлення full snapshot;
- явне відхилення зміненої історії, pending full turn та невідомої версії state;
- get_session(provider id) і відхилення непідтримуваного голого id у full mode;
- shared locks між instances у одному event loop;
- sequential graph, умовний цикл із повторним зверненням до агентів, паралельний fan-out/fan-in;
- as_tool invoke та ізоляцію provider-owned namespace при передачі сесії.
- порядок constructor/run middleware і providers, mutation, early termination;
- output/stream transforms, raw response для after_run, restore provider state;
- full-history після visible output transform, cached/fallback результатів;
- cancellation cleanup, session/callback guards і відхилення recursive same-session виклику;
- відхилення HistoryProvider, chat hooks, provider tools і некоректного provider state;
- mapping та скидання тимчасових instructions у SDK resume.
- SDK counter delta/unknown usage, buffered metadata та safe item summaries;
- категорії SDK помилок із chained cause та збереження primary failure при cleanup;
- provider-thread lease між різними сесіями, config validation та model drift guard.
- callable FunctionTool bridge, аргументи/результати/помилки та child.as_tool ізоляцію;
- scoped approval callbacks, delayed рішення, default deny, approve/reject/cancel;
- неблокувальний SDK reader, cleanup pending requests і захист catalog при resume.

Ruff lint/format і pip check пройшли.

## Реальна перевірка

`examples/check_maf_agent.py` успішно виконав чотири запити через авторизацію ChatGPT:

1. WorkflowBuilder → AgentExecutor → CodexAgent зберіг випадковий маркер A.
2. Друга сесія того самого агента зберегла інший маркер B.
3. Серіалізована та відновлена сесія A з новим екземпляром агента повернула маркер A.
4. Сесія B повернула маркер B.

Кожний запуск адаптера відкриває новий SDK процес і закриває його після завершення.
Це перевірило продовження розмов між процесами SDK. Запити виконано в порожній
робочій папці з read-only sandbox. Використання API-ключів не допускається адаптером.
Локальний звіт: `.state/maf-agent-check.json`.

Етап tools bridge також перевірено двома реальними turns через ChatGPT:
approved echo виконав MAF функцію; rejected echo після JSON restore сесії не
виконав її. Перевірено той самий thread і різні turn scopes погоджень.
Звіт: `.state/tools-check.json`. Native file/command side effects live не запускались.

Етап metadata/runtime також пройшов два live turns через ChatGPT: перший response
та JSON restore з buffered stream. Перевірено SDK counter delta й configured
model metadata. Звіт: `.state/metadata-check.json`.
Це попередня live перевірка мінімального агента. Під час підготовки контракту
і матриці сумісності повторні live запити не виконувались; розширено офлайн тести.

## Що ще не підтверджено

Нові full-history, get_session та multi-agent сценарії перевірено офлайн;
окремі нові live запити під час цього етапу не виконувались.
Інтеграцію middleware/text providers перевірено лише offline. Повні
egress/persistence gates та внутрішні SDK model/tool hooks не реалізовано.
Поступовий streaming, durable approvals та hosted tools,
ширші запуски готових orchestration builders, багатопроцесне спільне використання сесії,
checkpoint-відновлення workflow та реальне редагування коду ще не перевірені.
Переривання при скасуванні перевірено з імітацією SDK; реальний live cancellation
окремо не запускався. Робота на Windows/Linux ще не перевірена.

## Готові оркестрації — офлайн

Вісім тестів у test_orchestrations.py: Sequential зі звичайною відповіддю та
buffered stream; Concurrent із barrier та агрегацією; Group Chat із повторним
учасником в обох режимах відповіді; відхилення full history у стандартному
Group Chat; підтверджене відхилення CodexAgent у HandoffBuilder та штатному
agent manager. Live Codex під час цього етапу не запускався.
Деталі й відтворення: [orchestrations.md](orchestrations.md).

## CodexChatClient — offline та live

Додано 29 offline chat-client fixtures і runtime output_schema test: MAF Agent,
JSON history restore, обидва режими відповіді, tool call/result/error,
function middleware і runtime kwargs, Pydantic output до/після tools,
відхилення invalid envelope/unsupported options, межі ролей і мультимодальності,
штатні HandoffBuilder та Group Chat agent manager.

Фінальний запуск examples/check_chat_client.py 11 жовтня 2026 року пройшов
через ChatGPT sign-in, read-only sandbox і порожню робочу папку:

- MAF echo callback виконався один раз; модель повернула його результат.
- HandoffBuilder клонував штатних Agent; MAF middleware перехопив handoff,
  Specialist отримав task і повернув HANDOFF_OK.
- Group Chat manager через Pydantic output вибрав Writer і завершив із GROUP_OK.
- У фінальному сценарії було 7 реальних service calls; звіт .state/chat-client-check.json.

Два попередні діагностичні запуски echo не пройшли перевірку кількості callbacks
і також витрачали ліміти підписки. Діагностика другого показала п'ять однакових
викликів echo перед фінальною відповіддю: модель повторно просила інструмент.
Інструкцію протоколу уточнено щодо вже отриманих function_result; фінальний
сценарій пройшов. Це не гарантує exactly-once для довільних задач; автоматичного
повтору failed SDK turns не додано. Side-effect tools потребують власної політики
ідемпотентності/погоджень. Durable continuation і checkpoint recovery не перевірено.
