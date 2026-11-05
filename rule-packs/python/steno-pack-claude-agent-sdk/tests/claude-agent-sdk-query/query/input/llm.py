from claude_agent_sdk import ClaudeAgentOptions, query


async def ask(prompt: str):
    async for message in query(prompt=prompt, options=ClaudeAgentOptions(model="a-model")):
        print(message)
