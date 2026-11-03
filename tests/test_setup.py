import sys

import simulator


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


def test_simulator_importable():
    assert simulator.__name__ == "simulator"
