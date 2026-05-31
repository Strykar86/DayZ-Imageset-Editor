# ==============================================================================
# DayZ Imageset Editor
# Copyright (C) 2026 Strykar
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
# ==============================================================================

import sys
import os
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QFileDialog, QTreeWidget, 
                             QTreeWidgetItem, QGraphicsView, QGraphicsScene, 
                             QGraphicsPixmapItem, QGraphicsRectItem, QColorDialog, QLabel, QComboBox,
                             QCheckBox, QSpinBox, QMessageBox, QSplitter, QDialog,
                             QInputDialog, QLineEdit, QUndoStack, QUndoCommand, QShortcut, QTabWidget,
                             QScrollArea, QSizePolicy)
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPixmap, QColor, QPainter, QCursor, QKeySequence, QIcon, QImage
from PIL import Image
from imagesetconv_wrapper import ImagesetConverter

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

class MoveItemsCommand(QUndoCommand):
    def __init__(self, items_data, description="Move Items"):
        super().__init__(description)
        # items_data is a list of tuples: (element_item, old_pos, new_pos)
        self.items_data = items_data

    def undo(self):
        for element, old_pos, new_pos in self.items_data:
            element.setPos(old_pos)

    def redo(self):
        for element, old_pos, new_pos in self.items_data:
            element.setPos(new_pos)

class DeleteItemsCommand(QUndoCommand):
    def __init__(self, workspace, elements_to_delete, description="Delete Items"):
        super().__init__(description)
        self.workspace = workspace
        self.scene = workspace.scene
        self.tree = workspace.tree
        
        # Store everything needed to cleanly restore the items
        self.items_data = []
        for element in elements_to_delete:
            tree_item = getattr(element, 'tree_item', None)
            parent_item = tree_item.parent() or self.tree.invisibleRootItem() if tree_item else None
            self.items_data.append({
                'element': element,
                'tree_item': tree_item,
                'parent': parent_item
            })

    def undo(self):
        for data in self.items_data:
            element = data['element']
            self.scene.addItem(element)
            if data['tree_item'] and data['parent']:
                data['parent'].addChild(data['tree_item'])
        self.workspace.update_element_count()

    def redo(self):
        for data in self.items_data:
            element = data['element']
            element.hide_selection_overlay()
            self.scene.removeItem(element)
            if data['tree_item'] and data['parent']:
                data['parent'].removeChild(data['tree_item'])
        self.workspace.update_element_count()

class SpinBoxMoveCommand(QUndoCommand):
    def __init__(self, element, old_pos, new_pos, description="Move Item via SpinBox"):
        super().__init__(description)
        self.element = element
        self.old_pos = old_pos
        self.new_pos = new_pos

    def undo(self):
        self.element.setPos(self.old_pos)

    def redo(self):
        self.element.setPos(self.new_pos)

    def id(self):
        # A unique ID tells the UndoStack these commands are allowed to merge
        return 999

    def mergeWith(self, command):
        # If it's the same element, keep the original starting position but adopt its new end position
        if command.id() == self.id() and command.element == self.element:
            self.new_pos = command.new_pos
            return True
        return False

class DraggableAsset(QGraphicsPixmapItem):
    def __init__(self, name, pixmap, group_item, canvas_width=4096, canvas_height=4096):
        super().__init__(pixmap)
        self.name = name
        self.group_item = group_item
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.outline_color = QColor("#ffffff")
        
        # Use ItemIsSelectable but NOT ItemIsMovable (we handle dragging manually)
        self.setFlag(QGraphicsPixmapItem.ItemIsSelectable)
        self.setFlag(QGraphicsPixmapItem.ItemSendsGeometryChanges)
        
        # Create overlays ONCE as persistent child items
        self.selection_overlay = QGraphicsRectItem(self.boundingRect(), self)
        self.selection_overlay.setZValue(0.5)
        self.selection_overlay.setBrush(QColor(100, 150, 255, 80))
        self.selection_overlay.setPen(QColor(0, 0, 0, 0))
        self.selection_overlay.hide()
        
        # --- Make overlays transparent to mouse clicks ---
        self.selection_overlay.setAcceptedMouseButtons(Qt.NoButton)
        
        inset_rect = self.boundingRect().adjusted(1, 1, -1, -1)
        self.border_rect = QGraphicsRectItem(inset_rect, self)
        self.border_rect.setZValue(1)
        self.border_rect.setBrush(QColor(0, 0, 0, 0))
        self.border_rect.hide()
        
        # --- Make borders transparent to mouse clicks ---
        self.border_rect.setAcceptedMouseButtons(Qt.NoButton)
    
    def set_outline(self, color):
        self.outline_color = color
        pen = self.border_rect.pen()
        pen.setColor(color)
        pen.setWidth(1)
        self.border_rect.setPen(pen)
        self.border_rect.show()
    
    def clear_outline(self):
        self.border_rect.hide()
    
    def show_selection_overlay(self):
        self.selection_overlay.show()
    
    def hide_selection_overlay(self):
        self.selection_overlay.hide()
    
    def itemChange(self, change, value):
        if change == QGraphicsPixmapItem.ItemPositionChange:
            new_pos = value
            rect = self.boundingRect()
            x = max(0, min(new_pos.x(), self.canvas_width - rect.width()))
            y = max(0, min(new_pos.y(), self.canvas_height - rect.height()))
            return QPointF(x, y)
        return super().itemChange(change, value)
        
class ZoomableView(QGraphicsView):
    def __init__(self, scene, workspace=None):
        super().__init__(scene)
        self.workspace = workspace
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.pan_active = False
        self.pan_start = None
        self.selection_rect_active = False
        self.selection_rect_start = None
        self.selection_rect_item = None
        # Drag tracking for proper offset handling
        self.drag_active = False
        self.drag_start_scene_pos = None
        self.drag_start_item_positions = {}
        
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.pan_active = True
            self.pan_start = event.pos()
            self.setCursor(QCursor(Qt.ClosedHandCursor))
            event.accept()
        elif event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            item_under_mouse = self.scene().itemAt(scene_pos, self.transform())
            
            # Drill down to the parent element if we hit the visual overlay or border
            if item_under_mouse and item_under_mouse.parentItem():
                item_under_mouse = item_under_mouse.parentItem()
                
            if isinstance(item_under_mouse, DraggableAsset):
                # Record initial drag state for manual offset tracking
                self.drag_active = True
                self.drag_start_scene_pos = scene_pos
                self.drag_start_item_positions = {
                    item: item.pos() for item in self.scene().selectedItems() 
                    if isinstance(item, DraggableAsset)
                }
                
                # Let PyQt handle selection
                if not (item_under_mouse.isSelected() and event.modifiers() & Qt.ShiftModifier == 0):
                    if not (event.modifiers() & Qt.ShiftModifier):
                        self.scene().clearSelection()
                
                item_under_mouse.setSelected(True)
                event.accept()
                return
                
            # Clicked empty space: start selection box
            self.selection_rect_active = True
            self.selection_rect_start = scene_pos
            if not (event.modifiers() & Qt.ShiftModifier):
                self.scene().clearSelection()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pan_active and self.pan_start:
            delta = event.pos() - self.pan_start
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.pan_start = event.pos()
            event.accept()
        elif self.drag_active and self.drag_start_scene_pos is not None:
            # Manual drag: translate mouse movement to item movement
            current_scene_pos = self.mapToScene(event.pos())
            delta = current_scene_pos - self.drag_start_scene_pos
            
            # Move all selected items by the same delta
            for item, start_pos in self.drag_start_item_positions.items():
                new_pos = start_pos + delta
                
                # Apply snapping if enabled
                if self.workspace:
                    if self.workspace.snap_to_grid_enabled:
                        new_pos = self.workspace.snap_to_grid(new_pos)
                    if self.workspace.snap_to_elements_enabled:
                        new_pos = self.workspace.snap_to_elements(item, new_pos)
                
                item.setPos(new_pos)

                # --- Syncing Spinboxes during mouse drag ---
            if self.workspace and len(self.scene().selectedItems()) == 1:
                self.workspace.sync_spinboxes_to_item(self.scene().selectedItems()[0])
            
            event.accept()
        elif self.selection_rect_active and self.selection_rect_start:
            current_pos = self.mapToScene(event.pos())
            if self.selection_rect_item:
                self.scene().removeItem(self.selection_rect_item)
            
            # --- Normalize geometry so dragging up/left works flawlessly ---
            x = min(self.selection_rect_start.x(), current_pos.x())
            y = min(self.selection_rect_start.y(), current_pos.y())
            w = abs(current_pos.x() - self.selection_rect_start.x())
            h = abs(current_pos.y() - self.selection_rect_start.y())
            
            self.selection_rect_item = self.scene().addRect(x, y, w, h)
            self.selection_rect_item.setPen(QColor(100, 150, 255, 200))
            self.selection_rect_item.setBrush(QColor(100, 150, 255, 50))
            self.selection_rect_item.setZValue(100)
            self.selection_rect_item.is_selection_rect = True
            event.accept()
        else:
            super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.pan_active = False
            self.pan_start = None
            self.setCursor(QCursor(Qt.ArrowCursor))
            event.accept()
        elif event.button() == Qt.LeftButton:
            # Handle end of drag: create undo command if items moved
            if self.drag_active and self.drag_start_item_positions:
                # Check if any items actually moved
                items_moved = []
                for item, start_pos in self.drag_start_item_positions.items():
                    if item.pos() != start_pos:
                        items_moved.append((item, start_pos, item.pos()))
                
                # Create undo/redo command if items moved
                if items_moved and self.workspace:
                    self.workspace.undo_stack.push(MoveItemsCommand(items_moved))
                
                self.drag_active = False
                self.drag_start_scene_pos = None
                self.drag_start_item_positions = {}
            
            # Handle selection box
            elif self.selection_rect_active and self.selection_rect_start:
                current_pos = self.mapToScene(event.pos())
                rect_left = min(self.selection_rect_start.x(), current_pos.x())
                rect_right = max(self.selection_rect_start.x(), current_pos.x())
                rect_top = min(self.selection_rect_start.y(), current_pos.y())
                rect_bottom = max(self.selection_rect_start.y(), current_pos.y())
                
                from PyQt5.QtCore import QRectF
                selection_area = QRectF(rect_left, rect_top, rect_right - rect_left, rect_bottom - rect_top)
                
                for item in self.scene().items(selection_area):
                    if isinstance(item, DraggableAsset):
                        item.setSelected(True)
                
                if self.selection_rect_item:
                    self.scene().removeItem(self.selection_rect_item)
                    self.selection_rect_item = None
            
            self.selection_rect_active = False
            self.selection_rect_start = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        
    def wheelEvent(self, event):
        zoomInFactor = 1.15
        zoomOutFactor = 1 / zoomInFactor
        current_transform = self.transform()
        current_scale = current_transform.m11()
        
        if event.angleDelta().y() > 0:
            zoomFactor = zoomInFactor
        else:
            zoomFactor = zoomOutFactor
        
        new_scale = current_scale * zoomFactor
        if new_scale > 2.5:
            zoomFactor = 2.5 / current_scale
        
        self.scale(zoomFactor, zoomFactor)
        
        if self.workspace:
            new_transform = self.transform()
            new_scale = new_transform.m11()
            new_zoom_percentage = round(new_scale * 100)
            self.workspace.set_zoom_level(new_zoom_percentage)
    
    def set_zoom_level(self, percentage):
        scale_factor = percentage / 100.0
        current_transform = self.transform()
        current_scale = current_transform.m11()
        if current_scale > 0:
            zoom_factor = scale_factor / current_scale
            self.scale(zoom_factor, zoom_factor)
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            selected_items = self.scene().selectedItems()
            # Filter for DraggableAsset items only
            elements_to_delete = [item for item in selected_items if isinstance(item, DraggableAsset)]
            
            if elements_to_delete:
                # Show confirmation dialog
                item_names = ", ".join([element.name for element in elements_to_delete[:3]])
                if len(elements_to_delete) > 3:
                    item_names += f"... (+{len(elements_to_delete) - 3} more)"
                
                reply = QMessageBox.question(self.workspace, 
                    "Delete Images",
                    f"Are you sure you wish to delete {len(elements_to_delete)} item(s)? ({item_names})",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No)
                
                if reply == QMessageBox.Yes:
                    # Delete all selected elements
                    for element in elements_to_delete:
                        element.hide_selection_overlay()
                        if hasattr(element, 'tree_item') and element.tree_item:
                            parent = element.tree_item.parent() or self.workspace.tree.invisibleRootItem()
                            parent.removeChild(element.tree_item)
                        self.scene().removeItem(element)
                    self.workspace.update_element_count()
            event.accept()
        else:
            super().keyPressEvent(event)


