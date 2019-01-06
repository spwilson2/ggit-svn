#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for `ggit` package."""

import pytest
import os
import subprocess


def test_legacy():
    path = os.path.dirname(os.path.abspath(__file__))
    test = os.path.join(path, 'test.sh')
    ggit = os.path.join(path, '..', 'ggit.py')
    subprocess.check_call([test, ggit])
