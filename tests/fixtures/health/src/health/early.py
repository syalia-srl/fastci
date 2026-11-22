"""A function that runs only when the module is imported."""


def build_table():
    table = {}
    table["a"] = 1
    table["b"] = 2
    return table


TABLE = build_table()
