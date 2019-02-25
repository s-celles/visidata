import os.path
import sh

from visidata import *
from visidata.namedlist import namedlist


option('vgit_logfile', '', 'file to log all git commands run by vgit')

GitCmd = namedlist('GitCmd', 'sheet command output'.split())

class GitCmdLog(Sheet):
    rowtype = 'git commands'  # rowdef: GitCmd
    columns = [
        ColumnAttr('sheet'),
        ColumnAttr('command'),
        ColumnAttr('output.stdout'),
        ColumnAttr('output.stderr'),
    ]
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        self.rows = []

GitCmdLog.addCommand(ENTER, 'dive-row', 'vd.push(TextSheet(cursorRow[0], cursorRow[1]))', 'view output of this command'),


@VisiData.cached_property
def gitcmdlog(vd):
    return GitCmdLog('gitcmdlog')


def loggit(*args, **kwargs):
    cmdstr = 'git ' + ' '.join(args)
    gcmd = GitCmd([vd.sheet, cmdstr, None])
    vd.gitcmdlog.addRow(gcmd)

    gcmd.output = maybeloggit(*args, **kwargs)

    return gcmd.output


def maybeloggit(*args, **kwargs):
    if options.vgit_logfile:
        cmdstr = 'git ' + ' '.join(args)
        with open(options.vgit_logfile, 'a') as fp:
            fp.write(cmdstr + '\n')

    return sh.git(*args, **kwargs)


def git_all(*args, git=loggit, **kwargs):
    'Return entire output of git command.'

    try:
        cmd = git('--no-pager', *args, _decode_errors='replace', **kwargs)
        out = cmd.stdout
    except sh.ErrorReturnCode as e:
        status('git '+' '.join(args), 'error=%s' % e.exit_code)
        out = e.stdout

    out = out.decode('utf-8')

    return out


def git_lines(*args, git=loggit, **kwargs):
    'Generator of stdout lines from given git command'
    err = io.StringIO()
    try:
        for line in git('--no-pager', _err=err, *args, _decode_errors='replace', _iter=True, _bg_exc=False, **kwargs):
            yield line[:-1]  # remove EOL
    except sh.ErrorReturnCode as e:
        status('git '+' '.join(args), 'error=%s' % e.exit_code)

    errlines = err.getvalue().splitlines()
    if len(errlines) < 3:
        for line in errlines:
            status('stderr: '+line)
    else:
        vd.push(TextSheet('git ' + ' '.join(args), errlines))


def git_iter(*args, git=loggit, sep='\0', **kwargs):
    'Generator of chunks of stdout from given git command, delineated by sep character'
    bufsize = 512
    err = io.StringIO()

    chunks = []
    try:
      for data in git('--no-pager', *args, _decode_errors='replace', _out_bufsize=bufsize, _iter=True, _err=err, **kwargs):
        while True:
            i = data.find(sep)
            if i < 0:
                break
            chunks.append(data[:i])
            data = data[i+1:]
            yield ''.join(chunks)
            chunks.clear()

        chunks.append(data)
    except sh.ErrorReturnCode as e:
        errlines = err.getvalue().splitlines()
        if len(errlines) < 3:
            for line in errlines:
                status(line)
        else:
            vd.push(TextSheet('git ' + ' '.join(args), errlines))

        error('git '+' '.join(args)+'error=%s' % e.exit_code)

    r = ''.join(chunks)
    if r:
        yield r

    errlines = err.getvalue().splitlines()
    if len(errlines) < 3:
        for line in errlines:
            status(line)
    else:
        vd.push(TextSheet('git ' + ' '.join(args), errlines))


class GitUndo:
    def __init__(self, *args):
        self.cmdargs = args
    def __enter__(self):
        return self
    def __exit__(self, exctype, exc, tb):
        out = git_all(*self.cmdargs)


def getRootSheet(sheet):
    if isinstance(sheet.source, GitSheet):
        return getRootSheet(sheet.source)
    elif isinstance(sheet.source, Path):
        return sheet
    else:
        error('no apparent root GitStatus')


def getRepoPath(p):
    'Return path at p or above which has .git subdir'
    p = Path(p.abspath())
    if p.joinpath('.git').is_dir():
        return p
    if p.fqpn in ['/','']:
        return None
    return getRepoPath(p.parent)


class GitContext:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra_args = []
#        assert isinstance(self.source, (Path, GitSheet))

    def _git_args(self):
        worktree = getRepoPath(getRootSheet(self).source)  # Path
        return [
            '--git-dir', worktree.joinpath('.git').abspath(),
            '--work-tree', worktree.abspath()
        ]

    @Sheet.name.setter
    def name(self, name):
        self._name = name.strip()

    def git_iter(self, *args, **kwargs):
        yield from git_iter(*self._git_args(), *args, **kwargs)

    def git_lines(self, *args, **kwargs):
        return git_lines(*self._git_args(), *args, **kwargs)

    def git_all(self, *args, **kwargs):
        return git_all(*self._git_args(), *args, **kwargs)

    @asyncthread
    def git(self, *args, **kwargs):
        'Run git command that modifies the repo'
        args = list(args) + self.extra_args
        self.extra_args.clear()

        for line in self.git_lines(*args, **kwargs):
            status(line)

        if isinstance(self.source, GitSheet):
            self.source.reload()

        self.reload()

    @staticmethod
    def inProgress():
        if Path('.git/rebase-merge').exists() or Path('.git/rebase-apply/rebasing').exists():
            return 'rebasing'
        elif Path('.git/rebase-apply').exists():
            return 'applying'
        elif Path('.git/CHERRY_PICK_HEAD').exists():
            return 'cherry-picking'
        elif Path('.git/MERGE_HEAD').exists():
            return 'merging'
        elif Path('.git/BISECT_LOG').exists():
            return 'bisecting'
        return ''


    def abortWhatever(self):
        inp = self.inProgress()
        if inp.startswith('cherry-pick'):
            self.git('cherry-pick', '--abort')
        elif inp.startswith('merg'):
            self.git('merge', '--abort')
        elif inp.startswith('bisect'):
            self.git('bisect', 'reset')
        elif inp.startswith('rebas') or inp.startswith('apply'):
            self.git('rebase', '--abort')  # or --quit?
        else:
            status('nothing to abort')

    @property
    def rootSheet(self):
        if isinstance(self.source, GitSheet):
            return self.source.rootSheet
        return self

    def leftStatus(self):
        inp = self.inProgress()
        ret = ('[%s] ' % inp) if inp else ''
        if hasattr(self.rootSheet, 'branch'):
            ret += '‹%s%s› ' % (self.rootSheet.branch, self.rootSheet.remotediff)

        return ret + super().leftStatus()

    def git_apply(self, hunk, *args):
        self.git("apply", "-p0", "-", *args, _in="\n".join(hunk[7]) + "\n")
        status('applied hunk (lines %s-%s)' % (hunk[3], hunk[3]+hunk[4]))

class GitSheet(GitContext, Sheet):
    pass

GitSheet.addCommand('f', 'git-force', 'extra_args.append("--force"); status("--force next git command")', 'add --force to next git command')

options.set('disp_note_none', '', GitSheet)
