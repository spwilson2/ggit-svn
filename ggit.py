#!/usr/bin/python

import argparse
import itertools
import logging
import pprint
import tempfile
import os
import re
import shutil
import sys
import subprocess

class Cwd:
    def __init__(self, directory):
        self._dir = directory

    def __enter__(self):
        self._olddir = os.getcwd()
        os.chdir(self._dir)
        return self

    def __exit__(self, *args):
        if hasattr(self,'_olddir'):
            os.chdir(self._olddir)

class TemporaryDirectory(object):
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        return self

    def __exit__(self, *args):
        shutil.rmtree(self.dir)

def call(*args, **kwargs):
    if 'shell' not in kwargs:
        kwargs.setdefault('executable', 'bash')
        kwargs.setdefault('shell', True)

    pprint.pprint(('Call: ', args, kwargs), stream=sys.stderr)
    return subprocess.check_output(*args, **kwargs)

def get_revision():
    head_entry = call('git svn log --limit 1')
    head_entry = head_entry.splitlines()
    match = re.search('r(\d*)', head_entry[1])
    assert match
    revision = match.group(0)
    return revision

def git_update(revision):
    call('svn revert -q -R .')
    call('svn update -q --force --accept theirs-full --set-depth=infinity -r %s'
            % revision)
    call('git checkout --force')

class Subcommand(object):

    @classmethod
    def _map_subcommand(cls, command):
        commands = {}
        for subclass in cls.__subclasses__():
            subcommand = subclass.__name__.lower()
            assert subcommand not in commands
            commands[subcommand] = subclass

        return commands[command.lower()]

    @classmethod
    def run_command(cls, command, args):
        subclass = cls._map_subcommand(command)
        return subclass().run(args)

    @classmethod
    def init_parsers(cls, subparsers):
        subclasses = cls.__subclasses__()
        for subclass in subclasses:
            subparser = subparsers.add_parser(subclass.__name__.lower())
            subclass().init_parser(subparser)

    def init_parser(self, parser):
        pass

    def run(self, args):
        raise NotImplemented


class Clone(Subcommand):
    '''
    Command used to clone an existing ggit repo. 
    (A mixed git-svn repository)
    '''

    def init_parser(self, parser):
        parser.add_argument('remote')
        parser.add_argument('destination')
        #parser.add_argument('forward', nargs=argparse.REMAINDER)

    def run(self, args):
        # TODO Add support for cloning the svn
        dest = args['destination']

        call('git clone'.split() + [args['remote'], dest], shell=False)
        with Cwd(dest):
            call('git show origin/git-svn-config:config >> .git/config')
            call('git fetch origin "refs/heads/svn/*:refs/remotes/rtosvc/svn/*"')
            call('git svn fetch rtosvc')
            svn_url = call('git svn info --url')
            svn_url = svn_url.rstrip()

            # Checkout svn to a temporary directory
            # Copy over the .svn
            # Update the svn repo
            # Restore files updated with the svn revert metadata bs.
            with TemporaryDirectory() as td:
                checkout_cmd = 'svn co --depth=empty'.split() + \
                        [svn_url, td.dir]
                call(checkout_cmd, shell=False)
                call('cp -r'.split() + [os.path.join(td.dir, '.svn'), '.svn'], shell=False)
            rev = get_revision()
            git_update(rev)


class Setup(Subcommand):
    '''
    Command used to fetch and initialize a ggit repo from an svn path.
    (Create a mixed git and svn repository from an svn path.)

    Also used to initialize a new repository on a bare head.
    '''
    pass


class Switch(Subcommand):
    '''
    Change branches and update the hidden svn repository to match the branch's
    svn revision.
    '''
    pass


class Rebase(Subcommand):
    '''
    Rebase the current branch on top of a the latest fetched git-svn.
    '''
    def init_parser(self, parser):
        pass

    def run(self, args):
        call('git svn rebase')
        Update().run(args)


class Pull(Subcommand):
    '''
    Fetch the latest svn changes and rebase the current branch on top of them.
    '''
    def run(self, args):
        call('git svn fetch')
        Rebase().run(args)


class Update(Subcommand):
    '''
    Update .svn to the current branch's svn revision and branch.
    '''
    def init_parser(self, parser):
        pass

    def run(self, args):
        rev = get_revision()
        git_update(rev)

class GenerateIgnore(Subcommand):
    '''Generate an ignore file'''

    def init_parser(self, parser):
        # TODO Accept optional argument of gitignroe manual file
        pass

    @staticmethod
    def get_externs():
        # Get a list of svn externals
        # (Parse svn st for externals)
        externs = []
        changelist = call('svn st')
        for line in changelist.splitlines():
            match = re.search(r'^\s*X\s*(.*)$', line)
            if match:
                externs.append(match.groups(0))
        return externs

    def run(self, args):
        externs = self.get_externs()
        svn_ignores = call('git svn show-ignore')
        svn_ignores = filter(lambda string: not string.startswith('#'), svn_ignores.splitlines())
        for line in sorted(set(externs) and set(svn_ignores)):
            print(line)


def parse_args():
    parser = argparse.ArgumentParser(prog='ggit')
    subparsers = parser.add_subparsers(dest='command')
    Subcommand.init_parsers(subparsers)
    options = parser.parse_args()
    return options.command, vars(options)

def main(command, args):
    # TODO Assert user had git-svn installed.
    # Check the subparser used, pass the args to the class named after it.
    Subcommand.run_command(command, args)

if __name__ == '__main__':
    parsed = parse_args()
    main(*parsed)
