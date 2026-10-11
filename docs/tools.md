# Інструменти та погодження

Базовий міст реалізовано для MAF 1.21.0 і Codex SDK/runtime 0.162.1.
Dynamic tools — experimental API App Server. Високорівневий Python SDK не
відкриває його параметрів/handlers, тому sdk_bridge.py ізолює pinned transport seam.

## Реєстрація та виклик

```python
from agent_framework import FunctionTool
from maf_codex import CodexAgent, CodexAgentConfig

def echo(text: str) -> str:
    return text

agent = CodexAgent(config, tools=[
    FunctionTool(name="echo", func=echo, approval_mode="always_require")
], approval_handler=review)
```

Підтримуються callable FunctionTool з моделлю або JSON schema MAF, включно з
child_agent.as_tool(). Hosted/declaration-only tools, довільні objects і raw
callables не підтримуються. Імена унікальні, 1..60 символів A-Z/a-z/0-9/_/-.
У Codex вони реєструються як maf_<name> з JSON inputSchema і описом.

Tools задаються в конструкторі, через run(tools=[...]) або ContextProvider.tools.
Run-level list замінює constructor list; tools providers додаються. None означає
constructor default, порожній список — явне очищення до додавання providers.
Options.tools не підтримується. Непідтримувані contributions відхиляються.

Для item/tool/call перевіряються активні thread/turn, call id, registry name,
namespace та аргументи. Міст використовує той самий prepared-arguments механізм
MAF, що FunctionTool.invoke; аргументи погодження — копія нормалізованих supplied
arguments. Native function defaults лишаються частиною довіреної реалізації tool.

FunctionInvocationContext містить сесію MAF, thread/turn/call metadata і
parent_service_session_state_keys={maf_codex}. Це дозволяє child.as_tool() отримати
приватний provider context без успадкування розмови батьківського CodexAgent.

Текстові Content items перетворюються на inputText results. Невідомий tool,
невалідні аргументи, exception або rich media result дають success=false.
Exception messages не передаються моделі; передається лише категорія помилки.
Помилка tool не обов'язково означає помилку AgentResponse: Codex може завершити
turn текстовим поясненням. Safe item summaries доступні в AgentResponse.additional_properties.codex.items; raw output не копіюється.

FunctionTool виконується в Python-процесі застосунку з його правами доступу.
Sandbox Codex обмежує native операції Codex, але не ізолює ці Python callbacks.
Перевірки доступу й ізоляція host tools є відповідальністю їхньої реалізації.

## Погодження

```python
async def review(request):
    # Передайте request до UI або своєї policy. Тут можна await рішення користувача.
    return "approve"  # або "reject", "cancel"
```

ApprovalRequest містить kind (tool/command/file), session_id, thread_id, turn_id,
request_id, name й arguments. Для native запитів arguments містить original
request data та item preview (command/diff), якщо item/started був отриманий;
інакше item=None. UI має перевіряти preview, а не припускати його наявність.

- FunctionTool.always_require викликає review до виконання функції; без handler відмова.
- FunctionTool.never_require дозволений політикою зареєстрованого tool й не запитує review.
- Native command/file approval запити за замовчуванням відхиляються. Handler може
  повернути лише accept/decline/cancel через відповідні approve/reject/cancel.
- Persistent acceptForSession, execpolicy/network amendments не підтримуються.
- Без handler thread policy=never; з handler policy=on-request, reviewer=user.
  Managed constraints Codex залишаються чинними. Операції всередині наявних
  sandbox permissions можуть виконуватися без окремого approval request.
- Permissions requests повертають порожній grant; MCP elicitation скасовується;
  інші server requests, включно з requestUserInput, отримують явну RPC помилку.
- Handler exception або невідоме рішення — відмова. Native availableDecisions
  враховується; відсутнє в ньому accept не видається.

Cancel з handler зупиняє collection, запитує interrupt активного turn і дає
CancelledError. Закриття runtime скасовує pending callbacks; пізнє рішення не
може виконати tool. Stdout reader не блокується, поки review очікує рішення.
Callback очікування входить у runtime timeout. Уже виконані side effects не відкочуються.

Це in-process callback approvals, а не MAF FunctionApprovalRequest/Response чи
durable human-in-the-loop checkpoint. При завершенні процесу pending рішення
не зберігається. Повні ToolApprovalMiddleware/function middleware ще не підключені.

## Сесії й обмеження

App Server зберігає dynamic tools при thread/start і відновлює їх при resume.
Тому binding містить tools_fingerprint (schema + approval modes). Зміна catalog
у тій самій сесії відхиляється; створіть нову сесію. Callback/функції не
серіалізуються: застосунок має відновити їх при створенні агента. Fingerprint
не визначає версію Python-реалізації функції; code/registry не можна змінювати
під час active turn.

Tool-enabled agent.get_session(thread_id) відхиляється без catalog binding;
для відновлення потрібен snapshot створеної адаптером сесії та відповідний registry.
Namespace tools, rich media output та live зміна registration не підтримуються.

Duplicate dynamic call ids блокуються в межах одного turn. Native RPC request ids
розрізняються, бо один item може запитувати кілька окремих погоджень. Exactly-once
між перезапусками/процесами не гарантується; automatic retries відсутні.

## Перевірки

Offline: tools validation/approval, native mapping, delayed decisions, cancellation,
transport routing/cleanup, provider tools, catalog binding, child.as_tool isolation.
Live: examples/check_tools.py виконав два turns через ChatGPT — approved echo та
rejected echo після JSON restore тієї самої сесії. Звіт: .state/tools-check.json.
Native command/file approvals перевірено лише offline; file/shell side effects
live не запускались. Experimental protocol може змінитися з версією runtime.

Джерело: [App Server — approvals та dynamic tools](https://learn.chatgpt.com/docs/app-server).
Схема додатково перевірена локальним generate-json-schema --experimental runtime 0.162.1.