class CustomSizeDialog(QDialog):
    def __init__(self, current_w, current_h, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Canvas Size")
        self.setFixedSize(250, 120)
        
        layout = QVBoxLayout(self)
        
        form_layout = QHBoxLayout()
        self.spin_w = QSpinBox()
        self.spin_w.setRange(128, 16384)
        self.spin_w.setValue(current_w)
        
        self.spin_h = QSpinBox()
        self.spin_h.setRange(128, 16384)
        self.spin_h.setValue(current_h)
        
        form_layout.addWidget(QLabel("W:"))
        form_layout.addWidget(self.spin_w)
        form_layout.addWidget(QLabel("H:"))
        form_layout.addWidget(self.spin_h)
        layout.addLayout(form_layout)
        
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("Apply")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def get_dimensions(self):
        return self.spin_w.value(), self.spin_h.value()

class DayZImageset(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DayZ Imageset Editor - Make something cool for DayZ! 🎨🖌")
        self.setWindowIcon(QIcon(resource_path("resources/app_icon.ico")))
        self.setGeometry(100, 100, 1600, 900)
        
        self.raw_images = {}
        self.canvas_width = 4096
        self.canvas_height = 4096
        self.current_zoom_percentage = 100
        self.canvas_background_rect = None
        
        self.snap_to_grid_enabled = False
        self.grid_size = 32
        self.show_gridlines = False
        self.snap_to_elements_enabled = False
        self.snap_distance = 15
        self.show_outlines = False
        self.outline_color = QColor("#ffffff")
        self.grid_lines = []
        
        self.max_elements = 1000
        self.current_element_count = 0
        self._is_updating_spins = False # Flag to prevent recursive updates when syncing spinboxes during drag

        # --- Undo / Redo Setup ---
        self.undo_stack = QUndoStack(self)
        
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo_stack.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.undo_stack.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.undo_stack.redo)
        # --- Help me Obi Wan Kenobi, you're my only hope! 🙏 ---
        QShortcut(QKeySequence("Ctrl+H"), self, self.show_help_dialog)
        QShortcut(QKeySequence("F1"), self, self.show_help_dialog)

        self._build_ui()
        self._apply_dark_theme()

    def _build_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Top Toolbar (Split into two rows for cleaner UI) ---
        top_toolbar = QWidget()
        top_toolbar_layout = QVBoxLayout(top_toolbar)
        top_toolbar_layout.setContentsMargins(5, 5, 5, 5)
        
        # Row 1: Workspace layout & Canvas Setup
        row1_layout = QHBoxLayout()
        
        self.btn_sidebar_pos = QPushButton("Sidebar →")
        self.btn_sidebar_pos.setToolTip("Slide to the right!")
        self.btn_sidebar_pos.setMaximumWidth(100)
        self.btn_sidebar_pos.clicked.connect(self.toggle_sidebar_position)
        row1_layout.addWidget(self.btn_sidebar_pos)

        # --- Undo / Redo Buttons ---
        row1_layout.addWidget(QLabel(" | "))
        btn_undo = QPushButton()
        btn_undo.setIcon(QIcon(resource_path("resources/reply-fill.svg")))
        btn_undo.setToolTip("Undo (Ctrl+Z)")
        btn_undo.clicked.connect(self.undo_stack.undo)
        row1_layout.addWidget(btn_undo)

        btn_redo = QPushButton()
        btn_redo.setIcon(QIcon(resource_path("resources/share-forward-fill.svg")))
        btn_redo.setToolTip("Redo (Ctrl+Y)")
        btn_redo.clicked.connect(self.undo_stack.redo)
        row1_layout.addWidget(btn_redo)
        
        row1_layout.addWidget(QLabel(" | Canvas Size:"))
        self.combo_size = QComboBox()
        self.combo_size.addItems(["512", "1024", "2048", "4096", "8192"])
        self.combo_size.setCurrentText("4096")
        self.combo_size.currentTextChanged.connect(self.on_preset_size_changed)
        row1_layout.addWidget(self.combo_size)
        
        btn_custom_size = QPushButton("Custom Size")
        btn_custom_size.clicked.connect(self.open_custom_size_dialog)
        row1_layout.addWidget(btn_custom_size)
        
        btn_bg_color = QPushButton("Canvas Color")
        btn_bg_color.clicked.connect(self.change_bg_color)
        row1_layout.addWidget(btn_bg_color)
        
        # --- Icon-based Alignment Buttons ---
        row1_layout.addWidget(QLabel(" | Align:"))
        
        btn_align_left = QPushButton()
        btn_align_left.setIcon(QIcon(resource_path("resources/align-item-left-fill.svg")))
        btn_align_left.setToolTip("Align Left")
        btn_align_left.clicked.connect(lambda: self.align_selected("left"))
        row1_layout.addWidget(btn_align_left)
        
        btn_align_center = QPushButton()
        btn_align_center.setIcon(QIcon(resource_path("resources/align-item-horizontal-center-fill.svg")))
        btn_align_center.setToolTip("Align Center")
        btn_align_center.clicked.connect(lambda: self.align_selected("center"))
        row1_layout.addWidget(btn_align_center)
        
        btn_align_right = QPushButton()
        btn_align_right.setIcon(QIcon(resource_path("resources/align-item-right-fill.svg")))
        btn_align_right.setToolTip("Align Right")
        btn_align_right.clicked.connect(lambda: self.align_selected("right"))
        row1_layout.addWidget(btn_align_right)
        
        btn_align_bottom = QPushButton()
        btn_align_bottom.setIcon(QIcon(resource_path("resources/align-item-bottom-fill.svg")))
        btn_align_bottom.setToolTip("Align Bottom")
        btn_align_bottom.clicked.connect(lambda: self.align_selected("bottom"))
        row1_layout.addWidget(btn_align_bottom)

        btn_align_top = QPushButton()
        btn_align_top.setIcon(QIcon(resource_path("resources/align-item-top-fill.svg")))
        btn_align_top.setToolTip("Align Top")
        btn_align_top.clicked.connect(lambda: self.align_selected("top"))
        row1_layout.addWidget(btn_align_top)
        
        self.check_align_to_canvas = QCheckBox("Align to Canvas")
        self.check_align_to_canvas.setChecked(False)
        row1_layout.addWidget(self.check_align_to_canvas)
        
        row1_layout.addStretch()

        # Help button
        btn_help = QPushButton("Help")
        btn_help.setMaximumWidth(80)
        btn_help.clicked.connect(self.show_help_dialog)
        row1_layout.addWidget(btn_help)

        # About button
        btn_about = QPushButton("About")
        btn_about.setMaximumWidth(80)
        btn_about.clicked.connect(self.show_about_dialog)
        row1_layout.addWidget(btn_about)
        
        # Row 2: Overlays and Snapping
        row2_layout = QHBoxLayout()
        self.check_snap_grid = QCheckBox("Snap to Grid")
        self.check_snap_grid.toggled.connect(self.on_snap_grid_toggled)
        row2_layout.addWidget(self.check_snap_grid)
        
        row2_layout.addWidget(QLabel("Grid Size:"))
        self.spin_grid_size = QSpinBox()
        self.spin_grid_size.setMinimum(4)
        self.spin_grid_size.setMaximum(256)
        self.spin_grid_size.setValue(32)
        self.spin_grid_size.setSingleStep(4)
        self.spin_grid_size.valueChanged.connect(self.on_grid_size_changed)
        row2_layout.addWidget(self.spin_grid_size)
        
        row2_layout.addWidget(QLabel(" |  "))
        self.check_show_gridlines = QCheckBox("Show Gridlines")
        self.check_show_gridlines.toggled.connect(self.on_show_gridlines_toggled)
        row2_layout.addWidget(self.check_show_gridlines)
        
        self.check_snap_elements = QCheckBox("Snap to Elements")
        self.check_snap_elements.setChecked(False)
        self.check_snap_elements.toggled.connect(self.on_snap_elements_toggled)
        row2_layout.addWidget(self.check_snap_elements)
        
        self.check_show_outlines = QCheckBox("Show Outlines")
        self.check_show_outlines.toggled.connect(self.on_show_outlines_toggled)
        row2_layout.addWidget(self.check_show_outlines)
        
        btn_outline_color = QPushButton("Outline Color")
        btn_outline_color.clicked.connect(self.set_outline_color)
        row2_layout.addWidget(btn_outline_color)

        # Move view controls down here
        row2_layout.addWidget(QLabel(" |  "))
        zoom_ctrl_layout = QHBoxLayout()
        btn_zoom_out = QPushButton("-")
        btn_zoom_out.setMaximumWidth(40)
        btn_zoom_out.clicked.connect(self.zoom_out)
        zoom_ctrl_layout.addWidget(btn_zoom_out)
        
        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setMaximumWidth(40)
        btn_zoom_in.clicked.connect(self.zoom_in)
        zoom_ctrl_layout.addWidget(btn_zoom_in)
        
        self.combo_zoom = QComboBox()
        zoom_presets = ["5%", "10%", "20%", "25%", "30%", "40%", "50%", "60%", "75%", "80%", "90%", "100%", "110%", "120%", "130%", "140%", "150%"]
        self.combo_zoom.addItems(zoom_presets)
        self.combo_zoom.setCurrentText("100%")
        self.combo_zoom.currentTextChanged.connect(self.on_zoom_preset_changed)
        zoom_ctrl_layout.addWidget(QLabel("Zoom:"))
        zoom_ctrl_layout.addWidget(self.combo_zoom)
        
        self.label_zoom_value = QLabel("100%")
        self.label_zoom_value.setMinimumWidth(40)
        zoom_ctrl_layout.addWidget(self.label_zoom_value)
        row2_layout.addLayout(zoom_ctrl_layout)

        row2_layout.addStretch()

        top_toolbar_layout.addLayout(row1_layout)
        top_toolbar_layout.addLayout(row2_layout)
        main_layout.addWidget(top_toolbar)

        # --- Main Content with Splitter ---
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # --- Sidebar ---
        self.sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.sidebar)
        
        btn_layout = QHBoxLayout()
        btn_import_image = QPushButton("Import Image(s)")
        btn_import_image.clicked.connect(self.import_images)
        btn_import_folder = QPushButton("Import Folder(Subfolders as Groups)")
        btn_import_folder.clicked.connect(self.import_folders)
        btn_layout.addWidget(btn_import_image)
        btn_layout.addWidget(btn_import_folder)
        sidebar_layout.addLayout(btn_layout)
        btn_unpack = QPushButton("Unpack .imageset to Workspace")
        btn_unpack.setToolTip("Import an existing .imageset and its associated .edds file, unpacking all elements into the workspace for editing. Great for modifying existing assets or using them as a base for new creations!")
        btn_unpack.setStyleSheet("background-color: #2E8B57; color: white;") # Make it distinct
        btn_unpack.clicked.connect(self.unpack_imageset_action)
        sidebar_layout.addWidget(btn_unpack)
        
        proj_btn_layout = QHBoxLayout()
        btn_save_proj = QPushButton("Save Project")
        btn_save_proj.clicked.connect(self.save_project)
        btn_load_proj = QPushButton("Load Project")
        btn_load_proj.clicked.connect(lambda: self.load_project())
        proj_btn_layout.addWidget(btn_save_proj)
        proj_btn_layout.addWidget(btn_load_proj)
        sidebar_layout.addLayout(proj_btn_layout)
                
        group_ctrl_layout = QHBoxLayout()
        btn_add_group = QPushButton("+ Add Group")
        btn_add_group.clicked.connect(self.add_manual_group)
        btn_rem_group = QPushButton("- Remove Group")
        btn_rem_group.clicked.connect(self.remove_tree_item)
        group_ctrl_layout.addWidget(btn_add_group)
        group_ctrl_layout.addWidget(btn_rem_group)
        sidebar_layout.addLayout(group_ctrl_layout)

        sidebar_layout.addWidget(QLabel("Layer Hierarchy:"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Structure"])
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.model().rowsMoved.connect(self.on_tree_items_moved)
        # Enable multi-select
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        sidebar_layout.addWidget(self.tree)
        
        # Assign to Group controls
        assign_layout = QHBoxLayout()
        assign_layout.addWidget(QLabel("Assign to Group:"))
        self.combo_assign_group = QComboBox()
        self.combo_assign_group.addItem("Select a group...")
        self.combo_assign_group.currentIndexChanged.connect(self.on_assign_group_changed)
        assign_layout.addWidget(self.combo_assign_group)
        sidebar_layout.addLayout(assign_layout)

        # --- Properties Panel ---
        self.prop_layout = QVBoxLayout()
        self.prop_layout.addWidget(QLabel("Item Properties:"))
        
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Name:"))
        self.edit_item_name = QLineEdit()
        self.edit_item_name.setEnabled(False)
        self.edit_item_name.editingFinished.connect(self.on_name_edit_finished)
        name_layout.addWidget(self.edit_item_name)
        self.prop_layout.addLayout(name_layout)
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Path:"))
        self.edit_item_path = QLineEdit()
        self.edit_item_path.setReadOnly(True)
        self.edit_item_path.setEnabled(False)
        path_layout.addWidget(self.edit_item_path)
        self.prop_layout.addLayout(path_layout)

        # --- DIMENSIONS & COORDINATES ---
        dim_layout = QHBoxLayout()
        
        self.label_item_width = QLabel("W: -")
        self.label_item_height = QLabel("H: -")
        
        self.spin_item_x = QSpinBox()
        self.spin_item_x.setRange(0, 8192) # Max canvas size, will be clamped in DraggableAsset
        self.spin_item_x.setEnabled(False)
        self.spin_item_x.setMaximumWidth(60)
        self.spin_item_x.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.spin_item_x.valueChanged.connect(self.on_xy_spin_changed)
        
        self.spin_item_y = QSpinBox()
        self.spin_item_y.setRange(0, 8192) # Same as above, type what you want but it will be clamped to canvas size in DraggableAsset
        self.spin_item_y.setEnabled(False)
        self.spin_item_y.setMaximumWidth(60)
        self.spin_item_y.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.spin_item_y.valueChanged.connect(self.on_xy_spin_changed)
        
        dim_layout.addWidget(self.label_item_width)
        dim_layout.addWidget(self.label_item_height)
        dim_layout.addStretch() # Pushes the coordinate controls to the right side of the properties panel, while keeping dimensions on the left for better visual grouping. Looks cool 😎
        dim_layout.addWidget(QLabel(" X:"))
        dim_layout.addWidget(self.spin_item_x)
        dim_layout.addWidget(QLabel(" Y:"))
        dim_layout.addWidget(self.spin_item_y)
        
        
        self.prop_layout.addLayout(dim_layout)

        sidebar_layout.addLayout(self.prop_layout)
        
        element_count_layout = QHBoxLayout()
        element_count_layout.addWidget(QLabel("Element Count:"))
        # UI shows only the current element count; the max is handled in script to prevent from going over the limit, but showing it here for user awareness
        self.label_element_count = QLabel(f"0/{self.max_elements}")
        self.label_element_count.setMinimumWidth(80)
        element_count_layout.addWidget(self.label_element_count)
        sidebar_layout.addLayout(element_count_layout)
        
        btn_export = QPushButton("EXPORT IMAGESET + EDDS")
        btn_export.setStyleSheet("background-color: #00aa00; color: white; font-weight: bold; padding: 10px;")
        btn_export.clicked.connect(self.export_imageset_and_edds)
        sidebar_layout.addWidget(btn_export)

        
        # Connect tree edits
        self.tree.itemChanged.connect(self.on_tree_item_edited)

        # Graphics View
        self.scene = QGraphicsScene()
        self.scene.selectionChanged.connect(self.on_scene_selection_changed)
        
        self.view = ZoomableView(self.scene, self)
        
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 1250])
        
        content_layout.addWidget(self.splitter)
        
        # Stretch factor 1 added here so main_layout pushes all extra space to the splitter (not topbar)
        main_layout.addWidget(content_widget, 1)
        
        self.sidebar_on_left = True
        self.update_canvas_size()

    # --- Sidebar Management ---
    def toggle_sidebar_position(self):
        """Swap sidebar from left to right or vice versa"""
        if self.sidebar_on_left:
            # Move sidebar to the right by inserting it at index 1
            self.splitter.insertWidget(1, self.sidebar)
            self.btn_sidebar_pos.setText("Sidebar ←")
            self.btn_sidebar_pos.setToolTip("Slide to the left!")
            self.sidebar_on_left = False
        else:
            # Move sidebar back to left by inserting at index 0
            self.splitter.insertWidget(0, self.sidebar)
            self.btn_sidebar_pos.setText("Sidebar →")
            self.btn_sidebar_pos.setToolTip("Slide to the right!")
            self.sidebar_on_left = True
    
    def align_selected(self, align_type):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        
        elements = [item for item in selected_items if isinstance(item, DraggableAsset)]
        if not elements: return
        
        align_to_canvas = self.check_align_to_canvas.isChecked()
        if align_to_canvas:
            self._align_to_canvas(elements, align_type)
        else:
            self._align_to_selection(elements, align_type)
    
    def _align_to_canvas(self, elements, align_type):
        if align_type == "top":
            for element in elements:
                element.setPos(element.pos().x(), 0)       
        elif align_type == "left":
            for element in elements:
                element.setPos(0, element.pos().y())
        elif align_type == "right":
            for element in elements:
                rect_width = element.boundingRect().width()
                element.setPos(self.canvas_width - rect_width, element.pos().y())
        elif align_type == "center":
            for element in elements:
                rect_width = element.boundingRect().width()
                center_x = (self.canvas_width - rect_width) / 2
                element.setPos(center_x, element.pos().y())
        elif align_type == "bottom":
            for element in elements:
                rect_height = element.boundingRect().height()
                element.setPos(element.pos().x(), self.canvas_height - rect_height)
    
    def _align_to_selection(self, elements, align_type):
        if not elements: return
        
        avg_x = sum(element.pos().x() for element in elements) / len(elements)
        avg_y = sum(element.pos().y() for element in elements) / len(elements)
        
        if align_type == "top":
            target_y = min(element.pos().y() for element in elements)
            for element in elements:
                element.setPos(element.pos().x(), target_y)
        elif align_type == "left":
            target_x = min(element.pos().x() for element in elements)
            for element in elements:
                element.setPos(target_x, element.pos().y())
        elif align_type == "right":
            target_x = max(element.pos().x() + element.boundingRect().width() for element in elements) - elements[0].boundingRect().width()
            for element in elements:
                element.setPos(target_x, element.pos().y())
        elif align_type == "center":
            target_x = avg_x - elements[0].boundingRect().width() / 2
            for element in elements:
                element.setPos(target_x, element.pos().y())
        elif align_type == "bottom":
            target_y = max(element.pos().y() + element.boundingRect().height() for element in elements) - elements[0].boundingRect().height()
            for element in elements:
                element.setPos(element.pos().x(), target_y)
        
    def on_assign_group_changed(self, index):
        """Handle group assignment via dropdown"""
        if index <= 0:
            return
        
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return
        
        group_name = self.combo_assign_group.currentText()
        
        # Find target group
        target_group = None
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group.text(0) == group_name:
                target_group = group
                break
        
        if not target_group:
            return
        
        # Assign all selected items to the group
        for item in selected_items:
            # Don't move groups themselves, only elements
            if hasattr(item, 'element_item') and item.element_item:
                self.assign_item_to_group(item, group_name)
        
        # Reset dropdown
        self.combo_assign_group.blockSignals(True)
        self.combo_assign_group.setCurrentIndex(0)
        self.combo_assign_group.blockSignals(False)
    
    def refresh_group_dropdown(self):
        """Refresh the groups list in the assign dropdown"""
        self.combo_assign_group.blockSignals(True)
        current_text = self.combo_assign_group.currentText()
        self.combo_assign_group.clear()
        self.combo_assign_group.addItem("Select a group...")
        
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if not hasattr(group, 'element_item') or not group.element_item:
                self.combo_assign_group.addItem(group.text(0))
        
        self.combo_assign_group.blockSignals(False)
    
    def on_tree_item_edited(self, item, column):
        if column == 0:
            new_name = item.text(0)
            if hasattr(item, 'element_item') and item.element_item:
                item.element_item.name = new_name
            # If the currently selected item was edited in the tree, update the text box
            if self.tree.selectedItems() and self.tree.selectedItems()[0] == item:
                self.edit_item_name.blockSignals(True)
                self.edit_item_name.setText(new_name)
                self.edit_item_name.blockSignals(False)

    def on_name_edit_finished(self):
        selected_items = self.tree.selectedItems()
        if len(selected_items) == 1:
            item = selected_items[0]
            new_name = self.edit_item_name.text()
            if item.text(0) != new_name:
                item.setText(0, new_name) # Triggers on_tree_item_edited automatically

    def on_tree_items_moved(self):
        self._update_all_element_groups()
        self.refresh_group_dropdown()
    
    def _update_all_element_groups(self):
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            self._update_group_elements(group, group)
    
    def _update_group_elements(self, item, group_parent):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.childCount() > 0:
                self._update_group_elements(child, child)
            else:
                if hasattr(child, 'element_item') and child.element_item:
                    child.element_item.group_item = group_parent
    
    def assign_item_to_group(self, item, group_name):
        target_group = None
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group.text(0) == group_name:
                target_group = group
                break
        
        if not target_group: return
        
        current_parent = item.parent() or self.tree.invisibleRootItem()
        current_parent.removeChild(item)
        target_group.addChild(item)
        
        if hasattr(item, 'element_item') and item.element_item:
            item.element_item.group_item = target_group
        self.tree.expandItem(target_group)

    def save_project(self):
        save_file, _ = QFileDialog.getSaveFileName(self, "Save Project State", "", "Imageset Project (*.json)")
        if not save_file: 
            return False
            
        project_data = {
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "settings": {
                "snap_to_grid": self.snap_to_grid_enabled,
                "grid_size": self.grid_size,
                "show_gridlines": self.show_gridlines,
                "snap_to_elements": self.snap_to_elements_enabled,
                "snap_distance": self.snap_distance,
                "show_outlines": self.show_outlines,
                "outline_color": self.outline_color.name(),
                "canvas_color": (self.canvas_background_rect.brush().color().name() if self.canvas_background_rect is not None else "#2a2a2a"),
                "zoom": self.current_zoom_percentage
            },
            "tree": [],
            "elements": {}
        }
        
        # Serialize tree structure
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            project_data["tree"].append(self._serialize_tree_item(group, project_data["elements"]))
        
        import json
        try:
            with open(save_file, "w", encoding="utf-8") as f:
                json.dump(project_data, f, indent=4)
            print(f"Project state successfully saved to {save_file}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to write project file:\n{e}")
            return False

    def _serialize_tree_item(self, item, elements_dict):
        """Recursively serialize tree item and its children"""
        node_data = {"name": item.text(0), "children": []}
        
        # If this is an element (not a group), store its data
        if hasattr(item, 'element_item') and item.element_item:
            element = item.element_item
            if hasattr(item, 'filepath'):
                element_key = str(item.filepath)
                elements_dict[element_key] = {
                    "filepath": str(item.filepath),
                    "name": element.name,
                    "x": element.pos().x(),
                    "y": element.pos().y()
                }
                node_data["element_path"] = element_key
        
        # Serialize children
        for i in range(item.childCount()):
            child = item.child(i)
            node_data["children"].append(self._serialize_tree_item(child, elements_dict))
        
        return node_data

    def load_project(self, filepath=None):
        if filepath is None:
            load_file, _ = QFileDialog.getOpenFileName(self, "Load Project State", "", "Imageset Project (*.json)")
        else:
            load_file = filepath

        if not load_file:
            return
            
        import json
        try:
            with open(load_file, "r", encoding="utf-8") as f:
                project_data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to read project file:\n{e}")
            return
            
        # Clear scene and tree
        self.tree.clear()
        self.raw_images.clear()
        self.scene.clear()
        self.grid_lines.clear()
        self.current_element_count = 0
        
        # Load canvas size
        self.canvas_width = project_data.get("canvas_width", 4096)
        self.canvas_height = project_data.get("canvas_height", 4096)

        self.combo_size.blockSignals(True)
        self.combo_size.setCurrentText(str(self.canvas_width) if self.canvas_width == self.canvas_height else f"{self.canvas_width}x{self.canvas_height}")
        self.combo_size.blockSignals(False)
        self.update_canvas_size()
        
        # Load workspace settings
        settings = project_data.get("settings", {})
        self.snap_to_grid_enabled = settings.get("snap_to_grid", False)
        self.check_snap_grid.blockSignals(True)
        self.check_snap_grid.setChecked(self.snap_to_grid_enabled)
        self.check_snap_grid.blockSignals(False)
        
        self.grid_size = settings.get("grid_size", 32)
        self.spin_grid_size.blockSignals(True)
        self.spin_grid_size.setValue(self.grid_size)
        self.spin_grid_size.blockSignals(False)
        
        self.show_gridlines = settings.get("show_gridlines", False)
        self.check_show_gridlines.blockSignals(True)
        self.check_show_gridlines.setChecked(self.show_gridlines)
        self.check_show_gridlines.blockSignals(False)
        
        self.snap_to_elements_enabled = settings.get("snap_to_elements", False)
        self.check_snap_elements.blockSignals(True)
        self.check_snap_elements.setChecked(self.snap_to_elements_enabled)
        self.check_snap_elements.blockSignals(False)
        
        self.snap_distance = settings.get("snap_distance", 15)
        
        self.show_outlines = settings.get("show_outlines", False)
        self.check_show_outlines.blockSignals(True)
        self.check_show_outlines.setChecked(self.show_outlines)
        self.check_show_outlines.blockSignals(False)
        
        outline_color_str = settings.get("outline_color", "#ffffff")
        self.outline_color = QColor(outline_color_str)
        
        zoom_level = settings.get("zoom", 100)
        self.set_zoom_level(zoom_level)
        
        # Restore canvas background color if provided
        canvas_color_str = settings.get("canvas_color", "#2a2a2a")
        if self.canvas_background_rect is not None:
            try:
                self.canvas_background_rect.setBrush(QColor(canvas_color_str))
            except Exception:
                pass
        
        # Redraw gridlines if enabled
        if self.show_gridlines:
            self.draw_gridlines()
        
        # Load elements and tree structure
        elements_data = project_data.get("elements", [])
        tree_structure = project_data.get("tree", [])
        
        # We use a list to pass by reference so the recursive function can update it
        missing_files = [0] 
        
        if tree_structure:
            # Format A: Native Editor JSON (Hierarchical Tree)
            elements_dict = elements_data if isinstance(elements_data, dict) else {}
            for tree_node in tree_structure:
                self._deserialize_tree_item(tree_node, None, elements_dict, missing_files)
                
        elif isinstance(elements_data, list):
            # Format B: Unpacker JSON (Flat List) - Dynamically construct the Tree
            group_items = {}
            for item_data in elements_data:
                group_name = item_data.get("group", "ROOT (Ungrouped)")
                
                if group_name not in group_items:
                    g_item = QTreeWidgetItem(self.tree, [group_name])
                    if group_name != "ROOT (Ungrouped)":
                        g_item.setFlags(g_item.flags() | Qt.ItemIsEditable)
                    group_items[group_name] = g_item
                    self.tree.expandItem(g_item)
                
                parent_item = group_items[group_name]
                
                filepath_str = item_data.get("filepath", "")
                if filepath_str:
                    filepath = Path(filepath_str)
                    if filepath.exists():
                        name = item_data.get("name", "Unknown")
                        self.raw_images[str(filepath)] = Image.open(filepath).convert("RGBA")
                        self.raw_images[name] = self.raw_images[str(filepath)]
                        
                        tree_item = QTreeWidgetItem(parent_item, [name])
                        tree_item.setFlags(tree_item.flags() | Qt.ItemIsEditable)
                        tree_item.filepath = filepath
                        
                        pixmap = QPixmap(str(filepath))
                        element_item = DraggableAsset(name, pixmap, parent_item, self.canvas_width, self.canvas_height)
                        element_item.setPos(item_data.get("x", 0.0), item_data.get("y", 0.0))
                        element_item.tree_item = tree_item
                        tree_item.element_item = element_item
                        
                        if self.show_outlines:
                            element_item.set_outline(self.outline_color)
                        
                        self.scene.addItem(element_item)
                        self.current_element_count += 1
                    else:
                        missing_files[0] += 1
        
        self.update_element_count()
        self.refresh_group_dropdown()
        
        if missing_files[0] > 0:
            QMessageBox.warning(self, "Missing Source Files", f"Project loaded, but {missing_files[0]} image file(s) were skipped.")

    def _deserialize_tree_item(self, node_data, parent_item, elements_dict, missing_files):
        """Recursively deserialize tree from structure"""
        if parent_item is None:
            tree_item = QTreeWidgetItem(self.tree, [node_data["name"]])
        else:
            tree_item = QTreeWidgetItem(parent_item, [node_data["name"]])
        
        tree_item.setFlags(tree_item.flags() | Qt.ItemIsEditable)
        
        # If this is an element, load and place it
        if "element_path" in node_data:
            element_path = node_data["element_path"]
            if element_path in elements_dict:
                element_data = elements_dict[element_path]
                filepath = Path(element_data["filepath"])
                
                if filepath.exists():
                    name = element_data["name"]
                    self.raw_images[str(filepath)] = Image.open(filepath).convert("RGBA")
                    self.raw_images[name] = self.raw_images[str(filepath)]
                    
                    tree_item.filepath = filepath
                    pixmap = QPixmap(str(filepath))
                    element_item = DraggableAsset(name, pixmap, parent_item or tree_item, self.canvas_width, self.canvas_height)
                    element_item.setPos(element_data["x"], element_data["y"])
                    element_item.tree_item = tree_item
                    tree_item.element_item = element_item
                    
                    if self.show_outlines:
                        element_item.set_outline(self.outline_color)
                    
                    self.scene.addItem(element_item)
                    self.current_element_count += 1
                else:
                    missing_files[0] += 1
        
        # Recursively load children
        for child_node in node_data.get("children", []):
            self._deserialize_tree_item(child_node, tree_item, elements_dict, missing_files)
        
        self.tree.expandItem(tree_item)

    def _apply_dark_theme(self):
        # Resolve resource paths for stylesheet images
        arrow_down = resource_path("resources/arrow-down-s-fill.svg").replace("\\", "/")
        arrow_up = resource_path("resources/arrow-up-s-fill.svg").replace("\\", "/")
        checkbox_icon = resource_path("resources/close-fill.svg").replace("\\", "/")
        
        stylesheet = f"""
            QMainWindow, QWidget {{ background-color: #1F2329; color: #8AA2AE; }}
            QPushButton {{ background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; border-radius: 3px; padding: 4px; }}
            QPushButton:hover {{ background-color: #24282E; }}
            QPushButton:pressed {{ background-color: #1F2329; }}
            QComboBox {{ background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; border-radius: 3px; padding: 4px; }}
            QComboBox::drop-down {{ image: url("{arrow_down}"); border: none; background-color: #24282E; }}
            QComboBox QAbstractItemView {{ background-color: #2C3136; color: #8AA2AE; selection-background-color: #24282E; }}
            QSpinBox {{ background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; border-radius: 3px; padding: 4px; }}
            QSpinBox::up-button {{ image: url("{arrow_up}"); background-color: #24282E; border: none; width: 16px; }} 
            QSpinBox::down-button {{ image: url("{arrow_down}"); background-color: #24282E; border: none; width: 16px; }}
            QLabel {{ color: #8AA2AE; }}
            QCheckBox {{ color: #8AA2AE; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #24282E; border-radius: 2px; }}
            QCheckBox::indicator:unchecked {{ background-color: #2C3136; }}
            QCheckBox::indicator:checked {{ image: url("{checkbox_icon}"); background-color: #24282E; }}
            QTreeWidget {{ background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; gridline-color: #24282E; }}
            QTreeWidget::item:selected {{ background-color: #24282E; }}
            QScrollBar:vertical, QScrollBar:horizontal {{ background-color: #1F2329; border: 1px solid #24282E; }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background-color: #2C3136; border: 1px solid #24282E; border-radius: 2px; }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background-color: #24282E; }}
        """
        self.setStyleSheet(stylesheet)

    def on_preset_size_changed(self, text):
        try:
            val = int(text)
            self.canvas_width = val
            self.canvas_height = val
            self.update_canvas_size()
        except ValueError:
            pass
            
    def open_custom_size_dialog(self):
        dialog = CustomSizeDialog(self.canvas_width, self.canvas_height, self)
        if dialog.exec_() == QDialog.Accepted:
            w, h = dialog.get_dimensions()
            self.canvas_width = w
            self.canvas_height = h
            
            # Update Combo Box with custom text silently
            self.combo_size.blockSignals(True)
            custom_label = f"{w}x{h}"
            # Check if this size is already in the list, if not add it
            if self.combo_size.findText(custom_label) == -1:
                self.combo_size.addItem(custom_label)
            self.combo_size.setCurrentText(custom_label)
            self.combo_size.blockSignals(False)
            
            self.update_canvas_size()

    def update_canvas_size(self):
        self.scene.setSceneRect(0, 0, self.canvas_width, self.canvas_height)
        
        # Safely remove the old background without looping through the entire scene
        if self.canvas_background_rect is not None and self.canvas_background_rect.scene() == self.scene:
            self.scene.removeItem(self.canvas_background_rect)
        
        self.canvas_background_rect = self.scene.addRect(0, 0, self.canvas_width, self.canvas_height)
        self.canvas_background_rect.setBrush(QColor("#2a2a2a"))
        self.canvas_background_rect.setPen(QColor("#ff5500"))
        self.canvas_background_rect.is_canvas_bg = True
        self.canvas_background_rect.setZValue(-1)
        
        # Inform existing draggable items of the new bounds
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                item.canvas_width = self.canvas_width
                item.canvas_height = self.canvas_height
        
        # Redraw gridlines (this will safely clear the old ones via clear_gridlines)
        if self.show_gridlines:
            self.draw_gridlines()

    def change_bg_color(self):
        color = QColorDialog.getColor()
        if color.isValid() and self.canvas_background_rect:
            self.canvas_background_rect.setBrush(color)
    
    def on_snap_grid_toggled(self, checked):
        self.snap_to_grid_enabled = checked
    
    def on_snap_elements_toggled(self, checked):
        self.snap_to_elements_enabled = checked
    
    def snap_to_grid(self, pos):
        """Snap a position to the grid"""
        if self.grid_size <= 0:
            return pos
        
        x = round(pos.x() / self.grid_size) * self.grid_size
        y = round(pos.y() / self.grid_size) * self.grid_size
        return QPointF(x, y)
    
    def snap_to_elements(self, current_item, pos):
        """Snap current item to nearby elements within snap distance"""
        snap_distance = self.snap_distance
        snapped_pos = QPointF(pos)
        
        # Get current item's bounding rect in scene coordinates
        current_rect = current_item.boundingRect()
        current_rect.moveTo(pos)
        
        # Check all other items for snapping opportunities
        min_dist_x = snap_distance
        min_dist_y = snap_distance
        snap_x = None
        snap_y = None
        
        for item in self.scene.items():
            if not isinstance(item, DraggableAsset) or item == current_item:
                continue
            
            item_rect = item.boundingRect()
            item_rect.moveTo(item.pos())
            
            # Check horizontal snapping (left, right edges)
            # Snap to item's right edge
            dist = abs(current_rect.left() - item_rect.right())
            if dist < min_dist_x:
                min_dist_x = dist
                snap_x = item_rect.right()
            
            # Snap to item's left edge
            dist = abs(current_rect.right() - item_rect.left())
            if dist < min_dist_x:
                min_dist_x = dist
                snap_x = item_rect.left() - current_rect.width()
            
            # Check vertical snapping (top, bottom edges)
            # Snap to item's bottom edge
            dist = abs(current_rect.top() - item_rect.bottom())
            if dist < min_dist_y:
                min_dist_y = dist
                snap_y = item_rect.bottom()
            
            # Snap to item's top edge
            dist = abs(current_rect.bottom() - item_rect.top())
            if dist < min_dist_y:
                min_dist_y = dist
                snap_y = item_rect.top() - current_rect.height()
        
        # Apply snapping if within snap distance
        if snap_x is not None:
            snapped_pos.setX(snap_x)
        if snap_y is not None:
            snapped_pos.setY(snap_y)
        
        return snapped_pos
    
    def on_grid_size_changed(self, value):
        self.grid_size = value
        if self.show_gridlines:
            self.draw_gridlines()
    
    def on_show_gridlines_toggled(self, checked):
        self.show_gridlines = checked
        if checked:
            self.draw_gridlines()
        else:
            self.clear_gridlines()
    
    def draw_gridlines(self):
        self.clear_gridlines()
        grid_color = QColor(100, 100, 100, 100)
        
        for x in range(0, self.canvas_width + 1, self.grid_size):
            line = self.scene.addLine(x, 0, x, self.canvas_height)
            line.setPen(QColor(grid_color))
            line.is_grid_line = True
            line.setZValue(-0.5)
            self.grid_lines.append(line)
        
        for y in range(0, self.canvas_height + 1, self.grid_size):
            line = self.scene.addLine(0, y, self.canvas_width, y)
            line.setPen(QColor(grid_color))
            line.is_grid_line = True
            line.setZValue(-0.5)
            self.grid_lines.append(line)
    
    def clear_gridlines(self):
        for line in self.grid_lines:
            # Check if it actually belongs to the scene before removing it
            if line.scene() == self.scene:
                self.scene.removeItem(line)
        self.grid_lines.clear()
    
    def on_show_outlines_toggled(self, checked):
        self.show_outlines = checked
        self.update_all_outlines()
    
    def update_all_outlines(self):
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                if self.show_outlines:
                    item.set_outline(self.outline_color)
                else:
                    item.clear_outline()
    
    def set_outline_color(self):
        color = QColorDialog.getColor(self.outline_color)
        if color.isValid():
            self.outline_color = color
            self.update_all_outlines()
    
    def zoom_in(self):
        new_zoom = min(150, self.current_zoom_percentage + 5)
        self.set_zoom_level(new_zoom)
    
    def zoom_out(self):
        new_zoom = max(0, self.current_zoom_percentage - 5)
        self.set_zoom_level(new_zoom)
    
    def set_zoom_level(self, percentage):
        self.current_zoom_percentage = percentage
        self.view.set_zoom_level(percentage)
        self.label_zoom_value.setText(f"{percentage}%")
        self.combo_zoom.blockSignals(True)
        preset_text = f"{percentage}%"
        index = self.combo_zoom.findText(preset_text)
        if index >= 0:
            self.combo_zoom.setCurrentIndex(index)
        else:
            self.combo_zoom.setCurrentIndex(-1)
        self.combo_zoom.blockSignals(False)
    
    def on_zoom_preset_changed(self, text):
        if text: 
            percentage = int(text.rstrip('%'))
            self.set_zoom_level(percentage)

    def add_manual_group(self):
        group_item = QTreeWidgetItem(self.tree, ["NewGroup"])
        group_item.setFlags(group_item.flags() | Qt.ItemIsEditable)
        self.tree.expandItem(group_item)
        self.refresh_group_dropdown()

    def remove_tree_item(self):
        selected = self.tree.selectedItems()
        if selected:
            root = self.tree.invisibleRootItem()
            (selected[0].parent() or root).removeChild(selected[0])
            self.refresh_group_dropdown()

    def import_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Images to Import", "", "Image Files (*.png *.tga *.jpg *.bmp)")
        if not files: return
        
        files_to_place = []
        for file in files:
            path = Path(file)
            if path.is_file():
                child = self._load_file_to_tree(path, None)
                if child is not None:
                    files_to_place.append((path, child))
        
        self.update_canvas_size()
        self.auto_place_images(files_to_place)

    def import_folders(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Root Directory")
        if not directory: return
        
        root_path = Path(directory)
        valid_exts = {'.png', '.tga', '.jpg', '.bmp'}
        
        self.tree.clear()
        self.raw_images.clear()
        self.scene.clear()
        self.grid_lines.clear()
        self.current_element_count = 0
        
        root_group = QTreeWidgetItem(self.tree, ["ROOT (Ungrouped)"])
        files_to_place = []
        
        for file in os.listdir(root_path):
            full_path = root_path / file
            if os.path.isfile(full_path) and full_path.suffix.lower() in valid_exts:
                child = self._load_file_to_tree(full_path, root_group)
                if child is not None:
                    files_to_place.append((full_path, child))
        self.tree.expandItem(root_group)
                
        for item in os.listdir(root_path):
            dir_path = root_path / item
            if os.path.isdir(dir_path):
                group_item = QTreeWidgetItem(self.tree, [item.lower()])
                for file in os.listdir(dir_path):
                    full_path = dir_path / file
                    if os.path.isfile(full_path) and full_path.suffix.lower() in valid_exts:
                        child = self._load_file_to_tree(full_path, group_item)
                        if child is not None:
                            files_to_place.append((full_path, child))
                self.tree.expandItem(group_item)
        
        self.update_canvas_size()
        self.auto_place_images(files_to_place)

    def unpack_imageset_action(self):
        # 1. Select the .imageset file
        imageset_path, _ = QFileDialog.getOpenFileName(
            self, "Select Imageset to Unpack", "", "Imageset Files (*.imageset)"
        )
        if not imageset_path:
            return

        imageset_path = Path(imageset_path)
        project_name = imageset_path.stem

        # 2. Inform user and select output directory
        QMessageBox.information(self, "Output Directory", 
            f"Please select a destination folder.\n\nA new folder named '{project_name}' will be created inside it to hold your extracted assets.")
            
        output_parent_dir = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", str(imageset_path.parent)
        )
        
        if not output_parent_dir:
            return
            
        # Construct the exact path where the new folder will live
        output_dir = Path(output_parent_dir) / project_name

        # 3. Run the converter
        try:
            converter = ImagesetConverter()
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Converter Not Found", str(e))
            return
            
        # Force the wrapper to use our specific output directory
        result = converter.unpack_to_project(imageset_path, output_dir=str(output_dir))

        if not result.get('success'):
            QMessageBox.critical(self, "Unpack Failed", result.get('error', 'Unknown error'))
            return

        # 4. Prompt to replace open workspace (Only if canvas currently has items)
        if self.scene.items():
            reply = QMessageBox.question(
                self, 
                "Replace Workspace?", 
                "Unpack successful!\n\nDo you want to clear your current workspace and load the newly unpacked imageset?",
                QMessageBox.Yes | QMessageBox.No, 
                QMessageBox.Yes
            )
            if reply == QMessageBox.No:
                QMessageBox.information(self, "Success", f"Unpacked assets safely saved to:\n{output_dir}")
                return

        # 5. Load the new project to the canvas
        json_file = output_dir / f"{project_name}.json"
        if json_file.exists():
            self.load_project(str(json_file))
        else:
            QMessageBox.warning(self, "Warning", "Could not locate the generated JSON project file.")

    def _load_project_from_manifest(self, manifest, project_dir):
        """Load project structure from manifest JSON."""
        # Clear current project
        self.tree.clear()
        self.scene.clear()
        self.raw_images.clear()
        
        # Group images by group name
        groups_dict = {}
        for img in manifest['images']:
            group_name = img['group']
            if group_name not in groups_dict:
                groups_dict[group_name] = []
            groups_dict[group_name].append(img)
        
        # Create tree structure and load images
        for group_name in sorted(groups_dict.keys()):
            group_item = QTreeWidgetItem(self.tree, [group_name])
            
            for img in groups_dict[group_name]:
                # Load PNG image
                png_path = project_dir / img['file']
                if not png_path.exists():
                    print(f"Warning: Image file not found: {png_path}")
                    continue
                
                # Load image
                pil_image = Image.open(str(png_path))
                self.raw_images[img['name']] = pil_image
                qpixmap = QPixmap.fromImage(
                    self._convert_pil_to_qimage(pil_image)
                )
                
                # Create element item
                element = DraggableAsset(img['name'], qpixmap, group_item,
                    canvas_width=self.canvas_width, canvas_height=self.canvas_height)
                self.scene.addItem(element)
                element.setPos(0, 0)  # Start at top-left
                
                # Create tree item
                tree_item = QTreeWidgetItem(group_item, [img['name']])
                tree_item.element_item = element
                element.tree_item = tree_item
                element.group_item = group_item
        
        self.tree.expandAll()
        self.update_element_count()
    
    def _load_file_to_tree(self, filepath, parent_node):
        try:
            name = filepath.stem.lower()
            self.raw_images[str(filepath)] = Image.open(filepath).convert("RGBA")
            
            child = QTreeWidgetItem(parent_node, [name])
            child.filepath = filepath
            child.element_item = None
            child.setFlags(child.flags() | Qt.ItemIsEditable)
            return child
        except Exception as e:
            print(f"Failed to load {filepath}: {e}")
            return None
    
    def auto_place_images(self, files_to_place):
        x_pos, y_pos = 50, 50
        row_height = 0
        spacing = 20
        
        for full_path, group_item in files_to_place:
            if self.current_element_count >= self.max_elements:
                break
            
            name = full_path.stem.lower()
            pixmap = QPixmap(str(full_path))
            
            if x_pos + pixmap.width() + spacing > self.canvas_width:
                x_pos = 50
                y_pos += row_height + spacing
                row_height = 0
            
            if y_pos + pixmap.height() + spacing > self.canvas_height:
                x_pos = 50
                y_pos = 50
                row_height = 0
            
            element_item = DraggableAsset(name, pixmap, group_item.parent() or group_item, self.canvas_width, self.canvas_height)
            element_item.setPos(x_pos, y_pos)
            element_item.tree_item = group_item
            group_item.element_item = element_item
            
            if self.show_outlines:
                element_item.set_outline(self.outline_color)
            
            self.scene.addItem(element_item)
            
            row_height = max(row_height, pixmap.height())
            x_pos += pixmap.width() + spacing
            self.current_element_count += 1
        
        self.update_element_count()
        self.refresh_group_dropdown()
    
    def update_element_count(self):
        count = 0
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                count += 1
        self.current_element_count = count
        self.label_element_count.setText(f"{count}/{self.max_elements}")
    
    def on_scene_selection_changed(self):
        try:
            selected_items = self.scene.selectedItems()
        except RuntimeError:
            return
        
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                item.hide_selection_overlay()
        
        for item in selected_items:
            if isinstance(item, DraggableAsset):
                item.show_selection_overlay()
        
        # Update tree selection to match scene selection (multi-select support)
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        
        # Collect all selected elements and their tree items
        tree_items_to_select = []
        for item in selected_items:
            if isinstance(item, DraggableAsset) and hasattr(item, 'tree_item') and item.tree_item:
                tree_items_to_select.append(item.tree_item)
        
        # Select all corresponding tree items
        for tree_item in tree_items_to_select:
            tree_item.setSelected(True)
        
        # Scroll to first selected item if any
        if tree_items_to_select:
            self.tree.scrollToItem(tree_items_to_select[0])
        
        self.tree.blockSignals(False)
        
        # --- Update Properties Panel ---
        if len(tree_items_to_select) == 1:
            item = tree_items_to_select[0]
            self.edit_item_name.setEnabled(True)
            self.edit_item_name.blockSignals(True)
            self.edit_item_name.setText(item.text(0))
            self.edit_item_name.blockSignals(False)
            
            if hasattr(item, 'filepath'):
                self.edit_item_path.setText(str(item.filepath))
            else:
                self.edit_item_path.setText("")
                
            # Update dimensions and spinboxes
            element_item = getattr(item, 'element_item', None)
            if element_item and isinstance(element_item, DraggableAsset):
                self.label_item_width.setText(f"W: {int(element_item.pixmap().width())} px")
                self.label_item_height.setText(f"H: {int(element_item.pixmap().height())} px")
                
                self._is_updating_spins = True
                self.spin_item_x.setEnabled(True)
                self.spin_item_y.setEnabled(True)
                self.spin_item_x.setValue(int(element_item.scenePos().x()))
                self.spin_item_y.setValue(int(element_item.scenePos().y()))
                self._is_updating_spins = False
            else:
                self.label_item_width.setText("W: -")
                self.label_item_height.setText("H: -")
                self.spin_item_x.setEnabled(False)
                self.spin_item_y.setEnabled(False)
        else:
            self.edit_item_name.setEnabled(False)
            self.edit_item_path.setText("")
            self.edit_item_name.blockSignals(True)
            self.edit_item_name.setText("")
            self.edit_item_name.blockSignals(False)
            # Reset dimensions and spinboxes
            self.label_item_width.setText("W: -")
            self.label_item_height.setText("H: -")
            self.spin_item_x.setEnabled(False)
            self.spin_item_y.setEnabled(False)
        
        self.tree.blockSignals(False)

    def on_tree_selection_changed(self):
        selected_items = self.tree.selectedItems()

        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                item.hide_selection_overlay()

        self.scene.blockSignals(True)
        self.scene.clearSelection()
        self.scene.blockSignals(False)

        self.refresh_group_dropdown()

        # --- Update Properties Panel ---
        if len(selected_items) == 1:
            item = selected_items[0]
            self.edit_item_name.setEnabled(True)
            self.edit_item_name.blockSignals(True)
            self.edit_item_name.setText(item.text(0))
            self.edit_item_name.blockSignals(False)
            
            if hasattr(item, 'filepath'):
                self.edit_item_path.setText(str(item.filepath))
            else:
                self.edit_item_path.setText("")
        else:
            self.edit_item_name.setEnabled(False)
            self.edit_item_path.setText("")
            self.edit_item_name.blockSignals(True)
            self.edit_item_name.setText("")
            self.edit_item_name.blockSignals(False)

        if selected_items:
            # Support multi-select: process all selected tree items
            first_element_to_center = None
            
            for tree_item in selected_items:
                # Check if this is a group (has children and no element_item)
                if tree_item.childCount() > 0 and (not hasattr(tree_item, 'element_item') or not tree_item.element_item):
                    # This is a group - select all child items
                    self._select_group_children(tree_item, save_first=True)
                    if not first_element_to_center:
                        first_child = tree_item.child(0) if tree_item.childCount() > 0 else None
                        if first_child:
                            first_element_to_center = getattr(first_child, 'element_item', None)
                else:
                    # This is a single item
                    element_item = getattr(tree_item, 'element_item', None)
                    if isinstance(element_item, DraggableAsset):
                        element_item.setSelected(True)
                        element_item.show_selection_overlay()
                        if not first_element_to_center:
                            first_element_to_center = element_item
            
            # Center view on first selected element
            if isinstance(first_element_to_center, DraggableAsset):
                self.view.centerOn(first_element_to_center)
    
    def _select_group_children(self, group_item, save_first=False):
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            
            if child.childCount() > 0:
                self._select_group_children(child, save_first=False)
            else:
                element_item = getattr(child, 'element_item', None)
                if isinstance(element_item, DraggableAsset):
                    element_item.setSelected(True)
                    element_item.show_selection_overlay()

    def show_about_dialog(self):
        QMessageBox.about(self, "About", 
            "DayZ Imageset Editor v1.2\n\n"
            "Engineered by Strykar.\n"
            "AI-assisted? You bet! 🤖\n\n"
            "Dedicated to the trolls 🧌\n"
            "Yes, AI had its grubby hand all up in this code. 🦾\n"
            "No, it doesn't make the tool any less effective. "
            "Stay salty! 😉")
    
    def show_help_dialog(self):
        """Shows a custom help dialog with shortcuts, tips, and a clickable Discord link."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Help & Shortcuts")
        dialog.setFixedSize(450, 600)
        
        # Main layout
        layout = QVBoxLayout(dialog)
        
        # --- Create Tabs ---
        tabs = QTabWidget()
        
        # Tab 1: Shortcuts
        shortcuts_tab = QScrollArea()
        shortcuts_tab.setWidgetResizable(True) # Allows the internal widget to scale with the scroll area
        shortcuts_tab.setFrameShape(QScrollArea.NoFrame)
        shortcuts_tab = QWidget()
        shortcuts_layout = QVBoxLayout(shortcuts_tab)
        shortcuts_text = """
        <h3 style='color: #ff5500;'>Keyboard & Mouse Shortcuts</h3>
        <table width='100%' cellpadding='6'>
            <tr><td width='40%'><b>Ctrl + Z</b></td><td>Undo last action</td></tr>
            <tr><td><b>Ctrl + Y / Ctrl+Shift+Z</b></td><td>Redo action</td></tr>
            <tr><td><b>Delete</b></td><td>Delete selected element(s)</td></tr>
            <tr><td><b>Ctrl + H / F1</b></td><td>Open this Help menu</td></tr>
            <tr><td><b>Right-Click + Drag</b></td><td>Pan around the canvas</td></tr>
            <tr><td><b>Scroll Wheel</b></td><td>Zoom in and out</td></tr>
            <tr><td><b>Shift + Click/Drag</b></td><td>Select multiple elements</td></tr>
        </table>
        """
        lbl_shortcuts = QLabel(shortcuts_text)
        lbl_shortcuts.setWordWrap(True)
        lbl_shortcuts.setStyleSheet("font-size: 14px;")
        shortcuts_layout.addWidget(lbl_shortcuts)
        shortcuts_layout.addStretch()
        tabs.addTab(shortcuts_tab, "Shortcuts")
        
        # Tab 2: Tips & Cues
        tips_tab = QScrollArea()
        tips_tab.setWidgetResizable(True)
        tips_tab.setFrameShape(QScrollArea.NoFrame)

        tips_content = QWidget()
        tips_layout = QVBoxLayout(tips_content)
        tips_text = """
        <h3 style='color: #ff5500;'>Pro Tips</h3>
        <table width='100%' cellpadding='6' cellspacing='0'>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Sidebar Magic:</b> The sidebar can be resized by dragging its edge an it even collapses! Click the <b>Sidebar →</b> button to switch sides!</td>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Group Assignment:</b> Select multiple items in the tree, then use the <i>Assign to Group</i> dropdown to instantly organize your UI layers.</td>
            </tr>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Canvas Alignment:</b> Check <i>Align to Canvas</i> before using the alignment buttons to snap your icons cleanly to the absolute edges of the canvas.</td>
            </tr>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Precision Layouts:</b> Enable both <i>Snap to Grid</i> and <i>Snap to Elements</i> to easily align bounding boxes without manually typing coordinates.</td>
            </tr>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Vanilla Compatibility:</b> When unpacking official .imageset files, the tool automatically figures out all the nested groups for you!</td>
            </tr>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Custom Canvas Sizes:</b> Need a non-square layout? Select <i>Custom...</i> from the canvas size dropdown to enter any dimensions you want, up to 4096x4096.</td>
            </tr>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Rename Element or Group:</b> Simply select the item in the tree and press F2 or double-click to edit its name. Changes will reflect in the properties panel.</td>
            </tr>
            <tr>
                <td valign='top' width='20' style='font-size: 16px; padding-bottom: 10px;'>&bull;</td>
                <td style='padding-bottom: 10px; line-height: 1.2;'><b>Select Groups:</b> Clicking a group in the hierarchy will select all its child elemets on the canvas, making it easy to move or edit entire sections of your UI at once.</td>
            </tr>
        </table>
        """
        lbl_tips = QLabel(tips_text)
        lbl_tips.setWordWrap(True)
        lbl_tips.setStyleSheet("font-size: 14px;")
        tips_layout.addWidget(lbl_tips)
        tips_layout.addStretch()

        tips_tab.setWidget(tips_content)
        tabs.addTab(tips_tab, "Tips & Tricks")
        
        layout.addWidget(tabs)
        
        # --- Bottom Section: Discord & Help ---
        layout.addSpacing(10)
        
        help_text = QLabel("Need help, found a bug, or just want to chat?")
        help_text.setAlignment(Qt.AlignCenter)
        help_text.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(help_text)
        

        discord_url = "https://discord.gg/urfjtY8dy6"
        link_layout = QHBoxLayout()
        link_layout.setContentsMargins(50, 0, 50, 0)
        
        lbl_link = QLabel(f"<a href='{discord_url}' style='color: #4eb4f5;'>Join the DayZ Modders Discord</a>")
        lbl_link.setOpenExternalLinks(True) 
        lbl_link.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        btn_copy = QPushButton("Copy URL")
        btn_copy.setMaximumWidth(80)
        # Put the URL in the clipboard when clickedn
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(discord_url))
        
        link_layout.addWidget(lbl_link)
        link_layout.addWidget(btn_copy)
        
        layout.addLayout(link_layout)
        layout.addSpacing(10)
        
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.exec_()

    def closeEvent(self, event):
        # Prevent prompt if the canvas is completely empty
        if self.current_element_count > 0:
            reply = QMessageBox.question(
                self, 
                "Save Project",
                "Do you want to save your project before closing?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes
            )

            if reply == QMessageBox.Yes:
                # Only close if the save was actually successful
                if self.save_project():
                    event.accept()
                else:
                    event.ignore()
                    return
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return
            else:
                # User clicked No, proceed with closing
                event.accept()
        else:
            event.accept()

        try:
            self.scene.selectionChanged.disconnect(self.on_scene_selection_changed)
        except Exception:
            pass

    def _convert_pil_to_qimage(self, pil_image):
        """Convert PIL image to QImage."""
        pil_image = pil_image.convert("RGBA")
        data = pil_image.tobytes("raw", "RGBA")
        qimage = QImage(data, pil_image.width, pil_image.height, QImage.Format_RGBA8888)
        return qimage

    def export_imageset_and_edds(self):
        """Export both imageset and EDDS files in the same location."""
        # Ask for save directory
        save_dir = QFileDialog.getExistingDirectory(self, "Select Export Directory")
        if not save_dir:
            return
        
        # Ask for filename
        filename, ok = QInputDialog.getText(self, "Enter Filename", "Filename (without extension):")
        if not ok or not filename:
            return
        
        filename = filename.replace(" ", "_").lower()
        save_dir = Path(save_dir)
        
        # Calculate relative EDDS path from root drive
        # Example: If save_dir is "E:/Tools/Test", relative path is "Tools/Test/filename.edds"
        # NOTE: DayZ may require full resource path with GUID prefix, e.g.: "{GUID}Gui/imagesets/filename.edds"
        try:
            # Use anchor (drive + separator) for relative_to on Windows
            # e.g., "E:\\" for absolute paths
            relative_path = save_dir.relative_to(save_dir.anchor)
            edds_relative = str(relative_path / f"{filename}.edds").replace("\\", "/")
        except ValueError:
            # Fallback if relative path fails
            edds_relative = f"{filename}.edds"
        
        imageset_path = save_dir / f"{filename}.imageset"
        edds_path = save_dir / f"{filename}.edds"
        
        # Build imageset content
        compiled_sheet = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        
        groups = {}
        ungrouped = []
        
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                x, y = max(0, int(item.scenePos().x())), max(0, int(item.scenePos().y()))
                # Use actual pixmap dimensions instead of boundingRect (which may include padding)
                pixmap = item.pixmap()
                w, h = pixmap.width(), pixmap.height()
                
                compiled_sheet.paste(self.raw_images[str(item.tree_item.filepath)], (x, y))
                
                item_data = f"\t\t\t\tImageSetDefClass {item.name} {{\n\t\t\t\t\tName \"{item.name}\"\n\t\t\t\t\tPos {x} {y}\n\t\t\t\t\tSize {w} {h}\n\t\t\t\t\tFlags 0\n\t\t\t\t}}"
                
                group_name = item.group_item.text(0)
                if group_name == "ROOT (Ungrouped)":
                    ungrouped.append(item_data[1:])
                else:
                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(item_data)
        
        # Create imageset content
        # Calculate mpix based on max dimension (standard is log2 of largest dimension + 1)
        max_dim = max(self.canvas_width, self.canvas_height)
        import math
        mpix = max(1, math.ceil(math.log2(max_dim)) - 6)  # Typically 1-3 for standard sizes
        
        output = [
            f"ImageSetClass {{",
            f"\tName \"{filename}\"",
            f"\tRefSize {self.canvas_width} {self.canvas_height}",
            f"\tTextures {{",
            f"\t\tImageSetTextureClass {{",
            f"\t\t\tmpix {mpix}",
            f"\t\t\tpath \"{edds_relative}\"",
            f"\t\t}}",
            f"\t}}",
            f"\tImages {{"
        ]
        
        output.extend(ungrouped)
        output.append("\t}")
        
        if groups:
            output.append("\tGroups {")
            for g_name, g_items in groups.items():
                output.append(f"\t\tImageSetGroupClass {g_name} {{")
                output.append(f"\t\t\tName \"{g_name}\"")
                output.append(f"\t\t\tImages {{")
                output.extend(g_items)
                output.append(f"\t\t\t}}")
                output.append(f"\t\t}}")
            output.append("\t}")
        
        output.append("}")
        
        # Write imageset file
        try:
            with open(imageset_path, "w", encoding="utf-8") as f:
                f.write("\n".join(output))
            print(f"Imageset saved: {imageset_path}")
            print(f"  mpix: {mpix}")
            print(f"  texture path: {edds_relative}")
            print(f"  (If DayZ requires resource GUID prefix, manually edit the texture path in the .imageset file)")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save imageset:\n{str(e)}")
            return
        
        # Convert compiled sheet to EDDS
        temp_png = save_dir / f"_{filename}_temp.png"
        try:
            compiled_sheet.save(temp_png, "PNG")
            print(f"Temporary PNG created: {temp_png}")
            
            converter = ImagesetConverter()
            result = converter.png_to_edds(
                str(temp_png),
                str(edds_path),
                format_type="BGRA8",
                mipmaps=1,
                quality=5
            )
            
            if not result['success']:
                QMessageBox.critical(self, "EDDS Conversion Failed", result['error'])
                temp_png.unlink(missing_ok=True)
                return
            
            print(f"EDDS saved: {edds_path}")
            temp_png.unlink(missing_ok=True)
            
            QMessageBox.information(self, "Export Successful",
                f"Files saved successfully:\n\nImageset: {imageset_path}\nEDDS: {edds_path}")
            print("Export Complete!")
        
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export EDDS:\n{str(e)}")
            temp_png.unlink(missing_ok=True)
    
    def export_to_edds(self):
        """Export the compiled sheet to EDDS format."""
        save_file, _ = QFileDialog.getSaveFileName(self, "Export to EDDS", "", "DayZ EDDS (*.edds)")
        if not save_file:
            return
        
        out_path = Path(save_file)
        
        # First, create a PNG from the canvas
        compiled_sheet = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                x, y = max(0, int(item.scenePos().x())), max(0, int(item.scenePos().y()))
                compiled_sheet.paste(self.raw_images[str(item.tree_item.filepath)], (x, y))
        
        # Save temporary PNG
        temp_png = out_path.with_suffix(".png")
        compiled_sheet.save(temp_png, "PNG")
        
        try:
            converter = ImagesetConverter()
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Converter Not Found", 
                f"GO converter tool not found.\n\n{str(e)}\n\nPlease build the GO tool first.")
            return
        
        # Convert PNG to EDDS using the converter
        result = converter.png_to_edds(
            str(temp_png),
            str(out_path),
            format_type="BGRA8",
            mipmaps=1,
            quality=5
        )
        
        if not result['success']:
            QMessageBox.critical(self, "Conversion Failed", result['error'])
            temp_png.unlink(missing_ok=True)
            return
        
        # Clean up temporary PNG
        temp_png.unlink(missing_ok=True)
        
        QMessageBox.information(self, "Success", 
            f"Successfully exported to EDDS:\n{out_path}")
        print("EDDS Export Complete!")

    def sync_spinboxes_to_item(self, item):
        """Called by the canvas when dragging to update the UI boxes seamlessly"""
        if isinstance(item, DraggableAsset):
            self._is_updating_spins = True
            self.spin_item_x.setValue(int(item.scenePos().x()))
            self.spin_item_y.setValue(int(item.scenePos().y()))
            self._is_updating_spins = False

    def on_xy_spin_changed(self):
        """Triggered when the user clicks the up/down arrows or types a number"""
        if self._is_updating_spins:
            return
            
        selected_items = self.scene.selectedItems()
        if len(selected_items) == 1:
            item = selected_items[0]
            if isinstance(item, DraggableAsset):
                new_x = self.spin_item_x.value()
                new_y = self.spin_item_y.value()
                new_pos = QPointF(new_x, new_y)
                
                # Push the custom merging command so that several clicks only creates 1 undo state
                command = SpinBoxMoveCommand(item, item.scenePos(), new_pos)
                self.undo_stack.push(command)

if __name__ == "__main__":
    # Beep-Boop-Beep - Starting the editor with a dash of humor and a sprinkle of AI magic! 🤖✨
    print("---------------------------------------------------------")
    print("DayZ Imageset Editor - Initializing...")
    print("Developed by a human, powered by Artificial Intelligence.")
    print("Criticism noted, but the code works just fine! ☕")
    print("---------------------------------------------------------")
    app = QApplication(sys.argv)
    window = DayZImageset()
    window.show()
    sys.exit(app.exec_())
