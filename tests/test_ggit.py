#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for `ggit` package."""

import os
import tempfile
import shutil

import ggit

call = ggit.check_call


class FailedTestException(Exception):
    pass


def run_ggit(args=[]):
    ggit.main(['ggit'] + args)


class TempDir(object):
    def __init__(self, cleanup=True):
        self.cleanup = cleanup
        self.path = tempfile.mkdtemp()

    def create(self):
        call(['mkdir', '-p', self.path])

    def delete(self):
        shutil.rmtree(self.path)

    def __enter__(self):
        self.create()
        return self

    def __exit__(self, *args):
        if self.cleanup:
            pass
            # self.delete()


def svn_checkout(server_path, working_dir):
    call(['svn', 'co', path_to_svn_server(server_path), working_dir])


class SvnCheckout():
    def __init__(self, src, dst, cleanup=True):
        self.cleanup = cleanup
        self.src = src
        self.dst = dst

    def __enter__(self):
        svn_checkout(self.src, self.dst)
        return self

    def __exit__(self, *args):
        if self.cleanup:
            shutil.rmtree(self.dst)


def path_to_svn_server(path):
    return 'file://%s' % path


def setup_svn_server():
    with TempDir() as td:
        create_svn_branch(server_path, td.path, TRUNK_BRANCH, APTRUNK_BRANCH)
        create_svn_commits(server_path, td.path)
    return server_dir


class SvnRepoFixture():
    def __init__(self):
        pass

    def create_svn_repo(self):
        call(['mkdir', '-p', self.server_path])
        call(['svnadmin', 'create', self.server_path])

    def commit_empty_dir(self, empty_dirname):
        with TempDir() as td:
            local_path = os.path.join(td.path, 'checkout')
            empty_dirpath = os.path.join(local_path, empty_dirname)
            with SvnCheckout(self.server_path, local_path),\
                    ggit.Chdir(local_path):
                call(['mkdir', '-p', empty_dirpath])
                call('svn add *')
                call(['svn', 'ci', '-m', 'Creating empty %s' % empty_dirname])

    def commit_empty_file(self, repo_dir):
        with TempDir() as td:
            local_path = os.path.join(td.path, 'checkout')
            with SvnCheckout(self.server_path, local_path),\
                    ggit.Chdir(local_path):
                repo_dir = os.path.join(local_path, repo_dir)
                _, tmppath = tempfile.mkstemp(dir=repo_dir)
                call(['svn', 'add', tmppath])
                call(['svn', 'commit', '-m', 'Test commit of tmpfile.'], )

    def commit_branch(self, src_branch, dst_branch):
        with TempDir() as td:
            local_path = os.path.join(td.path, 'checkout')
            new_basedir = os.path.join(local_path, dst_branch)
            new_basedir, _ = os.path.split(new_basedir)

            with SvnCheckout(self.server_path, local_path),\
                    ggit.Chdir(local_path):
                call(['mkdir', '-p', new_basedir])
                call('svn add --force *')

                src_path = os.path.join(self.server_path, src_branch)
                src_path = path_to_svn_server(src_path)
                call(['svn', 'cp', src_path, dst_branch])
                call(['svn', 'ci', '-m', 'Create %s branch' % dst_branch])

    def _setup(self):
        server_tmpdir = TempDir()
        self._cleanup = [server_tmpdir.__enter__()]
        self.server_path = server_tmpdir.path
        self.server_url = path_to_svn_server(self.server_path)

        self.create_svn_repo()
        self.commit_empty_dir(TRUNK_BRANCH)
        self.commit_empty_file(TRUNK_BRANCH)
        self.commit_branch(TRUNK_BRANCH, APTRUNK_BRANCH)

    def __enter__(self):
        self._setup()
        return self

    def __exit__(self, *args):
        for i in self._cleanup:
            i.__exit__(*args)


class FixtureContainer():
    def __init__(self, fixtures):
        self.fixtures = fixtures

    def register(self, fixture):
        fixture.__enter__()
        self.fixtures.append(fixture)

    def __enter__(self):
        for f in self.fixtures:
            f.__enter__()
        return self

    def __exit__(self, *args):
        for f in self.fixtures():
            f.__exit__(*args)


TRUNK_BRANCH = 'trunk/rtos'
APTRUNK_BRANCH = 'branches/ap/trunk/rtos'


