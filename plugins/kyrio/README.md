# README

## Running the Tests

```sh
# -t tests is the part that's easy to get wrong.
# It sets the top-level directory, which puts tests/ on sys.path so import _path resolves — and _path is what puts scripts/ on the path so from kyrio import config works.
# Without -t tests you get an import error, not a test failure.
# 
# No third-party runner, no pip install, nothing to configure — stdlib unittest only, same command on any machine with Python 3.12+.

# From kyrio/plugins/kyrio/:
python -m unittest discover -s tests -t tests          # all 79
python -m unittest discover -s tests -t tests -v       # name each test
python -m unittest discover -s tests -t tests -p test_config.py     # one module
python -m unittest discover -s tests -t tests -k test_root_true     # one test, by substring
```