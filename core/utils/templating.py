import jinja2
from starlette.templating import Jinja2Templates
from .path_utils import resource_path

template_path = resource_path("web/templates")

# Python 3.14 + 최신 Jinja2 조합에서 캐시 키로 (name, globals_dict) 튜플을 사용할 때
# globals_dict가 unhashable해서 TypeError가 발생하는 버그가 있음.
# cache_size=0 으로 캐시를 비활성화해 우회한다.
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(template_path),
    autoescape=True,
    cache_size=0,
)
templates = Jinja2Templates(env=_env)
