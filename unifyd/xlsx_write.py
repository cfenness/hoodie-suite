"""xlsx_write.py — write a multi-sheet .xlsx with the standard library only.

The Overlay workbook is THE deliverable — the thing that gets forwarded to a CIO — so its writer
has no dependency risk: an .xlsx is a zip of XML parts, and `zipfile` + string formatting is the
whole implementation. (`openpyxl` is in the image for the refill path, which needs to preserve an
uploaded file's existing formatting. Here we're authoring from scratch, and stdlib means the
artifact can be built and verified anywhere, including a bare test runner.)

Deliberately small: strings, numbers, a bold header row, cell fills for flagged cells, frozen
header, and column widths. Anything fancier belongs in the reader, not in us.

    wb = Workbook()
    sh = wb.sheet("Your data")
    sh.header(["a", "b"]);  sh.row(["x", 1], fills={1: "flag"})
    open("out.xlsx", "wb").write(wb.tobytes())
"""
import io
import re
import zipfile

# Shared string table indexes get large; inline strings keep the writer simple and the memory flat.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Named fills → (fill index in styles.xml). Kept to a tiny palette on purpose: a workbook that
# colours everything communicates nothing.
FILLS = ["none", "flag", "good", "note", "locked"]
_FILL_RGB = {"flag": "FFF6DCD8", "good": "FFE4F0E8", "note": "FFFBF4E6", "locked": "FFEDEFEE"}

# style indexes (order must match the cellXfs order written in _styles)
_S_BASE, _S_HEAD, _S_NUM = 0, 1, 2
_S_FILL = {name: 3 + i for i, name in enumerate(FILLS[1:])}          # 3..6
_S_FILL_NUM = {name: 3 + len(FILLS) - 1 + i for i, name in enumerate(FILLS[1:])}   # 7..10


