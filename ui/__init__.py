"""UI package: CustomTkinter views, widgets and dialogs.

This layer contains no PDF processing logic - it collects user input, hands
work to :mod:`core` on a background thread, and renders the results.

To add a tool: create ``ui/<tool>_view.py`` with a :class:`ui.base_view.BaseView`
subclass, register it in :meth:`app.PdfToolkitApp._create_views`, and add a
:class:`ui.home.ToolEntry` to :meth:`ui.home.HomeView.tool_entries`.
"""

from ui.base_view import AppController, BaseView
from ui.home import HomeView
from ui.merge_view import MergeView
from ui.split_view import SplitView

__all__ = ["AppController", "BaseView", "HomeView", "MergeView", "SplitView"]
