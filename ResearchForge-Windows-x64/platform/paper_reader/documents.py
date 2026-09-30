import re
from pathlib import Path

from pypdf import PdfReader

MAX_CHARS = 1500000


def extract_document(path, filename):
    suffix = Path(filename).suffix.lower()
    warnings = []
    if suffix == '.pdf':
        try:
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('这份 PDF 有密码保护，请先解密后上传。')
            if len(reader.pages) > 300:
                raise ValueError('当前单篇最多支持 300 页，请拆分正文与附录后上传。')
            pages, missing, count = [], [], 0
            for number, page in enumerate(reader.pages, 1):
                try:
                    content = page.extract_text() or ''
                except Exception:
                    content = ''
                content = content.replace('\x00', '').strip()
                if len(re.sub(r'\s', '', content)) < 30:
                    missing.append(number)
                count += len(content)
                if count > MAX_CHARS:
                    raise ValueError('正文超过 150 万字符，请拆分后上传。')
                pages.append(f'\n[PDF 第 {number} 页]\n{content or "[此页未提取到可读文本]"}')
            if count < 100 or len(missing) > len(pages) * 0.5:
                raise ValueError('这份 PDF 主要是扫描图片，未取得足够正文。请先用 OCR 转成可检索 PDF 或 Markdown，再上传分析。')
            if missing:
                warnings.append('以下 PDF 页面的可读文本很少或缺失：' + '、'.join(map(str, missing)) + '。这些页面中的图表或扫描内容尚未读取。')
            warnings.append('已提取 PDF 文本；图片、复杂表格与公式可能不完整，请结合原文核对。页码为 PDF 文件顺序页码。')
            text = '\n'.join(pages)
            meta = dict(pages=len(pages), missing_pages=missing, locator='PDF 文件页码')
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('无法读取这份 PDF，请确认文件没有损坏，或转换成 Markdown 后上传。') from exc
    elif suffix in ('.md', '.markdown', '.txt'):
        raw = Path(path).read_bytes()
        content = None
        for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
            try:
                candidate = raw.decode(encoding)
                if '\x00' not in candidate:
                    content = candidate
                    break
            except UnicodeError:
                continue
        if content is None or len(content.strip()) < 100:
            raise ValueError('未读取到足够的论文正文，请上传 UTF-8 编码的完整 Markdown 或文本文件。')
        if len(content) > MAX_CHARS:
            raise ValueError('正文超过 150 万字符，请拆分后上传。')
        text = '\n'.join(f'[L{i}] {line}' for i, line in enumerate(content.splitlines(), 1))
        meta = dict(pages=None, lines=len(content.splitlines()), locator='原文行号')
        if re.search(r'!\[.*?\]\(', content):
            warnings.append('Markdown 中的图片未读取；图片中的图表或公式需要另行核对。')
    else:
        raise ValueError('支持 PDF、MD、Markdown 和 TXT 文件。')
    return dict(text=text, chars=len(text), warnings=warnings, **meta)


def split_text(text, limit):
    """Non-overlapping, lossless segments; never silently drop text."""
    chunks = []
    while len(text) > limit:
        end = text.rfind('\n', limit // 2, limit)
        if end == -1:
            end = limit
        else:
            end += 1
        chunks.append(text[:end])
        text = text[end:]
    if text:
        chunks.append(text)
    return chunks
