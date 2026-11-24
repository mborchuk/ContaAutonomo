"""Process state the core sets once at start-up and reads at request time.

`module_manager` is the ModuleManager that `contaautonomo.app.bootstrap()`
creates (None before it runs). Routes, `log_activity` and the app read it
here; tests set it here.
"""
module_manager = None
