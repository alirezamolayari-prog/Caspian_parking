"""Built-in screens. Each phase adds its screens to ``register_builtin_screens`` (sidebar order)."""

from __future__ import annotations

from caspian_parking.core.permissions import Permission
from caspian_parking.ui.shell.registry import ScreenSpec, register


def register_builtin_screens() -> None:
    from caspian_parking.ui.screens.ads import AdsScreen
    from caspian_parking.ui.screens.audit import AuditScreen
    from caspian_parking.ui.screens.blocklist import BlocklistScreen
    from caspian_parking.ui.screens.dashboard import DashboardScreen
    from caspian_parking.ui.screens.free_access import FreeAccessScreen
    from caspian_parking.ui.screens.gate import GateScreen
    from caspian_parking.ui.screens.home import HomeScreen
    from caspian_parking.ui.screens.reports import ReportsScreen
    from caspian_parking.ui.screens.review import ReviewScreen
    from caspian_parking.ui.screens.settings_screen import SettingsScreen
    from caspian_parking.ui.screens.shops import ShopsScreen
    from caspian_parking.ui.screens.subscribers import SubscribersScreen
    from caspian_parking.ui.screens.system import SystemScreen
    from caspian_parking.ui.screens.tariffs import TariffsScreen
    from caspian_parking.ui.screens.users import UsersScreen

    specs = [
        ScreenSpec(
            "gate",
            "nav.gate",
            "circle-parking",
            GateScreen,
            Permission.OPERATE_GATE,
            keywords=("gate", "ورود", "خروج", "درب", "رسید"),
        ),
        ScreenSpec(
            "dashboard",
            "nav.dashboard",
            "gauge",
            DashboardScreen,
            Permission.VIEW_REPORTS,
            keywords=("dashboard", "داشبورد", "امروز"),
        ),
        ScreenSpec("home", "nav.home", "layout-dashboard", HomeScreen, keywords=("home", "خانه")),
        ScreenSpec(
            "subscribers",
            "nav.subscribers",
            "users",
            SubscribersScreen,
            Permission.MANAGE_SUBSCRIBERS,
            keywords=("subscriber", "مشترک", "اشتراک", "پیگیری"),
        ),
        ScreenSpec(
            "shops", "nav.shops", "store", ShopsScreen, Permission.MANAGE_SHOPS, keywords=("shop", "مغازه", "کیف پول")
        ),
        ScreenSpec(
            "free_access",
            "nav.free_access",
            "badge-percent",
            FreeAccessScreen,
            Permission.GRANT_GUEST,
            keywords=("guest", "مهمان", "پرسنل", "رایگان"),
        ),
        ScreenSpec(
            "blocklist",
            "nav.blocklist",
            "ban",
            BlocklistScreen,
            Permission.MANAGE_BLOCKLIST,
            keywords=("block", "مسدود"),
        ),
        ScreenSpec(
            "ads",
            "nav.ads",
            "megaphone",
            AdsScreen,
            Permission.MANAGE_ADS,
            keywords=("ads", "تبلیغ", "کوپن", "قرعه‌کشی", "قالب رسید"),
        ),
        ScreenSpec(
            "reports",
            "nav.reports",
            "chart-column",
            ReportsScreen,
            Permission.VIEW_REPORTS,
            keywords=("report", "گزارش", "مالی", "خروجی"),
        ),
        ScreenSpec(
            "review",
            "nav.review",
            "list-checks",
            ReviewScreen,
            Permission.CANCEL_TRANSACTIONS,
            keywords=("review", "بررسی", "تکراری", "تعارض"),
        ),
        ScreenSpec(
            "users",
            "nav.users",
            "user-cog",
            UsersScreen,
            Permission.MANAGE_USERS,
            keywords=("user", "کاربر", "دسترسی"),
        ),
        ScreenSpec("audit", "nav.audit", "history", AuditScreen, Permission.VIEW_AUDIT, keywords=("audit", "تغییر")),
        ScreenSpec(
            "tariffs",
            "nav.tariffs",
            "coins",
            TariffsScreen,
            Permission.CHANGE_TARIFFS,
            keywords=("tariff", "تعرفه", "ساعات کاری", "تعطیل", "قیمت"),
        ),
        ScreenSpec(
            "system",
            "nav.system",
            "hard-drive-download",
            SystemScreen,
            Permission.BACKUP_RESTORE,
            keywords=("backup", "پشتیبان", "سال مالی", "عکس", "آموزشی"),
        ),
        ScreenSpec("settings", "nav.settings", "settings", SettingsScreen, keywords=("settings", "تنظیم", "تم")),
    ]
    for spec in specs:
        register(spec)
