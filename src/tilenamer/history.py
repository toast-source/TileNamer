from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from PySide6.QtGui import QUndoCommand

if TYPE_CHECKING:
    from .ui import MainWindow


class AssignmentStateCommand(QUndoCommand):
    def __init__(
        self,
        window: "MainWindow",
        before: dict,
        after: dict,
        text: str,
    ) -> None:
        super().__init__(text)
        self.window = window
        self.before = deepcopy(before)
        self.after = deepcopy(after)

    def undo(self) -> None:
        self.window._restore_assignment_state(self.before)

    def redo(self) -> None:
        self.window._restore_assignment_state(self.after)


class LayerVisibilityCommand(QUndoCommand):
    def __init__(
        self,
        window: "MainWindow",
        before: dict[str, bool],
        after: dict[str, bool],
        text: str,
        first_redo_applied: bool = False,
    ) -> None:
        super().__init__(text)
        self.window = window
        self.before = dict(before)
        self.after = dict(after)
        self.first_redo_applied = first_redo_applied

    def undo(self) -> None:
        self.window._restore_layer_visibility(self.before)

    def redo(self) -> None:
        if self.first_redo_applied:
            self.first_redo_applied = False
            return
        self.window._restore_layer_visibility(self.after)


class TemporaryTagStateCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", before_tags: list[str], before_assignments: dict,
                 after_tags: list[str], after_assignments: dict, text: str) -> None:
        super().__init__(text)
        self.window = window
        self.before_tags = list(before_tags)
        self.before_assignments = deepcopy(before_assignments)
        self.after_tags = list(after_tags)
        self.after_assignments = deepcopy(after_assignments)

    def undo(self) -> None:
        self.window._restore_temporary_tag_state(self.before_tags, self.before_assignments)

    def redo(self) -> None:
        self.window._restore_temporary_tag_state(self.after_tags, self.after_assignments)


class AlignmentStateCommand(QUndoCommand):
    def __init__(self, window: "MainWindow", before: dict[str, tuple[int, int]],
                 after: dict[str, tuple[int, int]], text: str,
                 first_redo_applied: bool = False,
                 before_enabled: bool | None = None,
                 after_enabled: bool | None = None) -> None:
        super().__init__(text)
        self.window = window
        self.before = dict(before)
        self.after = dict(after)
        self.first_redo_applied = first_redo_applied
        self.before_enabled = before_enabled
        self.after_enabled = after_enabled

    def undo(self) -> None:
        self.window._restore_alignment_state(self.before, self.before_enabled)

    def redo(self) -> None:
        if self.first_redo_applied:
            self.first_redo_applied = False
            return
        self.window._restore_alignment_state(self.after, self.after_enabled)