class PairedRepos():
    def __init__(self):
        pass

    def __enter__(self):
        self.svn = SvnRepoFixture()
        self.ggit = TempDir()

        self.svn.__enter__()
        self.ggit.__enter__()

        with ggit.Chdir(self.ggit.path):
            call('git init --bare'.split())

        with TempDir() as tmp_clone:
            with ggit.Chdir(tmp_clone.path):
                init_ggit_repo(self.svn.server_url)
                call(['git', 'remote', 'add', 'origin', self.ggit.path])
                run_ggit(['push', 'origin'])
        return self

    def __exit__(self, *args):
        self.svn.__exit__(*args)
        self.ggit.__exit__(*args)


def test_svn_repo_fixture_setup():
    with SvnRepoFixture():
        pass


def init_ggit_repo(server):
    run_ggit(['init', server,
              TRUNK_BRANCH+':trunk',
              APTRUNK_BRANCH+':aptrunk'])


def test_smoketest_init():
    '''
    Test the ggit init command with the archetypical branches.
    '''
    with SvnRepoFixture() as svn_repo:
        with TempDir() as td:
            with ggit.Chdir(td.path):
                init_ggit_repo(svn_repo.server_url)


def test_clone_from_ggit_repo():
    '''
    Test using ggit to clone from an exisiting ggit repo.

    Ensure it correctly sets up the cloned repository to use git-svn.
    '''
    with SvnRepoFixture() as svn_repo:
        with TempDir() as orig, TempDir() as clone:
            checkout_path = os.path.join(clone.path, 'checkout')
            with ggit.Chdir(orig.path):
                init_ggit_repo(svn_repo.server_url)
            run_ggit(['clone', orig.path, checkout_path])
            with ggit.Chdir(checkout_path):
                call('git svn fetch'.split())
            # TODO Also ensure the cloned repo can fetch updates.


def test_push_ggit_repo():
    with PairedRepos():
        pass


def test_clone_from_remote():
    with PairedRepos() as repos:
        run_ggit(['clone', repos.ggit.path])


def test_ggit_switch_branches():
    with PairedRepos() as repos:
        with TempDir() as clone:
            checkout_path = os.path.join(clone.path, 'checkout')
            run_ggit(['clone', repos.ggit.path, checkout_path])

            with ggit.Chdir(checkout_path):
                run_ggit('switch origin/svn/aptrunk'.split())
                output = ggit.call_output('svn info --show-item url'.split())
                if 'ap/trunk/rtos' not in output:
                    raise FailedTestException(
                            "ggit switch didn't switch svn path.\n"
                            "Repo Path: %s\n"
                            "Output: %s\n" % (checkout_path, output))


def test_ggit_sync_switches_branches():
    with PairedRepos() as repos:
        with TempDir() as clone:
            checkout_path = os.path.join(clone.path, 'checkout')
            run_ggit(['clone', repos.ggit.path, checkout_path])

            with ggit.Chdir(checkout_path):
                call('git checkout origin/svn/aptrunk'.split())
                run_ggit('sync --force'.split())
                output = ggit.call_output('svn info --show-item url'.split())
                if 'ap/trunk/rtos' not in output:
                    raise FailedTestException(
                            "git sync didn't switch svn path.\n"
                            "Repo Path: %s\n"
                            "Output: %s\n" % (checkout_path, output))


def test_ggit_sync_works_after_git_clean():
    with PairedRepos() as repos:
        with TempDir() as clone:
            checkout_path = os.path.join(clone.path, 'checkout')
            run_ggit(['clone', repos.ggit.path, checkout_path])

            with ggit.Chdir(checkout_path):
                call('git checkout origin/svn/aptrunk'.split())
                call('git clean -xfd')
                run_ggit('sync'.split())
                output = ggit.call_output('svn info --show-item url'.split())
                if 'ap/trunk/rtos' not in output:
                    raise FailedTestException(
                            "git sync failed after a git clean\n"
                            "Repo Path: %s\n"
                            "Output: %s\n" % (checkout_path, output))


# TODO Test coverage needed:
# * Check that svn and git are clean on a clone
# * Check that ggit sync changes to the correct svn revision.
# * Check ggit configure works after deleting the .git/ggit folder
# * Test generate-ignore
