#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for `ggit` package."""

import os
import ggit

def test_legacy():
    path = os.path.dirname(os.path.abspath(__file__))
    test = os.path.join(path, 'test.sh')
    ggit.forward_check_call(test)
