from crewai import Agent

from .prompts import ROLES


def build_agents(llm):
    agents = {}
    for key, spec in ROLES.items():
        agents[key] = Agent(
            role=spec["role"],
            goal=spec["goal"],
            backstory=spec["backstory"],
            llm=llm,
            allow_delegation=False,
            max_retry_limit=2,
            verbose=False,
        )
    return agents
