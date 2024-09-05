import itertools
import functools

from visidata import Sheet, Column, vd, asyncthread, Progress

def getslice(L, i, j):
    'Return L[i:j], except if i < 0 or j >= len(L), it pads with None, so the returned list always has j-i items.'
    r = []
    if i >= 0 and j < len(L):
        return L[i:j]

    a = i if i >= 0 else None
    b = j if j < len(L) else None

    for i in range(0, -i):
        r.append(None)

    r.extend(L[a:b])

    for i in range(0, j-len(L)):
        r.append(None)

    return r

@Sheet.api
def window(sheet, before:int=0, after:int=0):
    '''Generate (row, list[row]) for each row in *sheet*, where list[row] is the rows within *before* number of rows before and *after* number of rows after the *row*.  The *row* itself is always included in the list.'''
    for i, r in enumerate(sheet.rows):
        yield r, getslice(sheet.rows, i-before, i+after+1)


@Column.api
def window(col, before:int=0, after:int=0):
    'Generate (row, list[values]) for each row in the sheet.  Values are the typed values for this column at that row.'
    for r, rows in col.sheet.window(before, after):
        yield r, [col.getTypedValue(x) for x in rows]


class WindowColumn(Column):
    def getValue(self, row):
        return self.windowrows.get(id(row), None)

    @asyncthread
    def _calcWindowRows(self, outvals):
        for row, vals in Progress(self.sourcecol.window(self.before, self.after), total=self.sheet.nRows):
            self.windowrows[id(row)] = vals

    @property
    def windowrows(self):
        if not hasattr(self, '_windowrows'):
            self._windowrows = {}
            self._calcWindowRows(self._windowrows)

        return self._windowrows


@Sheet.api
def addcol_window(sheet, curcol):
    winsizestr = vd.input('# rows before/after window: ', value='1 1')
    before, after = map(int, winsizestr.split())
    newcol = WindowColumn(curcol.name+"_window", sourcecol=curcol, before=before, after=after)
    sheet.addColumnAtCursor(newcol)


@Sheet.api
def select_around(sheet, n):
    sheet.select(list(itertools.chain(*(winrows for row, winrows in sheet.window(int(n), int(n)) if sheet.isSelected(row)))))


Sheet.addCommand('w', 'addcol-window', 'addcol_window(cursorCol)', 'add column where each row contains a list of that row, nBefore rows, and nAfter rows')
Sheet.addCommand('', 'select-around-n', 'select_around(input("select rows around selected: ", value=1))', 'select additional N rows before/after each selected row')

vd.addMenuItem('Row', 'Select', 'N rows around each selected row', 'select-around-n')
