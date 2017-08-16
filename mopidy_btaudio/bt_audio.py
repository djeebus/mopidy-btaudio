import dbus
import functools
import logging
import pykka

from mopidy.audio.constants import PlaybackState
from mopidy.core import CoreListener
from mopidy.core.actor import Core

from mopidy_btaudio.agent import BlueAgent

logger = logging.getLogger('mopidy-btaudio')
dbus_properties_interface_name = 'org.freedesktop.DBus.Properties'


class ObjectManager(object):
    interface = None

    def __init__(self, bus):
        self._bus = bus
        self.objects = {}

    def add(self, dbus_object):
        logger.info('object added: %s', dbus_object.object_path)
        path = str(dbus_object.object_path)
        self.objects[path] = dbus_object

        self._added(dbus_object)

    def _added(self, dbus_object):
        pass

    def changed(self, dbus_object):
        logger.info('object changed: %s' % dbus_object.object_path)

        self._changed(dbus_object)

    def _changed(self, dbus_object):
        pass

    def remove(self, path):
        logger.info('object removed: %s' % path)
        del self.objects[path]

    def _remove(self, path):
        pass

    def start(self):
        self._start()

    def _start(self):
        pass

    def stop(self):
        self._stop()
        self.objects.clear()

    def _stop(self):
        pass


class AdapterManager(ObjectManager):
    interface = 'org.bluez.Adapter1'

    def __init__(self, bus, name):
        self.name = name
        super(AdapterManager, self).__init__(bus)

    def _added(self, dbus_object):
        self.configure_adapter(dbus_object)

    def configure_adapter(self, dbus_object):
        print('--- configuring adapter ---')
        int_ob = dbus.Interface(dbus_object, dbus_properties_interface_name)

        int_ob.Set(self.interface, 'Alias', self.name)
        int_ob.Set(self.interface, 'Powered', True)
        int_ob.Set(self.interface, 'Discoverable', True)

    def _stop(self):
        for path, dbus_object in self.objects.items():
            int_ob = dbus.Interface(
                dbus_object, dbus_properties_interface_name,
            )
            int_ob.Set(self.interface, 'Discoverable', False)

    def set_discoverable(self, enable):
        for path, adapter in self.objects.items():
            int_ob = dbus.Interface(adapter, dbus_properties_interface_name)

            discoverable = int_ob.Get(self.interface, 'Discoverable')
            if discoverable == enable:
                logger.info('discoverable already set to %s [%s]',
                            enable, path)
                continue

            logger.info('setting discoverable to %s', enable)
            int_ob.Set(self.interface, 'Discoverable', discoverable)


class DeviceManager(ObjectManager):
    interface = 'org.bluez.Device1'

    def __init__(self, bus, adapter_manager):
        super(DeviceManager, self).__init__(bus)

        self._devices_connected = set()
        self._adapter_manager = adapter_manager

    def _added(self, dbus_object):
        int_ob = dbus.Interface(dbus_object, dbus_properties_interface_name)
        connected = int_ob.Get(self.interface, 'Connected')
        if connected:
            path = str(dbus_object.object_path)
            self._add_connected_device(path)

    def _changed(self, dbus_object):
        int_ob = dbus.Interface(dbus_object, dbus_properties_interface_name)
        connected = int_ob.Get(self.interface, 'Connected')

        path = str(dbus_object.object_path)
        if connected:
            self._add_connected_device(path)
        else:
            self._remove_connected_device(path)

    def _remove(self, path):
        self._remove_connected_device(path)

    def _start(self):
        if self._devices_connected:
            return

        for dbus_ob in self.objects.values():
            int_ob = dbus.Interface(dbus_ob, self.interface)

            try:
                print('--- connecting to %s ---' % dbus_ob.object_path)
                int_ob.Connect()
            except dbus.DBusException as e:
                dbus_name = e.get_dbus_name()
                if dbus_name == 'org.freedesktop.DBus.Error.NoReply':
                    continue

                if dbus_name == 'org.bluez.Error.Failed':
                    continue

                raise

    def _remove_connected_device(self, path):
        print('--- disconnected from %s ---' % path)
        if path in self._devices_connected:
            self._devices_connected.remove(path)
            self._connections_updated()

    def _add_connected_device(self, path):
        print('--- connected to %s ---' % path)
        self._devices_connected.add(path)
        self._connections_updated()

    def _connections_updated(self):
        if self._devices_connected:
            self._adapter_manager.set_discoverable(False)
        else:
            self._adapter_manager.set_discoverable(True)


