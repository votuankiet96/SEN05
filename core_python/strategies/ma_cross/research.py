"""Compat facade — giữ import path `ma_cross.research` hoạt động.

Nội dung thực đã được chuyển vào package research_utils/.
Import trực tiếp từ research_utils/ được khuyến khích cho code mới.
"""

from .research_utils import *  # noqa: F401, F403
from .research_utils import __all__
