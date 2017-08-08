import bt_manager
import pykka
from mopidy.core import CoreListener
from mopidy.core.actor import Core


class BtAudioController(pykka.ThreadingActor, CoreListener):
    def __init__(self, config, core):
        pykka.ThreadingActor.__init__(self)
        self.config = config
        self.core = core  # type: Core

    def on_start(self):
        pass

    def on_stop(self):
        pass