class MediaPlayerManager(ObjectManager):
    interface = 'org.bluez.MediaPlayer1'

    _bt_is_playing = set()
    _mopidy_was_playing = False

    def __init__(self, bus, core):
        super(MediaPlayerManager, self).__init__(bus)
        self.core = core

    def _added(self, dbus_object):
        int_ob = dbus.Interface(dbus_object, dbus_properties_interface_name)
        media_player_status = int_ob.Get(self.interface, 'Status')
        if media_player_status:
            self._on_bt_media_player_state(
                dbus_object.object_path, media_player_status,
            )

    def _changed(self, dbus_object):
        int_ob = dbus.Interface(dbus_object, dbus_properties_interface_name)
        media_player_status = int_ob.Get(self.interface, 'Status')
        if media_player_status:
            self._on_bt_media_player_state(
                dbus_object.object_path, media_player_status,
            )

    def _remove(self, path):
        self._on_bt_media_player_state(path, 'stopped')

    def _on_bt_media_player_state(self, path, state):
        if state == 'playing':
            self._bt_is_playing.add(path)

        if state in ['paused', 'stopped', 'error']:
            path = str(path)
            if path in self._bt_is_playing:
                self._bt_is_playing.remove(path)

        self.process_state()

    def process_state(self):
        if self._bt_is_playing:
            # pause mopidy, if necessary
            self._pause_mopidy()
        else:
            # mopidy resume, if necessary
            self._resume_mopidy()

    def _pause_mopidy(self):
        mopidy_status = self.core.playback.get_state().get()
        if mopidy_status == PlaybackState.PLAYING:
            logger.info('pausing playback')
            self._mopidy_was_playing = True
            self.core.playback.pause()

    def _resume_mopidy(self):
        if self._mopidy_was_playing:
            logger.info('resuming playback')
            self.core.playback.play()
            self._mopidy_was_playing = False


class BluetoothManager(object):
    def __init__(self, config, core):
        self._bus = dbus.SystemBus()

        bt_name = config['btaudio']['name']

        self._adapter_manager = AdapterManager(self._bus, bt_name)
        self._media_player_manager = MediaPlayerManager(self._bus, core)

        self.managers = [
            self._adapter_manager,
            DeviceManager(self._bus, self._adapter_manager),
            self._media_player_manager,
        ]

        self._managers_by_interface = {
            manager.interface: manager
            for manager in self.managers
        }

    def start(self):
        self._init_objects()

        for manager in self.managers:
            manager.start()

    def stop(self):
        for manager in self.managers:
            manager.stop()

    def _init_objects(self):
        root = self._bus.get_object('org.bluez', '/')
        object_manager = dbus.Interface(
            root, 'org.freedesktop.DBus.ObjectManager',
        )

        managed_objects = object_manager.GetManagedObjects()
        for path, interfaces in managed_objects.items():
            self.on_interfaces_added(path, interfaces)

        object_manager.connect_to_signal(
            'InterfacesAdded', self.on_interfaces_added,
        )

        object_manager.connect_to_signal(
            'InterfacesRemoved', self.on_interfaces_removed,
        )

    def on_interfaces_added(self, path, interface_names):
        dbus_ob = self._bus.get_object('org.bluez', path)
        props_interface = dbus.Interface(
            dbus_ob, dbus_properties_interface_name,
        )

        found = False
        for interface in interface_names:
            manager = self._managers_by_interface.get(interface)
            if not manager:
                continue

            manager.add(dbus_ob)

            found = True

        if found:
            props_interface.connect_to_signal(
                'PropertiesChanged',
                functools.partial(self.on_properties_changed, dbus_ob),
            )

    def on_interfaces_removed(self, path, interfaces):
        for interface in interfaces:
            manager = self._managers_by_interface.get(interface)
            if not manager:
                continue

            manager.remove(path)

    def on_properties_changed(
        self, dbus_ob, interface, changed_props, invalid_props,
    ):
        manager = self._managers_by_interface.get(interface)
        manager and manager.changed(dbus_ob)

    def on_playback_state_changed(self):
        self._media_player_manager.process_state()


class BtAudioController(pykka.ThreadingActor, CoreListener):
    def __init__(self, config, core):
        pykka.ThreadingActor.__init__(self)

        self.adapters = {}

        self.config = config
        self.core = core  # type: Core

        self.agent = BlueAgent(self.config['btaudio']['pin'])
        self._bt_mgr = BluetoothManager(self.config, core)

    def on_start(self):
        self._bt_mgr.start()
        self.agent.register_as_default()

    def on_stop(self):
        self._bt_mgr.stop()
        self.agent.unregister()

    def playback_state_changed(self, old_state, new_state):
        self._bt_mgr.on_playback_state_changed()
