import contextlib
import os
import curses
import signal
import threading
import time
from unittest import mock

from visidata import vd, VisiData, colors, ESC, options, option

vd.curses_timeout = 100 # curses timeout in ms
vd.timeouts_before_idle = 10

option('disp_splitwin_pct', 0, 'height of second sheet on screen')
option('mouse_interval', 1, 'max time between press/release for click (ms)', sheettype=None)

class ReturnValue(BaseException):
    'raise ReturnValue(ret) to exit from an inner runresult() with its result.'
    pass



@VisiData.api
def draw_sheet(self, scr, sheet):
    'Erase *scr* and draw *sheet* on it, including status bars.'

    sheet.ensureLoaded()

    scr.erase()  # clear screen before every re-draw

    sheet._scr = scr

    self.drawLeftStatus(scr, sheet)
    self.drawRightStatus(scr, sheet)  # visible during this getkeystroke

    try:
        sheet.draw(scr)
    except Exception as e:
        self.exceptionCaught(e)

    scr.refresh()


vd.windowConfig = None
vd.winTop = mock.MagicMock(__bool__=mock.Mock(return_value=False))

@VisiData.api
def setWindows(vd, scr, pct=None):
    'Assign winTop, winBottom, win1 and win2 according to options.disp_splitwin_pct.'
    if pct is None:
        pct = options.disp_splitwin_pct  # percent of window for secondary sheet (negative means bottom)
    h, w = scr.getmaxyx()
    n = abs(pct)*h//100
    # on 100 line screen, pct = 25 means second window on lines 75-100.  pct -25 -> lines 0-25

    desiredConfig = dict(pct=pct, n=n, h=h, w=w)

    if vd.scrFull is not scr or vd.windowConfig != desiredConfig:
        vd.winTop = curses.newwin(n, w, 0, 0)
        vd.winTop.keypad(1)
        vd.winBottom = curses.newwin(h-n, w, n, 0)
        vd.winBottom.keypad(1)
        if pct == 0 or pct >= 100:  # no second pane
            vd.win1 = vd.winBottom
            # drawing to 0-line window causes problems
            vd.win2 = mock.MagicMock(__bool__=mock.Mock(return_value=False))
        elif pct > 0: # pane 2 from line n to bottom
            vd.win1 = vd.winTop
            vd.win2 = vd.winBottom
        elif pct < 0: # pane 2 from line 0 to n
            vd.win1 = vd.winBottom
            vd.win2 = vd.winTop

        for vs in vd.sheetstack(1)[0:1]+vd.sheetstack(2)[0:1]:
            vs.refresh()

        vd.windowConfig = desiredConfig
        vd.scrFull = scr
        return True

@VisiData.api
def draw_all(vd):
    'Draw all sheets in all windows.'
    ss1 = vd.sheetstack(1)
    ss2 = vd.sheetstack(2)
    if ss1 and not ss2:
        vd.activePane = 1
        ss1[0].options.disp_splitwin_pct = 0
        vd.setWindows(vd.scrFull)
        vd.draw_sheet(vd.win1, ss1[0])
        if vd.win2:
            vd.win2.erase()
            vd.win2.refresh()
    elif not ss1 and ss2:
        vd.activePane = 2
        ss2[0].options.disp_splitwin_pct = 0
        vd.setWindows(vd.scrFull)
        vd.draw_sheet(vd.win1, ss2[0])
        if vd.win2:
            vd.win2.erase()
            vd.win2.refresh()
    elif ss1 and ss2 and vd.win2:
        vd.draw_sheet(vd.win1, ss1[0])
        vd.draw_sheet(vd.win2, ss2[0])
    elif ss1 and ss2 and not vd.win2:
        vd.draw_sheet(vd.win1, vd.sheetstack(vd.activePane)[0])
        vd.setWindows(vd.scrFull)

@VisiData.api
def runresult(vd):
    try:
        err = vd.mainloop(vd.scrFull)
        if err:
            raise Exception(err)
    except ReturnValue as e:
        return e.args[0]


