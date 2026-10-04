"""Publish every Homie uAgent on Agentverse (what the Inspector's Connect button does, for all agents at once).

1. Create an API key at https://agentverse.ai (Profile → API Keys) and put it in .env as AGENTVERSE_API_KEY.
2. python -m scripts.agentverse_publish
3. Run the agents (python -m agents.run): each one polls its Agentverse mailbox.
"""

import agents  # noqa: F401  (sets SSL certs before any HTTPS)
import asyncio
import sys

from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
from uagents_core.registration import RegistrationRequest

from agents.homie_agent import homie
from agents.specialists import caller, later, negotiator, scout, vibecheck, memory, paperwork, pictures, policy, repairs
from homie.config import env


async def main() -> None:
    key = env("AGENTVERSE_API_KEY")
    if not key:
        sys.exit("Put AGENTVERSE_API_KEY in .env first (https://agentverse.ai → Profile → API Keys).")
    for agent in (homie, caller, negotiator, paperwork, repairs, policy, pictures, memory, later, scout, vibecheck):
        details = RegistrationRequest(
            address=agent.address, name=agent.name, handle=agent._handle, url=None,
            profile=agent._build_registration_profile(), endpoints=agent._endpoints,
            protocols=list(agent.protocols.keys()), metadata=agent.metadata,
        )
        result = await register_in_agentverse(
            AgentverseConnectRequest(user_token=key, agent_type="mailbox"),
            agent._identity, agent._prefix, agent._agentverse, details,
        )
        print(f"{agent.name:18} {'published' if result.success else 'FAILED: ' + str(result.detail)}  {agent.address}")


if __name__ == "__main__":
    asyncio.run(main())
