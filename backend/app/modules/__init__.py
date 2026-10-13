"""Module package for the MiniTV backend.

Importing this package registers every ORM model on the shared declarative
base so Alembic autogenerate and create_all see the full metadata.
Stage 1 implements account / content / wallet / billing models plus shared
infrastructure tables; playback has no persistent tables yet and social /
ops / analytics stay as empty packages by design.
"""

from app.modules.account import models as _account_models  # noqa: F401
from app.modules.billing import models as _billing_models  # noqa: F401
from app.modules.common import models as _common_models  # noqa: F401
from app.modules.content import models as _content_models  # noqa: F401
from app.modules.ops import models as _ops_models  # noqa: F401
from app.modules.wallet import models as _wallet_models  # noqa: F401

from app.modules.analytics import models as _analytics_models  # noqa: F401
from app.modules.ops import config_models as _config_models  # noqa: F401
from app.modules.playback import models as _playback_models  # noqa: F401
from app.modules.social import models as _social_models  # noqa: F401
