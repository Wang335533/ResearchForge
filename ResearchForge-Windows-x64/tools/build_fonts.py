#!/usr/bin/env python3
"""Build the interface display-serif subset served from /ui/fonts/ (development tool).

The source is Source Han Serif SemiBold (SIL OFL 1.1, Reserved Font Name "Source").
Subsetting makes a Modified Version, so the output is renamed "RF Song" and ships with
the license text. Glyphs are split into two unicode-range slices: the core slice covers
Latin, punctuation, every character used by the interface and GB2312 level 1; the
extended slice adds GB2312 level 2. Browsers download a slice only when a page uses it.

Needs fontTools and brotli in the interpreter that runs it (not part of the app runtime):
    python tools/build_fonts.py [--source path/to/SourceHanSerif*-SemiBold.otf]
"""
import argparse
import os
from pathlib import Path
import re

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'platform/shared_ui/fonts'
CSS = ROOT / 'platform/shared_ui/fonts.css'
FAMILY = 'RF Song'
LOCAL_FONTS = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/Windows/Fonts'
CANDIDATES = [LOCAL_FONTS / 'SourceHanSerifCN-SemiBold.otf', Path('C:/Windows/Fonts/SourceHanSerifCN-SemiBold.otf')]
UI_SOURCES = ['platform/**/*.html', 'platform/**/*.js', 'platform/**/*.css', 'docs/setup.html', 'platform/**/*.py']


def gb2312(level):
    rows = range(0xB0, 0xD8) if level == 1 else range(0xD8, 0xF8)
    chars = []
    for high in rows:
        for low in range(0xA1, 0xFF):
            try:
                chars.append(bytes([high, low]).decode('gb2312'))
            except UnicodeDecodeError:
                pass
    return set(chars)


def interface_chars():
    found = set()
    for pattern in UI_SOURCES:
        for path in ROOT.glob(pattern):
            if 'vendor' in path.parts or '__pycache__' in path.parts:
                continue
            found.update(path.read_text(encoding='utf-8', errors='ignore'))
    return {c for c in found if ord(c) >= 0x2E80}


def base_chars():
    ranges = [(0x20, 0x7E), (0xA0, 0xFF), (0x2000, 0x206F), (0x2100, 0x214F), (0x2190, 0x21FF),
              (0x2460, 0x24FF), (0x25A0, 0x25FF), (0x3000, 0x303F), (0xFF00, 0xFFEF)]
    return {chr(c) for start, end in ranges for c in range(start, end + 1)}


def ranges_css(codepoints):
    points = sorted(codepoints)
    spans, start, prev = [], points[0], points[0]
    for point in points[1:]:
        if point == prev + 1:
            prev = point
            continue
        spans.append((start, prev))
        start = prev = point
    spans.append((start, prev))
    return ', '.join(f'U+{a:X}' if a == b else f'U+{a:X}-{b:X}' for a, b in spans)


def rename(font):
    table = font['name']
    copyright_notice = table.getDebugName(0)
    license_text, license_url = table.getDebugName(13), table.getDebugName(14)
    table.names = []
    for name_id, value in ((0, copyright_notice), (1, FAMILY), (2, 'Regular'), (3, 'RFSong-SemiBold;subset'),
                           (4, FAMILY + ' SemiBold'), (5, 'Version 1.0; interface subset'), (6, 'RFSong-SemiBold'),
                           (13, license_text), (14, license_url), (16, FAMILY), (17, 'SemiBold')):
        table.setName(value, name_id, 3, 1, 0x409)
    if 'CFF ' in font:
        cff = font['CFF '].cff
        cff.fontNames = ['RFSong-SemiBold']
        top = cff.topDictIndex[0]
        top.FullName, top.FamilyName = FAMILY + ' SemiBold', FAMILY


def build(source, chars, target):
    options = subset.Options()
    options.flavor = 'woff2'
    options.layout_features = ['kern', 'palt', 'liga', 'locl', 'ccmp']
    options.drop_tables += ['vhea', 'vmtx', 'VORG', 'DSIG', 'BASE']
    options.name_IDs = ['*']
    options.hinting = False
    options.desubroutinize = True
    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted(ord(c) for c in chars))
    subsetter.subset(font)
    rename(font)
    subset.save_font(font, str(target), options)
    present = set(TTFont(str(target))['cmap'].getBestCmap())
    return {ord(c) for c in chars} & present


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    source = args.source or next((p for p in CANDIDATES if p.is_file()), None)
    if not source or not source.is_file():
        parser.error('未找到 Source Han Serif SemiBold，请用 --source 指定 .otf 文件。')
    OUT.mkdir(parents=True, exist_ok=True)
    core = base_chars() | interface_chars() | gb2312(1)
    extended = gb2312(2) - core
    slices = [('core', core), ('ext', extended)]
    faces = []
    for name, chars in slices:
        target = OUT / f'rf-song-600-{name}.woff2'
        covered = build(source, chars, target)
        faces.append((target.name, ranges_css(covered)))
        print(f'{target.name}: {len(covered)} 字符, {target.stat().st_size / 1024:.0f} KB')
    notice = TTFont(str(source))['name'].getDebugName(0) or ''
    (OUT / 'OFL.txt').write_text(
        notice + '\n\nRF Song is a renamed interface subset (Modified Version) of Source Han Serif SemiBold.\n'
        'RF Song 是 Source Han Serif SemiBold 的界面子集（修改版），按 OFL 要求改名，不使用保留字体名称。\n\n'
        'This Font Software is licensed under the SIL Open Font License, Version 1.1.\n\n'
        + (ROOT / 'tools/licenses/OFL-1.1.txt').read_text(encoding='utf-8'), encoding='utf-8')
    rules = ['/* Generated by tools/build_fonts.py — do not edit by hand. */']
    for filename, unicode_range in faces:
        rules.append(
            "@font-face {\n  font-family: \"RF Song\";\n  font-style: normal;\n  font-weight: 500 700;\n  font-display: swap;\n"
            "  src: local(\"Source Han Serif SC SemiBold\"), local(\"Source Han Serif CN SemiBold\"), local(\"SourceHanSerifCN-SemiBold\"),\n"
            "       local(\"Noto Serif CJK SC SemiBold\"), local(\"NotoSerifCJKsc-SemiBold\"),\n"
            f"       url(\"/ui/fonts/{filename}\") format(\"woff2\");\n  unicode-range: {unicode_range};\n}}")
    CSS.write_text('\n'.join(rules) + '\n', encoding='utf-8')
    print('写入', CSS.relative_to(ROOT))


if __name__ == '__main__':
    main()
