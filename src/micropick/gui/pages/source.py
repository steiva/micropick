"""Manual pick's Source step (#88): what the operator picks from.

A tile for the source dish and one per plate on the deck
(`Session.plates`), each with its slot, nothing preselected. A click
chooses (`Session.choose_source`), for the robot session; a plate moved or
taken off the deck is not chosen any more, and its tile shows it. The
steps after this one follow the choice (`tasks.steps`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QPushButton, QVBoxLayout,
                               QWidget)

from ..camera_moves import pose_slot
from ..readiness import DISH_POSITION
from ..session import SOURCE_DISH, Session, SessionError
from ..theme import SPACING
from ..theme.factory import heading
from ..widgets.toast import show_toast
from ..workers import describe_error

__all__ = ["SourcePage", "TITLE"]

TITLE = "Source"

COLUMNS = 3
TILE_WIDTH = 320
TILE_HEIGHT = 96


class SourcePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        # By what `Session.choose_source` takes.
        self.tiles: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING * 3)
        layout.addStretch(1)
        layout.addWidget(heading("Where do you pick from?", 1, self), 0,
                         Qt.AlignmentFlag.AlignHCenter)
        self.grid = QGridLayout()
        self.grid.setSpacing(SPACING * 2)
        layout.addLayout(self.grid)
        layout.setAlignment(self.grid, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)

        session.labware_changed.connect(lambda _s: self.refresh())
        session.robot_state_changed.connect(lambda _s: self.refresh())
        session.profile_changed.connect(lambda _p: self.refresh())
        session.confirmations_changed.connect(self.refresh)
        self.refresh()

    def _dish_text(self) -> str:
        profile = self.session.profile
        observe = (profile.positions.get(DISH_POSITION)
                   if profile is not None else None)
        if observe is None:
            return "Source dish"
        slot = pose_slot(self.session, DISH_POSITION, observe[:2])
        return f"Source dish\nSlot {slot}"

    def _wanted(self) -> dict[str, str]:
        wanted = {SOURCE_DISH: self._dish_text()}
        for slot, _entry, definition in self.session.plates():
            wanted[slot] = f"{definition.display_name}\nSlot {slot}"
        return wanted

    def _list_tiles(self, wanted: dict[str, str]) -> None:
        for tile in self.tiles.values():
            tile.hide()
            tile.deleteLater()
        self.tiles = {}
        for index, (key, text) in enumerate(wanted.items()):
            tile = QPushButton(text, self)
            tile.setObjectName("tile")
            tile.setCheckable(True)
            tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tile.setFixedSize(TILE_WIDTH, TILE_HEIGHT)
            tile.clicked.connect(lambda _=False, k=key: self._choose(k))
            self.grid.addWidget(tile, *divmod(index, COLUMNS))
            self.tiles[key] = tile

    def _choose(self, key: str) -> None:
        try:
            self.session.choose_source(key)
        except SessionError as exc:
            show_toast(self, describe_error(exc), refusal=True)
            self.refresh()

    def _chosen(self) -> str | None:
        plate = self.session.source_plate
        return plate.slot if plate is not None else self.session.source

    def refresh(self) -> None:
        wanted = self._wanted()
        if wanted != {k: t.text() for k, t in self.tiles.items()}:
            self._list_tiles(wanted)
        chosen = self._chosen()
        for key, tile in self.tiles.items():
            tile.setChecked(key == chosen)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The position over the dish may have been set elsewhere.
        self.refresh()
