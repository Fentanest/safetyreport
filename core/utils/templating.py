import jinja2
from starlette.templating import Jinja2Templates
from .path_utils import resource_path

template_path = resource_path("web/templates")

# 기존 호환성 우회를 유지한다. Python 3.14/Jinja에서 dict globals가 캐시 키에
# 들어간다는 원인 설명은 격리 실험에서 재현되지 않았다. 제품 전체 렌더와
# frozen 환경 검증을 마치기 전에는 이 리팩터링에서 캐시를 활성화하지 않는다.
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(template_path),
    autoescape=True,
    cache_size=0,
)
# 상태 배지 색 이름(EO R-01: services/report_policy.badge_key 하나로 판정)
from services.report_policy import badge_key as _status_badge  # noqa: E402

_env.filters["status_badge"] = _status_badge
templates = Jinja2Templates(env=_env)
