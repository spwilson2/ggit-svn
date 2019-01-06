#!/usr/bin/python
from __future__ import print_function

import argparse
# import pprint
import os
import inspect
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading

VERSION = 0

GIT_CONFIG_FILE = 'config'
GIT_SVN_PFX = 'git-svn/'
GGIT_CONFIG_BRANCH = 'ggit-config'

NON_GIT_HEAD = '''
The HEAD is not pointing to a git-svn branch.

In order to setup a .svn folder, switch branches with ggit:

    ggit switch <branch-name>

'''
DIRTY_WORKING = '''
Refusing to change branches, working directory is dirty.

Consider stashing changes or re-running with --force

    git stash --include-untracked

'''

BRANCH_DOES_NOT_EXIST = """
Warning: git-svn branch '{remote}' doesn't exist.
Unable to resolve fetch for '{local}''
"""

REMOTE_TYPES = {
    'file:///',
    'http://',
    'git://',
    'svn://',
    'ssh://',
}

python_version = sys.version_info
python_version = python_version[0]


class Chdir:
    def __init__(self, directory):
        self._dir = directory

    def __enter__(self):
        self._olddir = os.getcwd()
        os.chdir(self._dir)
        return self

    def __exit__(self, *args):
        if hasattr(self, '_olddir'):
            os.chdir(self._olddir)


class Status:
    NoConfigBranch = 1
    NotInRepo = 2
    PathExists = 3
    RepoIsDirty = 4
    AmbiguousReference = 5
    NoSuchCommit = 6
    NonSvnCommit = 7
    InvalidArguments = 8
    MissingGitSvn = 9
    NoSvnRepo = 10
    Error = 100


class GGitExcpetion(Exception):
    def __init__(self, status, msg=None):
        self.status = status
        self.msg = '\n' + str(msg)
        Exception.__init__(self, msg)


class TemporaryDirectory(object):
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        return self

    def __exit__(self, *args):
        shutil.rmtree(self.dir)


class _CallWrapper(object):
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs

    def __enter__(self):
        self._pipes = []

        # Setup devnull as the default output pipe.
        for outfd in ('stderr', 'stdout'):
            if outfd not in self.kwargs:
                null = open(os.devnull, 'w')
                self._pipes.append(null)
                self.kwargs[outfd] = null

        if python_version > 2:
                self.kwargs['universal_newlines'] = True

        if 'shell' not in self.kwargs:
            self.kwargs.setdefault('executable', 'bash')
            self.kwargs.setdefault('shell', True)

        # TODO Add logging for the call.
        return self

    def call(self, func):
        # pprint.pprint(('%s: ' % func.__name__, self.command, self.kwargs),
        #                stream=sys.stderr)
        return func(self.command, **self.kwargs)

    def __exit__(self, *args):
        for p in self._pipes:
            p.close()


def call_output(command, **kwargs):
    if 'stdout' in kwargs:
        raise ValueError('stdout argument not allowed, it will be overridden.')
    with _CallWrapper(command, **kwargs) as cw:
        del cw.kwargs['stdout']
        return cw.call(Callback(subprocess.check_output))


def forward_check_call(command, **kwargs):
    kwargs.setdefault('stdout', None)
    kwargs.setdefault('stderr', None)
    with _CallWrapper(command, **kwargs) as cw:
        return cw.call(Callback(subprocess.check_call))


def check_call(command, **kwargs):
    with _CallWrapper(command, **kwargs) as cw:
        return cw.call(Callback(subprocess.check_call))


def call_status(command, **kwargs):
    with _CallWrapper(command, **kwargs) as cw:
        return cw.call(Callback(subprocess.call))


class CallbackError(TypeError):
    def __init__(self, msg, original_exe):
        self.msg = msg
        self.exe = original_exe
        TypeError.__init__(self, msg)


