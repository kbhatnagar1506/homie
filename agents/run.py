"""Runs every Homie agent in one process.

    python -m agents.run

Six Fetch.ai uAgents (Homie, Caller, Negotiator, Paperwork, Repairs, Policy),
plus the Relay bridge when RELAY_TOKEN_HOMIE is set in .env.
"""

from uagents import Bureau

from agents.homie_agent import homie
from agents.specialists import caller, later, negotiator, scout, vibecheck, memory, paperwork, pictures, policy, repairs
from homie.config import env

if __name__ == "__main__":
    agents = [homie, caller, negotiator, paperwork, repairs, policy, pictures, memory, later, scout, vibecheck]
    if env("WEB_CHAT", "0") == "1":  # talk to the team from /chat instead of Relay
        from agents.web_bridge import web_bridge

        agents.append(web_bridge)
    elif env("RELAY_TOKEN_HOMIE"):
        from agents.relay_bridge import bridge

        agents.append(bridge)
    from homie.rpc import register

    register(*agents)
    bureau = Bureau(agents=agents, port=8000)
    for a in agents:
        print(f"{a.name:20} {a.address}")
    bureau.run()
