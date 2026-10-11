# Розвиток адаптера

Мета — розширювати інтеграцію Codex з MAF. Workflows слугують контрактними
перевірками бібліотеки. Поточний стан: [матриця сумісності](compatibility.md).

## A0. Повідомлення, сесії та базова взаємодія — виконано

- Явні incremental/full режими; full snapshot має незмінний prefix і новий suffix.
- get_session(provider id) відновлює incremental thread; full вимагає snapshot.
- Versioned binding, сумісність зі старим incremental state, захист від невідомих версій.
- Shared session locks у межах одного event loop і provider-owned state для as_tool.
- Контрактні тести sequential graph, conditional cycle, fan-out/fan-in та as_tool.

Докази тут офлайн: справжній MAF із fake runtime. Іменовані Sequential/Concurrent та Group Chat із selection_func також перевірено
офлайн; handoff заблоковано вимогою MAF Agent. Checkpoint resume ще не перевірено. Pending full binding
після помилки потребує нової сесії або майбутнього узгодження з історією SDK.

## A1. Agent-level middleware та context providers — виконано в межах контракту

Підключити lifecycle MAF навколо run: зміна входу, виклик runtime, обробка
результату та persistence. Визначити, як доданий контекст потрапляє в Codex без
дублювання історії та без зміни повноважень повідомлень.

Готовність: перевірено порядок hooks, зміни входу/виходу, early termination,
помилки, відновлення сесії та обидва режими відповіді. Зовнішні hooks не означають
перехоплення кожної внутрішньої model/tool операції Codex.

Реалізовано AgentMiddlewareLayer, текстовий контекст/інструкції, provider state
і buffered stream transforms. Перевірено offline; докладні межі — [pipeline.md](pipeline.md).

## A2. Базовий міст інструментів і погоджень — виконано; durable continuation відкладено

Базовий as_tool та ізоляцію вже перевірено. Далі дослідити механізм надання tools
MAF самому Codex через офіційний SDK або MCP та міст запитів на погодження.
Доступність цих механізмів підтвердити по SDK; не припускати повну API-парність.

Готовність: tool call/result/error, delayed approval, reject/cancel і необхідні
межі сесій підтверджено. Tool-dependent handoff тестується після появи bridge.

Реалізовано experimental App Server bridge, callable FunctionTool, callback approvals
і child.as_tool. Два live turns (approve/reject/resume) пройшли. Durable pause/resume,
ToolApprovalMiddleware та ширша handoff інтеграція — окрема робота; [tools.md](tools.md).

## A3. Метадані, помилки та runtime guard — виконано; advanced recovery відкладено

Додати mapping usage/status, нормалізацію категорій SDK помилок, валідацію config
та задокументовані run options. Визначити політику ефективної model при resume,
узгодження pending full history, cleanup threads і lifetime SDK client.

Готовність: збереження історії не змінюється приховано; metadata узгоджені;
відновлення не втрачає binding. Визначено захист для різних session id, що
посилаються на той самий thread. Міжпроцесний захист залишається окремим рішенням.
Runtime pool розглядати після вимірювань, а не запроваджувати автоматично.

Реалізовано SDK counter delta/unknown usage, item summaries, categories, cleanup
і same-thread lease в одному loop. Два live turns metadata/restore/stream пройшли.
Pending full reconciliation, durable approval recovery та міжпроцесний guard
залишаються окремою роботою; [metadata.md](metadata.md).

## A4. Поступовий streaming та події — після визначення потреб continuation

Перетворювати text deltas SDK на AgentResponseUpdate; tool/progress events
відображати окремо від assistant text. Це покращує керування та спостереження,
але не є передумовою базових multi-agent графів.

Готовність: перший delta надходить до завершення turn; порядок і фінальний текст
узгоджені; partial failed/interrupted turn не стає успіхом. Закриття потоку та
скасування не залишають активні client/subscription; interrupt — best effort.

## A5. Готові оркестрації — базову сумісність перевірено; handoff потребує нового bridge

Sequential/Concurrent та Group Chat із Python selector перевірено справжніми
builders у восьми offline fixtures. Group Chat потребує incremental history.
HandoffBuilder вимагає MAF Agent, cloning і function middleware; штатний
agent manager також має додаткові передумови. Деталі: [orchestrations.md](orchestrations.md).

Реалізовано окремий CodexChatClient зі штатним MAF Agent, локальною історією,
function loop та Pydantic output. Handoff і agent manager перевірено офлайн;
контракт fresh-thread execution — [chat-client.md](chat-client.md).
Наступний пріоритет — durable approval continuation та recovery contract. Magentic та checkpoint resume
перевіряти після реалізації їхніх передумов.

Готовність: кожний підтриманий рядок матриці має конкретний доказ і опис меж.
Встановлення та приклади перевірено на підтримуваних ОС; залежності зафіксовано.
Live тести лишаються окремими й не запускаються звичайним pytest.

## Оновлення контракту

Після кожного етапу оновлювати contract.md, compatibility.md, verification.md
і README. Наявність сигнатури або механізму в MAF не доводить підтримку адаптером.
