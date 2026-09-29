"""Lazy, paginated table model (SPEC §2.4: never load tens of thousands of rows at once)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from caspian_parking.ui.theme.tokens import Size

Index = QModelIndex | QPersistentModelIndex
Fetcher = Callable[[int, int], Sequence[Any]]


@dataclass(frozen=True)
class Column:
    header: str
    value: Callable[[Any], object]
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeading
    width: int | None = None
    tooltip: Callable[[Any], str] | None = None


class LazyTableModel(QAbstractTableModel):
    """Rows are fetched in batches through ``fetcher(offset, limit)`` as the user scrolls."""

    def __init__(self, columns: Sequence[Column], fetcher: Fetcher, batch: int = 200, parent: Any = None) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._fetcher = fetcher
        self._batch = batch
        self._rows: list[Any] = []
        self._exhausted = False

    # -- data access -------------------------------------------------
    def columns(self) -> list[Column]:
        return list(self._columns)

    def row_object(self, row: int) -> Any:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def loaded_count(self) -> int:
        return len(self._rows)

    def reset(self, fetcher: Fetcher | None = None) -> None:
        self.beginResetModel()
        if fetcher is not None:
            self._fetcher = fetcher
        self._rows = []
        self._exhausted = False
        self.endResetModel()
        if self.canFetchMore(QModelIndex()):
            self.fetchMore(QModelIndex())

    # -- Qt model API -------------------------------------------------
    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._columns)

    def canFetchMore(self, parent: Index) -> bool:
        return not parent.isValid() and not self._exhausted

    def fetchMore(self, parent: Index) -> None:
        if parent.isValid() or self._exhausted:
            return
        batch = list(self._fetcher(len(self._rows), self._batch))
        if len(batch) < self._batch:
            self._exhausted = True
        if not batch:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(batch) - 1)
        self._rows.extend(batch)
        self.endInsertRows()

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        column = self._columns[index.column()]
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            value = column.value(row)
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(column.align)
        if role == Qt.ItemDataRole.ToolTipRole and column.tooltip is not None:
            return column.tooltip(row)
        if role == Qt.ItemDataRole.UserRole:
            return row
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].header
        return None


class DataTable(QTableView):
    """Table view with the app's defaults: row selection, no grid, comfortable rows."""

    def __init__(self, model: LazyTableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(Size.ROW)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, column in enumerate(model.columns()):
            if column.width:
                self.setColumnWidth(index, column.width)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def lazy_model(self) -> LazyTableModel:
        model = self.model()
        assert isinstance(model, LazyTableModel)
        return model

    def selectRow(self, row: int) -> None:
        """Select a whole row (QTableView.selectRow is unreliable with our lazy model)."""
        index = self.lazy_model().index(row, 0)
        if not index.isValid():
            return
        flags = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        self.selectionModel().setCurrentIndex(index, flags)
        self.scrollTo(index)

    def selected_object(self) -> Any:
        indexes = self.selectionModel().selectedRows()
        return self.lazy_model().row_object(indexes[0].row()) if indexes else None