class Callback(object):
    '''A class dedicated to making callbacks more debuggable.'''

    FAIL_TEMPLATE = textwrap.dedent('''
    Unable to bind arguments to callback.

    Arguments:

        Args: {ARGS}

        Kwargs: {KWARGS}

    Callback created here:
    File "{FILE}", line {LINE}, in {FUNC}
      {CODE}
    ''')

    def __init__(self, func):
        self.func = func
        self.caller_frame = inspect.stack()[1]

    def __call__(self, *args, **kwargs):
        try:
            inspect.getcallargs(self.func, *args, **kwargs)
            return self.func(*args, **kwargs)
        except TypeError as e:
            msg = self.FAIL_TEMPLATE.format(
                    CODE=self.caller_frame[4][0],
                    FUNC=self.caller_frame[3],
                    LINE=self.caller_frame[2],
                    FILE=self.caller_frame[1],
                    ARGS=args,
                    KWARGS=kwargs,
                    )
            raise CallbackError(msg, e)


###############################################################################
#
# Lower level VCS utilities
#
###############################################################################

class Svn:
    @staticmethod
    def empty_checkout(url, directory):
        try:
            check_call(
                'svn checkout --depth=empty "{url}" "{tmpdir}"'
                .format(url=url, tmpdir=directory),
                stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise GGitExcpetion(Status.NoSvnRepo, textwrap.dedent(
                """
                {err}

                Unable to svn checkout '{url}' does the repository exist?
                """.format(url=url, err=e.output)))


class Git:
    UUID_RE = '[0-9a-z]'

    @staticmethod
    def staging_is_dirty():
        ret = call_status('git diff --cached --quiet --ignore-submodules --')
        return bool(ret)

    @staticmethod
    def working_is_dirty():
        ret = call_status('git ls-files :/ --other --exclude-standard'
                          '| sed q1',
                          shell=True)
        return bool(ret)

    @staticmethod
    def ref_exists_on_remote(remote, ref):
        ret = call_status('git ls-remote -q --exit-code {remote} {ref}'
                          .format(remote=remote, ref=ref))
        return not bool(ret)

    @staticmethod
    def is_dirty():
        return any((Git.staging_is_dirty(), Git.working_is_dirty()))

    @staticmethod
    def _in_a_repo():
        try:
            Git.toplevel()
        except GGitExcpetion as e:
            return (False, e.msg)
        return (True, None)

    @staticmethod
    def in_a_repo():
        (status, msg) = Git._in_a_repo()
        return status

    @staticmethod
    def enforce_in_repo():
        (status, msg) = Git._in_a_repo()
        if not status:
            raise GGitExcpetion(Status.NotInRepo, msg)

    @staticmethod
    def list_heads():
        remotes = call_output('git remote show -n')
        remotes = remotes.splitlines()

        remotes.append(None)
        heads = {remote: [] for remote in remotes}
        for remote in remotes:
            cmd = ('git for-each-ref refs/remotes/{remote}/'
                   ' --format "%(refname)"')
            refs = call_output(cmd.format(remote=remote))
            for ref in refs.splitlines():
                # e.g. refs/remotes/origin/git-svn/trunk
                # Remove refs/remotes
                ref = Git.branch_without_refs(ref)
                # remove <remote>/
                ref = ref[len(remote):].lstrip('/')
                heads[remote].append(ref)

        local = call_output(
            'git show-ref --heads'
            ' || $(git rev-parse --show-toplevel > /dev/null)')
        for line in local.splitlines():
            if line:
                _, ref = line.split()
                ref = Git.branch_without_refs(ref)
                heads[None].append(ref)
        return heads

    @staticmethod
    def commit_exists(hashish):
        ret = call_status('git cat-file -e {hashish}^{{commit}}'
                          .format(hashish=hashish))
        return not bool(ret)

    @staticmethod
    def branch_without_refs(branch):
        heads = 'refs/heads/'
        if branch.startswith(heads):
            return branch[len(heads):]
        remotes = 'refs/remotes/'
        if branch.startswith(remotes):
            return branch[len(remotes):]
        return branch

    @staticmethod
    def latest_svn_commit(hashish):
        '''
        Check the git log for the latest git-svn log entry. Return
        a GitSvnLogEntry object for the entry. If no such entry is found return
        None.
        '''
        kwargs = {
                'stdout': subprocess.PIPE,
                'shell': False,
        }
        command = ('git log %s' % hashish).split()

        entry = [None]  # Keep a mutable object so thread can mutate it.

        with _CallWrapper(command, **kwargs) as cw:
            git = cw.call(Callback(
                lambda *args, **kwargs: subprocess.Popen(*args, **kwargs)))
            line_iter = iter(git.stdout.readline, '')

            def get_entry(line_iter, entry_list):
                entry_list[0] = GitSvnLogEntry.find_entry(line_iter)

            thread = threading.Thread(target=get_entry,
                                      args=(line_iter, entry))
            thread.start()
            thread.join()
            git.communicate()
        return entry[0]

    @staticmethod
    def toplevel():
        try:
            output = call_output('git rev-parse --show-toplevel',
                                 stderr=subprocess.STDOUT).rstrip()
            return output
        except subprocess.CalledProcessError as e:
            raise GGitExcpetion(Status.NotInRepo, e.output)

    @staticmethod
    def dot_git():
        return os.path.join(Git.toplevel(), '.git')

    @staticmethod
    def basename(repo):
        if repo.endswith('.git'):
            repo = repo[:len(repo) - len('.git')]
        repo = repo.rstrip('/')
        base = os.path.basename(repo)
        return re.sub(r'.git$', '', base)

    @staticmethod
    def find_branch(search):
        '''
        Check that the branch exists locally or as a remote by if not local,
        but in multiple remotes fail.
        '''
        remote_heads = Git.list_heads()
        local_heads = remote_heads.pop(None)
        if search in local_heads:
            return search

        matches = []
        for remote, heads in remote_heads.items():
            for head in heads:
                if head == search:
                    if remote is not None:
                        head = os.path.join(remote, head)
                    matches.append(head)

        if len(matches) > 1:
            raise GGitExcpetion(Status.AmbiguousReference,
                                ', '.join(matches))
        if matches:
            return matches[0]

    @staticmethod
    def enforce_gitsvn():
        ret = call_status('git svn help')
        if ret != 0:
            raise GGitExcpetion(Status.MissingGitSvn,
                                'Unable to use "git svn help".'
                                ' Is it installed?')


class GitSvnLogEntry:
    # git-svn-id: \
    # http://rtosvc/trunk/rtos@274190 d5d84855-3516-0410-9f1e-893281b4b339
    SVN_ENTRY_REGEX = re.compile(
            r'\s*git-svn-id:\s+(?P<url>[^@]+)@(?P<rev>\d+)'
            r'\s+(?P<svn_uuid>[0-9a-z\-]+)')

    # commit 3505cb1adc010f7dcf18247846fe09d7353acb9c
    GIT_ENTRY_REGEX = re.compile('commit (?P<hash>[0-9a-z]+)')

    def __init__(self, url, rev, git_uuid, svn_uuid):
        self.url = url
        self.revision = rev
        self.git_hash = git_uuid
        self.git_svn_hash = svn_uuid

    @classmethod
    def find_entry(cls, line_iter):
        # Keep a running log of git hashes,
        # Since git hashes are before git-svn entries, once we find a git-svn
        # entry the most recent git hash goes with the svn entry.
        for line in line_iter:
            git_match = cls.GIT_ENTRY_REGEX.match(line)
            svn_match = cls.SVN_ENTRY_REGEX.match(line)
            if git_match:
                git_uuid = git_match.groups(1)
            if svn_match:
                match = svn_match.groupdict()
                match['git_uuid'] = git_uuid
                return GitSvnLogEntry(**match)
        return None


###############################################################################
#
# Config file items
#
###############################################################################


class GitSvnRemote():
    CONFIG_SECTION = 'svn-remote'

    def __init__(self, name, url, fetches):
        '''
        :param url:
            Url of the svn remote to pull both git-svn and svn updates from
        :param fetches:
            A (path, ref) tuple used to configure git-svn fetching.
        '''
        self.name = name
        self.url = url

        self.fetches = fetches
        self.branches = []
        self.urls = []

        for fetch in fetches:
            (path, fetch_ref) = fetch.split(':')

            # TODO Do I want to remove refs/(heads)|(remotes)/
            # from branch name?
            self.branches.append(fetch_ref)
            self.urls.append(os.path.join(url, path))

    def as_config_entries(self):
        def entry(key, value):
            return GitConfig.Entry(self.CONFIG_SECTION, self.name, key, value)

        entries = []
        for fetch in self.fetches:
            entries.append(entry('fetch', fetch))
        entries.append(entry('url', self.url))
        return entries


class GitConfig(object):
    _ENTRY_RE = re.compile('(?P<path>[^=]+)=(?P<val>.*)')

    class Entry(object):

        @classmethod
        def _path_to_pair(cls, path):
            path = path.split('.')
            subsection = None
            if len(path) == 3:
                section, subsection, key = path
            elif len(path) == 2:
                section, key = path
            else:
                raise ValueError(
                    'Path must be a 2 or 3 tuple separated by "."')
            return (section, subsection, key)

        @classmethod
        def from_path(cls, path, value):
            (section, subsection, key) = cls._path_to_pair(path)
            return cls(section, subsection, key, value)

        @property
        def path(self):
            path = filter(None, (self.section, self.subsection, self.key))
            return '.'.join(path)

        def __init__(self, section, subsection, key, value):
            self.value = value
            self.section = section
            self.subsection = subsection
            self.key = key

    def __init__(self, entries=None):
        if entries is None:
            entries = []
        self.entries = entries

    def get_vals(self, section, subsection, key):
        values = []
        for e in self.entries:
            if (e.section, e.subsection, e.key) == (section, subsection, key):
                values.append(e.value)
        return values if values else None

    def list_subsections(self, section):
        return [e.subsection for e in self.entries if e.section == section]

    def write(self, filename):
        paths = {e.path for e in self.entries}
        for path in paths:
            # Ignore failed remove
            call_status('git config --file {filename} --unset-all {path}'
                        .format(filename=filename, path=path))

        for entry in self.entries:
            check_call('git config --file {filename} --add {path} {val}'
                       .format(filename=filename, path=entry.path,
                               val=entry.value))

    @classmethod
    def from_str(cls, string):
        self = GitConfig()
        self.entries = []
        for line in string.splitlines():
            match = self._ENTRY_RE.search(line)
            if match:
                val = match.group('val')
                path = match.group('path')
                entry = self.Entry.from_path(path, val)
                self.entries.append(entry)
        return self

    @classmethod
    def from_blob(cls, blob):
        try:
            output = call_output('git config -l --blob %s' % blob)
        except subprocess.CalledProcessError as e:
            raise GGitExcpetion(Status.Error, textwrap.dedent(
                '''

                {err}

                Failed to "git config -l --blob {blob}"

                Does the branch exist and contain the config file?
                '''
                .format(blob=blob, err=e.output)))
        return cls.from_str(output)

    @classmethod
    def from_file(cls, path):
        output = call_output('git config -l --file %s' % path)
        return cls.from_str(output)


class GGitConfig():
    def __init__(self, dot_git):
        self._remotes = []
        self._git_config = GitConfig()
        self.dot_git = dot_git

    def _init_config(self, config):
        self._git_config = config

        remote_sect = GitSvnRemote.CONFIG_SECTION
        subsections = self._git_config.list_subsections(remote_sect)
        for subsection in subsections:
            urls = self._git_config.get_vals(remote_sect, subsection, 'url')
            if urls is None or len(urls) > 1:
                raise ValueError('git-svn remote must have single url')
            url = urls[0]

            fetches = self._git_config.get_vals(remote_sect, subsection,
                                                'fetch')
            if fetches is None:
                raise ValueError('git-svn remote must have a url')
            self._remotes.append(GitSvnRemote(subsection, url, fetches))

    @classmethod
    def from_branch(cls, branch):
        self = cls(Git.dot_git())
        self._init_config(GitConfig.from_blob(branch + ':config'))
        return self

    @classmethod
    def from_dot_git(cls, dot_git):
        self = cls(dot_git)
        self._git_config = GitConfig.from_file(self.config_path)
        return self

    def write(self, path=None):
        if path is None:
            path = self.config_path
        self._git_config.write(path)

    def iter_remotes(self):
        return iter(self._remotes)

    @classmethod
    def from_fetch_list(cls, dot_git, url, fetches):
        self = cls(dot_git)
        remote = GitSvnRemote('svn', url, fetches)
        self._add_remote(remote)
        return self

    @classmethod
    def url_to_svn_cache(cls, dot_git, url):
        # Remove the protocol from the url
        for remote_type in REMOTE_TYPES:
            if url.startswith(remote_type):
                url = url[len(remote_type):]
                break

        return os.path.join(cls.get_dot_svn_path(dot_git), url)

    @property
    def config_path(self):
        return os.path.join(self.dot_git, 'config')

    @property
    def ggit_path(self):
        return self.get_ggit_path(self.dot_git)

    @staticmethod
    def get_ggit_path(dot_git):
        return os.path.join(dot_git, 'ggit')

    @classmethod
    def get_dot_svn_path(cls, dot_git):
        return os.path.join(cls.get_ggit_path(dot_git), 'svn')

    def _add_remote(self, remote):
        entries = remote.as_config_entries()
        self._git_config.entries.extend(entries)
        self._remotes.append(remote)


###############################################################################
#
# High level operations
#
###############################################################################

class GGit(object):
    @staticmethod
    def switch_svn(dot_git, url, rev):
        '''
        NOTE: There are a couple strange details when it comes to implementing
        a fast switch call for svn.

        One option would be to use svn switch.  There are a couple problems
        with this approach.  First, .svn doesn't do a good job of keeping a lot
        of state around, so on very different branches a switch will
        effectively be a new clone.  Second, switch is still pretty broken when
        it comes to switching svn externals across branches.  This often leads
        to a broken .svn and would confuse users.

        The second strange choice we have to deal with is the fact that
        subversion doesn't use .svn if it is a symlink. So, rather than being
        able to create a single symlink, we symlink all files in the .svn
        folder.

        The one issue that could arise with this approach is if subversion
        creates new files in the .svn folder they will be deleted on switch. We
        only maintain the folders and files from the original svn checkout.
        '''
        svn_path = GGitConfig.url_to_svn_cache(dot_git, url)

        if os.path.exists('.svn'):
            shutil.rmtree('.svn')
        # Create a .svn dir and symlink the files because subversion won't look
        # at a symlink for it's .svn dir.
        os.mkdir('.svn')
        os.mkdir('.svn/tmp')

        # Svn also has a tendency to remove the tmp dir at random times. It
        # does so by calling a rmdir routine, we will just let it do its thing
        # and not link this.
        dirs = list(os.listdir(svn_path))
        dirs.remove('tmp')
        for f in dirs:
            os.symlink(os.path.join(svn_path, f), os.path.join('.svn', f))

        # Run svn update to set the revision and depth.
        check_call('svn cleanup')
        forward_check_call('svn update --force --accept working'
                           ' --set-depth=infinity -r {rev}'
                           ''.format(rev=rev))
        # Run svn revert to clean to a known svn state.
        check_call('svn revert -R .')

    @classmethod
    def _backup_ggit(cls, ggit_path):
        if not os.path.exists(ggit_path):
            os.mkdir(ggit_path)
        else:
            backups = os.path.join(ggit_path, 'backups')
            if not os.path.exists(backups):
                os.mkdir(backups)
            backup_dir = tempfile.mkdtemp(dir=backups)
            old_files = os.listdir(ggit_path)
            old_files = (os.path.join(ggit_path, f) for f in old_files
                         if f != 'backups')
            for f in old_files:
                shutil.move(f, backup_dir)

    @classmethod
    def setup_git_svn_config(cls, config):
        # Create a .git/ggit/ folder backing up previous contents
        cls._backup_ggit(config.ggit_path)

        # Add .svn to the .git/info/exclude folder.
        exclude = os.path.join(config.dot_git, 'info', 'exclude')
        check_call('echo .svn >> ' + exclude)

        config.write()

    @classmethod
    def setup_empty_svn(cls, config):
        '''
        Create empty svn checkout indexes for each remote in the ggit config.
        '''
        for remote in config.iter_remotes():
            for svn_url in remote.urls:
                svn_path = GGitConfig.url_to_svn_cache(config.dot_git, svn_url)
                if os.path.lexists(svn_path):
                    shutil.rmtree(svn_path)
                with TemporaryDirectory() as td:
                    Svn.empty_checkout(svn_url, td.dir)
                    shutil.copytree(os.path.join(td.dir, '.svn'), svn_path)


###############################################################################
#
# User level commands
#
###############################################################################


class Subcommand(object):
    name = None

    @classmethod
    def _map_subcommand(cls, command):
        commands = {}
        for subclass in cls.__subclasses__():
            subcommand = cls.subcommand_name(subclass)
            assert subcommand not in commands
            commands[subcommand] = subclass

        return commands[command.lower()]

    @classmethod
    def run_command(cls, command, args):
        subclass = cls._map_subcommand(command)
        return subclass().run(args)

    @staticmethod
    def subcommand_name(subcommand):
        if subcommand.name is not None:
            return subcommand.name
        return subcommand.__name__.lower()

    @classmethod
    def init_parsers(cls, subparsers):
        subclasses = cls.__subclasses__()
        for subclass in subclasses:
            subparser = subparsers.add_parser(cls.subcommand_name(subclass),
                                              help=subclass.__doc__)
            subclass().init_parser(subparser)

    def init_parser(self, parser):
        pass

    def run(self, args):
        raise NotImplementedError


class Clone(Subcommand):
    def init_parser(self, parser):
        parser.add_argument('repository')
        parser.add_argument('directory', default=None, nargs='?')
        parser.add_argument('--config-branch', default=GGIT_CONFIG_BRANCH)
        parser.add_argument(
            '--remap', default='%s:' % GIT_SVN_PFX,
            help='Replace an svn-remote fetch path when fetching'
            ' from origin')
        parser.add_argument('--fetch-gitsvn', default=True,
                            action='store_false')

    def run(self, args):
        repository = args['repository']
        directory = args['directory']
        config_branch = args['config_branch']
        fetch_svn = args['fetch_gitsvn']
        remap = args['remap']
        try:
            remap_src, remap_dst = remap.split(':')
        except ValueError:
            raise GGitExcpetion(Status.InvalidArguments,
                                '--remap expects "[]:[]" ')

        if not Git.ref_exists_on_remote(repository, config_branch):
            print(textwrap.dedent(
                """
                The repository '%s' does not contain ggit config branch '%s'
                The branch can be set with the --config-branch optioon.
                """
                % (repository, config_branch)))
            raise GGitExcpetion(Status.NoConfigBranch)

        # If no local folder name provided, use git's naming scheme.
        if directory is None:
            directory = Git.basename(repository)

        if os.path.exists(directory):
            raise GGitExcpetion(Status.PathExists,
                                "Destination: '%s' already exists" % directory)

        forward_check_call('git clone {repo} {directory}'
                           .format(repo=repository, directory=directory))

        with Chdir(directory):
            config = Configure().run(args)

            if fetch_svn:
                # TODO Refactor this config logic out of a high level command
                branches = []
                for remote in config.iter_remotes():
                    for branch in remote.branches:
                        branch = Git.branch_without_refs(branch)
                        remote = re.sub(remap_src, remap_dst, branch, count=1)
                        branches.append((remote, branch))

                # Setup git svn
                for (remote, local) in branches:
                    if Git.ref_exists_on_remote('origin', remote):
                        forward_check_call('git fetch origin "refs/heads/%s:'
                                           'refs/remotes/%s"'
                                           % (remote, local))
                    else:
                        print(BRANCH_DOES_NOT_EXIST.format(
                            remote=remote, local=local))


class Switch(Subcommand):
    def init_parser(self, parser):
        parser.add_argument('hashish')
        parser.add_argument('--force', action='store_true')

    def run(self, args):
        force = args['force']
        hashish = args['hashish']
        force = '--force' if force else ''

        # Check that we are in a git repo.
        Git.enforce_in_repo()

        # Check that the working directory is clean.
        if Git.working_is_dirty() and not force:
            raise GGitExcpetion(Status.RepoIsDirty, DIRTY_WORKING)

        if not Git.commit_exists(hashish):
            raise GGitExcpetion(
                Status.NoSuchCommit,
                "The given commit '%s' doesn't exist."
                % hashish)

        # Search the log for latest git-svn commit
        log_entry = Git.latest_svn_commit(hashish)

        if log_entry is None:
            raise GGitExcpetion(
                Status.NonSvnCommit,
                "The given commit '%s' isn't on a git-svn repository."
                % hashish)

        with Chdir(Git.toplevel()):
            # Run git checkout to complete head change.
            check_call('git checkout %s %s' % (force, hashish))

            # Check the .git/ggit/config file for the remote to .svn mapping.
            GGit.switch_svn(Git.dot_git(), log_entry.url, log_entry.revision)

            # run checkout once more time to undo git revert attribute changes
            check_call('git checkout %s %s -- :/' % (force, hashish))


class Sync(Subcommand):
    '''
    Update the svn repository to the latest svn commit on the current HEAD
    '''
    def init_parser(self, parser):
        parser.add_argument('--force', action='store_true')

    def run(self, args):
        force = args['force']
        Git.enforce_in_repo()

        log_entry = Git.latest_svn_commit('HEAD')
        if log_entry is None:
            raise GGitExcpetion(
                Status.NonSvnCommit,
                "The current git branch doesn't contain git-svn history.")

        # Check that the working directory is clean.
        if Git.working_is_dirty() and not force:
            raise GGitExcpetion(Status.RepoIsDirty, DIRTY_WORKING)

        with Chdir(Git.toplevel()):
            # Check the .git/ggit/config file for the remote to .svn mapping.
            GGit.switch_svn(Git.dot_git(), log_entry.url, log_entry.revision)


class Configure(Subcommand):
    def init_parser(self, parser):
        parser.add_argument('--config-branch', default=GGIT_CONFIG_BRANCH)

    def run(self, args):
        '''
        Read from the ggit-config branch and configure the git repository to
        match.
        '''
        config_branch = args['config_branch']
        # Check that we are in a git repo.
        Git.enforce_in_repo()

        branch = Git.find_branch(config_branch)
        config = GGitConfig.from_branch(branch)

        with Chdir(Git.toplevel()):
            GGit.setup_git_svn_config(config)
            GGit.setup_empty_svn(config)

            # Check if the current HEAD is a git svn branch.
            log_entry = Git.latest_svn_commit('HEAD')
            if log_entry is None:
                print(NON_GIT_HEAD)
            else:
                GGit.switch_svn(config.dot_git, log_entry.url,
                                log_entry.revision)
            return config


class Init(Subcommand):
    '''
    Initialze a git repsitory fetching svn commits and setting up ggit.

    It's recommended to use this within a bare repo or an empty folder.
    '''
    def init_parser(self, parser):
        parser.add_argument('--config-branch', default=GGIT_CONFIG_BRANCH)
        parser.add_argument('-r', '--revision', default='0')
        parser.add_argument('--remote-base',
                            default='refs/remotes/git-svn/svn')
        parser.add_argument('url')
        parser.add_argument('remote-names', nargs='+',
                            help='E.g. branches/ap/trunk:aptrunk')

    def _parse_remotes(self, remotes, base):
        fetches = []
        for item in remotes:
            match = re.search('([^:]+):([^:]+)', item)
            if not match:
                raise ValueError('Argument incorrect format')
            new_ref = os.path.join(base, match.group(2))

            fetches.append(':'.join((match.group(1), new_ref)))
        return fetches

    def run(self, args):
        url = args['url']
        remotes = args['remote-names']
        rev = args['revision']
        conf_branch = args['config_branch']
        base = args['remote_base']

        fetches = self._parse_remotes(remotes, base)

        Git.enforce_gitsvn()

        if not Git.in_a_repo():
            forward_check_call('git init')

        if Git.staging_is_dirty():
            raise GGitExcpetion(Status.RepoIsDirty, DIRTY_WORKING)

        # TODO Save the original HEAD so we can restore it.

        with Chdir(Git.toplevel()):
            # If branch already exists, just checkout naturally.
            if Git.find_branch(conf_branch) is not None:
                forward_check_call('git checkout {branch}'
                                   .format(branch=conf_branch))
            else:
                forward_check_call('git checkout --orphan {branch}'
                                   .format(branch=conf_branch))
            check_call('git reset')

            # Create our config file
            config = GGitConfig.from_fetch_list(Git.dot_git(), url, fetches)
            config.write(GIT_CONFIG_FILE)

            # Check if the file is the same as the config if so, don't try
            # to commit.
            check_call('git add {config}'.format(config=GIT_CONFIG_FILE))
            if Git.staging_is_dirty():
                forward_check_call('git commit -m'
                                   ' "ggit-autocommit: Create config file"')
            else:
                check_call('git reset')

            Configure().run(args)
            forward_check_call('git svn fetch -r {revision}:HEAD'
                               .format(revision=rev))


class Push(Subcommand):
    '''
    Push git-svn branches and ggit-config to a remote.
    '''
    def init_parser(self, parser):
        parser.add_argument('remote')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--config-branch', default=GGIT_CONFIG_BRANCH)
        # TODO support partial push

    def run(self, args):
        remote = args['remote']
        config_branch = args['config_branch']

        force = args['force']
        force = '--force' if force else ''

        Git.enforce_in_repo()

        # Push the config branch
        local_ref = Git.find_branch(config_branch)
        forward_check_call(
            'git push {force} {remote} {local_ref}:refs/heads/{config_branch}'
            .format(local_ref=local_ref, config_branch=config_branch,
                    remote=remote, force=force))

        # TODO: Read the ggitconfig for branch names
        forward_check_call(
            'git push {force} {remote} "refs/remotes/{pfx}*'
            ':refs/heads/*"'.format(
                force=force, remote=remote, pfx=GIT_SVN_PFX))


class GenerateIgnore(Subcommand):
    '''Generate an ignore file, printing it to stdout'''
    name = 'generate-ignore'

    def init_parser(self, parser):
        # TODO Accept optional argument of gitignroe manual file
        pass

    @staticmethod
    def get_externs():
        # Get a list of svn externals
        # (Parse svn st for externals)
        externs = []
        changelist = call_output('svn st')
        for line in changelist.splitlines():
            # e.g
            #       X folder/thats/external
            match = re.search(r'^\s*X\s*(.*)$', line)
            if match:
                externs.append(match.group(1))
        return externs

    def run(self, args):
        externs = self.get_externs()
        svn_ignores = call_output('git svn show-ignore')

        Git.enforce_gitsvn()
        Git.enforce_in_repo()

        def is_not_comment(string):
            return not string.startswith('#')

        ignores = (line for line in svn_ignores.splitlines() if
                   is_not_comment(line))

        for line in sorted(set(externs) | set(ignores)):
            print(line)

# TODO Command to forward args to git svn fetch
# TODO Command to forward args to git svn rebase, and run svn update afterwards
# TODO Command to create a gcrucible diff
# TODO Command to initialize a new repository with svn url and branches. (REQ)
# TODO Command to automate updating a repository with git-svn data (REQ)


def parse_args(argv):
    name = argv[0]
    args = argv[1:] if len(argv) > 1 else []

    parser = argparse.ArgumentParser(prog='ggit')
    subparsers = parser.add_subparsers(dest='command')
    Subcommand.init_parsers(subparsers)
    options = parser.parse_args()
    parser.parse_args(args)
    return options.command, vars(options)


def main(argv):
    command, args = parse_args(argv)
    # TODO Assert user had git-svn installed.
    # Check the subparser used, pass the args to the class named after it.
    Subcommand.run_command(command, args)
    return 0


def entrypoint():
    return main(sys.argv)


if __name__ == '__main__':
    entrypoint()
