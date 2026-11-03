"""The single command service shared by API requests.

Model-written text (recommendation explanations, public alerts) attaches in the background
so the plan and approval never wait for a model call.
"""

from ..operations import CommandService

service = CommandService(background_models=True)