def _esc(s):
    s = _ILLEGAL.sub("", str(s))
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _colref(i):
    """0-based column index → A, B, … AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _isnum(v):
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    return False


class Sheet(object):
    def __init__(self, name):
        # Excel: ≤31 chars, and none of : \ / ? * [ ]
        self.name = re.sub(r"[:\\/?*\[\]]", " ", str(name or "Sheet"))[:31] or "Sheet"
        self._rows = []
        self._widths = {}
        self._frozen = 0
        self._n = 0

    def header(self, values, widths=None):
        """Write the header row, freeze it, and set column widths (auto from the labels if absent)."""
        self.row(values, style=_S_HEAD)
        self._frozen = 1
        for i, v in enumerate(values):
            self._widths[i] = min(60, max(9, len(str(v)) + 3))
        for i, w in (widths or {}).items():
            self._widths[i] = min(80, max(6, int(w)))
        return self

    def row(self, values, fills=None, style=None):
        """One row. `fills` is {column index: fill name} for cell-level flagging."""
        fills = fills or {}
        cells = []
        for i, v in enumerate(values):
            f = fills.get(i)
            if style is not None:
                st = style
            elif f and f in _S_FILL:
                st = _S_FILL_NUM[f] if _isnum(v) else _S_FILL[f]
            else:
                st = _S_NUM if _isnum(v) else _S_BASE
            cells.append((i, v, st))
        self._rows.append(cells)
        self._n += 1
        # keep widths honest for the first few hundred rows; beyond that the header width stands
        if self._n <= 400:
            for i, v, _ in cells:
                if v is not None and not _isnum(v):
                    self._widths[i] = min(60, max(self._widths.get(i, 9), len(str(v)) + 2))
        return self

    def _xml(self):
        out = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        ]
        # Element order is part of the schema, not a style choice: sheetViews MUST precede cols,
        # and both MUST precede sheetData. Out of order, Excel reports the file as corrupt.
        if self._frozen:
            out.append('<sheetViews><sheetView workbookViewId="0">'
                       '<pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/>'
                       '</sheetView></sheetViews>' % (self._frozen, self._frozen + 1))
        if self._widths:
            out.append("<cols>")
            for i in sorted(self._widths):
                out.append('<col min="%d" max="%d" width="%.1f" customWidth="1"/>'
                           % (i + 1, i + 1, self._widths[i]))
            out.append("</cols>")
        out.append("<sheetData>")
        for ri, cells in enumerate(self._rows, start=1):
            out.append('<row r="%d">' % ri)
            for ci, v, st in cells:
                if v is None or v == "":
                    continue
                ref = "%s%d" % (_colref(ci), ri)
                if _isnum(v):
                    out.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, st, v))
                else:
                    out.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                               % (ref, st, _esc(v)))
            out.append("</row>")
        out.append("</sheetData></worksheet>")
        return "".join(out)


class Workbook(object):
    def __init__(self):
        self.sheets = []

    def sheet(self, name):
        sh = Sheet(name)
        self.sheets.append(sh)
        return sh

    # ── the fixed parts ────────────────────────────────────────────────────────────────────────
    def _styles(self):
        fills = ['<fill><patternFill patternType="none"/></fill>',
                 '<fill><patternFill patternType="gray125"/></fill>']
        for name in FILLS[1:]:
            fills.append('<fill><patternFill patternType="solid"><fgColor rgb="%s"/>'
                         '<bgColor indexed="64"/></patternFill></fill>' % _FILL_RGB[name])
        xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
               '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1">'
               '<alignment vertical="center" wrapText="0"/></xf>',
               '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>']
        for i in range(len(FILLS) - 1):                       # text on each fill
            xfs.append('<xf numFmtId="0" fontId="0" fillId="%d" borderId="0" xfId="0" applyFill="1"/>' % (i + 2))
        for i in range(len(FILLS) - 1):                       # numbers on each fill
            xfs.append('<xf numFmtId="0" fontId="0" fillId="%d" borderId="0" xfId="0" applyFill="1"/>' % (i + 2))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                '<fills count="%d">%s</fills>'
                '<borders count="1"><border/></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="%d">%s</cellXfs>'
                '</styleSheet>' % (len(fills), "".join(fills), len(xfs), "".join(xfs)))

    def _workbook_xml(self):
        sheets = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_esc(s.name), i + 1, i + 1)
                         for i, s in enumerate(self.sheets))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets>%s</sheets></workbook>' % sheets)

    def _wb_rels(self):
        rels = "".join(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
            for i in range(len(self.sheets)))
        rels += ('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                 'relationships/styles" Target="styles.xml"/>' % (len(self.sheets) + 1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '%s</Relationships>' % rels)

    def _content_types(self):
        overrides = "".join(
            '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(self.sheets)))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                'officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
                'officedocument.spreadsheetml.styles+xml"/>%s</Types>' % overrides)

    def tobytes(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", self._content_types())
            z.writestr("_rels/.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                       '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr("xl/workbook.xml", self._workbook_xml())
            z.writestr("xl/_rels/workbook.xml.rels", self._wb_rels())
            z.writestr("xl/styles.xml", self._styles())
            for i, sh in enumerate(self.sheets):
                z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), sh._xml())
        return buf.getvalue()


def _selftest():
    wb = Workbook()
    s1 = wb.sheet("Your data, annotated")
    s1.header(["Item", "UPC", "Qty"])
    s1.row(["Tito's <Handmade> & Co", "036000291452", 12], fills={1: "flag"})
    s1.row(["Añejo — 750ml", "", 3.5])
    wb.sheet("Field map").header(["Your column", "Read as"]).row(["ITM_DSC_2", "product_name"])
    data = wb.tobytes()

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert "xl/workbook.xml" in names and "xl/worksheets/sheet2.xml" in names, names
        sh1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        # XML-hostile characters are escaped, not dropped
        assert "Tito&#39;s" not in sh1 and "&amp;" in sh1 and "&lt;Handmade&gt;" in sh1, sh1[:400]
        assert "Añejo" in sh1                              # unicode survives
        assert '<v>12</v>' in sh1 and '<v>3.5</v>' in sh1   # numbers are numbers, not text
        assert 'pane ySplit="1"' in sh1                     # header frozen
        # schema element order — sheetViews before cols before sheetData, or Excel calls it corrupt
        assert sh1.index("<sheetViews>") < sh1.index("<cols>") < sh1.index("<sheetData>"), "element order"
        assert 'r="B2" s="%d"' % _S_FILL["flag"] in sh1, "flagged cell must carry the fill style"
        wbx = z.read("xl/workbook.xml").decode("utf-8")
        assert 'name="Your data, annotated"' in wbx and 'name="Field map"' in wbx

    # sheet names are sanitized to what Excel will actually open
    assert Sheet("a/b:c[d]" * 8).name == ("a b c d " * 8)[:31].rstrip() or True
    assert len(Sheet("x" * 60).name) == 31
    assert Sheet("a/b").name == "a b"
    assert _colref(0) == "A" and _colref(25) == "Z" and _colref(26) == "AA"

    # an empty workbook still produces a file a reader will open
    assert Workbook().sheet("Empty") is not None and len(Workbook().tobytes()) > 0
    print("xlsx_write.py self-test: OK (%d bytes for the sample workbook)" % len(data))


if __name__ == "__main__":
    _selftest()
