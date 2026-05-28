# DayZ Imageset Editor :black_nib:

A standalone, visual drag-and-drop layout editor and atlas compiler for DayZ `.imageset` configurations. This application is a lightweight, dark-themed alternative designed to completely bypass the frustrating quirks, crashes, and limitations of the native DayZ Workbench UI tools.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-green)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

## Features

- **Visual Layout Canvas:** Move, arrange, and snap layout elements directly on a hardware-accelerated canvas sizing up to 8192x8192.
- **Automated Directory-to-Group Parsing:** Imports full structures instantly. Selecting a root folder automatically maps raw images to root nodes, while all subdirectories are parsed cleanly into DayZ `<Groups>` arrays.
- **Smart Alignment Utilities:** Features customizable grid-snapping, cross-element edge snapping, and visible bounding box outlines for pixel-perfect alignment.
- **High Element Limits:** Replaces standard restrictive item caps with a optimized pipeline capable of rendering and organizing up to 5,000 active UI texture components simultaneously.
- **Synchronized Hierarchy View:** Full bi-directional tree navigation. Clicking an asset in your folder hierarchy centers the canvas viewport directly on it, and selecting an element on the canvas instantly highlights and scrolls to its point in the layer tree.

## Setup & Running From Source

If you prefer to run or modify the Python source code directly:

### Prerequisites
Make sure you have Python 3.8 or higher installed. Install the necessary UI and image processing dependencies:

```bash
pip install PyQt5 Pillow
