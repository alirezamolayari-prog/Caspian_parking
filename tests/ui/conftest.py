from __future__ import annotations

import pytest

from caspian_parking.app import create_application
from caspian_parking.services import auth
from caspian_parking.services.auth import CurrentUser
from caspian_parking.ui.theme.manager import ThemeManager


@pytest.fixture
def themed(qapp):
    create_application()
    ThemeManager.instance().apply("dark")
    yield ThemeManager.instance()
    ThemeManager.instance().apply("dark")


def make_user(ctx, username: str, preset: str = "admin", password: str = "secret1"):
    with ctx.uow() as session:
        return auth.create_user(session, username, username.title(), password, preset=preset)


@pytest.fixture
def admin_ctx(app_ctx, themed):
    user = make_user(app_ctx, "admin")
    app_ctx.user = CurrentUser.from_user(user)
    return app_ctx


@pytest.fixture
def operator_ctx(app_ctx, themed):
    user = make_user(app_ctx, "operator1", preset="operator")
    app_ctx.user = CurrentUser.from_user(user)
    return app_ctx
