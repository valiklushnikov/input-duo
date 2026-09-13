"""Вторая половина цикла: импорт отложенный, но для ast он такой же узел."""


def back():
    from .cycle_a import B

    return B
