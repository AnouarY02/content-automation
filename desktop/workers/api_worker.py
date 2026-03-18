"""
ApiWorker — voert één API-call uit in een QThread

GEBRUIK:
  worker = ApiWorker(fn=campaigns_api.list, kwargs={"status": "pending"})
  worker.success.connect(lambda data: store.update_campaigns(data))
  worker.error.connect(lambda msg: store.report_error("Fout", msg))
  worker.start()

WAAROM QTHREAD:
  httpx-calls blokkeren de main thread → UI bevriest.
  Door elke call in een QThread te doen blijft de UI responsief.
  Qt's signal/slot mechanisme zorgt dat de callback veilig in de main thread uitvoert.
"""

from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class ApiWorker(QThread):
    """Voert één callable uit in een achtergrond-thread."""

    success  = pyqtSignal(object)   # data (dict / list / bool)
    error    = pyqtSignal(str)       # foutmelding
    finished = pyqtSignal()

    def __init__(
        self,
        fn: Callable,
        args: tuple = (),
        kwargs: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs or {}

    def run(self):
        try:
            from api.client import ApiResponse
            result = self._fn(*self._args, **self._kwargs)

            if isinstance(result, ApiResponse):
                if result.ok:
                    self.success.emit(result.data)
                else:
                    self.error.emit(result.error_message)
            else:
                self.success.emit(result)

        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {str(e)[:200]}")
        finally:
            self.finished.emit()


def run_api(
    fn: Callable,
    on_success: Callable[[Any], None],
    on_error: Callable[[str], None] | None = None,
    args: tuple = (),
    kwargs: dict | None = None,
    parent=None,
) -> ApiWorker:
    """
    Convenience functie: maak en start een ApiWorker.
    Houdt een referentie bij zodat de worker niet vroegtijdig door GC wordt opgeruimd.
    """
    worker = ApiWorker(fn=fn, args=args, kwargs=kwargs, parent=parent)
    worker.success.connect(on_success)
    if on_error:
        worker.error.connect(on_error)
    else:
        from state.store import AppStore
        worker.error.connect(lambda msg: AppStore.instance().report_error("API Fout", msg))

    # Bewaar referentie in parent om GC te voorkomen
    if parent and not hasattr(parent, "_workers"):
        parent._workers = []
    if parent:
        parent._workers.append(worker)
        worker.finished.connect(lambda: parent._workers.remove(worker) if worker in parent._workers else None)

    worker.start()
    return worker
