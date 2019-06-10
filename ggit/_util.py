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

        if 'shell' not in self.kwargs and isinstance(self.command, str):
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


