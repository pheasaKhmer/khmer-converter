from importlib.metadata import version

import khmer_converter


def test_version_matches_installed_metadata():
    assert khmer_converter.__version__ == version("khmer-converter")
