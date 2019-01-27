#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for `ggit` package."""

import os
import tempfile
import shutil

import ggit

call = ggit.check_call


def run_ggit(args=[]):
    ggit.main(['ggit'] + args)


class TempDir(object):
    def __init__(self, cleanup=True):
        self.cleanup = cleanup
        self.td = tempfile.mkdtemp()

    def __enter__(self):
        ggit.check_call("mkdir -p '%s'" % self.td, shell='/bin/bash')
        return self

    def __exit__(self, *args):
        if self.cleanup:
            pass
            #shutil.rmtree(self.td)


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


def create_svn_repo(server_path):
    call(['mkdir','-p', server_path])
    call('svnadmin create .', cwd=server_path)


def create_svn_branch(server_path, working_dir, src_branch, dst_branch):
    local_path = os.path.join(working_dir, 'checkout')
    new_basedir = os.path.join(local_path, dst_branch)
    new_basedir, _ = os.path.split(new_basedir)

    with SvnCheckout(server_path, local_path):
        with ggit.Chdir(local_path):
            call(['mkdir', '-p', new_basedir])
            call('svn add --force *')

            src_path = os.path.join(server_path, src_branch)
            src_path = path_to_svn_server(src_path)
            call(['svn', 'cp', src_path, dst_branch])
            call(['svn', 'ci', '-m', 'Create %s branch' % dst_branch])

def create_empty_dir(server_path, working_dir, empty_dirname):
    with ggit.Chdir(working_dir):
        local_path = os.path.join(working_dir, 'checkout')
        empty_dirpath = os.path.join(local_path, empty_dirname)
        with SvnCheckout(server_path, local_path):
            svn_checkout(server_path, local_path)
            with ggit.Chdir(local_path):
                call(['mkdir', '-p', empty_dirpath])
                call('svn add *')
                call(['svn', 'ci', '-m', 'Creating empty %s' % empty_dirname])

def create_svn_commits(server_path, working_dir):
    with ggit.Chdir(working_dir):
        local_path = os.path.join(working_dir, 'checkout')
        with SvnCheckout(server_path, local_path):
            with ggit.Chdir(local_path):
                _, tmppath = tempfile.mkstemp(dir=local_path)
                call(['svn', 'add', tmppath])
                call(['svn', 'commit', '-m', 'Test commit of tmpfile.'], )


def path_to_svn_server(path):
    return 'file://%s' % path


def setup_svn_server():
    server_dir = TempDir()
    server_path = server_dir.td
    create_svn_repo(server_path)

    with TempDir() as td:
        create_empty_dir(server_path, td.td, TRUNK_BRANCH)
        create_svn_branch(server_path, td.td, TRUNK_BRANCH, APTRUNK_BRANCH)
        create_svn_commits(server_path, td.td)
    return server_dir



TRUNK_BRANCH = 'trunk/rtos'
APTRUNK_BRANCH = 'branches/ap/trunk/rtos'


def test_svn_repo_fixture_setup():
    setup_svn_server()


def init_ggit_repo(server):
    run_ggit(['init', server, 
              TRUNK_BRANCH+':trunk', 
              APTRUNK_BRANCH+':aptrunk'])


def test_smoketest_init():
    '''
    Test the ggit init command with the archetypical branches.
    '''
    with setup_svn_server() as serverdir:
        server = path_to_svn_server(serverdir.td)
        with TempDir() as td:
            with ggit.Chdir(td.td):
                init_ggit_repo(server)


def test_clone_from_ggit_repo():
    '''
    Test using ggit to clone from an exisiting ggit repo.
    '''
    with setup_svn_server() as serverdir:
        server = path_to_svn_server(serverdir.td)
        with TempDir() as orig, TempDir() as clone:
            with ggit.Chdir(orig.td):
                init_ggit_repo(server)
            run_ggit(['clone', orig.td, os.path.join(clone.td, 'checkout')])


# TODO Improve fixtures (going to be very deep, copy prone tests if I keep this
# up.

# TODO Test coverage needed:
# * Check that svn and git are clean on a clone
# * Check that svn works
# * Check ggit switch
# * Check ggit sync after a git checkout correctly switches branches.
# * Check ggit sync works after git clean -xfd
# * Check ggit configure works after deleting the .git/ggit folder
# * Test generate-ignore
