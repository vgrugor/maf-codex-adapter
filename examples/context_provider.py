"""Usage example only: running this file spends Codex plan usage."""

import asyncio
from pathlib import Path

from agent_framework import AgentContext, ContextProvider, Message, agent_middleware

from maf_codex import CodexAgent, CodexAgentConfig


class ProjectContext(ContextProvider):
    async def before_run(self, *, agent, session, context, state):
        context.extend_messages(
            self.source_id, [Message("user", ["This project is a Python library."])]
        )
        context.extend_instructions(self.source_id, "Answer in Ukrainian.")

    async def after_run(self, *, agent, session, context, state):
        state["completed_runs"] = state.get("completed_runs", 0) + 1


@agent_middleware
async def progress(context: AgentContext, call_next):
    print("Starting", context.agent.name)
    await call_next()
    print("Finished", context.result.text)


async def main():
    workspace = Path(__file__).resolve().parents[1] / ".state" / "provider-example"
    workspace.mkdir(parents=True, exist_ok=True)
    agent = CodexAgent(
        CodexAgentConfig(cwd=workspace),
        id="provider-example",
        middleware=[progress],
        context_providers=[ProjectContext("project_context")],
    )
    session = agent.create_session()
    await agent.run("Explain how to test a Python library. Do not use tools.", session=session)
    print("Provider state:", session.state["project_context"])


if __name__ == "__main__":
    asyncio.run(main())
