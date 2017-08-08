import logging
import pkg_resources

from mopidy.config import Boolean
from mopidy.ext import Extension

__version__ = '0.1.0'
logger = logging.getLogger('mopidy-btaudio')


class BtAudioExtension(Extension):
    dist_name = 'Mopidy-BtAudio'
    ext_name = 'btaudio'
    version = __version__

    def get_default_config(self):
        fp = pkg_resources.resource_stream('mopidy_btaudio', 'ext.conf')
        with fp:
            return fp.read()

    def get_config_schema(self):
        schema = super(BtAudioExtension, self).get_config_schema()
        return schema

    def setup(self, registry):
        from .bt_audio import BtAudioController
        registry.add('frontend', BtAudioController)
