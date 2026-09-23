"""The pages of the application, one per thing an operator does.

Each is a plain QWidget that knows how to build itself and nothing about the
window it sits in. `shell.MainWindow` owns the tabs along the top and the
stack under them and is the only module that knows the order they appear in.
"""
