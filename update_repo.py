#!/usr/bin/env python3
"""
Regenerates addons.xml and addons.xml.md5 from all addon.xml files in subdirectories.
Run this script after adding or updating any addon.
"""
import os
import hashlib
from xml.etree import ElementTree as ET

base = os.path.dirname(os.path.abspath(__file__))

addon_dirs = [
    d for d in os.listdir(base)
    if os.path.isdir(os.path.join(base, d)) and os.path.exists(os.path.join(base, d, 'addon.xml'))
]
addon_dirs.sort()

addons_content = '<?xml version="1.0" encoding="UTF-8"?>\n<addons>\n'

for d in addon_dirs:
    xml_path = os.path.join(base, d, 'addon.xml')
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ET.indent(root, space='    ')
    inner = ET.tostring(root, encoding='unicode', xml_declaration=False)
    indented = '\n'.join('    ' + line for line in inner.splitlines())
    addons_content += indented + '\n'
    print(f'  + {d}')

addons_content += '</addons>\n'

with open(os.path.join(base, 'addons.xml'), 'w', encoding='utf-8') as f:
    f.write(addons_content)

md5 = hashlib.md5(addons_content.encode('utf-8')).hexdigest()
with open(os.path.join(base, 'addons.xml.md5'), 'w', encoding='utf-8') as f:
    f.write(md5)

print(f'\naddons.xml updated ({len(addon_dirs)} addons)')
print(f'addons.xml.md5: {md5}')
