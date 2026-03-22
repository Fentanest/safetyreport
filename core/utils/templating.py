from fastapi.templating import Jinja2Templates
from .path_utils import resource_path

# Centralized Jinja2Templates configuration to ensure consistent path resolution
# works both in development and in PyInstaller bundled environments.
template_path = resource_path("web/templates")
templates = Jinja2Templates(directory=template_path)
