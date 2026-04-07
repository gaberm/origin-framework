import pkgutil
import importlib
import os

from .base_adapter import BaseAdapter
from .adapter_worker import AdapterWorker

models_path = os.path.join(os.path.dirname(__file__), "model_adapter")
for _, module_name, _ in pkgutil.iter_modules([models_path]):
    importlib.import_module(f".model_adapter.{module_name}", package=__name__)
