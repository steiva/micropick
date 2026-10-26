"""The pages of the application, one per thing an operator does.

Each is a plain QWidget that knows how to build itself and nothing about the
window it sits in. `shell.MainWindow` owns the top bar and the stack under
them, and is the only module that knows which page is which step of a task.
"""
