# Розвиток адаптера

Мета — розширювати інтеграцію Codex з MAF. Workflows слугують контрактними
перевірками бібліотеки. Поточний стан: [матриця сумісності](compatibility.md).

## A0. Повідомлення, сесії та базова взаємодія — виконано

- Явні incremental/full режими; full snapshot має незмінний prefix і новий suffix.
- get_session(provider id) відновлює incremental thread; full вимагає snapshot.
- Versioned binding, сумісність зі старим incremental state, захист від невідомих версій.
- Shared session locks у межах одного event loop і provider-owned state для as_tool.
- Контрактні тести sequential graph, conditional cycle, fan-out/fan-in та as_tool.

Докази тут офлайн: справжній MAF із fake runtime. Іменовані orchestration builders,
group chat, handoff та checkpoint resume ще не перевірено. Pending full binding
після помилки потребує нової сесії або майбутнього узгодження з історією SDK.

## A1. Agent-level middleware та context providers — наступний етап

Підключити lifecycle MAF навколо run: зміна входу, виклик runtime, обробка
результату та persistence. Визначити, як доданий контекст потрапляє в Codex без
дублювання історії та без зміни повноважень повідомлень.

Готовність: перевірено порядок hooks, зміни входу/виходу, early termination,
помилки, відновлення сесії та обидва режими відповіді. Зовнішні hooks не означають
перехоплення кожної внутрішньої model/tool операції Codex.

## A2. Міст інструментів і погоджень

Базовий as_tool та ізоляцію вже перевірено. Далі дослідити механізм надання tools
MAF самому Codex через офіційний SDK або MCP та міст запитів на погодження.
Доступність цих механізмів підтвердити по SDK; не припускати повну API-парність.

Готовність: tool call/result/error, delayed approval, reject/cancel і необхідні
межі сесій підтверджено. Tool-dependent handoff тестується після появи bridge.

## A3. Метадані, помилки, runtime і відновлення

Додати mapping usage/status, нормалізацію категорій SDK помилок, валідацію config
та задокументовані run options. Визначити політику ефективної model при resume,
узгодження pending full history, cleanup threads і lifetime SDK client.

Готовність: збереження історії не змінюється приховано; metadata узгоджені;
відновлення не втрачає binding. Визначено захист для різних session id, що
посилаються на той самий thread. Міжпроцесний захист залишається окремим рішенням.
Runtime pool розглядати після вимірювань, а не запроваджувати автоматично.

## A4. Поступовий streaming та події

Перетворювати text deltas SDK на AgentResponseUpdate; tool/progress events
відображати окремо від assistant text. Це покращує керування та спостереження,
але не є передумовою базових multi-agent графів.

Готовність: перший delta надходить до завершення turn; порядок і фінальний текст
узгоджені; partial failed/interrupted turn не стає успіхом. Закриття потоку та
скасування не залишають активні client/subscription; interrupt — best effort.

## A5. Решта оркестрацій і випуск

Перевірити іменовані Sequential/Concurrent builders, group chat, Magentic,
handoff та checkpoint resume малими contract fixtures. Нові механізми MAF
перевіряти тоді, коли їхні prerequisites реалізовані адаптером.

Готовність: кожний підтриманий рядок матриці має конкретний доказ і опис меж.
Встановлення та приклади перевірено на підтримуваних ОС; залежності зафіксовано.
Live тести лишаються окремими й не запускаються звичайним pytest.

## Оновлення контракту

Після кожного етапу оновлювати contract.md, compatibility.md, verification.md
і README. Наявність сигнатури або механізму в MAF не доводить підтримку адаптером.
