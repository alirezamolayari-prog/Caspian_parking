"""Lane tile (SPEC §4.2): live camera preview, last read plate, detected vehicle type, confidence."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from caspian_parking.devices.plate_source import PlatePass
from caspian_parking.i18n import tr
from caspian_parking.i18n.format import fa_digits
from caspian_parking.ui.theme.tokens import Size, Space
from caspian_parking.ui.widgets.basics import chip, label, set_chip
from caspian_parking.ui.widgets.feedback import StatusLight
from caspian_parking.ui.widgets.plate import PlateWidget


class CameraTile(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)
        head = QHBoxLayout()
        self.light = StatusLight("black", 12)
        head.addWidget(self.light)
        self.title = label(title, "caption")
        head.addWidget(self.title, 1)
        self.vehicle = chip("", "neutral")
        self.confidence = chip("", "neutral")
        head.addWidget(self.vehicle)
        head.addWidget(self.confidence)
        layout.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(Space.S)
        self.preview = QLabel(tr("camera.no_signal"), alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName("CameraPreview")
        self.preview.setFixedHeight(Size.CAMERA_PREVIEW_H)
        self.preview.setMinimumWidth(Size.CAMERA_PREVIEW_H)
        row.addWidget(self.preview, 3)
        self.plate = PlateWidget(None, height=48)
        row.addWidget(self.plate, 2, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)
        self.vehicle.setVisible(False)
        self.confidence.setVisible(False)
        self.online = False

    def set_online(self, online: bool) -> None:
        self.online = online
        self.light.set_status("green" if online else "red")
        if not online:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(tr("camera.offline_short"))

    def set_frame(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
        )
        self.preview.setText("")
        self.preview.setPixmap(pixmap)

    def show_pass(self, item: PlatePass) -> None:
        result = item.result
        self.plate.set_plate(result.plate)
        self.vehicle.setVisible(result.vehicle_type is not None)
        if result.vehicle_type:
            set_chip(self.vehicle, tr(f"vehicle.{result.vehicle_type}"), "neutral")
        self.confidence.setVisible(True)
        if result.plate is None:
            set_chip(self.confidence, tr("camera.unreadable"), "danger")
        else:
            percent = round(result.confidence * 100)
            set_chip(
                self.confidence,
                tr("camera.confidence", n=fa_digits(percent)),
                "success" if result.accepted else "warning",
            )
        if item.snapshot:
            image = QImage.fromData(item.snapshot)
            if not image.isNull():
                self.set_frame(image)
