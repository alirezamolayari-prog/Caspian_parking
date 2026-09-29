"""Audit log viewer: who changed what, when and where (read-only)."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPlainTextEdit

from caspian_parking.data.models import AuditLog, Node, User
from caspian_parking.data.repositories.system import AuditRepository
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_datetime
from caspian_parking.services.context import AppContext
from caspian_parking.ui.screens.base import Screen
from caspian_parking.ui.theme.tokens import Space
from caspian_parking.ui.widgets.basics import Card, label
from caspian_parking.ui.widgets.feedback import EmptyState
from caspian_parking.ui.widgets.table import Column, DataTable, LazyTableModel

ENTITIES = ("users", "settings", "gates", "levels", "role_presets", "nodes")
MAX_SUMMARY = 80


def entity_label(entity: str) -> str:
    key = f"entity.{entity}"
    text = tr(key)
    return entity if text == key else text


def summarize(entry: AuditLog) -> str:
    changes = entry.changes or {}
    if entry.action == "create":
        return tr("audit.created_fields", n=len(changes))
    parts = []
    for key, value in changes.items():
        if isinstance(value, list) and len(value) == 2:
            parts.append(f"{key}: {value[0]} → {value[1]}")
        else:
            parts.append(key)
    text = "، ".join(parts)
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 1] + "…"


class AuditScreen(Screen):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, tr("audit.title"), tr("audit.subtitle"))
        self._names: dict[str, str] = {}
        self.entity = QComboBox()
        self.entity.addItem(tr("audit.all_entities"), "")
        for entity in ENTITIES:
            self.entity.addItem(entity_label(entity), entity)
        self.entity.currentIndexChanged.connect(lambda _i: self.reload())
        self.header_actions.addWidget(label(tr("audit.filter"), "muted"))
        self.header_actions.addWidget(self.entity)
        row = QHBoxLayout()
        row.setSpacing(Space.L)
        self.body.addLayout(row, 1)
        columns = [
            Column(tr("audit.when"), lambda e: fa_datetime(e.created_at_utc, seconds=True), width=190),
            Column(tr("audit.who"), lambda e: self._name(e.created_by), width=150),
            Column(tr("audit.entity"), lambda e: entity_label(e.entity), width=120),
            Column(tr("audit.action"), lambda e: tr(f"audit.action.{e.action}"), width=110),
            Column(tr("audit.summary"), summarize),
        ]
        self.model = LazyTableModel(columns, self._fetch)
        self.table = DataTable(self.model)
        self.table.clicked.connect(lambda _i: self._show_detail(self.table.selected_object()))
        table_card = Card()
        table_card.add(self.table, 1)
        self.empty = EmptyState("history", tr("audit.empty"), tr("audit.empty_hint"))
        table_card.add(self.empty, 1)
        row.addWidget(table_card, 3)
        detail_card = Card(tr("audit.detail"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        detail_card.add(self.detail, 1)
        row.addWidget(detail_card, 2)
        self.reload()

    def _name(self, user_id: str | None) -> str:
        if not user_id:
            return tr("audit.system")
        if user_id not in self._names:
            with self.ctx.read() as session:
                user = session.get(User, user_id)
                node = session.get(Node, user_id) if user is None else None
            self._names[user_id] = user.display_name if user else (node.name if node else user_id[:8])
        return self._names[user_id]

    def _fetch(self, offset: int, limit: int) -> list[AuditLog]:
        with self.ctx.read() as session:
            repo = AuditRepository(session)
            return list(repo.page(offset, limit, repo.filtered(entity=self.entity.currentData() or None)))

    def reload(self) -> None:
        self.model.reset()
        has_rows = self.model.loaded_count() > 0
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)

    def on_show(self) -> None:
        self.reload()

    def _show_detail(self, entry: AuditLog | None) -> None:
        if entry is None:
            return
        header = [
            f"{tr('audit.when')}: {fa_datetime(entry.created_at_utc, seconds=True)}",
            f"{tr('audit.who')}: {self._name(entry.created_by)}",
            f"{tr('audit.node')}: {self._name(entry.origin_node)}",
            f"{tr('audit.reason')}: {entry.reason or '—'}",
            "",
        ]
        body = json.dumps(entry.changes, ensure_ascii=False, indent=2)
        self.detail.setPlainText("\n".join(header) + body)
