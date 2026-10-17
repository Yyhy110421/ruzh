"""The XML plumbing that .docx and .xlsx have in common.

Both formats are a zip of XML parts, and both need the same four tricks:

* reuse the document's own namespace prefixes instead of ElementTree's ns0, ns1...
* put the original root start tag back after serialising, because ElementTree only emits
  the declarations it actually uses - and Word and Excel both reference prefixes (w14,
  mc, x14ac...) inside attribute values without ever using them as element names. Dropping
  their declarations leaves the attribute pointing at a prefix that is no longer declared,
  which both applications report as an unreadable file.
* find an element's parent, so a merged/removed block can be taken out of the tree.
* copy the archive, replacing only the parts that changed.

Kept in one place because the two translators must not drift apart on any of it.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from xml.etree import ElementTree as ET

#: Namespace of the xml:space attribute, which marks text as whitespace-significant.
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_NAMESPACE = re.compile(rb'xmlns(?::([A-Za-z0-9_.\-]+))?="([^"]+)"')
_START_TAG = re.compile(rb"<([A-Za-z_][A-Za-z0-9_.\-]*(?::[A-Za-z0-9_.\-]+)?)")
_HAS_DECLARATION = re.compile(rb"^\s*<\?xml")


def register_namespaces(raw: bytes) -> None:
    """Reuse the document's own prefixes instead of ElementTree's ns0, ns1..."""
    for match in _NAMESPACE.finditer(raw):
        prefix = (match.group(1) or b"").decode("ascii", "ignore")
        uri = match.group(2).decode("utf-8", "replace")
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            continue


def root_start_tag(raw: bytes) -> bytes:
    """The document element's start tag, verbatim, so its declarations can be restored."""
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index:index + 1].isspace():
            index += 1
        if raw.startswith(b"<?", index):
            end = raw.find(b"?>", index)
            index = length if end < 0 else end + 2
            continue
        if raw.startswith(b"<!--", index):
            end = raw.find(b"-->", index)
            index = length if end < 0 else end + 3
            continue
        break
    if index >= length or raw[index:index + 1] != b"<":
        return b""
    quote = b""
    cursor = index
    while cursor < length:
        char = raw[cursor:cursor + 1]
        if quote:
            if char == quote:
                quote = b""
        elif char in (b'"', b"'"):
            quote = char
        elif char == b">":
            return raw[index:cursor + 1]
        cursor += 1
    return b""


def splice_root_tag(serialized: bytes, original: bytes) -> bytes:
    """Put the original start tag (and its namespace declarations) back on the root."""
    if not original:
        return serialized
    match = _START_TAG.search(serialized)
    if match is None:
        return serialized
    quote = b""
    cursor = match.start()
    while cursor < len(serialized):
        char = serialized[cursor:cursor + 1]
        if quote:
            if char == quote:
                quote = b""
        elif char in (b'"', b"'"):
            quote = char
        elif char == b">":
            return serialized[:match.start()] + original + serialized[cursor + 1:]
        cursor += 1
    return serialized


def rebuild_part(original: bytes, root) -> bytes:
    """Serialise one part, keeping the declarations and the declaration's presence.

    Word writes an XML declaration, openpyxl does not; neither is wrong, so the original's
    choice is preserved rather than normalised.
    """
    body = splice_root_tag(ET.tostring(root, encoding="UTF-8"), root_start_tag(original))
    if _HAS_DECLARATION.match(original):
        return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + body
    return body


def element_parents(root) -> dict[int, object]:
    """id(element) -> parent, so a moved or removed block can be detached again."""
    parents: dict[int, object] = {}
    for parent in root.iter():
        for child in parent:
            parents[id(child)] = parent
    return parents


def rewrite_zip(source: Path, target: Path, replacements: dict[str, bytes]) -> None:
    """Copy the archive, swapping in new bytes for the named parts and nothing else."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive, \
            zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for item in archive.infolist():
            payload = replacements.get(item.filename)
            if payload is None:
                payload = archive.read(item.filename)
            out.writestr(item, payload)
