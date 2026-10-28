from dataclasses import dataclass

from ..model import Coordinate, EmergencyService, Incident
@dataclass
class Role: 
    name: str
    capabilities: frozenset[str]
    

@dataclass 
class Agent: 
    id: str
    role: Role
    location: Coordinate
    services: list[EmergencyService]
    report: list[Incident]


@dataclass
class Authorative_State: 
    incidents: list[Incident]
    services: list[EmergencyService]
    Agents: list[Agent]
