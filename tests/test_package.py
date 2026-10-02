from freqtrade_truth import __version__


def test_version_is_defined() -> None:
    assert __version__ == "0.0.1"
