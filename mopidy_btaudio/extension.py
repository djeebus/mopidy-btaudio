import pkg_resources

from mopidy.config import Boolean, String, List
from mopidy.ext import Extension

from . import __version__


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
        schema['name'] = String()
        schema['pin'] = String()
        schema['interfaces_to_disable'] = List(optional=True)
        return schema

    def setup(self, registry):
        from .bt_audio import BtAudioController
        registry.add('frontend', BtAudioController)
