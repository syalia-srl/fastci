"""Imported by a test; nothing in it is called."""


def never_called(x):
    a = x + 1
    b = a + 1
    c = b + 1
    d = c + 1
    e = d + 1
    f = e + 1
    g = f + 1
    return g


def outer():
    def inner():
        return 1

    return inner
