"""
Baseline JPEG Converter Module

Converts images inside EPUB files to baseline (non-progressive) JPEG format.
This improves compatibility with e-readers that struggle with progressive JPEGs
or other image formats like PNG/WebP.
"""

import zipfile
import tempfile
import os
import shutil
import re
from io import BytesIO


def convert_image_to_baseline(image_data, quality=85):
    """
    Convert image data to baseline JPEG format.
    
    Handles transparency by compositing onto a white background,
    since JPEG doesn't support alpha channels.
    
    Returns None if conversion fails for any reason.
    """
    from PIL import Image

    try:
        img = Image.open(BytesIO(image_data))

        # Handle images with transparency - composite onto white background
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode in ('RGBA', 'LA'):
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert('RGB')
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Save as baseline JPEG - the key is progressive=False
        output = BytesIO()
        img.save(output, format='JPEG', quality=quality, progressive=False, optimize=False)
        return output.getvalue()
    except Exception:
        return None


def convert_epub_images(epub_path, output_path=None, quality=85, logger=None):
    """
    Convert all images in an EPUB to baseline JPEG.
    
    This handles the whole process:
    - Converting PNG, GIF, WebP, BMP to JPEG
    - Re-encoding existing JPEGs as baseline (non-progressive)
    - Updating all the references in XHTML, HTML, CSS, NCX files
    - Fixing the media-type attributes in the OPF manifest
    
    The EPUB spec requires mimetype to be the first file and uncompressed,
    so we're careful to preserve that.
    """
    if output_path is None:
        output_path = epub_path

    converted_count = 0
    renamed_files = {}  # old filename -> new .jpg filename

    # We'll write to a temp file first, then move it into place
    temp_fd, temp_path = tempfile.mkstemp(suffix='.epub')
    os.close(temp_fd)

    try:
        with zipfile.ZipFile(epub_path, 'r') as zin:
            # First pass: figure out which files need to be renamed
            for item in zin.infolist():
                lower_name = item.filename.lower()
                if lower_name.endswith(('.png', '.gif', '.webp', '.bmp')):
                    base_name = item.filename.rsplit('.', 1)[0]
                    new_name = base_name + '.jpg'
                    renamed_files[item.filename] = new_name

            # Second pass: process everything
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    filename = item.filename
                    lower_name = filename.lower()

                    # Image files get converted to baseline JPEG
                    if lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')):
                        new_data = convert_image_to_baseline(data, quality)
                        if new_data:
                            data = new_data
                            converted_count += 1
                            if filename in renamed_files:
                                filename = renamed_files[filename]
                                if logger:
                                    logger(f'[Baseline JPEG] Converted: {item.filename} -> {filename}')

                    # Content files need their image references updated
                    elif lower_name.endswith(('.xhtml', '.html', '.htm', '.css', '.ncx')):
                        try:
                            text = data.decode('utf-8')
                            for old_name, new_name in renamed_files.items():
                                old_basename = old_name.split('/')[-1]
                                new_basename = new_name.split('/')[-1]
                                text = text.replace(old_basename, new_basename)
                                text = text.replace(old_name, new_name)
                            data = text.encode('utf-8')
                        except Exception:
                            pass  # If we can't decode it, just leave it alone

                    # OPF needs both filename updates AND media-type fixes
                    elif lower_name.endswith('.opf'):
                        try:
                            text = data.decode('utf-8')
                            # Update the href attributes
                            for old_name, new_name in renamed_files.items():
                                old_basename = old_name.split('/')[-1]
                                new_basename = new_name.split('/')[-1]
                                text = text.replace(old_basename, new_basename)
                                text = text.replace(old_name, new_name)
                            # Fix media-type for files that are now JPEGs
                            text = re.sub(
                                r'href="([^"]+\.jpg)"([^>]*)media-type="image/(png|gif|webp|bmp)"',
                                r'href="\1"\2media-type="image/jpeg"',
                                text
                            )
                            text = re.sub(
                                r'media-type="image/(png|gif|webp|bmp)"([^>]*)href="([^"]+\.jpg)"',
                                r'media-type="image/jpeg"\2href="\3"',
                                text
                            )
                            data = text.encode('utf-8')
                        except Exception:
                            pass

                    # Write the file - mimetype must be stored uncompressed per EPUB spec
                    if item.filename == 'mimetype':
                        zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
                    else:
                        new_info = zipfile.ZipInfo(filename)
                        new_info.compress_type = zipfile.ZIP_DEFLATED
                        zout.writestr(new_info, data)

        # All done - move temp file to final location
        shutil.move(temp_path, output_path)

    except Exception as e:
        # Clean up temp file if something went wrong
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

    return converted_count
