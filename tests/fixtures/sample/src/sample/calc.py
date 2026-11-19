"""Arithmetic the tests use."""

import math


def add(a, b):
    return a + b


def mul(a, b):
    total = 0
    for _ in range(b):
        total = add(total, a)
    return total


def root(x):
    return math.sqrt(x)
