# Middleware та context providers

Адаптер використовує стандартний AgentMiddlewareLayer MAF 1.21.0. Providers
працюють через ContextProvider.before_run/after_run та SessionContext.
Приклад: `examples/context_provider.py` (запуск витрачає ліміти плану).

## Порядок виконання

Звичайний run: перевірка історії та новий suffix → constructor middleware →
run middleware → before_run providers у прямому порядку → SDK → after_run
providers у зворотному порядку → завершення middleware → підтвердження snapshot.

Session lock охоплює весь pipeline. Timeout конфігурації охоплює SDK boundary;
довгі hooks можна зупинити скасуванням asyncio task.

У stream=True працює lazy семантика MAF: call_next повертає потік, тому код
middleware після нього може виконатися до SDK/providers. Для обробки завершення
використовуйте context.stream_result_transforms, для updates — stream_update_transforms.
Адаптер поки споживає внутрішній потік до кінця й повертає фінальні повідомлення
зі збереженням їхніх авторів. Поступовий текст у реальному часі ще не передається.

## Підтримка

- AgentMiddleware та agent-level callables, зокрема @agent_middleware.
- Зміна нових context.messages, раннє завершення без SDK, override відповіді,
  обробка винятків і streaming transforms.
- Providers додають текстові user/assistant context messages та інструкції.
- Provider state зберігається в session.state[source_id] і серіалізується з сесією.
- options={"instructions": "..."} додає інструкції поточного run.

Інструкції SDK: config → options → providers. Наступний resume явно передає
актуальні базові інструкції, включно з порожнім рядком, щоб тимчасові інструкції
не залишалися лише через None/omission. Це не видаляє попередній модельний контекст.

SessionContext.options — копія для reflection, не канал зміни runtime settings.
After_run отримує raw відповідь SDK до зовнішніх middleware transforms.

## Повна історія

Prefix перевіряється до hooks; middleware/providers бачать новий suffix.
Provider context не входить у caller-owned snapshot. Він додається у поточний
turn і може навмисно повторюватися між turns.

Після SDK успіху history підтверджує оригінальний caller input і видимий фінальний
результат middleware. Raw історія Codex не переписується. Cached результат без
SDK не підтверджує unsubmitted input: у наступному full snapshot він лишається
новим suffix. Fallback після невдалого SDK не очищає pending binding.
Помилка after_run також не відкочує SDK turn і не підтверджує snapshot.

## Межі

- Chat/function middleware, provider middleware та інші options відхиляються.
- Callable provider tools підтримано через [dynamic bridge](tools.md); він зберігає catalog binding.
- HistoryProvider відхиляється, бо історією вже керує Codex. Інші спеціальні
  providers потребують окремої перевірки своїх contributions.
- Provider source ids унікальні, непорожні та не дорівнюють maf_codex; state має бути dict.
- Middleware не може підмінити reconciled session або повторно викликати Codex handler.
- Early termination без result повертає порожню AgentResponse без SDK/providers.
- Final result — текстова AgentResponse або сумісний ResponseStream.
- Hooks не є транзакцією: before-state не відкочується; after_run не є cleanup
  при невдалому SDK turn. Для streaming cleanup потрібні MAF stream hooks.
- Повна інтеграція egress/persistence gates та durable MAF approvals ще
  не реалізована. Зміна outward result не відкочує raw історію або provider state.

## Залежність від версії

providers.py ізолює private seams MAF 1.21.0: categorize_middleware для перевірки
типів і SessionContext._response для заповнення read-only response. Сам MAF Agent
використовує цей setter. Перед оновленням залежності запускайте contract suite.

Перевірки: tests/test_pipeline.py, tests/test_pipeline_edges.py та SDK arguments
у tests/test_runtime.py. Live перевірки цього етапу не виконувались.
