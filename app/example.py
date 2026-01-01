def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def complex_function(x: int) -> int:
    """Demonstrate complexity check."""
    if x > 0:
        if x > 10:
            return 1
        else:
            return 2
    else:
        return 3
