![Logo](images/DayZ-Image-Editor-logo.png)
# DayZ Imageset Editor :black_nib:

A standalone, visual drag-and-drop layout editor and atlas compiler for DayZ `.imageset` configurations. This application is a lightweight, dark-themed alternative designed to completely bypass the frustrating quirks, crashes, and many limitations of the DayZ Workbench UI tools.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![PyQt5](https://img.shields.io/badge/UI-PyQt5-green)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

## My Philosophy 🔓
This utility was built by me, a humble DayZ modder, for DayZ modders. **The DayZ Imageset Editor is, and will ALWAYS remain, 100% free, open-source, and unrestricted for everyone.** It is published under the GNU GPLv3 license to ensure that the source code stays transparent and accessible to the community forever.

## Features

- **Visual Layout Canvas:** Fluidly arrange, position, and pack layout elements directly on a hardware-accelerated canvas supporting sizes from 512x512 up to massive 8192x8192 texture sheets.
- **Automated Directory-to-Group Parsing:** Imports full structures instantly. Selecting a root folder automatically maps raw images to root nodes, while all subdirectories are parsed cleanly into DayZ `<Groups>` arrays.
- **Smart Alignment Utilities:** Features customizable grid-snapping, cross-element edge snapping, and visible bounding box outlines for pixel-perfect alignment.
- **Synchronized Hierarchy View:** Full bi-directional tree navigation. Clicking an asset in your folder hierarchy centers the canvas viewport directly on it, and selecting an element on the canvas instantly highlights and scrolls to its point in the layer tree.
- **Workspace Project Saving:** Progress can be seamlessly saved and resumed later. The application tracks layout spatial metrics across session reloads, keeping your workspace intact without requiring you to finalize formatting all in one sitting. Saving will outputs a `.json` file.
- **DayZ Syntax Export:** Instantly compiles both the packed, transparent `.png` texture sheet and flawlessly indented, DayZ formatted `.imageset` file ready for `.edds` conversion via Workbench. (I plan to eliminate the need for Workbench entirely in the future)

## Setup & Running From Source

If you prefer to run or modify the Python source code directly:

### Prerequisites
Make sure you have Python 3.8 or higher installed. Install the necessary UI and image processing dependencies:

```bash
pip install PyQt5 Pillow
```
## Roadmap & Planned Features

Development is actively ongoing. Planned features for upcoming major releases include:

*   \[ \] **Native Reverse-Parsing:** The ability to import pre-existing `.imageset` files directly back into the engine, automatically rebuilding the interactive canvas workspace and outputting `.png` files with folders created for `groups`.
*   \[ \] **Direct .EDDS Decompression:** Integrating localized binary extraction to convert `.edds` textures directly into readable `.png` images without needing external command-line conversion workarounds or opening the dread Workbench.
*   \[ \] **Integrated Packing Packing Algorithms:** Optional automated texture packing (Bin-Packing) routines to auto-arrange asset groups cleanly at the click of a button. To give you a baseline to work with.

## Previews & Interface

<img width="720" alt="DayZ_Imageset_Editor_screenshot1" src="https://github.com/user-attachments/assets/1c2c2f90-8671-4a50-b2df-6a347c9fa58d" />
<img width="250" alt="Drag & Drop functionality" src="https://github.com/user-attachments/assets/1abff907-088b-4503-b883-ee2701820745" />
<img width="720" alt="DayZ_Imageset_Editor_screenshot3" src="https://github.com/user-attachments/assets/fe42dcc5-d415-4070-a5d8-8a435cd92028" />
<img width="720" alt="Show Outlines" src="https://github.com/user-attachments/assets/e3f29a8a-a488-40df-9742-78e896dbe424" />
<img width="720" alt="Show Gridlines" src="https://github.com/user-attachments/assets/70329035-29db-4141-8a51-218928fe5f1c" />
<img width="720" alt="Save Prompt on Close" src="https://github.com/user-attachments/assets/189e3de1-12fb-41d9-becc-9e50d00de11a" />
