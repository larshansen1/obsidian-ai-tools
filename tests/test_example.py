from app.example import add, complex_function


def test_add() -> None:
    assert add(1, 2) == 3


def test_complex_function() -> None:
    assert complex_function(11) == 1
    assert complex_function(5) == 2
    assert complex_function(-1) == 3
