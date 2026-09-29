"""Built-in screens. Each phase adds its screens to ``register_builtin_screens``."""

from __future__ import annotations

from caspian_parking.core.permissions import Permission
from caspian_parking.ui.shell.registry import ScreenSpec, register


def register_builtin_screens() -> None:
    from caspian_parking.ui.screens.audit import AuditScreen
    from caspian_parking.ui.screens.home import HomeScreen
    from caspian_parking.ui.screens.settings_screen import SettingsScreen
    from caspian_parking.ui.screens.tariffs import TariffsScreen
    from caspian_parking.ui.screens.users import UsersScreen

    register(ScreenSpec("home", "nav.home", "layout-dashboard", HomeScreen, keywords=("home", "خانه")))
    register(
        ScreenSpec(
            "users", "nav.users", "users", UsersScreen, Permission.MANAGE_USERS, keywords=("user", "کاربر", "دسترسی")
        )
    )
    register(
        ScreenSpec("audit", "nav.audit", "history", AuditScreen, Permission.VIEW_AUDIT, keywords=("audit", "تغییر"))
    )
    register(
        ScreenSpec(
            "tariffs",
            "nav.tariffs",
            "coins",
            TariffsScreen,
            Permission.CHANGE_TARIFFS,
            keywords=("tariff", "تعرفه", "ساعات کاری", "تعطیل", "قیمت"),
        )
    )
    register(ScreenSpec("settings", "nav.settings", "settings", SettingsScreen, keywords=("settings", "تنظیم", "تم")))
