import os as _os
import sys as _sys

# ADK가 부모 디렉토리에서 패키지로 임포트할 때 내부 절대 임포트가 동작하도록
# 패키지 루트(Context_RAG_Agent/)를 sys.path 앞에 추가
_pkg_root = _os.path.dirname(_os.path.abspath(__file__))
if _pkg_root not in _sys.path:
    _sys.path.insert(0, _pkg_root)

# ADK는 패키지에서 root_agent 변수(BaseAgent 인스턴스)를 탐색함
# 모듈이 아닌 인스턴스를 직접 노출해야 함
from .root_agent_v1 import root_agent  # noqa: F401
