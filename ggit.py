import argparse
import logging
import pprint
import tempfile
import os
import subprocess
import shutil

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

    pprint.pprint(('Call: ', args, kwargs))
    return subprocess.check_output(*args, **kwargs)


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
            # TODO Assert user had git-svn installed.
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
            call('svn revert -q -R .')
            # TODO Update to same revision as the git branch.
            call('svn update -q --force --accept theirs-full --set-depth=infinity')
            call('git checkout --force')



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
    pass


class Pull(Subcommand):
    '''
    Fetch the latest svn changes and rebase the current branch on top of them.
    '''


class Update(Subcommand):
    '''
    Update .svn to the current branch's svn revision and branch.
    '''


def parse_args():
    parser = argparse.ArgumentParser(prog='ggit')
    subparsers = parser.add_subparsers(dest='command')
    Subcommand.init_parsers(subparsers)
    options = parser.parse_args()
    return options.command, vars(options)

def main(command, args):
    # Check the subparser used, pass the args to the class named after it.
    Subcommand.run_command(command, args)

if __name__ == '__main__':
    parsed = parse_args()
    main(*parsed)
