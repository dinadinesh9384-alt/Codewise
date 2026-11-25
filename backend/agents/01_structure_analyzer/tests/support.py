from importlib import import_module

prefix = "agents.01_structure_analyzer."


def module(name):
    return import_module(prefix + name)