@VisiData.api
def mainloop(self, scr):
    'Manage execution of keystrokes and subsequent redrawing of screen.'
    scr.timeout(vd.curses_timeout)
    with contextlib.suppress(curses.error):
        curses.curs_set(0)

    numTimeouts = 0
    prefixWaiting = False
    vd.scrFull = scr

    self.keystrokes = ''
    while True:
        if not self.stackedSheets and self.currentReplay is None:
            return

        sheet = self.activeSheet

        if not sheet:
            continue

        threading.current_thread().sheet = sheet
        vd.drawThread = threading.current_thread()

        vd.setWindows(scr)

        self.draw_all()

        if vd._nextCommands:
            sheet.execCommand(vd._nextCommands.pop(0), keystrokes=self.keystrokes)
            continue

        keystroke = self.getkeystroke(scr, sheet)

        if not keystroke and prefixWaiting and ESC in self.keystrokes:  # timeout ESC
            self.keystrokes = ''

        if keystroke:  # wait until next keystroke to clear statuses and previous keystrokes
            numTimeouts = 0
            if not prefixWaiting:
                self.keystrokes = ''

            self.statuses.clear()

            if keystroke == 'KEY_MOUSE':
                self.keystrokes = ''
                clicktype = ''
                try:
                    devid, x, y, z, bstate = curses.getmouse()
                    pct = vd.windowConfig['pct']
                    topPaneActive = ((vd.activePane == 1 and pct < 0)  or (vd.activePane == 2 and pct > 0))
                    bottomPaneActive = ((vd.activePane == 2 and pct < 0)  or (vd.activePane == 1 and pct > 0))
                    if vd.windowConfig and y > vd.windowConfig['n'] and topPaneActive:
                        y -= vd.windowConfig['n']
                        sheet.mouseX, sheet.mouseY = x, y
                    elif vd.windowConfig and y < vd.windowConfig['n'] and bottomPaneActive:
                        sheet.mouseX, sheet.mouseY = x, y
                    elif pct == 0:
                        sheet.mouseX, sheet.mouseY = x, y
                    if bstate & curses.BUTTON_CTRL:
                        clicktype += "CTRL-"
                        bstate &= ~curses.BUTTON_CTRL
                    if bstate & curses.BUTTON_ALT:
                        clicktype += "ALT-"
                        bstate &= ~curses.BUTTON_ALT
                    if bstate & curses.BUTTON_SHIFT:
                        clicktype += "SHIFT-"
                        bstate &= ~curses.BUTTON_SHIFT

                    keystroke = clicktype + curses.mouseEvents.get(bstate, str(bstate))

                    f = self.getMouse(scr, x, y, keystroke)
                    if f:
                        if isinstance(f, str):
                            for cmd in f.split():
                                sheet.execCommand(cmd)
                        else:
                            f(y, x, keystroke)

                        self.keystrokes = keystroke
                        keystroke = ''
                except curses.error:
                    pass
                except Exception as e:
                    self.exceptionCaught(e)

            if keystroke in self.keystrokes[:-1]:
                vd.warning('duplicate prefix')
                self.keystrokes = ''
            else:
                self.keystrokes += keystroke

        self.drawRightStatus(sheet._scr, sheet)  # visible for commands that wait for input

        if not keystroke:  # timeout instead of keypress
            pass
        elif keystroke == '^Q':
            return self.lastErrors and '\n'.join(self.lastErrors[-1])
        elif vd.bindkeys._get(self.keystrokes):
            sheet.execCommand(self.keystrokes, keystrokes=self.keystrokes)
            prefixWaiting = False
        elif keystroke in self.allPrefixes:
            prefixWaiting = True
        else:
            vd.status('no command for "%s"' % (self.keystrokes))
            prefixWaiting = False

        self.checkForFinishedThreads()
        sheet.checkCursorNoExceptions()

        # no idle redraw unless background threads are running
        time.sleep(0)  # yield to other threads which may not have started yet
        if vd.unfinishedThreads:
            scr.timeout(vd.curses_timeout)
        else:
            numTimeouts += 1
            if vd.timeouts_before_idle >= 0 and numTimeouts > vd.timeouts_before_idle:
                scr.timeout(-1)
            else:
                scr.timeout(vd.curses_timeout)


def initCurses():
    # reduce ESC timeout to 25ms. http://en.chys.info/2009/09/esdelay-ncurses/
    os.putenv('ESCDELAY', '25')
    curses.use_env(True)

    scr = curses.initscr()

    curses.start_color()

    colors.setup()

    curses.noecho()
    curses.cbreak()

    curses.raw()    # get control keys instead of signals
    curses.meta(1)  # allow "8-bit chars"
    curses.mousemask(-1 if options.mouse_interval else 0)
    curses.mouseinterval(options.mouse_interval)
    curses.mouseEvents = {}

    scr.keypad(1)

    curses.def_prog_mode()

    for k in dir(curses):
        if k.startswith('BUTTON') or k in ('REPORT_MOUSE_POSITION', '2097152'):
            curses.mouseEvents[getattr(curses, k)] = k

    return scr


def wrapper(f, *args, **kwargs):
    try:
        scr = initCurses()
        return f(scr, *args, **kwargs)
    finally:
        curses.endwin()


def run(*sheetlist):
    'Main entry point; launches vdtui with the given sheets already pushed (last one is visible)'

    try:
        # Populate VisiData object with sheets from a given list.
        for vs in sheetlist:
            vd.push(vs)

        vd.status('Ctrl+H opens help')

        scr = initCurses()
        ret = vd.mainloop(scr)
    finally:
        curses.endwin()

    if ret:
        print(ret)
