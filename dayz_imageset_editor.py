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
                             QInputDialog, QLineEdit, QUndoStack, QUndoCommand, QShortcut)
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPixmap, QColor, QPainter, QCursor, QKeySequence, QIcon
from PIL import Image

class MoveItemsCommand(QUndoCommand):
    def __init__(self, items_data, description="Move Items"):
        super().__init__(description)
        # items_data is a list of tuples: (asset_item, old_pos, new_pos)
        self.items_data = items_data

    def undo(self):
        for asset, old_pos, new_pos in self.items_data:
            asset.setPos(old_pos)

    def redo(self):
        for asset, old_pos, new_pos in self.items_data:
            asset.setPos(new_pos)

class DeleteItemsCommand(QUndoCommand):
    def __init__(self, workbench, assets_to_delete, description="Delete Items"):
        super().__init__(description)
        self.workbench = workbench
        self.scene = workbench.scene
        self.tree = workbench.tree
        
        # Store everything needed to cleanly restore the items
        self.items_data = []
        for asset in assets_to_delete:
            tree_item = getattr(asset, 'tree_item', None)
            parent_item = tree_item.parent() or self.tree.invisibleRootItem() if tree_item else None
            self.items_data.append({
                'asset': asset,
                'tree_item': tree_item,
                'parent': parent_item
            })

    def undo(self):
        for data in self.items_data:
            asset = data['asset']
            self.scene.addItem(asset)
            if data['tree_item'] and data['parent']:
                data['parent'].addChild(data['tree_item'])
        self.workbench.update_element_count()

    def redo(self):
        for data in self.items_data:
            asset = data['asset']
            asset.hide_selection_overlay()
            self.scene.removeItem(asset)
            if data['tree_item'] and data['parent']:
                data['parent'].removeChild(data['tree_item'])
        self.workbench.update_element_count()

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
        
        # --- CRITICAL FIX: Make overlays transparent to mouse clicks ---
        self.selection_overlay.setAcceptedMouseButtons(Qt.NoButton)
        
        inset_rect = self.boundingRect().adjusted(1, 1, -1, -1)
        self.border_rect = QGraphicsRectItem(inset_rect, self)
        self.border_rect.setZValue(1)
        self.border_rect.setBrush(QColor(0, 0, 0, 0))
        self.border_rect.hide()
        
        # --- CRITICAL FIX: Make borders transparent to mouse clicks ---
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
    def __init__(self, scene, workbench=None):
        super().__init__(scene)
        self.workbench = workbench
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
            
            # FIX 1: Drill down to the parent asset if we hit the visual overlay or border
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
                item.setPos(new_pos)
            
            event.accept()
        elif self.selection_rect_active and self.selection_rect_start:
            current_pos = self.mapToScene(event.pos())
            if self.selection_rect_item:
                self.scene().removeItem(self.selection_rect_item)
            
            # --- CRITICAL FIX: Normalize geometry so dragging up/left works flawlessly ---
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
                if items_moved and self.workbench:
                    self.workbench.undo_stack.push(MoveItemsCommand(items_moved))
                
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
        if new_scale > 2.0:
            zoomFactor = 2.0 / current_scale
        
        self.scale(zoomFactor, zoomFactor)
        
        if self.workbench:
            new_transform = self.transform()
            new_scale = new_transform.m11()
            new_zoom_percentage = round(new_scale * 100)
            self.workbench.set_zoom_level(new_zoom_percentage)
    
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
            assets_to_delete = [item for item in selected_items if isinstance(item, DraggableAsset)]
            
            if assets_to_delete:
                # Show confirmation dialog
                item_names = ", ".join([asset.name for asset in assets_to_delete[:3]])
                if len(assets_to_delete) > 3:
                    item_names += f"... (+{len(assets_to_delete) - 3} more)"
                
                reply = QMessageBox.question(self.workbench, 
                    "Delete Images",
                    f"Are you sure you wish to delete {len(assets_to_delete)} item(s)? ({item_names})",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No)
                
                if reply == QMessageBox.Yes:
                    # Delete all selected assets
                    for asset in assets_to_delete:
                        asset.hide_selection_overlay()
                        if hasattr(asset, 'tree_item') and asset.tree_item:
                            parent = asset.tree_item.parent() or self.workbench.tree.invisibleRootItem()
                            parent.removeChild(asset.tree_item)
                        self.scene().removeItem(asset)
                    self.workbench.update_element_count()
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
        self.setWindowTitle("DayZ Visual Imageset Builder - FUCK Workbench 🖕")
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

        # --- Undo / Redo Setup ---
        self.undo_stack = QUndoStack(self)
        
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo_stack.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.undo_stack.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.undo_stack.redo)

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
        
        self.btn_sidebar_pos = QPushButton("← Sidebar")
        self.btn_sidebar_pos.setMaximumWidth(100)
        self.btn_sidebar_pos.clicked.connect(self.toggle_sidebar_position)
        row1_layout.addWidget(self.btn_sidebar_pos)

        # --- Undo / Redo Buttons ---
        row1_layout.addWidget(QLabel(" | "))
        btn_undo = QPushButton()
        btn_undo.setIcon(QIcon("resources/reply-fill.svg"))
        btn_undo.setToolTip("Undo (Ctrl+Z)")
        btn_undo.clicked.connect(self.undo_stack.undo)
        row1_layout.addWidget(btn_undo)

        btn_redo = QPushButton()
        btn_redo.setIcon(QIcon("resources/share-forward-fill.svg"))
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
        btn_align_left.setIcon(QIcon("resources/align-item-left-fill.svg"))
        btn_align_left.setToolTip("Align Left")
        btn_align_left.clicked.connect(lambda: self.align_selected("left"))
        row1_layout.addWidget(btn_align_left)
        
        btn_align_center = QPushButton()
        btn_align_center.setIcon(QIcon("resources/align-item-horizontal-center-fill.svg"))
        btn_align_center.setToolTip("Align Center")
        btn_align_center.clicked.connect(lambda: self.align_selected("center"))
        row1_layout.addWidget(btn_align_center)
        
        btn_align_right = QPushButton()
        btn_align_right.setIcon(QIcon("resources/align-item-right-fill.svg"))
        btn_align_right.setToolTip("Align Right")
        btn_align_right.clicked.connect(lambda: self.align_selected("right"))
        row1_layout.addWidget(btn_align_right)
        
        btn_align_bottom = QPushButton()
        btn_align_bottom.setIcon(QIcon("resources/align-item-bottom-fill.svg"))
        btn_align_bottom.setToolTip("Align Bottom")
        btn_align_bottom.clicked.connect(lambda: self.align_selected("bottom"))
        row1_layout.addWidget(btn_align_bottom)

        btn_align_top = QPushButton()
        btn_align_top.setIcon(QIcon("resources/align-item-top-fill.svg"))
        btn_align_top.setToolTip("Align Top")
        btn_align_top.clicked.connect(lambda: self.align_selected("top"))
        row1_layout.addWidget(btn_align_top)
        
        self.check_align_to_canvas = QCheckBox("Align to Canvas")
        self.check_align_to_canvas.setChecked(False)
        row1_layout.addWidget(self.check_align_to_canvas)
        row1_layout.addStretch()
        
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
        btn_load = QPushButton("Import Folders as Groups")
        btn_load.clicked.connect(self.import_folders)
        btn_layout.addWidget(btn_load)
        sidebar_layout.addLayout(btn_layout)
        
        proj_btn_layout = QHBoxLayout()
        btn_save_proj = QPushButton("Save Project")
        btn_save_proj.clicked.connect(self.save_project)
        btn_load_proj = QPushButton("Load Project")
        btn_load_proj.clicked.connect(self.load_project)
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

        sidebar_layout.addLayout(self.prop_layout)
        
        element_count_layout = QHBoxLayout()
        element_count_layout.addWidget(QLabel("Element Count:"))
        # UI shows only the current element count; max is handled in script
        self.label_element_count = QLabel(f"0/{self.max_elements}")
        self.label_element_count.setMinimumWidth(80)
        element_count_layout.addWidget(self.label_element_count)
        sidebar_layout.addLayout(element_count_layout)
        
        btn_export = QPushButton("EXPORT DAYZ IMAGESET")
        btn_export.setStyleSheet("background-color: #ff5500; color: white; font-weight: bold; padding: 10px;")
        btn_export.clicked.connect(self.export_imageset)
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
        self.splitter.setSizes([350, 1250])
        
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
            self.sidebar_on_left = False
        else:
            # Move sidebar back to left by inserting at index 0
            self.splitter.insertWidget(0, self.sidebar)
            self.btn_sidebar_pos.setText("Sidebar →")
            self.sidebar_on_left = True
    
    def align_selected(self, align_type):
        selected_items = self.scene.selectedItems()
        if not selected_items: return
        
        assets = [item for item in selected_items if isinstance(item, DraggableAsset)]
        if not assets: return
        
        align_to_canvas = self.check_align_to_canvas.isChecked()
        if align_to_canvas:
            self._align_to_canvas(assets, align_type)
        else:
            self._align_to_selection(assets, align_type)
    
    def _align_to_canvas(self, assets, align_type):
        if align_type == "top":
            for asset in assets:
                asset.setPos(asset.pos().x(), 0)       
        elif align_type == "left":
            for asset in assets:
                asset.setPos(0, asset.pos().y())
        elif align_type == "right":
            for asset in assets:
                rect_width = asset.boundingRect().width()
                asset.setPos(self.canvas_width - rect_width, asset.pos().y())
        elif align_type == "center":
            for asset in assets:
                rect_width = asset.boundingRect().width()
                center_x = (self.canvas_width - rect_width) / 2
                asset.setPos(center_x, asset.pos().y())
        elif align_type == "bottom":
            for asset in assets:
                rect_height = asset.boundingRect().height()
                asset.setPos(asset.pos().x(), self.canvas_height - rect_height)
    
    def _align_to_selection(self, assets, align_type):
        if not assets: return
        
        avg_x = sum(asset.pos().x() for asset in assets) / len(assets)
        avg_y = sum(asset.pos().y() for asset in assets) / len(assets)
        
        if align_type == "top":
            target_y = min(asset.pos().y() for asset in assets)
            for asset in assets:
                asset.setPos(asset.pos().x(), target_y)
        elif align_type == "left":
            target_x = min(asset.pos().x() for asset in assets)
            for asset in assets:
                asset.setPos(target_x, asset.pos().y())
        elif align_type == "right":
            target_x = max(asset.pos().x() + asset.boundingRect().width() for asset in assets) - assets[0].boundingRect().width()
            for asset in assets:
                asset.setPos(target_x, asset.pos().y())
        elif align_type == "center":
            target_x = avg_x - assets[0].boundingRect().width() / 2
            for asset in assets:
                asset.setPos(target_x, asset.pos().y())
        elif align_type == "bottom":
            target_y = max(asset.pos().y() + asset.boundingRect().height() for asset in assets) - assets[0].boundingRect().height()
            for asset in assets:
                asset.setPos(asset.pos().x(), target_y)
        
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
            # Don't move groups themselves, only assets
            if hasattr(item, 'asset_item') and item.asset_item:
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
            if not hasattr(group, 'asset_item') or not group.asset_item:
                self.combo_assign_group.addItem(group.text(0))
        
        self.combo_assign_group.blockSignals(False)
    
    def on_tree_item_edited(self, item, column):
        if column == 0:
            new_name = item.text(0)
            if hasattr(item, 'asset_item') and item.asset_item:
                item.asset_item.name = new_name
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
        self._update_all_asset_groups()
        self.refresh_group_dropdown()
    
    def _update_all_asset_groups(self):
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            self._update_group_assets(group, group)
    
    def _update_group_assets(self, item, group_parent):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.childCount() > 0:
                self._update_group_assets(child, child)
            else:
                if hasattr(child, 'asset_item') and child.asset_item:
                    child.asset_item.group_item = group_parent
    
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
        
        if hasattr(item, 'asset_item') and item.asset_item:
            item.asset_item.group_item = target_group
        self.tree.expandItem(target_group)

    def save_project(self):
        save_file, _ = QFileDialog.getSaveFileName(self, "Save Project State", "", "Imageset Project (*.json)")
        if not save_file: 
            return False # Added return
            
        project_data = {
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "assets": []
        }
        
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                if hasattr(item, 'tree_item') and item.tree_item and hasattr(item.tree_item, 'filepath'):
                    group_name = item.group_item.text(0) if item.group_item else "ROOT (Ungrouped)"
                    project_data["assets"].append({
                        "filepath": str(item.tree_item.filepath),
                        "name": item.name,
                        "x": item.pos().x(),
                        "y": item.pos().y(),
                        "group": group_name
                    })
                    
        import json
        try:
            with open(save_file, "w", encoding="utf-8") as f:
                json.dump(project_data, f, indent=4)
            print(f"Project state successfully saved to {save_file}")
            return True # Added return
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to write project file:\n{e}")
            return False # Added return

    def load_project(self):
        load_file, _ = QFileDialog.getOpenFileName(self, "Load Project State", "", "Imageset Project (*.json)")
        if not load_file: return
            
        import json
        try:
            with open(load_file, "r", encoding="utf-8") as f:
                project_data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to read project file:\n{e}")
            return
            
        self.tree.clear()
        self.raw_images.clear()
        self.scene.clear()
        self.grid_lines.clear()
        self.current_element_count = 0
        
        # Backwards compatibility check incase you load a project saved before the width/height update
        if "canvas_size" in project_data:
            self.canvas_width = project_data["canvas_size"]
            self.canvas_height = project_data["canvas_size"]
        else:
            self.canvas_width = project_data.get("canvas_width", 4096)
            self.canvas_height = project_data.get("canvas_height", 4096)

        self.combo_size.blockSignals(True)
        self.combo_size.setCurrentText(str(self.canvas_width) if self.canvas_width == self.canvas_height else f"{self.canvas_width}x{self.canvas_height}")
        self.combo_size.blockSignals(False)
        self.update_canvas_size()
        
        group_nodes = {}
        missing_files_count = 0
        
        for asset_data in project_data.get("assets", []):
            filepath = Path(asset_data["filepath"])
            
            if not filepath.exists():
                missing_files_count += 1
                continue
                
            group_name = asset_data["group"]
            if group_name not in group_nodes:
                group_node = QTreeWidgetItem(self.tree, [group_name])
                group_nodes[group_name] = group_node
                self.tree.expandItem(group_node)
            else:
                group_node = group_nodes[group_name]
                
            name = asset_data["name"]
            self.raw_images[str(filepath)] = Image.open(filepath).convert("RGBA")
            self.raw_images[name] = self.raw_images[str(filepath)] 
            
            child = QTreeWidgetItem(group_node, [name])
            child.filepath = filepath
            
            pixmap = QPixmap(str(filepath))
            asset_item = DraggableAsset(name, pixmap, group_node, self.canvas_width, self.canvas_height)
            asset_item.setPos(asset_data["x"], asset_data["y"])
            asset_item.tree_item = child
            child.asset_item = asset_item
            
            if self.show_outlines:
                asset_item.set_outline(self.outline_color)
                
            self.scene.addItem(asset_item)
            
        self.update_element_count()
        self.refresh_group_dropdown()
        
        if missing_files_count > 0:
            QMessageBox.warning(self, "Missing Source Files", f"Project loaded, but {missing_files_count} image file(s) were skipped.")

    def _apply_dark_theme(self):
        stylesheet = """
            QMainWindow, QWidget { background-color: #1F2329; color: #8AA2AE; }
            QPushButton { background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; border-radius: 3px; padding: 4px; }
            QPushButton:hover { background-color: #24282E; }
            QPushButton:pressed { background-color: #1F2329; }
            QComboBox { background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; border-radius: 3px; padding: 4px; }
            QComboBox::drop-down { image: url(resources/arrow-down-s-fill.svg); border: none; background-color: #24282E; }
            QComboBox QAbstractItemView { background-color: #2C3136; color: #8AA2AE; selection-background-color: #24282E; }
            QSpinBox { background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; border-radius: 3px; padding: 4px; }
            QSpinBox::up-button { image: url(resources/arrow-up-s-fill.svg); background-color: #24282E; border: none; width: 16px; } 
            QSpinBox::down-button { image: url(resources/arrow-down-s-fill.svg); background-color: #24282E; border: none; width: 16px; }
            QLabel { color: #8AA2AE; }
            QCheckBox { color: #8AA2AE; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #24282E; border-radius: 2px; }
            QCheckBox::indicator:unchecked { background-color: #2C3136; }
            QCheckBox::indicator:checked { image: url(resources/close-fill.svg); background-color: #24282E; }
            QTreeWidget { background-color: #2C3136; color: #8AA2AE; border: 1px solid #24282E; gridline-color: #24282E; }
            QTreeWidget::item:selected { background-color: #24282E; }
            QScrollBar:vertical, QScrollBar:horizontal { background-color: #1F2329; border: 1px solid #24282E; }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background-color: #2C3136; border: 1px solid #24282E; border-radius: 2px; }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background-color: #24282E; }
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
        
        for item in self.scene.items():
            if hasattr(item, 'is_boundary') or hasattr(item, 'is_canvas_bg') or hasattr(item, 'is_grid_line'):
                self.scene.removeItem(item)
        
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
        
        if self.show_gridlines:
            self.draw_gridlines()

    def change_bg_color(self):
        color = QColorDialog.getColor()
        if color.isValid() and self.canvas_background_rect:
            self.canvas_background_rect.setBrush(color)
    
    def on_snap_grid_toggled(self, checked):
        self.snap_to_grid_enabled = checked
    
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

    def _load_file_to_tree(self, filepath, parent_node):
        try:
            name = filepath.stem.lower()
            self.raw_images[str(filepath)] = Image.open(filepath).convert("RGBA")
            
            child = QTreeWidgetItem(parent_node, [name])
            child.filepath = filepath
            child.asset_item = None
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
            
            asset_item = DraggableAsset(name, pixmap, group_item.parent() or group_item, self.canvas_width, self.canvas_height)
            asset_item.setPos(x_pos, y_pos)
            asset_item.tree_item = group_item
            group_item.asset_item = asset_item
            
            if self.show_outlines:
                asset_item.set_outline(self.outline_color)
            
            self.scene.addItem(asset_item)
            
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
        
        # Collect all selected assets and their tree items
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
            first_asset_to_center = None
            
            for tree_item in selected_items:
                # Check if this is a group (has children and no asset_item)
                if tree_item.childCount() > 0 and (not hasattr(tree_item, 'asset_item') or not tree_item.asset_item):
                    # This is a group - select all child items
                    self._select_group_children(tree_item, save_first=True)
                    if not first_asset_to_center:
                        first_child = tree_item.child(0) if tree_item.childCount() > 0 else None
                        if first_child:
                            first_asset_to_center = getattr(first_child, 'asset_item', None)
                else:
                    # This is a single item
                    asset_item = getattr(tree_item, 'asset_item', None)
                    if isinstance(asset_item, DraggableAsset):
                        asset_item.setSelected(True)
                        asset_item.show_selection_overlay()
                        if not first_asset_to_center:
                            first_asset_to_center = asset_item
            
            # Center view on first selected asset
            if isinstance(first_asset_to_center, DraggableAsset):
                self.view.centerOn(first_asset_to_center)
    
    def _select_group_children(self, group_item, save_first=False):
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            
            if child.childCount() > 0:
                self._select_group_children(child, save_first=False)
            else:
                asset_item = getattr(child, 'asset_item', None)
                if isinstance(asset_item, DraggableAsset):
                    asset_item.setSelected(True)
                    asset_item.show_selection_overlay()

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

    def export_imageset(self):
        save_file, _ = QFileDialog.getSaveFileName(self, "Export Imageset", "", "DayZ Imageset (*.imageset)")
        if not save_file: return
        
        out_path = Path(save_file)
        sheet_name = out_path.stem.lower()
        
        compiled_sheet = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        
        groups = {} 
        ungrouped = []
        
        for item in self.scene.items():
            if isinstance(item, DraggableAsset):
                x, y = max(0, int(item.scenePos().x())), max(0, int(item.scenePos().y()))
                w, h = int(item.boundingRect().width()), int(item.boundingRect().height())
                
                compiled_sheet.paste(self.raw_images[str(item.tree_item.filepath)], (x, y))
                
                item_data = f"\t\t\t\tImageSetDefClass {item.name} {{\n\t\t\t\t\tName \"{item.name}\"\n\t\t\t\t\tPos {x} {y}\n\t\t\t\t\tSize {w} {h}\n\t\t\t\t\tFlags 0\n\t\t\t\t}}"
                
                group_name = item.group_item.text(0)
                if group_name == "ROOT (Ungrouped)":
                    ungrouped.append(item_data[1:]) 
                else:
                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(item_data)
                    
        compiled_sheet.save(out_path.with_suffix(".png"), "PNG")
        
        output = [
            f"ImageSetClass {{",
            f"\tName \"{sheet_name}\"",
            f"\tRefSize {self.canvas_width} {self.canvas_height}",
            f"\tTextures {{",
            f"\t\tImageSetTextureClass {{",
            f"\t\t\tmpix 1",
            f"\t\t\tpath \"Fallout_DayZ_GUI/data/images/{sheet_name}.edds\"",
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

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(output))
            
        print("Export Complete!")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DayZImageset()
    window.show()
    sys.exit(app.exec_())