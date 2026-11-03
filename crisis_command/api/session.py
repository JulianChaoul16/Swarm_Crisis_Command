"""The single command service shared by API requests, and the demo timeline runner.

The app runs the seeded Richmond flood scenario. Model-written text (recommendation
explanations, public alerts) attaches in the background so plans never wait for it.
"""

from ..operations import DEMO_FLOOD_SCENARIO, CommandService
from ..operations.demo import DemoRunner

service = CommandService(scenario=DEMO_FLOOD_SCENARIO, background_models=True)
demo = DemoRunner(service)
