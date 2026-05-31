# ==============================================================================
# DayZ Imageset Project Unpacker and Asset Slicer
# Wrapper to seamlessly convert .imageset + .edds files to native app workspaces
# ==============================================================================

import os
import re
import json
import subprocess
from pathlib import Path
from PIL import Image

class ImagesetConverter:
    """Handles parsing, slicing, and packing pipelines using the Go tool backend"""

    def __init__(self, converter_exe_path=None):
        if converter_exe_path is None:
            base_dir = Path(__file__).parent
            converter_exe_path = base_dir / "converter" / "imagesetconv.exe"
        
        self.converter_exe = Path(converter_exe_path)
        if not self.converter_exe.exists():
            raise FileNotFoundError(f"imagesetconv.exe not found at {self.converter_exe}")

    def unpack_to_project(self, imageset_path, output_dir=None):
        """
        Parses an imageset text layout, decompresses the EDDS file, 
        slices icons into clean directories, and outputs a native app workspace JSON.
        """
        imageset_path = Path(imageset_path).resolve()
        if not imageset_path.exists():
            return {'success': False, 'error': f"File not found: {imageset_path}"}

        project_name = imageset_path.stem
        out_dir = Path(output_dir) if output_dir else imageset_path.parent / project_name
        elements_dir = out_dir / "images"

        # 1. Parse the Imageset file
        parsed_data = self._parse_imageset_file(imageset_path)
        if not parsed_data['success']:
            return parsed_data

        # 2. Locate the actual EDDS file on disk
        texture_path = imageset_path.with_suffix('.edds')
        
        if not texture_path.exists():
            texture_path = Path(parsed_data['texture_path'])
            if not texture_path.is_absolute():
                texture_path = imageset_path.parent / texture_path

        if not texture_path.exists():
            return {
                'success': False, 
                'error': f"Matching EDDS texture sheet not found.\nLooked for:\n1. {imageset_path.with_suffix('.edds')}\n2. {parsed_data['texture_path']}"
            }

        # 3. Use Go utility to convert EDDS to a single Master PNG Sheet
        temp_master_png = out_dir / "__master_sheet_temp.png"
        cmd = [str(self.converter_exe), "edds2png", "-edds", str(texture_path), "-png", str(temp_master_png)]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, creationflags=0x08000000) # Invisible window flag
            if res.returncode != 0:
                return {'success': False, 'error': f"Go extraction failed:\n{res.stderr}"}
            
            # 4. Open master sheet in Pillow and slice individual elements
            with Image.open(temp_master_png) as master_sheet:
                workspace_elements = []
                
                for element in parsed_data['images']:
                    name = element['name']
                    group = element['group']
                    x, y, w, h = element['coords']

                    # Define the target directory path based on grouping
                    group_folder = elements_dir if group == "ROOT (Ungrouped)" else elements_dir / group
                    group_folder.mkdir(parents=True, exist_ok=True)
                    
                    target_filepath = group_folder / f"{name}.png"

                    # Crop element in-memory and save
                    crop_box = (x, y, x + w, y + h)
                    cropped_icon = master_sheet.crop(crop_box)
                    cropped_icon.save(target_filepath, "PNG")

                    workspace_elements.append({
                        "filepath": str(target_filepath),
                        "name": name,
                        "x": float(x),
                        "y": float(y),
                        "group": group,
                        "group_path": [group]
                    })

            temp_master_png.unlink(missing_ok=True)

            # 5. Write the final workspace project JSON
            project_json_path = out_dir / f"{project_name}.json"
            project_payload = {
                "canvas_width": parsed_data["width"],
                "canvas_height": parsed_data["height"],
                "elements": workspace_elements
            }

            with open(project_json_path, 'w', encoding='utf-8') as jf:
                json.dump(project_payload, jf, indent=4)

            return {
                'success': True,
                'project_dir': str(out_dir),
                'json_path': str(project_json_path)
            }

        except Exception as e:
            if temp_master_png.exists():
                temp_master_png.unlink()
            return {'success': False, 'error': f"Pipeline crash: {str(e)}"}

    def png_to_edds(self, png_path, output_path, format_type="BGRA8", mipmaps=1, quality=5):
        """Converts a compiled canvas sheet directly into optimized EDDS format"""
        png_path = Path(png_path).resolve()
        output_path = Path(output_path).resolve()
        
        if not png_path.exists():
            return {'success': False, 'error': f"PNG file not found: {png_path}"}

        cmd = [
            str(self.converter_exe), "png2edds",
            "-png", str(png_path),
            "-output", str(output_path),
            "-format", format_type,
            "-mipmaps", str(mipmaps),
            "-quality", str(quality)
        ]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, creationflags=0x08000000)
            if res.returncode != 0:
                return {'success': False, 'error': f"Compression error:\n{res.stderr}"}
            return {'success': True, 'output_path': str(output_path)}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _parse_imageset_file(self, path):
        """Custom stateful scanner that correctly reads heavily nested Enforce .imageset files."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            ref_size_match = re.search(r'RefSize\s+(\d+)\s+(\d+)', content)
            texture_match = re.search(r'path\s+"([^"]+)"', content)

            if not ref_size_match or not texture_match:
                return {'success': False, 'error': "Invalid imageset layout header formatting."}

            width = int(ref_size_match.group(1))
            height = int(ref_size_match.group(2))
            texture_path = re.sub(r'\{[^}]+\}', '', texture_match.group(1))

            images = []

            # --- Structural Block Parsing Helpers ---
            def get_block(text, keyword):
                """Finds contents of a named block, cleanly tracking nested braces."""
                match = re.search(r'\b' + keyword + r'\s*\{', text)
                if not match: return None
                start = match.end()
                count = 1
                end = start
                while count > 0 and end < len(text):
                    if text[end] == '{': count += 1
                    elif text[end] == '}': count -= 1
                    end += 1
                return text[start:end-1] if count == 0 else None

            def get_named_blocks(text, pattern_str):
                """Finds all class-based blocks (like ImageSetGroupClass) via brace tracking."""
                blocks = []
                for match in re.finditer(r'\b' + pattern_str + r'\s+(\w+|"[^"]+")\s*\{', text):
                    start = match.end()
                    name = match.group(1).strip('"') # Strip quotes if "New Image" format
                    count = 1
                    end = start
                    while count > 0 and end < len(text):
                        if text[end] == '{': count += 1
                        elif text[end] == '}': count -= 1
                        end += 1
                    if count == 0:
                        blocks.append((name, text[start:end-1]))
                return blocks

            def extract_images(text, group_name):
                """Scans a text block for individual image coordinates."""
                # Handles both DayZ variations flawlessly using brace isolation
                for name, body in get_named_blocks(text, r'(?:ImageSetDefClass|ImageSetSubImageClass)'):
                    pos = re.search(r'Pos\s+(\d+)\s+(\d+)', body)
                    size = re.search(r'Size\s+(\d+)\s+(\d+)', body)
                    if pos and size:
                        images.append({
                            'name': name,
                            'group': group_name,
                            'coords': (int(pos.group(1)), int(pos.group(2)), int(size.group(1)), int(size.group(2)))
                        })

            # 1. Extract Global ROOT (Ungrouped) Images
            top_images_content = get_block(content, "Images")
            if top_images_content:
                extract_images(top_images_content, "ROOT (Ungrouped)")

            # 2. Extract Nested Group Images
            groups_content = get_block(content, "Groups")
            if groups_content:
                for g_name, g_body in get_named_blocks(groups_content, "ImageSetGroupClass"):
                    inner_images_content = get_block(g_body, "Images")
                    content_to_search = inner_images_content if inner_images_content else g_body
                    extract_images(content_to_search, g_name)

            return {
                'success': True,
                'width': width,
                'height': height,
                'texture_path': texture_path,
                'images': images
            }
        except Exception as e:
            return {'success': False, 'error': f"Failed to parse text layout: {str(e)}"}