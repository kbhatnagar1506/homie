"""Runs all five Homie agents in one process.

    python -m agents.run
"""

from uagents import Bureau

from agents.homie_agent import homie
from agents.specialists import caller, negotiator, paperwork, repairs

if __name__ == "__main__":
    bureau = Bureau(agents=[homie, caller, negotiator, paperwork, repairs], port=8000)
    for a in bureau._agents:
        print(f"{a.name:18} {a.address}")
    bureau.run()
