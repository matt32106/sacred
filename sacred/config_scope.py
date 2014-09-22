#!/usr/bin/env python
# coding=utf-8
from __future__ import division, print_function, unicode_literals
import ast
from copy import copy
from functools import update_wrapper
import inspect
import json
import re
from sacred.custom_containers import dogmatize, undogmatize

try:
    import numpy as np
except ImportError:
    np = None


__sacred__ = True


def get_function_body_code(func):
    func_code_lines, start_idx = inspect.getsourcelines(func)
    filename = inspect.getfile(func)
    func_code = ''.join(func_code_lines)
    arg = "(?:[a-zA-Z_][a-zA-Z0-9_]*)"
    arguments = r"{0}(?:\s*,\s*{0})*".format(arg)
    func_def = re.compile(
        r"^[ \t]*def[ \t]*{}[ \t]*\(\s*({})?\s*\)[ \t]*:[ \t]*\n\s*".format(
            func.__name__, arguments), flags=re.MULTILINE)
    defs = list(re.finditer(func_def, func_code))
    assert defs
    line_offset = func_code[:defs[0].end()].count('\n')
    func_body = func_code[defs[0].end():]
    body_code = compile(inspect.cleandoc(func_body), filename, "exec",
                        ast.PyCF_ONLY_AST)
    body_code = ast.increment_lineno(body_code, n=start_idx+line_offset-1)
    body_code = compile(body_code, filename, "exec")
    return body_code


class ConfigScope(dict):
    def __init__(self, func):
        super(ConfigScope, self).__init__()
        self.arg_spec = inspect.getargspec(func)
        assert self.arg_spec.varargs is None, \
            "varargs are not allowed for ConfigScope functions"
        assert self.arg_spec.keywords is None, \
            "kwargs are not allowed for ConfigScope functions"
        assert self.arg_spec.defaults is None, \
            "default values are not allowed for ConfigScope functions"

        self._func = func
        update_wrapper(self, func)
        self._body_code = get_function_body_code(func)
        self._initialized = False
        self.added_values = set()
        self.typechanges = {}

    def __call__(self, fixed=None, preset=None, fallback=None):
        """
        Execute this ConfigScope. This will evaluate the function body and
        fill the relevant local variables into entries into keys in this
        dictionary.

        :param fixed: Dictionary of entries that should stay fixed during the
                      evaluation. All of them will be part of the final config.
        :type fixed: dict
        :param preset: Dictionary of preset values that will be available during
                       the evaluation (if they are declared in the function
                       argument list). All of them will be part of the final
                       config.
        :type preset: dict
        :param fallback: Dictionary of fallback values that will be available
                         during the evaluation (if they are declared in the
                         function argument list). They will NOT be part of the
                         final config.
        :type fallback: dict
        :return: self
        :rtype: ConfigScope
        """
        self._initialized = True
        self.clear()
        cfg_locals = dogmatize(fixed or {})
        fallback = fallback or {}
        preset = preset or {}
        fallback_view = {}

        available_entries = set(preset.keys()) | set(fallback.keys())

        for a in self.arg_spec.args:
            if a not in available_entries:
                raise KeyError("'%s' not in preset for ConfigScope. "
                               "Available options are: %s" %
                               (a, available_entries))
            if a in preset:
                cfg_locals[a] = preset[a]
            else:  # a in fallback
                fallback_view[a] = fallback[a]

        cfg_locals.fallback = fallback_view
        eval(self._body_code, copy(self._func.__globals__), cfg_locals)
        self.added_values = cfg_locals.revelation()
        self.typechanges = cfg_locals.typechanges

        # fill in the unused presets
        for p in preset:
            if p not in cfg_locals:
                cfg_locals[p] = preset[p]

        for k, v in cfg_locals.items():
            if k.startswith('_'):
                continue
            if np and isinstance(v, np.bool_):
                # fixes an issue with numpy.bool_ not being json-serializable
                self[k] = bool(v)
                continue
            try:
                json.dumps(v)
                self[k] = undogmatize(v)
            except TypeError:
                pass
        return self

    def __getitem__(self, item):
        assert self._initialized, "ConfigScope has to be executed before access"
        return dict.__getitem__(self, item)