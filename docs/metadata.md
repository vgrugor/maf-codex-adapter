# Метадані, usage, помилки та захист runtime

## Відповідь MAF

Окрім text/thread/turn ids, AgentResponse містить additional_properties.codex:

- status, duration_ms, started_at, completed_at — значення SDK, None якщо відсутні;
- requested_model — конфігурація адаптера; configured_model — bootstrap metadata SDK;
- usage_report — оригінальні last/total/model_context_window лічильники SDK;
- usage_scope — sdk_counter_delta або unknown;
- items — безпечні підсумки завершених SDK items.

Items містять лише id/type/status/tool/server/success/exit_code/duration_ms, коли
ці поля доступні. Arguments, tool outputs, diffs, stdout і reasoning content не
копіюються в цей metadata view. Failed tool item може бути у completed turn:
це різні статуси, які workflow має враховувати окремо.

Configured model описує конфігурацію thread, не execution telemetry кожного
model call. Відоме значення зберігається у binding; якщо наступний resume
повідомляє іншу модель або не може підтвердити її, запуск блокується до turn/start.
Це не замінює перевірку entitlement і не гарантує відсутність backend rerouting.

## Usage без змішування session і turn

SDK total є накопиченим session report; last характеризує останній контекст.
UsageDetails обчислюється як різниця total проти останнього підтвердженого total,
тільки для thread, створеного адаптером і продовженого з тим самим session id.
Перший запуск нового thread використовує нульовий baseline.

| SDK counter delta | Поле MAF |
| --- | --- |
| input_tokens | input_token_count |
| output_tokens | output_token_count |
| total_tokens | total_token_count |
| cached_input_tokens | cache_read_input_token_count |
| cache_write_input_tokens | cache_creation_input_token_count |
| reasoning_output_tokens | reasoning_output_token_count |

Cache/reasoning поля є деталізацією, їх не треба додавати повторно до total.
Missing optional counter не стає штучним нулем. Imported/legacy thread без
ownership/baseline, зменшення лічильників, відсутній звіт або gap після failed
turn дають UsageDetails=None і scope=unknown; raw report лишається доступним.
Після успіху SDK новий baseline зберігається, навіть якщо наступний provider hook
не завершився. Це облік повідомлених SDK counters, а не ціна, billing або квота.

Binding додатково містить usage_owner, usage_total та configured_model.
Snapshot відновлює baseline з тим самим session id. Зміна цього id не дає права
вважати чужі totals власними витратами. Out-of-band запуск того самого provider
thread поза адаптером може зіпсувати точність delta і не входить у гарантії.

Usage передається через Content.from_usage у stream один раз. Final response
та metadata однакові в обох режимах без користувацьких transforms, що навмисно
редагують/видаляють metadata або usage content.

## Категорії помилок

| Категорія / тип | Поведінка |
| --- | --- |
| authentication / AuthenticationError | Немає ChatGPT auth або API env не дозволяє subscription check |
| session / SessionBindingError | Невірний binding, catalog або configured model drift |
| thread_busy / ThreadBusyError | Provider thread уже виконується в цьому event loop |
| transport / TransportError | Закритий SDK transport, помилка запуску чи IO |
| protocol / ProtocolError | SDK RPC/schema error; rpc_code зберігається, коли відомий |
| service_busy / ServiceBusyError | SDK перевантаження; transient=True |
| turn / TurnFailedError | Failed/interrupted/порожній turn або SDK RuntimeError при collection |
| TimeoutError / CancelledError | Стандартні Python сигнали timeout/cancel |

Оригінальний виняток SDK лишається у __cause__, але його довільний текст не
копіюється в публічний message. Cleanup failure не підміняє primary failure
або cancellation. Unexpected application/hook errors не маскуються як SDK errors.
Transient означає тимчасову умову, не безпечність повтору: automatic retry немає.
Collector SDK інколи втрачає structured TurnError, передаючи RuntimeError;
тоді доступна категорія turn, а не вигаданий HTTP/auth code.

## Конкурентність і відновлення

Session lock охоплює pipeline. Додатковий provider-thread lease запобігає
перекриттю різних agent/session ids у тому самому event loop. Зайнятий thread
відхиляється через ThreadBusyError до resume/model/instruction updates.
Новий thread отримує lease до публікації binding. Lease звільняється при
завершенні, помилці та cancellation. Різні provider threads лишаються незалежними.

Це не міжпроцесний/distributed lock і не контроль зовнішніх desktop/CLI writers.
Runtime client створюється на run; pool не додано без вимірювання потреби.
Неясна full історія після failed turn все ще блокується; автоматична reconciliation
із raw історією SDK та durable pending approvals ще не реалізовані.

## Перевірки

tests/test_metadata.py перевіряє delta/unknown usage, streaming, restore,
safe item summaries, categories, cleanup, model drift і provider-thread lease.
examples/check_metadata.py пройшов два live turns через ChatGPT: перший response,
JSON restore та buffered stream. Raw звіт: .state/metadata-check.json.
