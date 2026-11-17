"""Run-time configuration.

Every value is read from the environment (or a local ``.env`` file) when the app starts.
Nothing here is ever written back, logged or sent to the browser.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import dotenv_values, find_dotenv

ENV_SPACE = "SIGNALWIRE_SPACE"
ENV_PROJECT_ID = "SIGNALWIRE_PROJECT_ID"
ENV_API_TOKEN = "SIGNALWIRE_API_TOKEN"
ENV_PERSONAL_ACCESS_TOKEN = "SIGNALWIRE_PAT"
_PERSONAL_ACCESS_TOKEN_LONG = "SIGNALWIRE_PERSONAL_ACCESS_TOKEN"  # also accepted

_SPACE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ConfigError(ValueError):
    """The configuration cannot be used as given."""


@dataclass(frozen=True)
class Settings:
    """The space to read and the two credentials the documented APIs ask for.

    * The Logs APIs take a project ID and API token (Basic auth ``project_id:token``).
    * The space Billing and Balance APIs take a Personal access token (Basic auth with an
      empty username). A project API token is not accepted there.
    """

    space: str
    project_id: str = ""
    api_token: str = field(default="", repr=False)
    personal_access_token: str = field(default="", repr=False)

    @property
    def host(self) -> str:
        return f"{self.space}.signalwire.com"

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    @property
    def has_project_credentials(self) -> bool:
        return bool(self.project_id and self.api_token)

    @property
    def has_personal_access_token(self) -> bool:
        return bool(self.personal_access_token)


def normalize_space(value: str) -> str:
    """Accept ``example``, ``example.signalwire.com`` or the full URL; return ``example``."""
    name = value.strip().lower()
    name = re.sub(r"^https?://", "", name).split("/")[0]
    name = name.removesuffix(".signalwire.com")
    if not _SPACE_NAME.match(name):
        raise ConfigError(
            f'{ENV_SPACE} must be the space name alone, for example "example" for '
            "https://example.signalwire.com."
        )
    return name


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env``, or from the process environment plus ``.env``."""
    if env is None:
        # The environment wins over .env, except where a variable is set but empty (a
        # container started before .env was filled in): that must not hide the file's value.
        env = {
            **dotenv_values(find_dotenv(usecwd=True)),
            **{key: value for key, value in os.environ.items() if value},
        }
    space = (env.get(ENV_SPACE) or "").strip()
    if not space:
        raise ConfigError(f"{ENV_SPACE} is not set. Copy .env.example to .env and fill it in.")
    return Settings(
        space=normalize_space(space),
        project_id=(env.get(ENV_PROJECT_ID) or "").strip(),
        api_token=(env.get(ENV_API_TOKEN) or "").strip(),
        personal_access_token=(
            env.get(ENV_PERSONAL_ACCESS_TOKEN) or env.get(_PERSONAL_ACCESS_TOKEN_LONG) or ""
        ).strip(),
    )
