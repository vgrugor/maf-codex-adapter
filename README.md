# MAF Codex Adapter

Окремий Python-проєкт для адаптера Microsoft Agent Framework → Codex SDK.

Документація інтеграції:

- [Контракт поточної версії](docs/contract.md) — вхід, вихід, сесії, конфігурація та помилки.
- [Матриця сумісності з MAF](docs/compatibility.md) — статус кожної можливості та докази.
- [План розвитку адаптера](docs/roadmap.md) — етапи й критерії готовності.
- [Результати перевірки](docs/verification.md) — офлайн та попередні live перевірки.

## Поточний результат

11 жовтня 2026 року на цьому Mac перевірено:

- MAF `agent-framework-core==1.21.0` встановлено та імпортується.
- Codex SDK `openai-codex==0.162.1` працює зі своїм bundled runtime.
- SDK використовує наявну авторизацію ChatGPT без API-ключа.
- Перший запит успішно завершився.
- Після закриття SDK і запуску нового процесу відновлена розмова пам'ятала випадковий маркер.

Реалізовано мінімальний `CodexAgent`: текстові запити, окремі розмови для сесій
MAF, відновлення серіалізованої сесії, перевірка авторизації ChatGPT, обробка
невдалих відповідей, timeout та запит на переривання при скасуванні.

Звіт початкової перевірки SDK лежить у `.state/sdk-check.json`.
Приклад `examples/check_maf_agent.py` перевіряє MAF workflow та ізоляцію сесій
чотирма реальними запитами. Після успіху він записує `.state/maf-agent-check.json`.

## Встановлення на іншому ноутбуці

Потрібен Python 3.12 або новіший. Для першого відтворення рекомендовано Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
```

`requirements.lock` фіксує версії встановленого середовища. Це pip freeze,
а не lock з hashes; встановлення на Windows/Linux треба перевірити окремо.
Робоча папка: `/Users/vlad/projects/maf-codex-adapter`.
На цьому Mac `.venv` у робочій папці вже створено. На іншому ноутбуці створіть
його командами вище; середовище між папками/ноутбуками не копіюється.

## Використання агента

```python
import asyncio
from pathlib import Path
from maf_codex import CodexAgent, CodexAgentConfig

async def main():
    agent = CodexAgent(
        CodexAgentConfig(cwd=Path("/path/to/target-repository")),
        id="developer",  # збережіть цей id при відновленні сесії
        name="Developer",
    )
    session = agent.create_session()
    response = await agent.run("Поясни структуру цього репозиторію.", session=session)
    print(response.text)
    response = await agent.run("Який модуль варто тестувати першим?", session=session)
    print(response.text)

asyncio.run(main())
```

За замовчуванням `history_mode="incremental"`: передавайте лише нові повідомлення;
повторний текст вважається новим завданням. Для графів MAF, що повертають повну
історію при повторному зверненні до агента, задайте `history_mode="full"` у config.
Адаптер перевірить відомий prefix і передасть лише новий suffix. Зміна або обрізання
історії та невизначений стан після failed full turn викликають `HistoryMismatchError`.
У full режимі snapshot MAF також зберігає текст підтвердженої розмови.

Без `session=` кожний запуск починає нову розмову. Для відновлення використовуйте `session.to_dict()` та
`AgentSession.from_dict(...)`, зберігаючи той самий id і конфігурацію агента.
Стан MAF містить versioned binding, а не токени авторизації. Для incremental можна
також використати `agent.get_session(thread_id)`; full потребує snapshot із історією.

Запити до однієї сесії та agent id серіалізуються між екземплярами в одному event loop.
Для паралельних запусків використовуйте різні сесії; кілька незалежних процесів
не повинні одночасно запускати один і той самий збережений thread.
Різні session id, які вручну прив'язані до одного thread, також не синхронізуються.
Назви агентів у multi-agent workflow мають бути унікальними.

За замовчуванням застосовується `Sandbox.read_only`. Зміни файлів потребують
явного `Sandbox.workspace_write` у конфігурації та відповідних дозволів runtime.
Адаптер не повторює невдалий запит автоматично: команда могла вже мати побічні ефекти.

## Межі першої версії

- Підтримуються лише текстові user/assistant повідомлення. Інструкції задаються в конфігурації.
- `stream=True` сумісний із `ResponseStream`, але повертає одну готову відповідь.
- Динамічні tools, client kwargs, context providers, middleware та інтерактивні погодження MAF ще не підключені.
- Керування інструментами й політикою погоджень усередині запуску залишається у Codex.
- Збереження сесії MAF відновлює розмову; checkpoint-відновлення всього workflow ще не перевірено.
- Usage, деталі викликів інструментів і поступові події SDK ще не перетворюються на типи MAF.

## Перевірка через підписку

Якщо вже є авторизація Codex, SDK повторно використовує її.
За потреби ввійди через `codex login`, якщо Codex CLI доступний у PATH.
Авторизацію також можна виконати через SDK:

```python
from openai_codex import Codex

with Codex() as codex:
    login = codex.login_chatgpt()
    print(login.auth_url)
    # Відкрий URL у браузері та заверши вхід.
    print(login.wait().success)
```

Запусти перевірку з кореня проєкту:

```bash
unset OPENAI_API_KEY CODEX_API_KEY
.venv/bin/python examples/check_sdk.py
.venv/bin/python examples/check_maf_agent.py
```

Скрипт перевіряє тип активної авторизації перед кожним запитом і завершується,
якщо це не ChatGPT. Модель береться з конфігурації Codex. Скрипт виконує два
короткі запити, що витрачають ліміти плану, та створює тестову розмову.
Для неї використовується порожня робоча папка й read-only sandbox.
Після виконання залишаються стан розмови Codex та локальний звіт.

Runtime Codex потребує запису службового стану до своєї папки, зазвичай
`~/.codex`. Якщо запуск відбувається всередині sandbox, йому також потрібні
відповідні файлові та мережеві дозволи. Файли авторизації не копіюємо до проєкту.

## Наступні етапи

Основний результат наступних етапів — розвиток адаптера. Workflows слугують
невеликими контрактними перевірками його сумісності, а не прикладним продуктом.

Повідомлення/сесії та базові multi-agent contract tests виконано. Далі:

1. Підключити agent-level middleware та context providers MAF.
2. Опрацювати міст tools і погоджень для складнішої взаємодії агентів.
3. Зміцнити mapping метаданих/помилок, runtime lifecycle і конкурентність.
4. Додати поступовий streaming і події за потребами керування агентом.
5. Перевірити решту оркестрацій та підготувати відтворюваний випуск.

Критерії готовності та залежності етапів — у [плані розвитку](docs/roadmap.md).

## Перевірка коду

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check examples src tests
.venv/bin/ruff format --check examples src tests
```

Unit/contract тести використовують імітацію SDK та не запускають Codex.
Перевіряються також справжні `WorkflowBuilder`, `AgentSession` і `ResponseStream` MAF.
Реальні запуски Codex залишаються окремою явною перевіркою.

## Джерела

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [Авторизація Codex](https://learn.chatgpt.com/docs/auth)
- [Власні агенти MAF](https://learn.microsoft.com/en-us/agent-framework/concepts/agents/custom-agents)
